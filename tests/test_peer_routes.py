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
        with patch.object(wire, '_ipv4_interfaces', return_value=adapters):
            self.assertEqual(wire.lan_hosts(), ['192.168.0.70', '10.2.3.4'])
            self.assertEqual(wire.lan_hosts('127.0.0.1'), ['127.0.0.1'])
        with patch.object(wire, '_ipv4_interfaces', return_value=adapters[:4]):
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
        with patch.object(wire, 'lan_hosts', return_value=local), patch('socket.socket', return_value=sock):
            wire.discover(stopped, host, 4321, lambda identity, address: seen.append((identity, address)))
        self.assertEqual(joined,local[:2])
        self.assertEqual(outbound,local[:2])
        self.assertEqual(seen,[(phone,'10.1.1.20:47621')])
        beacon = wire.MAGIC + host.encode() + b' 4321'
        self.assertEqual([c.args for c in sock.sendto.call_args_list],[(beacon,(wire.GROUP,wire.DISCOVERY_PORT)),
            (beacon,(wire.GROUP,wire.DISCOVERY_PORT)),(beacon,sender)])
