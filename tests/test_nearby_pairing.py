"""Nearby pairing uses the two visible devices, not a typed secret or a trusted LAN."""
from contextlib import closing
from pathlib import Path
import socket
import threading
import tempfile
import time
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger import peer_nearby, peer_wire as wire
from flowledger.peer_network import PeerNode
from discovery_test_support import isolate_discovery


class NearbyPairingTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        isolate_discovery(self)

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
        self.assertTrue(outgoing['approved'])
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
        self.assertIn(owner.status()['peer_id'], [p['peer_id'] for p in requester.peers()])
        self.assertIn(requester.status()['peer_id'], [p['peer_id'] for p in owner.peers()])

    def test_one_receiver_confirmation_and_phone_can_join_without_inbound_tcp(self):
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
                phone.nearby_poll()
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
        with self.assertRaisesRegex(ValueError, 'Close other Flow windows'):
            gui.nearby_open()

    def test_three_peers_pair_by_name_once_then_relay_offline_edits(self):
        a, b, c = [self.node(name, discovery=True) for name in ('Custard', 'Dibbles', 'Phone')]
        nodes = [a, b, c]
        for node in nodes:
            node.nearby_open()
        deadline = time.monotonic() + 10
        found = {}
        while time.monotonic() < deadline:
            found = {n: {d['name'] for d in n.nearby_poll()['devices']} for n in nodes}
            if all(len(names) == 2 for names in found.values()):
                break
            time.sleep(.05)
        self.assertEqual(found[a], {'Dibbles', 'Phone'})
        self.assertEqual(found[b], {'Custard', 'Phone'})
        self.assertEqual(found[c], {'Custard', 'Dibbles'})
        for sender, recipient in ((a, b), (b, c), (c, a)):
            sid = recipient.status()['peer_id']
            outgoing, incoming = self.prompt(sender, recipient, sid)
            old = len(sender.peers()), len(recipient.peers())
            sender.nearby_poll()
            self.assertEqual((len(sender.peers()), len(recipient.peers())), old)
            recipient.nearby_approve(incoming)
            self.wait_for_trust(sender, recipient)
        self.assertTrue(all(len(n.peers()) == 2 for n in nodes))
        original = self.root / 'original.sqlite3'
        with closing(Repository(original)) as repo:
            record = repo.save_flow(dict(date='2026-11-18', name='Water', amount='4',
                                        units='L', from_account='Tank', to_account='Camp'))
        dataset = a.share(original, 'Water')
        b_path, c_path = self.root / 'b.sqlite3', self.root / 'c.sqlite3'
        b.join(dataset, a.status()['peer_id'], b_path)
        c.join(dataset, b.status()['peer_id'], c_path)
        c_port = c.status()['port']
        c.stop()
        with closing(Repository(original)) as repo:
            repo.save_flow(dict(record, amount='9'))
        b.sync_once()
        a.stop()
        with closing(Repository(c_path)) as repo:
            repo.save_flow(dict(record, notes='Edited offline'))
        c = self.node('Phone', port=c_port)
        c.sync_once()
        for path in (b_path, c_path):
            with closing(Repository(path)) as repo:
                flow = repo.get_flow(record['flow_id'])
                self.assertEqual((flow['amount'], flow['notes']), ('9', 'Edited offline'))

    def test_one_sided_forget_repairs_only_after_receiver_confirms(self):
        a, b = self.node('Custard'), self.node('Phone')
        _, request = self.prompt(a, b, self.visible(a, b))
        b.nearby_approve(request)
        self.wait_for_trust(a, b)
        a_id, b_id = a.status()['peer_id'], b.status()['peer_id']
        original_token = b._peer_snapshot()[0]['token']
        a.forget(b_id)
        self.assertEqual(a.peers(), [])
        self.assertEqual(b.peers()[0]['peer_id'], a_id)
        _, request = self.prompt(a, b, self.visible(a, b))
        a.nearby_poll()
        self.assertEqual(a.peers(), [])
        self.assertEqual(b._peer_snapshot()[0]['token'], original_token)
        b.nearby_approve(request)
        self.wait_for_trust(a, b)
        self.assertNotEqual(b._peer_snapshot()[0]['token'], original_token)
        self.assertEqual(a._peer_snapshot()[0]['token'], b._peer_snapshot()[0]['token'])

    def test_forget_clears_old_errors_and_ignores_inflight_failure(self):
        a, b = self.node('Custard'), self.node('Phone')
        _, request = self.prompt(a, b, self.visible(a, b))
        b.nearby_approve(request)
        self.wait_for_trust(a, b)
        a_id = a.status()['peer_id']
        source = self.root / 'kept.sqlite3'
        with closing(Repository(source)) as repo:
            repo.save_flow(dict(date='2026-11-18', amount='2', units='Wh',
                                from_account='Battery', to_account='Lamp'))
        dataset = b.share(source, 'Kept')
        peer = b._peer_snapshot()[0]
        b._save_peer_errors([(peer, 'Custard: connection refused')])
        self.assertIn('Custard', b.status()['error'])
        entered, release = threading.Event(), threading.Event()
        def delayed_failure(*args):
            entered.set()
            release.wait(3)
            raise ConnectionRefusedError('late connection refused')
        with patch.object(b, '_catalog', side_effect=delayed_failure):
            worker = threading.Thread(target=b.sync_once)
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                b.forget(a_id)
                self.assertEqual(b.peers(), [])
                self.assertEqual(b.status()['error'], '')
                self.assertEqual(b.local_files()[0]['dataset'], dataset)
                release.set()
                worker.join(4)
                self.assertFalse(worker.is_alive())
                self.assertEqual(b.status()['error'], '')
                self.assertTrue(source.exists())
                self.assertEqual(b.peers(), [])
                # An old pairing receipt cannot recreate a forgotten peer.
                with b._db() as db:
                    self.assertIsNone(peer_nearby._get(db, 'receipt'))
                    self.assertNotIn(a_id, peer_nearby._get(db, 'completed', []))
            finally:
                release.set()
                worker.join(4)

    def test_forget_during_pending_request_prevents_late_approval(self):
        a, b = self.node('Custard'), self.node('Phone')
        outgoing, incoming = self.prompt(a, b, self.visible(a, b))
        b.forget(a.status()['peer_id'])
        with self.assertRaisesRegex(ValueError, 'no longer active'):
            b.nearby_approve(incoming)
        a.nearby_poll()
        self.assertEqual((a.peers(), b.peers()), ([], []))
        self.assertIsNone(a.nearby_poll()['request'])

    def test_forget_stops_retries_to_another_saved_address(self):
        a, b = self.node('Custard'), self.node('Phone')
        _, request = self.prompt(a, b, self.visible(a, b))
        b.nearby_approve(request)
        self.wait_for_trust(a, b)
        peer_id = a.status()['peer_id']
        b._sighting(peer_id, '127.0.0.1:1')
        def fail_and_forget(*args, **kwargs):
            b.forget(peer_id)
            raise ConnectionRefusedError('First address stopped responding')
        with patch.object(wire, 'call', side_effect=fail_and_forget) as call:
            with self.assertRaisesRegex(ValueError, 'forgotten'):
                b._catalog(peer_id)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(b.peers(), [])
