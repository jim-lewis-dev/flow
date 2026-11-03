"""Phone adapter checks for local replicas and edits arriving during entry."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository
from flowledger.sync import SyncState
from mobile.bridge import MobileStore


class MobileSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.server = Repository(self.root / 'server.sqlite3')
        self.flow = self.server.save_flow(dict(date='2026-11-01', from_account='Battery',
            to_account='Laptop', amount='10', units='Wh', name='Charge', notes='First', tags=[]))
        self.server.save_account('Battery', '100', '2026-11-01', 'Wh')
        self.shared = SyncState(self.server)
        self.shared.configure(dataset='money', server=True)
        self.configuration = dict(dataset='money', endpoint='https://dibbles:8787',
                                  token='a'*40, certificate='b'*64)
        self.store = MobileStore(self.root / 'phone')
        self.store.dispatch('init', {})

    def tearDown(self):
        self.store.close()
        self.server.close()
        self.temp.cleanup()

    def join(self, name='money'):
        return self.store.dispatch('sync_join', dict(name=name, configuration=self.configuration,
                                                   seed=self.shared.export_seed()))

    def exchange(self):
        prepared = self.store.sync_prepare()
        return self.store.sync_accept(dict(session=prepared['session'],
            response=self.shared.server_exchange(prepared['request'])))

    def test_join_is_named_local_file_and_never_replaces_existing_file(self):
        result = self.join()
        self.assertEqual(result['state']['dataset'], 'money.sqlite3')
        self.assertEqual(self.store.repo.get_flow(self.flow['flow_id'])['amount'], '10')
        self.assertTrue(result['state']['sync']['enabled'])
        self.assertNotIn('token', result['state']['sync'])
        self.assertNotIn('certificate', result['state']['sync'])
        with self.assertRaisesRegex(ValueError, 'already connected'):
            self.join()
        self.assertEqual(self.store.dataset, 'money.sqlite3')
        self.assertEqual(sorted(p.name for p in self.store.root.glob('*.sqlite3')),
                         ['flow.sqlite3', 'money.sqlite3'])
        self.assertFalse(list(self.store.root.glob('.sync-*')))

    def test_matching_local_file_connects_in_place_without_another_file(self):
        target = self.store.path('money')
        seed = self.shared.export_seed()
        # A standalone copy has the same records but no synchronization tables.
        replica = Repository(target)
        replica.save_flow(self.flow)
        replica.save_account('Battery', '100', '2026-11-01', 'Wh')
        replica.close()
        self.store.open('money')
        before = self.store.repo.list_flows()
        files = sorted(p.name for p in self.store.root.glob('*.sqlite3'))
        self.join()
        self.assertEqual(self.store.repo.list_flows(), before)
        self.assertTrue(self.store.state()['sync']['enabled'])
        self.assertEqual(sorted(p.name for p in self.store.root.glob('*.sqlite3')), files)
        self.assertEqual(self.store.repo.path, str(target))

    def test_different_existing_local_file_is_not_replaced(self):
        self.store.dispatch('new_dataset', {'name': 'money'})
        record = self.store.dispatch('add_flow', {'flow': {**self.flow, 'amount': '99'}})['record']
        before = self.store.repo.list_flows()
        with self.assertRaisesRegex(ValueError, 'differs from the shared file'):
            self.join()
        self.assertEqual(self.store.repo.list_flows(), before)
        self.assertEqual(self.store.dataset, 'money.sqlite3')
        self.assertFalse(self.store.state()['sync']['enabled'])
        self.store.open('money')
        self.assertFalse(self.store.state()['sync']['enabled'])
        self.assertEqual(self.store.repo.get_flow(record['flow_id'])['amount'], '99')

    def test_invalid_join_keeps_current_file_and_leaves_no_candidate(self):
        with self.assertRaises(ValueError):
            self.store.dispatch('sync_join',dict(name='money',configuration=self.configuration,seed={}))
        self.assertEqual(self.store.dataset,'flow.sqlite3')
        self.assertFalse(self.store.path('money').exists())
        self.assertFalse(list(self.store.root.glob('.sync-*')))

    def test_partial_form_edit_preserves_received_other_field_and_rejects_overlap(self):
        self.join()
        current = self.store.repo.get_flow(self.flow['flow_id'])
        self.store.repo.save_flow({**current,'amount':'25','notes':'Received note'})
        result=self.store.dispatch('update_flow',{'flow':{'flow_id':self.flow['flow_id'],'name':'Solar charge'},
                                                 'baseline':{'name':'Charge'}})
        self.assertEqual((result['record']['amount'],result['record']['notes']),('25','Received note'))
        with self.assertRaisesRegex(ValueError,'changed on another device'):
            self.store.dispatch('update_flow',{'flow':{'flow_id':self.flow['flow_id'],'notes':'My edit'},
                                               'baseline':{'notes':'First'}})
        self.assertEqual(self.store.repo.get_flow(self.flow['flow_id'])['notes'],'Received note')

    def test_partial_anchor_edit_preserves_received_balance(self):
        self.join()
        self.store.repo.save_account('Battery','150','2026-11-01','Wh')
        self.store.dispatch('save_anchor',dict(name='Battery',units='Wh',opening_at='2026-10-31',
                                             baseline={'opening_at':'2026-11-01 00:00:00'}))
        account=self.store.repo.get_account('Battery','Wh')
        self.assertEqual(account['opening_amount'],'150')
        with self.assertRaisesRegex(ValueError,'changed on another device'):
            self.store.dispatch('save_anchor',dict(name='Battery',units='Wh',opening_amount='200',
                                                 baseline={'opening_amount':'100'}))

    def test_pending_edit_survives_reopen_and_network_failure(self):
        self.join()
        self.store.dispatch('update_flow',dict(flow={'flow_id':self.flow['flow_id'],'amount':'12'},baseline={'amount':'10'}))
        self.assertEqual(self.store.state()['sync']['pending'],1)
        session=self.store.sync_session
        self.store.sync_accept(dict(session=session,error='Coordinator unavailable.'))
        self.assertEqual(self.store.repo.get_flow(self.flow['flow_id'])['amount'],'12')
        self.store.open('money')
        self.assertFalse(self.store.state()['sync']['connected'])
        self.assertEqual(self.store.state()['sync']['pending'],1)
        self.exchange()
        self.assertTrue(self.store.state()['sync']['connected'])
        self.assertEqual(self.server.get_flow(self.flow['flow_id'])['amount'],'12')
        self.assertEqual(self.store.state()['sync']['pending'],0)

    def test_late_sync_response_cannot_modify_new_active_file(self):
        self.join()
        prepared=self.store.sync_prepare()
        response=self.shared.server_exchange(prepared['request'])
        self.store.dispatch('new_dataset',{'name':'other'})
        self.assertEqual(self.store.sync_accept(dict(session=prepared['session'],response=response)),{'ignored':True})
        self.assertEqual(self.store.repo.list_flows(),[])
        self.assertFalse(self.store.state()['sync']['enabled'])

    def test_map_drag_does_not_replace_a_received_position(self):
        self.join()
        self.store.repo.save_map_positions({'Battery':(1,2)})
        args=dict(dataset=self.store.dataset,positions={'Battery':[3,4]},baseline={'Battery':[1,2]})
        self.store.repo.save_map_positions({'Battery':(5,6)})
        with self.assertRaisesRegex(ValueError,'moved on another device'):
            self.store.dispatch('save_positions',args)
        self.assertEqual(self.store.repo.load_map_positions()['Battery'],(5,6))

    def test_update_connection_keeps_pending_edits_and_rejects_other_dataset(self):
        self.join()
        self.store.dispatch('update_flow',dict(flow={'flow_id':self.flow['flow_id'],'notes':'Queued'},baseline={'notes':'First'}))
        config={**self.configuration,'endpoint':'https://new-dibbles:8787'}
        self.store.dispatch('sync_reconnect',dict(session=self.store.sync_session,configuration=config))
        self.assertEqual(self.store.state()['sync']['endpoint'],config['endpoint'])
        self.assertEqual(self.store.state()['sync']['pending'],1)
        self.assertEqual(self.store.repo.get_flow(self.flow['flow_id'])['notes'],'Queued')
        with self.assertRaisesRegex(ValueError,'different shared dataset'):
            self.store.dispatch('sync_reconnect',dict(session=self.store.sync_session,configuration={**config,'dataset':'other'}))
        self.assertEqual(self.store.state()['sync']['pending'],1)

    def test_paginated_pull_reports_catching_up_until_final_page(self):
        self.join()
        response = self.shared.server_exchange(self.store.sync_prepare()['request'])
        result = self.store.sync_accept(dict(session=self.store.sync_session,response={**response,'more':True}))
        self.assertTrue(result['state']['sync']['more'])
        self.assertFalse(result['state']['sync']['connected'])
        result = self.store.sync_accept(dict(session=self.store.sync_session,response={**response,'more':False}))
        self.assertFalse(result['state']['sync']['more'])
        self.assertTrue(result['state']['sync']['connected'])

    def test_saf_document_cannot_start_per_change_sync(self):
        self.join()
        self.store.dataset='document:'+'0'*64
        with self.assertRaisesRegex(ValueError,'local phone replica'):
            self.store.sync_prepare()


if __name__ == '__main__':
    unittest.main()
