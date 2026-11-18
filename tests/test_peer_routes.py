"""A VPN default route must never displace the LAN signaled to phones."""
import socket
import threading
import unittest
from unittest.mock import Mock, patch

from flowledger import peer_wire as wire


class PeerRoutesTests(unittest.TestCase):
    def test_enumeration_does_not_assume_10_means_vpn_or_advertise_a_tunnel(self):
        up, broadcast, point_to_point, loopback = 1, 2, 16, 8
        adapters = [('nordlynx', up | point_to_point, '10.5.0.2'),
                    ('docker0', up | broadcast, '172.17.0.1'),
                    ('lo', up | loopback, '127.0.0.1'),
                    ('enp2s0', broadcast, '192.168.2.1'),
                    ('wlp0s1', up | broadcast, '10.2.3.4'),
                    ('enp3s0', up | broadcast, '192.168.0.70')]
        with patch.object(wire, '_ipv4_interfaces', return_value=[(*a, None) for a in adapters]):
            self.assertEqual(wire.lan_hosts(), ['192.168.0.70', '10.2.3.4'])
            self.assertEqual(wire.lan_hosts('127.0.0.1'), ['127.0.0.1'])
        with patch.object(wire, '_ipv4_interfaces', return_value=[(*a, None) for a in adapters[:4]]):
            self.assertEqual(wire.lan_hosts(), [])

    def test_android_denied_enumeration_does_not_disable_discovery_or_advertise_vpn_ip(self):
        sock = Mock()
        stop = threading.Event()
        with patch.object(wire, '_ipv4_interfaces', side_effect=PermissionError()), \
                patch.object(wire, '_android', return_value=True), patch('socket.socket', return_value=sock):
            self.assertEqual(wire.lan_hosts(), [])
            wire.discover(stop, 'a' * 64, 4321, self.fail, ready=stop.set)
        sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                        socket.inet_aton(wire.GROUP) + socket.inet_aton('0.0.0.0'))

    def test_multihomed_beacons_keep_working_if_one_adapter_fails(self):
        # Control the interfaces without touching the test machine's network.
        # Assert memberships, outbound interface, and a wire-compatible reply
        # to a stock 0.4.0 phone that only sends its existing multicast beacon.
        host = '1' * 64
        phone = '2' * 64
        sender = ('10.1.1.20', wire.DISCOVERY_PORT)
        local = ['10.1.1.5', '192.168.0.70', '10.1.7.1']
        stopped = threading.Event()
        sock = Mock()
        seen, joined, outbound = [], [], []
        def set_opt(level, option, value):
            if level == socket.IPPROTO_IP and option == socket.IP_ADD_MEMBERSHIP:
                ip = socket.inet_ntoa(value[4:])
                if ip == local[-1]:
                    raise OSError('interface lost')
                joined.append(ip)
            if level == socket.IPPROTO_IP and option == socket.IP_MULTICAST_IF:
                outbound.append(socket.inet_ntoa(value))
        def got(size):
            stopped.set()
            return wire.MAGIC + phone.encode() + b' 47621', sender
        sock.setsockopt.side_effect = set_opt
        sock.recvfrom.side_effect = got
        with patch.object(wire, '_lan_routes', return_value=[(ip, None, None) for ip in local]), patch('socket.socket', return_value=sock):
            wire.discover(stopped, host, 4321, lambda identity, address: seen.append((identity, address)))
        self.assertEqual(joined,local[:2])
        self.assertEqual(outbound,local[:2])
        self.assertEqual(seen,[(phone,'10.1.1.20:47621')])
        beacon = wire.MAGIC + host.encode() + b' 4321'
        self.assertEqual([c.args for c in sock.sendto.call_args_list],[(beacon,(wire.GROUP,wire.DISCOVERY_PORT)),
            (beacon,(wire.GROUP,wire.DISCOVERY_PORT)),(beacon,sender)])

    def test_directed_broadcast_uses_actual_netmask(self):
        self.assertEqual(wire._broadcast_address('192.168.2.70', '255.255.252.0'), '192.168.3.255')
        for mask in ('255.255.255.254', '255.255.255.255', '0.0.0.0'):
            self.assertIsNone(wire._broadcast_address('192.168.2.70', mask))

    def test_listener_survives_no_routes_and_multicast_failure_then_recovers(self):
        import time
        stop, sock = threading.Event(), Mock()
        phone = '2' * 64
        local = ('192.168.2.70', '192.168.3.255', 'wlp0s20f3')
        seen, messages = [], []
        clock = [10.0]
        def set_opt(level, opt, data):
            if level == socket.IPPROTO_IP and opt == socket.IP_ADD_MEMBERSHIP and clock[0] < 20:
                raise OSError(19, 'No such device')
        sock.setsockopt.side_effect = set_opt
        def receive(size):
            clock[0] += 5
            if clock[0] > 20:
                stop.set()
            return wire.MAGIC + phone.encode() + b' 47621', ('192.168.3.1', wire.DISCOVERY_PORT)
        sock.recvfrom.side_effect = receive
        with patch.object(wire, '_lan_routes', side_effect=[PermissionError(13, 'denied'), [local], [local]]), \
                patch.object(wire, '_android', return_value=False), patch('socket.socket', return_value=sock), \
                patch.object(time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(wire, '_send_broadcast') as broadcast:
            wire.discover(stop, 'a' * 64, 47621, lambda who, endpoint: seen.append(endpoint),
                          on_status=messages.append)
        sock.bind.assert_called_once_with(('', wire.DISCOVERY_PORT))
        self.assertEqual(seen, ['192.168.3.1:47621'] * 3)
        self.assertIn('denied', messages[0])
        self.assertIn('Multicast join on 192.168.2.70', messages[1])
        self.assertEqual(messages[-1], '')
        self.assertEqual(broadcast.call_count, 2)
        self.assertEqual(broadcast.call_args.args, (sock, wire.MAGIC + b'a' * 64 + b' 47621', *local))

    def test_real_broadcast_and_reply_when_multicast_is_blocked(self):
        # Real loopback datagrams only: no contact with devices on the test runner's LAN.
        port = wire.DISCOVERY_PORT
        stop, reply = threading.Event(), threading.Event()
        addresses = []
        def seen(identity, endpoint):
            addresses.append((identity, endpoint))
            reply.set()
        real_socket = socket.socket
        class NoMulticast(real_socket):
            def setsockopt(self, level, opt, *args):
                if level == socket.IPPROTO_IP and opt in (socket.IP_ADD_MEMBERSHIP, socket.IP_MULTICAST_IF):
                    raise PermissionError('multicast blocked for test')
                super().setsockopt(level, opt, *args)
        phone = wire.MAGIC + b'2' * 64 + b' 4321'
        with real_socket(socket.AF_INET, socket.SOCK_DGRAM) as observer:
            observer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            observer.bind(('127.255.255.255', port))
            observer.settimeout(3)
            with patch.object(wire, '_lan_routes', return_value=[('127.0.0.1', '127.255.255.255', 'lo')]), \
                    patch('socket.socket', NoMulticast):
                thread = threading.Thread(target=wire.discover, args=(stop, '1' * 64, wire.PORT, seen), daemon=True)
                thread.start()
                try:
                    data, source = observer.recvfrom(256)
                    self.assertEqual(data, wire.MAGIC + b'1' * 64 + b' 47621')
                    self.assertEqual(source, ('127.0.0.1', port))
                    with real_socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                        sender.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        sender.bind(('127.0.0.2', port))
                        sender.sendto(phone, source)
                        sender.settimeout(2)
                        self.assertEqual(sender.recvfrom(256), (data, source))
                    self.assertTrue(reply.wait(1))
                    self.assertEqual(addresses[0], ('2' * 64, '127.0.0.2:4321'))
                finally:
                    stop.set()
                    thread.join(2)
