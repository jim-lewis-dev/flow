"""Mobile form requests against live peer edits and real SQLite snapshots."""
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from flowledger import peer_store
from flowledger.core import Repository
from mobile.bridge import MobileStore
from mobile import revisions


class MobilePeerRevisionTests(TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.phone = MobileStore(root / 'phone')
        self.addCleanup(self.phone.close)
        self.phone.open('water', create=True)
        self.flow = self.phone.repo.save_flow(dict(date='2026-01-01', from_account='Tank', to_account='Field',
                                                  amount='2', units='kg', tags=['Primary'], name='Water'))
        self.phone.repo.save_account('Tank', '10', '2026-01-01', 'kg')
        self.phone.repo.conn.execute('PRAGMA journal_mode=WAL')
        peer_store.share(self.phone.repo, 'Water')
        self.other = Repository(root / 'other.flow')
        self.addCleanup(self.other.close)
        peer_store.join(self.other, peer_store.seed(self.phone.repo))
        state = self.phone.state()
        self.stamp = state['flows'][0]['edit_revision']
        self.anchor = next(a for a in state['accounts'] if a['name'] == 'Tank')

    def receive(self):
        peer_store.receive(self.phone.repo, peer_store.missing(self.other, peer_store.inventory(self.phone.repo)))

    def edit(self, revision, original, **changes):
        return self.phone.dispatch('update_flow', dict(flow=dict(flow_id=self.flow['flow_id'], **changes),
                                                       baseline=original, revision=revision))

    def test_amount_draft_cannot_be_applied_in_a_different_unit(self):
        self.other.save_flow(dict(self.flow, units='liters'))
        self.receive()
        before = peer_store.inventory(self.phone.repo)
        for revision in (None, self.stamp):
            with self.assertRaisesRegex(ValueError, 'Reopen'):
                self.edit(revision, {'amount': '2'}, amount='3')
        self.assertEqual(before, peer_store.inventory(self.phone.repo))
        self.assertFalse(self.phone.history.can_undo)
        current = self.phone.state()['flows'][0]
        self.assertEqual(('2', 'liters'), (current['amount'], current['units']))
        result = self.edit(current['edit_revision'], {'amount': '2'}, amount='3')
        self.assertEqual('3', result['record']['amount'])
        self.assertTrue(self.phone.history.can_undo)

    def test_aba_and_delete_detect_even_a_remote_change_back_to_the_same_value(self):
        self.other.save_flow(dict(self.flow, name='Temporary'))
        self.other.save_flow(dict(self.flow))
        self.receive()
        with self.assertRaisesRegex(ValueError, 'Name changed'):
            self.edit(self.stamp, {'name': 'Water'}, name='Stale title')
        with self.assertRaisesRegex(ValueError, 'Name changed'):
            self.phone.dispatch('delete_flow', dict(flow_id=self.flow['flow_id'], revision=self.stamp))
        result = self.edit(self.stamp, {'notes': ''}, notes='Independent note')
        self.assertEqual('Water', result['record']['name'])
        self.assertEqual('Independent note', result['record']['notes'])

    def test_tag_only_change_invalidates_delete_and_a_mismatched_row_cannot_be_edited(self):
        self.other.save_flow(dict(self.flow, tags=['New tag']))
        self.receive()
        with self.assertRaisesRegex(ValueError, 'Tags changed'):
            self.phone.dispatch('delete_flow', dict(flow_id=self.flow['flow_id'], revision=self.stamp))
        with self.assertRaisesRegex(ValueError, 'Reopen'):
            self.edit(self.anchor['edit_revision'], {'notes': ''}, notes='Wrong form')
        latest = self.phone.state()['flows'][0]
        self.phone.dispatch('delete_flow', dict(flow_id=self.flow['flow_id'], revision=latest['edit_revision']))
        self.assertIsNone(self.phone.repo.get_flow(self.flow['flow_id']))

    def test_stale_opening_balance_rejected_while_untouched_anchor_date_can_change(self):
        self.other.save_account('Tank', '12', '2026-01-01', 'kg')
        self.other.save_account('Tank', '10', '2026-01-01', 'kg')
        self.receive()
        args = dict(name='Tank', units='kg', revision=self.anchor['edit_revision'])
        with self.assertRaisesRegex(ValueError, 'Opening Amount|Opening amount'):
            self.phone.dispatch('save_anchor', dict(args, opening_amount='99', baseline={'opening_amount': '10'}))
        self.phone.dispatch('save_anchor', dict(args, opening_at='2026-01-03', baseline={'opening_at': self.anchor['opening_at']}))
        saved = self.phone.repo.get_account('Tank', 'kg')
        self.assertEqual('10', saved['opening_amount'])
        self.assertTrue(saved['opening_at'].startswith('2026-01-03'))

    def test_state_values_and_revisions_are_from_one_read_snapshot(self):
        self.other.save_flow(dict(self.flow, units='liters'))
        events = peer_store.missing(self.other, [])
        original = revisions.decorate
        with closing(Repository(self.phone.repo.path)) as writer:
            def receive_between_values_and_tokens(repo, flows, anchors):
                self.assertTrue(repo.conn.in_transaction)
                peer_store.receive(writer, events)
                original(repo, flows, anchors)
            with patch.object(revisions, 'decorate', side_effect=receive_between_values_and_tokens):
                drawn = self.phone.state()['flows'][0]
        self.assertEqual(('kg', self.stamp), (drawn['units'], drawn['edit_revision']))
        with self.assertRaisesRegex(ValueError, 'Amount changed'):
            self.edit(drawn['edit_revision'], {'amount': '2'}, amount='3')
        self.assertEqual('liters', self.phone.state()['flows'][0]['units'])
