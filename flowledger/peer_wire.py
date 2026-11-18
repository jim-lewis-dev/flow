"""Length-delimited Flow peer protocol over pinned TLS. No HTTP or external server."""
from __future__ import annotations

import array
import contextlib
import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import socket
import ssl
import sqlite3
import struct
import threading
import sys

PORT = 47621
GROUP = '239.255.46.21'
DISCOVERY_PORT = 47622
MAGIC = b'FLOW-PEER/1 '
FRAME = 64 * 1024 * 1024
HEADER = 4096
CONNECT_TIMEOUT = 3
IO_TIMEOUT = 30


class Refused(ValueError):
    """A public protocol error with no private paths, input values or credentials."""


def pin(cert):
    return hashlib.sha256(cert).hexdigest()


def is_id(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def parse_address(address):
    # IPv4 LAN discovery; manual hosts may be IPv4, DNS or [IPv6]:port.
    if not isinstance(address, str) or not address.strip():
        raise ValueError('Enter a network address such as 192.168.1.10:47621.')
    address = address.strip()
    match = re.fullmatch(r'(?:\[([0-9A-Fa-f:.%\w-]+)\]|([a-zA-Z0-9._-]+))(?::(\d{1,5}))?', address)
    if not match:
        raise ValueError('Enter a host or host:port without a URL or path.')
    port = int(match.group(3) or PORT)
    if not 1 <= port <= 65535:
        raise ValueError('The port must be between 1 and 65535.')
    return match.group(1) or match.group(2), port


def address(host, port):
    return ('[' + host + ']' if ':' in host else host) + ':' + str(port)


def _exact(sock, size):
    result = bytearray()
    while len(result) < size:
        block = sock.recv(min(65536, size - len(result)))
        if not block:
            raise OSError('Peer disconnected before completing the transfer.')
        result.extend(block)
    return result


def read(sock, limit=FRAME):
    length, = struct.unpack('!I', _exact(sock, 4))
    if not 0 < length <= limit:
        raise Refused('Peer message exceeds the 64 MiB transfer limit.' if limit == FRAME
                      else 'Invalid or oversized peer header.')
    return json.loads(_exact(sock, length).decode('utf-8'))


def write(sock, value, limit=FRAME):
    data = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode('ascii')
    if len(data) > limit:
        raise Refused('Peer message exceeds the 64 MiB transfer limit.' if limit == FRAME
                      else 'Invalid or oversized peer header.')
    sock.sendall(struct.pack('!I', len(data)))
    sock.sendall(data)


def call(endpoint, expected, header, body=None, *, key_proof=None):
    """Pin the TLS peer before sending credentials or ledger data."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Actual certificate pin checked before the first write.
    host, port = parse_address(endpoint)
    op = header.get('op', '')
    nearby = op.startswith('nearby_')
    timeout = min(CONNECT_TIMEOUT, 1.5) if op == 'nearby_hello' else CONNECT_TIMEOUT
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as stream:
            fingerprint = pin(stream.getpeercert(binary_form=True))
            # The only unauthenticated query is the public opt-in device label.
            public_hello = (expected is None and header == {'op': 'nearby_hello'} and body is None
                            and key_proof is None)
            if not public_hello and (not isinstance(expected, str) or not hmac.compare_digest(fingerprint, expected)):
                raise ValueError('Device certificate does not match the expected device.')
            stream.settimeout(timeout if op == 'nearby_hello' else (5 if nearby else IO_TIMEOUT))
            write(stream, dict(header, v=1), HEADER)
            # Authenticate small headers before accepting any large ledger input.
            if key_proof is not None:
                from .peer_nearby import exchange_proof
                ack = read(stream, HEADER)
                if not isinstance(ack, dict) or ack.get('ready') is not True:
                    raise ValueError((ack.get('error') if isinstance(ack, dict) else '') or 'Other device declined pairing.')
                exchange_proof(stream, key_proof[0], None, key_proof[1])
            if body is not None:
                ack = read(stream, HEADER)
                if (header['op'] == 'pull' and isinstance(ack, dict)
                        and ack.get('result') == {'unchanged': True}):
                    return fingerprint, ack['result']
                if not isinstance(ack, dict) or ack.get('ready') is not True:
                    raise ValueError((ack.get('error') if isinstance(ack, dict) else '') or
                                     'Peer refused access to this request.')
                write(stream, body)
            reply = read(stream, HEADER if nearby else FRAME)
            if not isinstance(reply, dict) or 'result' not in reply:
                error = Refused if nearby else ValueError
                raise error((reply.get('error') if isinstance(reply, dict) else '') or 'Invalid peer response.')
            return fingerprint, reply['result']


class Listener:
    """A bounded set of TLS workers; an incomplete handshake cannot stall accept()."""
    def __init__(self, host, port, context, handle):
        self.context = context
        self.handle = handle
        self.stopped = threading.Event()
        self.slots = threading.BoundedSemaphore(12)
        self.socket = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind((host, port))
            self.socket.listen(32)
            self.socket.settimeout(0.4)
        except BaseException:
            self.socket.close()
            raise
        self.port = self.socket.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, name='flow-peer-listener', daemon=True)

    def _accept(self):
        while not self.stopped.is_set():
            try:
                conn, source = self.socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self.slots.acquire(blocking=False):
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn, source[0]),
                             name='flow-peer-request', daemon=True).start()

    def _serve(self, conn, host):
        try:
            with conn:
                conn.settimeout(5)
                with self.context.wrap_socket(conn, server_side=True) as stream:
                    try:
                        header = read(stream, HEADER)
                        if not isinstance(header, dict) or header.get('v') != 1:
                            raise ValueError('Unsupported peer protocol.')
                        stream.settimeout(IO_TIMEOUT)
                        result = self.handle(header, stream, host)
                        write(stream, {'result': result})
                    except Refused as exc:
                        write(stream, {'error': str(exc)})
                    except PermissionError:
                        write(stream, {'error': 'Access refused. Pair these devices again.'})
                    except (OSError, TypeError, LookupError, ValueError, RuntimeError, sqlite3.Error):
                        write(stream, {'error': 'Peer could not complete the request. Check the file and device status.'})
        except (OSError, ValueError):
            pass
        finally:
            self.slots.release()

    def close(self):
        self.stopped.set()
        self.socket.close()
        self.thread.join(timeout=1)


def _ipv4_interfaces():
    """Read Linux/Android interface addresses without netlink, sysfs or a default route."""
    item_size = 40 if struct.calcsize('P') == 8 else 32  # Linux struct ifreq
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        size = 32 * item_size
        while True:
            buf = array.array('B', bytes(size))
            arg = struct.pack('iP', size, buf.buffer_info()[0])
            used = struct.unpack('iP', fcntl.ioctl(probe, 0x8912, arg))[0]  # SIOCGIFCONF
            if used + item_size < size or size >= 1024 * item_size:
                break
            size *= 2
        buf = buf.tobytes()
        for off in range(0, used, item_size):
            name = buf[off:off+16].split(b'\0')[0]
            if struct.unpack_from('H', buf, off + 16)[0] != socket.AF_INET:
                continue
            try:
                req = fcntl.ioctl(probe, 0x8913, name.ljust(item_size, b'\0'))  # SIOCGIFFLAGS
            except OSError:
                continue
            flags = struct.unpack_from('H', req, 16)[0]
            ip = socket.inet_ntoa(buf[off+20:off+24])
            broadcast = None
            if flags & 2 and not flags & (8 | 16):  # IFF_BROADCAST, not loopback or point-to-point
                try:
                    mask = fcntl.ioctl(probe, 0x891b, name.ljust(item_size, b'\0'))  # SIOCGIFNETMASK
                    broadcast = _broadcast_address(ip, socket.inet_ntoa(mask[20:24]))
                except (OSError, ValueError):
                    pass  # Still usable for multicast.
            yield name.decode('ascii', 'replace'), flags, ip, broadcast


def _broadcast_address(ip, mask):
    network = ipaddress.IPv4Network((ip, mask), strict=False)
    # /31 and /32 have no broadcast hosts; never use the global broadcast.
    if 0 < network.prefixlen < 31:
        return str(network.broadcast_address)


def _lan_routes(host):
    """Usable LAN interfaces, without consulting the default route (often a VPN)."""
    if host not in ('', '0.0.0.0', '::'):
        return [(host, None, None)]
    candidates = []
    for name, flags, ip, broadcast in _ipv4_interfaces():
        if not flags & 1 or flags & (8 | 16):  # not UP, or LOOPBACK / POINTOPOINT
            continue
        if ip == '0.0.0.0' or ip.startswith('127.'):
            continue
        if (name.startswith(('nordlynx', 'tailscale', 'docker', 'veth', 'virbr', 'cni',
                             'flannel', 'rmnet', 'p2p', 'tun', 'tap', 'wg', 'zt')) or
                re.fullmatch(r'br-[0-9a-f]{12,}', name)):
            continue
        rank = 0 if name.startswith(('wl', 'wifi', 'en', 'eth')) else 1
        candidates.append((rank, name, ip, broadcast))
    return [(ip, broadcast, name) for _, name, ip, broadcast in sorted(candidates)]


def lan_hosts(host='0.0.0.0'):
    try:
        return list(dict.fromkeys(ip for ip, _, _ in _lan_routes(host)))
    except OSError:
        return []


def _send_broadcast(sock, beacon, ip, broadcast, name):
    target = (broadcast, DISCOVERY_PORT)
    # Use the listener socket so the existing phone's unicast reply reaches it.
    # Linux IP_PKTINFO gives this one packet the LAN interface and source IP,
    # even if a VPN also installed routes. No raw socket or special permission.
    if sys.platform.startswith('linux') and hasattr(sock, 'sendmsg'):
        index = socket.if_nametoindex(name)
        info = struct.pack('=I4s4s', index, socket.inet_aton(ip), bytes(4))
        sock.sendmsg([beacon], [(socket.IPPROTO_IP, getattr(socket, 'IP_PKTINFO', 8), info)], 0, target)
    else:
        sock.sendto(beacon, target)


def _android():
    return (sys.platform == 'android' or hasattr(sys, 'getandroidapilevel') or
            bool(os.environ.get('ANDROID_ROOT') and os.environ.get('ANDROID_DATA')))


def discover(stopped, identity, port, note, interface='0.0.0.0', ready=None, on_status=None):
    beacon = MAGIC + identity.encode('ascii') + b' ' + str(port).encode('ascii')
    import time
    with contextlib.closing(socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', DISCOVERY_PORT))
        sock.settimeout(0.5)
        group = socket.inet_aton(GROUP)
        joined = set()
        options_error = []
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError as exc:
            options_error.append(f'Enable broadcast: {exc}')
        first, previous_status = True, None
        next_refresh = next_send = 0
        replied = {}
        # Beacons are untrusted hints; bound their rate before writing the local cache.
        bucket_at, tokens, last_seen = time.monotonic(), 32.0, {}
        while not stopped.is_set():
            now = time.monotonic()
            if now >= next_refresh:
                next_refresh = now + 5
                setup_errors = list(options_error)
                try:
                    routes = _lan_routes(interface)
                except OSError as exc:
                    routes = []
                    setup_errors.append(f'List LAN interfaces: {exc}')
                if not routes:
                    if _android():
                        routes = [('0.0.0.0', None, None)]
                    elif not setup_errors:
                        setup_errors.append('No active LAN interface.')
                active = {ip for ip, _, _ in routes}
                for ip in joined - active:
                    with contextlib.suppress(OSError):
                        sock.setsockopt(socket.IPPROTO_IP, socket.IP_DROP_MEMBERSHIP, group + socket.inet_aton(ip))
                    joined.remove(ip)
                for ip in sorted(active - joined):
                    try:
                        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, group + socket.inet_aton(ip))
                        joined.add(ip)
                    except OSError as exc:
                        setup_errors.append(f'Multicast join on {ip}: {exc}')
                if first and ready is not None:
                    ready()
                first = False
            if now >= next_send:
                next_send = now + 2
                errors = list(setup_errors)
                for ip, broadcast, name in routes:
                    if ip in joined:
                        try:
                            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                            sock.sendto(beacon, (GROUP, DISCOVERY_PORT))
                        except OSError as exc:
                            errors.append(f'Multicast send on {ip}: {exc}')
                    if broadcast:
                        try:
                            _send_broadcast(sock, beacon, ip, broadcast, name)
                        except OSError as exc:
                            errors.append(f'Broadcast send to {broadcast}: {exc}')
                status = '; '.join(errors)[:500]
                if on_status is not None and status != previous_status:
                    if on_status(status) is not False:
                        previous_status = status
            if stopped.is_set():
                break
            try:
                data, source = sock.recvfrom(256)
            except socket.timeout:
                continue
            if not data.startswith(MAGIC):
                continue
            try:
                candidate, tcp_port = data[len(MAGIC):].decode('ascii').split(' ')
                if candidate != identity and is_id(candidate) and 0 < int(tcp_port) < 65536:
                    now = time.monotonic()
                    tokens = min(32.0, tokens + (now - bucket_at) * 16)
                    bucket_at = now
                    if tokens < 1 or now - last_seen.get(candidate, -2) < 1:
                        continue
                    tokens -= 1
                    if len(last_seen) >= 256:
                        last_seen = {k: v for k, v in last_seen.items() if now - v < 4}
                    last_seen[candidate] = now
                    note(candidate, address(source[0], int(tcp_port)))
                    # A phone may be able to send multicast even when incoming
                    # multicast is filtered. Its existing UDP listener also accepts
                    # the ordinary beacon as a unicast reply. Never send a secret.
                    if source[1] == DISCOVERY_PORT and now - replied.get(source, -10) >= 10:
                        replied = {a: t for a, t in replied.items() if now - t < 10}
                        if len(replied) < 128:
                            replied[source] = now
                            with contextlib.suppress(OSError):
                                sock.sendto(beacon, source)
            except (UnicodeError, ValueError):
                continue
