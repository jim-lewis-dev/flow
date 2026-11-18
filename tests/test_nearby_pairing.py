"""Nearby pairing uses the two visible devices, not a typed secret or a trusted LAN."""
from contextlib import closing
from pathlib import Path
import socket
import tempfile
import time
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger import peer_nearby, peer_wire as wire
from flowledger.peer_network import PeerNode


class NearbyPairingTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)

    def node(self, name, port=0, directory=None, discovery=False):
        n = PeerNode(directory or self.root / name, name=name, host='127.0.0.1',
                     port=port, discovery=discovery, interval=3600)
        self.addCleanup(n.stop)
        n.start()
        return n

    def visible(self, requester, owner):
        requester.nearby_open()
        owner.nearby_open()
        # The radio listener would have supplied this address. No address or
        # certificate is typed or trusted by the person making the request.
        other = owner.status()
        requester._sighting(other['peer_id'], other['address'])
        devices = requester.nearby_poll()['devices']
        self.assertIn(other['peer_id'], [d['peer_id'] for d in devices])
        return other['peer_id']

    def prompt(self, requester, owner, identity):
        outgoing = requester.nearby_request(identity)['request']
        incoming = owner.nearby_poll()['request']
        self.assertEqual((outgoing['direction'], incoming['direction']), ('outgoing', 'incoming'))
        self.assertRegex(outgoing['number'], r'^[0-9]{6}$')
        self.assertEqual(outgoing['number'], incoming['number'])
        self.assertEqual((incoming['peer_id'], outgoing['peer_id']),
                         (requester.status()['peer_id'], owner.status()['peer_id']))
        self.assertFalse(outgoing['approved'])
        self.assertFalse(incoming['approved'])
        return outgoing['id'], incoming['id']

    def wait_for_trust(self, requester, owner):
        limit = time.monotonic() + 4
        while time.monotonic() < limit:
            requester.nearby_poll()
            owner.nearby_poll()
            if requester.peers() and owner.peers():
                break
            time.sleep(.03)
        self.assertEqual([owner.status()['peer_id']], [p['peer_id'] for p in requester.peers()])
        self.assertEqual([requester.status()['peer_id']], [p['peer_id'] for p in owner.peers()])

    def test_both_must_match_and_phone_can_join_without_inbound_tcp(self):
        phone = self.node('phone', discovery=True)
        desktop = self.node('desktop', discovery=True)
        ledger = self.root / 'lab.sqlite3'
        with closing(Repository(ledger)) as repo:
            example = repo.save_flow(dict(name='Sample', date='2026-11-01', amount='25', units='L',
                                          from_account='Tank', to_account='Filter'))
        dataset = desktop.share(ledger, 'Lab')
        phone_port = phone.status()['port']
        dial = socket.create_connection

        def restricted(address, *args, **kwargs):
            if address[1] == phone_port:
                raise ConnectionRefusedError('Simulated Wi-Fi policy: phone cannot accept connections')
            return dial(address, *args, **kwargs)

        with patch('socket.create_connection', side_effect=restricted):
            phone.nearby_open()
            desktop.nearby_open()
            desk_id = desktop.status()['peer_id']
            # Exercise the actual multicast listener and the device-name list,
            # without entering an address or injecting a discovery result.
            deadline = time.monotonic() + 10
            names = {}
            while time.monotonic() < deadline:
                names = {d['peer_id']: d['name'] for d in phone.nearby_poll()['devices']}
                if desk_id in names:
                    break
                time.sleep(.05)
            self.assertEqual(names.get(desk_id), 'desktop')
            outbound, inbound = self.prompt(phone, desktop, desk_id)
            phone.nearby_approve(outbound)
            phone.nearby_poll()
            self.assertEqual((phone.peers(), desktop.peers(), phone.available()), ([], [], []))
            desktop.nearby_approve(inbound)
            self.wait_for_trust(phone, desktop)
            self.assertEqual([dataset], [v['dataset'] for v in phone.available()])
            copy = self.root / 'phone-copy.sqlite3'
            phone.join(dataset, desk_id, copy)
            with closing(Repository(copy)) as repo:
                self.assertEqual(example, repo.get_flow(example['flow_id']))

    def test_closed_device_is_not_offered_and_cancelled_number_cannot_be_approved(self):
        a, b = self.node('sending'), self.node('receiving')
        a.nearby_open()
        details = b.status()
        a._sighting(details['peer_id'], details['address'])
        self.assertEqual(a.nearby_poll()['devices'], [])
        # A node that was not open at the last probe should be discoverable
        # on a subsequent cycle, without retyping a name or address.
        with patch('flowledger.peer_nearby._now', return_value=int(time.time()) + 10):
            peer = self.visible(a, b)
        outbound, inbound = self.prompt(a, b, peer)
        b.nearby_cancel(inbound)
        # A delayed press of an old button must never establish trust, even
        # if either UI temporarily still displays that old comparison.
        for action, args in ((a.nearby_approve, (outbound,)), (a.nearby_poll, ()),
                             (b.nearby_approve, (inbound,))):
            try:
                action(*args)
            except (ValueError, RuntimeError, OSError):
                pass
        self.assertEqual((a.peers(), b.peers()), ([], []))
        self.assertIsNone(b.nearby_poll()['request'])
        a.nearby_close()
        b.nearby_close()
        a.nearby_open()
        b.nearby_open()
        self.assertIsNone(a.nearby_poll()['request'])
        self.assertIsNone(b.nearby_poll()['request'])

    def test_expired_comparison_cannot_finish_pairing(self):
        a, b = self.node('sending'), self.node('receiving')
        outbound, inbound = self.prompt(a, b, self.visible(a, b))
        b.nearby_approve(inbound)
        with patch('flowledger.peer_nearby._now', return_value=int(time.time()) + 3600):
            with self.assertRaisesRegex(ValueError, 'no longer active'):
                a.nearby_approve(outbound)
            self.assertIsNone(a.nearby_poll()['request'])
            self.assertIsNone(b.nearby_poll()['request'])
            self.assertEqual((a.peers(), b.peers()), ([], []))

    def test_follower_can_approve_on_behalf_of_background_listener(self):
        desktop = self.node('desktop')
        gui = self.node('gui', directory=desktop.directory)
        self.assertFalse(gui.status()['owner'])
        phone = self.node('phone')
        gui.nearby_open()
        phone.nearby_open()
        # Even without discovery, a direct address can locate the invite.
        lookup = phone.nearby_lookup(desktop.status()['address'])
        peer = desktop.status()['peer_id']
        self.assertIn(peer, [d['peer_id'] for d in lookup['devices']])
        outbound, inbound = self.prompt(phone, gui, peer)
        gui.nearby_approve(inbound)
        self.assertFalse(gui.peers())
        self.assertFalse(phone.peers())
        phone.nearby_approve(outbound)
        self.wait_for_trust(phone, gui)
        gui.stop()
        self.assertEqual(desktop.peers()[0]['peer_id'], phone.status()['peer_id'])

    def test_device_at_reused_address_must_match_selected_identity(self):
        phone, original = self.node('phone'), self.node('original')
        peer = self.visible(phone, original)
        port = original.status()['port']
        original.stop()
        replacement = self.node('replacement', port=port)
        replacement.nearby_open()
        with self.assertRaises((ValueError, OSError, RuntimeError)):
            phone.nearby_request(peer)
        self.assertEqual(replacement.peers(), [])
        self.assertEqual(phone.peers(), [])
        self.assertIsNone(replacement.nearby_poll()['request'])

    def test_requester_must_prove_its_own_key_before_a_number_is_shown(self):
        phone, desktop = self.node('phone'), self.node('desktop')
        other = self.node('other')
        peer = self.visible(phone, desktop)
        wrong_key = peer_nearby._context(other)
        with patch.object(peer_nearby, '_context', return_value=wrong_key):
            with self.assertRaises((ValueError, OSError)):
                phone.nearby_request(peer)
        self.assertIsNone(desktop.nearby_poll()['request'])
        self.assertEqual((phone.peers(), desktop.peers()), ([], []))

    def test_lost_final_reply_is_recovered_without_repairing(self):
        phone, desktop = self.node('phone'), self.node('desktop')
        peer = self.visible(phone, desktop)
        outbound, inbound = self.prompt(phone, desktop, peer)
        desktop.nearby_approve(inbound)
        call = wire.call
        lost = []

        def lose_final_reply(endpoint, expected, header, *args, **kwargs):
            identity, result = call(endpoint, expected, header, *args, **kwargs)
            if header['op'] == 'nearby_step' and result.get('state') == 'paired':
                lost.append(True)
                raise ConnectionResetError('Wi-Fi disconnected after the desktop accepted')
            return identity, result

        with patch.object(wire, 'call', side_effect=lose_final_reply):
            try:
                phone.nearby_approve(outbound)
            except OSError:
                pass
        self.assertEqual(lost, [True])
        self.assertFalse(phone.peers())
        self.assertEqual(len(desktop.peers()), 1)
        self.wait_for_trust(phone, desktop)
        self.assertIsNone(phone.nearby_poll()['request'])

    def test_report_stale_listener_instead_of_advertising_unusable_pairing(self):
        owner = self.node('background')
        with owner._db() as db:
            # Old listener publishes its address but not its nearby capability.
            db.execute("DELETE FROM meta WHERE key='nearby_listener'")
        gui = self.node('gui', directory=owner.directory)
        self.assertFalse(gui.status()['owner'])
        with self.assertRaisesRegex(ValueError, 'Restart other Flow windows'):
            gui.nearby_open()
