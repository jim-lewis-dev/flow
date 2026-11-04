"""Cross-platform checks for the Android adapter and desktop file round trips."""
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from mobile.bridge import MobileStore, required_hint
from flowledger.core import Repository, ledger_balance_at


class MobileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = MobileStore(self.root / 'phone')
        self.store.dispatch('init', {})

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def record(self, **changes):
        value = dict(date='2026-10-1 8:4:2', name='Test', from_account='Battery',
                     to_account='Laptop', amount='1.23 + 4.56', units='Wh', status='Complete',
                     category='Work', tags=['camp'], notes='one\ntwo')
        value.update(changes)
        return value

    def add(self, **changes):
        return self.store.dispatch('add_flow', {'flow': self.record(**changes)})['record']

    def test_required_hint_and_arithmetic_precision(self):
        self.assertEqual(required_hint({}), 'Date, To, From, Amount, and Units required')
        self.assertEqual(required_hint({'date':'2026-10-01','to_account':'B','from_account':'A'}),
                         'Amount and Units required')
        self.assertEqual(self.store.dispatch('normalize_input',{'field':'amount','value':'=1.23+4.56'}),{'value':'5.79'})
        self.assertEqual(self.store.dispatch('normalize_input',{'field':'amount','value':'=2^3^2'}),{'value':'512'})
        self.assertEqual(self.store.dispatch('normalize_input',{'field':'amount','value':'-2^2'}),{'value':'-4'})
        self.assertEqual(self.store.dispatch('normalize_input',{'field':'amount','value':'9^.5'}),{'value':'3'})
        self.assertEqual(self.store.dispatch('normalize_input',{'field':'date','value':'2026-12-30 9'}),{'value':'2026-12-30 09:00:00'})
        self.assertEqual(self.store.repo.list_flows(),[])
        flow = self.add(amount='=9007199254740993.01 + .02')
        self.assertEqual(flow['amount'], '9007199254740993.03')
        self.assertEqual(flow['date'], '2026-10-01 08:04:02')
        before = self.store.repo.list_flows()
        with self.assertRaisesRegex(ValueError, 'Amount:'):
            self.add(amount='1/0')
        with self.assertRaisesRegex(ValueError, 'Amount:'):
            self.add(amount='0^0')
        self.assertEqual(self.store.repo.list_flows(), before)

    def test_copy_edit_delete_undo_and_reopen(self):
        original = self.add()
        copy = self.store.dispatch('copy_flow', {'flow_id':original['flow_id']})['record']
        self.assertNotEqual(copy['flow_id'], original['flow_id'])
        self.assertEqual({k:v for k,v in copy.items() if k!='flow_id'},
                         {k:v for k,v in original.items() if k!='flow_id'})
        copy['notes'] = 'changed\nnotes'
        self.store.dispatch('update_flow', {'flow':copy})
        self.assertEqual(self.store.repo.get_flow(original['flow_id'])['notes'], 'one\ntwo')
        self.store.dispatch('delete_flow', {'flow_id':copy['flow_id']})
        self.assertEqual(len(self.store.repo.list_flows()), 1)
        self.store.dispatch('undo', {})
        self.assertEqual(self.store.repo.get_flow(copy['flow_id'])['notes'], 'changed\nnotes')
        self.store.dispatch('redo', {})
        self.store.open('flow')
        self.assertEqual(len(self.store.repo.list_flows()), 1)
        self.assertFalse(self.store.history.can_undo)

    def test_current_vocabulary_and_same_account_units(self):
        flow = self.add(from_account='Mistake', to_account='Tank', units='L', amount='-.25')
        flow['from_account'] = 'Tank'
        self.store.dispatch('update_flow', {'flow':flow})
        self.assertEqual(self.store.state()['vocabulary']['accounts'], ['Tank'])
        self.store.dispatch('save_anchor',dict(name='Tank',units='L',opening_at='2026-10-01',opening_amount='10'))
        self.store.dispatch('view', {'as_of':'2026-10-28'})
        self.assertEqual(self.store.state()['accounts'][0]['balance'], '10')
        self.add(from_account='Tank',to_account='Tank',amount='0',units='l')
        self.assertEqual(self.store.state()['vocabulary']['units'], ['L','l'])

    def test_pending_future_canceled_and_graph_start(self):
        self.add(date='2026-10-01', amount='10')
        self.add(date='2026-10-28', amount='5',status='Pending')
        self.add(date='2026-10-02', amount='1000',status='Canceled')
        self.store.dispatch('save_anchor',dict(name='Battery',units='Wh',opening_at='2026-10-01',opening_amount='100'))
        self.store.dispatch('view',dict(as_of='2026-10-28',basis='Including Pending'))
        battery=next(a for a in self.store.state()['accounts'] if a['name']=='Battery')
        self.assertEqual((battery['confirmed'],battery['balance']), ('90','85'))
        graph=self.store.graph(dict(pairs=[['Battery','Wh']],basis='Including Pending'))
        self.assertEqual([p[1] for p in graph['series'][0]['points']], ['100','90','85'])
        with self.assertRaisesRegex(ValueError,'starting balance'):
            self.store.graph(dict(pairs=[['Laptop','Wh']]))

    def test_graph_date_only_end_includes_whole_day(self):
        self.add(date='2026-10-01 20:30', amount='10')
        self.add(date='2026-10-02', amount='50')
        self.store.dispatch('save_anchor',dict(name='Battery',units='Wh',opening_at='2026-10-01',opening_amount='100'))
        args=dict(pairs=[['Battery','Wh']],basis='Confirmed',start='2026-10-01',end='2026-10-01')
        graph=self.store.graph(args)
        self.assertEqual(graph['series'][0]['points'][-1][1],'90')
        self.assertEqual(graph['end'],'2026-10-01 23:59:59')
        args['end']='2026-10-01 12:00'
        self.assertEqual(self.store.graph(args)['series'][0]['points'][-1][1],'100')

    def test_experimental_groups_signed_quantities_units_and_date_boundaries(self):
        one=self.add(date='2026-10-01 10:00',amount='2')
        negative=self.add(date='2026-10-01 10:00',amount='-.25')
        zero=self.add(date='2026-10-01 23:59:59',amount='0',to_account='Battery')
        pending=self.add(date='2026-10-01 11',amount='5',status='Pending')
        self.add(date='2026-10-01 12',amount='3',status='Planned')
        self.add(date='2026-10-01 13',amount='1000',status='Canceled')
        self.add(date='2026-10-02',amount='1000')
        water=self.add(date='2026-10-01',amount='4',units='L')
        args=dict(units='Wh',start='2026-10-01',end='2026-10-01',basis='Including Pending')
        result=self.store.dispatch('experimental',args)
        groups=result['groups']
        self.assertEqual(result['available_units'],['L','Wh'])
        self.assertEqual([len(g['flows']) for g in groups],[2,1,1])
        events={f['flow_id']:f for g in groups for f in g['flows']}
        self.assertEqual(set(events),{one['flow_id'],negative['flow_id'],zero['flow_id'],pending['flow_id']})
        self.assertEqual((events[negative['flow_id']]['source'],events[negative['flow_id']]['target']),('Laptop','Battery'))
        self.assertEqual(events[negative['flow_id']]['amount'],'-0.25')
        self.assertEqual((events[zero['flow_id']]['source'],events[zero['flow_id']]['target']),('Battery','Battery'))
        self.assertEqual(events[zero['flow_id']]['radius'],5)
        self.assertGreater(events[pending['flow_id']]['radius'],events[negative['flow_id']]['radius'])
        self.assertEqual(self.store.repo.get_flow(negative['flow_id']),negative)
        self.assertEqual(self.store.dispatch('experimental',{**args,'flow_ids':[]})['groups'],[])
        water_result=self.store.dispatch('experimental',{**args,'units':'L'})
        self.assertEqual(water_result['groups'][0]['flows'][0]['flow_id'],water['flow_id'])
        with self.assertRaisesRegex(ValueError,'Start must not be after end'):
            self.store.dispatch('experimental',{**args,'start':'2026-10-02'})
        with self.assertRaisesRegex(ValueError,'Start:'):
            self.store.dispatch('experimental',{**args,'start':'nope'})

    def test_experimental_details_use_filtered_totals_but_whole_ledger_balance(self):
        self.add(date='2026-10-01 8',amount='10')
        outgoing=self.add(date='2026-10-01 10',amount='2.01')
        incoming=self.add(date='2026-10-01 10',amount='-.25')
        self.add(date='2026-10-01 10',amount='500',status='Canceled')
        self.store.dispatch('save_anchor',dict(name='Battery',units='Wh',opening_at='2026-10-01',opening_amount='100'))
        args=dict(units='Wh',basis='Confirmed',flow_ids=[outgoing['flow_id'],incoming['flow_id']],name='Battery')
        details=self.store.dispatch('experimental_details',args)
        self.assertEqual((details['incoming'],details['outgoing']),('0.25','2.01'))
        self.assertIsNone(details['balance'])
        details=self.store.dispatch('experimental_details',{**args,'step':0})
        self.assertEqual(details['balance'],'88.24')
        self.assertEqual(details['at'],'2026-10-01 10:00:00')
        unknown=self.store.dispatch('experimental_details',{**args,'name':'Laptop','step':0})
        self.assertIsNone(unknown['balance'])
        self.assertEqual(unknown['opening_display'],'Unknown')
        with self.assertRaisesRegex(ValueError,'current map step'):
            self.store.dispatch('experimental_details',{**args,'step':1})
        self.store.dispatch('delete_flow',{'flow_id':outgoing['flow_id']})
        result=self.store.dispatch('experimental',args)
        self.assertEqual(len(result['groups'][0]['flows']),1)

    def test_experimental_positions_persist_merge_and_reject_stale_dataset(self):
        self.add()
        self.add(from_account='Tank',to_account='Cooking',units='L')
        self.store.repo.save_map_positions({'Tank':(-.5,1.8),'Battery':(.3,.7)})
        args=dict(dataset='flow.sqlite3',positions={'Battery':[-2.5,3.5],'Laptop':[5,-4]})
        result=self.store.dispatch('save_positions',args)
        self.assertEqual(result['positions'],{'Tank':(-.5,1.8),'Battery':(-2.5,3.5),'Laptop':(5.,-4.)})
        for point in [[float('nan'),0],[0,float('inf')],[True,0],['1',0]]:
            with self.assertRaises(ValueError):
                self.store.dispatch('save_positions',{**args,'positions':{'Battery':point}})
            self.assertEqual(self.store.repo.load_map_positions(),result['positions'])
        self.store.open('flow')
        nodes=self.store.dispatch('experimental',{'units':'Wh'})['nodes']
        battery=next(n for n in nodes if n['name']=='Battery')
        self.assertEqual((battery['x'],battery['y']),(-2.5,3.5))
        export=self.store.dispatch('prepare_export',{})
        desktop=Repository(export['path'])
        try:self.assertEqual(desktop.load_map_positions(),result['positions'])
        finally:desktop.close()
        self.store.dispatch('new_dataset',{'name':'another'})
        self.add()
        with self.assertRaisesRegex(ValueError,'active dataset changed'):
            self.store.dispatch('save_positions',args)
        self.assertEqual(self.store.repo.load_map_positions(),{})

    def test_map_drag_undo_redo_interleaves_with_record_edits(self):
        flow=self.add()
        self.store.repo.save_map_positions({'Battery':(.25,.5),'Laptop':(.75,.5)})
        before=self.store.repo.load_map_positions()
        records=self.store.repo.list_flows()
        args=dict(dataset='flow.sqlite3',positions={'Battery':[-1,2]})
        result=self.store.dispatch('save_positions',args)
        after=result['positions']
        self.assertEqual(result['state']['undo'],'Move account')
        self.assertEqual(result['state']['map_positions'],after)
        self.store.dispatch('delete_flow',{'flow_id':flow['flow_id']})
        self.store.dispatch('undo',{})
        self.assertEqual(self.store.repo.list_flows(),records)
        self.assertEqual(self.store.repo.load_map_positions(),after)
        undo=self.store.dispatch('undo',{})
        self.assertEqual(self.store.repo.load_map_positions(),before)
        self.assertEqual(undo['state']['map_positions'],before)
        self.assertEqual(self.store.repo.list_flows(),records)
        self.assertEqual(self.store.state()['redo'],'Move account')
        # A no-op drag does not consume the redo branch or add a history step.
        self.store.dispatch('save_positions',{**args,'positions':{'Battery':[.25,.5]}})
        self.assertEqual(self.store.state()['redo'],'Move account')
        with self.assertRaises(ValueError):
            self.store.dispatch('save_positions',{**args,'positions':{'Battery':[float('nan'),0]}})
        self.assertEqual(self.store.state()['redo'],'Move account')
        redo=self.store.dispatch('redo',{})
        self.assertEqual(self.store.repo.load_map_positions(),after)
        self.assertEqual(redo['state']['map_positions'],after)
        self.assertEqual(self.store.repo.list_flows(),records)
        self.store.open('flow')
        self.assertEqual(self.store.repo.load_map_positions(),after)
        self.assertFalse(self.store.history.can_undo)
        self.assertFalse(self.store.history.can_redo)

    def test_desktop_round_trip_keeps_anchors_audit_layout_and_precision(self):
        desktop = self.root / 'desktop.sqlite3'
        with_db=Repository(desktop)
        original=with_db.save_flow(self.record(amount='0.000000000000000001',date='2026-10-01'))
        with_db.save_account('Battery','100','2026-10-01','Wh')
        with_db.save_map_positions({'Battery':(-.5,1.8),'Laptop':(.5,.3)})
        before_bytes=desktop.read_bytes()
        with_db.close()
        shutil.copy2(desktop,self.store.root/'.incoming')
        key = 'a' * 64
        imported=self.store.dispatch('install_document',dict(key=key,name='desktop.sqlite3'))['state']
        self.assertEqual(imported['dataset'],'document:' + key)
        self.assertEqual(imported['dataset_name'],'desktop.sqlite3')
        self.assertEqual(desktop.read_bytes(),before_bytes)
        self.assertEqual(self.store.repo.get_flow(original['flow_id'])['amount'],'0.000000000000000001')
        self.add(amount='-.5')
        export=self.store.dispatch('prepare_export',{})
        returned=Repository(export['path'])
        try:
            self.assertEqual(returned.load_map_positions(),{'Battery':(-.5,1.8),'Laptop':(.5,.3)})
            self.assertEqual(len(returned.list_flows()),2)
            self.assertEqual(returned.get_account('Battery','Wh')['opening_amount'],'100')
            self.assertGreater(len(returned.history()),0)
            self.assertEqual(returned.conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        finally:returned.close()
        self.assertEqual(export['name'], 'desktop.sqlite3')
        # The host writes the snapshot back to the selected document. Opening
        # that document later refreshes the same cache, preserving all values.
        shutil.copy2(export['path'],desktop)
        cache_path=self.store.repo.path
        self.store.dispatch('open_dataset',{'name':'flow'})
        shutil.copy2(desktop,self.store.root/'.incoming')
        self.store.dispatch('install_document',dict(key=key,name='desktop.sqlite3'))
        self.assertEqual(self.store.repo.path,cache_path)
        self.assertEqual(len(self.store.repo.list_flows()),2)
        self.assertEqual(self.store.state()['datasets'],['flow.sqlite3'])
        self.assertEqual(len(list((self.store.root/'.documents').glob('*.sqlite3'))),1)
        self.store.close()
        self.store=MobileStore(self.store.root)
        self.store.dispatch('open_document',dict(key=key,name='desktop.sqlite3'))
        self.assertEqual(len(self.store.repo.list_flows()),2)
        self.assertFalse(self.store.history.can_undo)

    def test_invalid_document_and_dataset_name_do_not_replace_current(self):
        self.add()
        path=self.store.repo.path
        (self.store.root/'.incoming').write_text('not a database')
        with self.assertRaisesRegex(ValueError,'SQLite'):
            self.store.dispatch('install_document',dict(key='b'*64,name='bad'))
        self.assertEqual(self.store.repo.path,path)
        self.assertEqual(len(self.store.repo.list_flows()),1)
        self.assertFalse((self.store.root/'.incoming').exists())
        for bad in ['../bad','/bad','.hidden','a/b']:
            with self.assertRaises(ValueError):self.store.open(bad,create=True)
        with self.assertRaisesRegex(ValueError,'already exists'):
            self.store.open('flow',create=True)

    def test_documents_with_same_name_and_phone_only_files_stay_separate(self):
        local=self.add(name='Local original')
        exported=self.store.dispatch('prepare_export',{})
        source=self.root/'source.sqlite3'
        shutil.copy2(exported['path'],source)
        for key,label in [('a'*64,'First document'),('b'*64,'Second document')]:
            shutil.copy2(source,self.store.root/'.incoming')
            self.store.dispatch('install_document',dict(key=key,name='flow.sqlite3'))
            self.add(name=label)
            self.store.dispatch('save_positions',dict(dataset='document:'+key,
                                                       positions={'Battery':[2,3]}))
        self.store.dispatch('open_document',dict(key='a'*64,name='flow.sqlite3'))
        self.assertEqual({f['name'] for f in self.store.repo.list_flows()},
                         {'Local original','First document'})
        self.assertEqual(self.store.repo.load_map_positions(),{'Battery':(2.,3.)})
        with self.assertRaisesRegex(ValueError,'active dataset changed'):
            self.store.dispatch('save_positions',dict(dataset='document:'+'b'*64,
                                                       positions={'Battery':[4,5]}))
        self.store.dispatch('new_dataset',{'name':'calories'})
        self.assertEqual(self.store.repo.list_flows(),[])
        only_phone=self.add(units='kcal',name='Phone only')
        self.store.dispatch('open_dataset',{'name':'flow'})
        self.assertEqual(self.store.repo.list_flows(),[local])
        self.store.dispatch('open_dataset',{'name':'calories'})
        self.assertEqual(self.store.repo.list_flows(),[only_phone])
        self.assertEqual(self.store.state()['datasets'],['calories.sqlite3','flow.sqlite3'])

    def test_invalid_refresh_or_failed_replace_keeps_current_document(self):
        self.add(name='Original')
        exported=self.store.dispatch('prepare_export',{})
        source=self.root/'source.sqlite3'
        shutil.copy2(exported['path'],source)
        args=dict(key='a'*64,name='flow.sqlite3')
        shutil.copy2(source,self.store.root/'.incoming')
        self.store.dispatch('install_document',args)
        newest=self.add(name='Latest')
        path=self.store.repo.path
        (self.store.root/'.incoming').write_text('not SQLite')
        with self.assertRaisesRegex(ValueError,'SQLite'):
            self.store.dispatch('install_document',args)
        self.assertEqual(self.store.repo.get_flow(newest['flow_id']),newest)
        self.assertTrue(self.store.history.can_undo)
        shutil.copy2(source,self.store.root/'.incoming')
        with patch('mobile.bridge.os.replace',side_effect=OSError('disk error')):
            with self.assertRaisesRegex(OSError,'disk error'):
                self.store.dispatch('install_document',args)
        self.assertEqual(self.store.repo.path,path)
        self.assertEqual(self.store.repo.get_flow(newest['flow_id']),newest)
        self.assertEqual(list((self.store.root/'.documents').glob('.*.incoming')),[])

    def test_malformed_record_cannot_replace_or_switch_the_active_file(self):
        self.add(name='Original')
        exported=self.store.dispatch('prepare_export',{})
        source=self.root/'bad.sqlite3'
        shutil.copy2(exported['path'],source)
        args=dict(key='a'*64,name='flow.sqlite3')
        shutil.copy2(source,self.store.root/'.incoming')
        self.store.dispatch('install_document',args)
        self.add(name='Latest')
        before=self.store.state()
        history=self.store.history
        cached=Path(self.store.repo.path).read_bytes()
        with sqlite3.connect(source) as conn:
            conn.execute("UPDATE flows SET date='invalid'")
            self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0],'ok')
        for action,args in [
            ('install_document',args),
            ('open_dataset',{'name':'bad.sqlite3'}),
        ]:
            shutil.copy2(source,self.store.root/'.incoming')
            shutil.copy2(source,self.store.root/'bad.sqlite3')
            with self.assertRaises(ValueError):
                self.store.dispatch(action,args)
            after=self.store.state()
            for field in ['dataset','dataset_name','flows','undo','redo']:
                self.assertEqual(after[field],before[field])
            self.assertIs(self.store.history,history)
            self.assertEqual(Path(self.store.repo.path).read_bytes(),cached)
        self.store.dispatch('undo',{})
        self.assertEqual([f['name'] for f in self.store.repo.list_flows()],['Original'])

    def test_delete_inactive_local_file_keeps_active_records_and_history(self):
        original = self.add()
        history = self.store.history
        other = Repository(self.store.path('unused'))
        other.close()
        result = self.store.dispatch('delete_dataset', {'name': 'unused'})['state']
        self.assertEqual(result['datasets'], ['flow.sqlite3'])
        self.assertEqual(result['dataset'], 'flow.sqlite3')
        self.assertIs(self.store.history, history)
        self.assertEqual(self.store.repo.get_flow(original['flow_id'])['name'], 'Test')
        self.store.dispatch('undo', {})
        self.assertEqual(self.store.repo.list_flows(), [])

    def test_delete_current_file_leaves_other_files_and_no_open_file(self):
        self.add()
        other = Repository(self.store.path('camp'))
        other.save_flow(self.record(amount='8'))
        other.close()
        result = self.store.dispatch('delete_dataset', {'name': 'flow'})['state']
        self.assertEqual(result['dataset'], '')
        self.assertEqual(result['datasets'], ['camp.sqlite3'])
        self.assertEqual(result['flows'], [])
        self.assertEqual(result['undo'], '')
        self.assertFalse(result['sync']['enabled'])
        self.assertFalse(self.store.path('flow').exists())
        self.store.dispatch('open_dataset', {'name': 'camp'})
        self.assertEqual(self.store.state()['flows'][0]['amount'], '8')

    def test_delete_last_file_stays_empty_and_can_create_again(self):
        self.add()
        session = self.store.sync_session
        self.store.dispatch('delete_dataset', {'name': 'flow'})
        for action, args in [('init', {}), ('view', {'as_of': '2026-11-04'}), ('state', {})]:
            state = self.store.dispatch(action, args)['state']
            self.assertEqual(state['dataset'], '')
            self.assertEqual(state['datasets'], [])
        self.assertNotEqual(self.store.sync_session, session)
        self.assertEqual(self.store.sync_prepare(), {'enabled': False})
        self.assertEqual(self.store.sync_accept({'session': session, 'response': {}}), {'ignored': True})
        with self.assertRaisesRegex(ValueError, 'Open or create'):
            self.add()
        self.store.close()
        self.store = MobileStore(self.store.root)
        self.assertEqual(self.store.dispatch('init', {'dataset': ''})['state']['datasets'], [])
        self.store.dispatch('new_dataset', {'name': 'flow'})
        self.assertEqual(self.store.state()['datasets'], ['flow.sqlite3'])
        self.assertEqual(self.store.state()['flows'], [])

    def test_delete_rejects_external_paths_and_symlinks(self):
        self.add()
        original = self.store.path('flow').read_bytes()
        for name in ('../flow', '/tmp/flow', '.documents/example', 'document:abc/path', ''):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.store.dispatch('delete_dataset', {'name': name})
        link = self.store.path('link')
        link.symlink_to(self.store.path('flow'))
        with self.assertRaisesRegex(ValueError, 'local file'):
            self.store.dispatch('delete_dataset', {'name': 'link'})
        self.assertEqual(self.store.path('flow').read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'no longer available'):
            self.store.dispatch('delete_dataset', {'name': 'missing'})
        self.store.path('broken').write_bytes(b'not a database')
        with self.assertRaisesRegex(ValueError, 'cannot be checked'):
            self.store.dispatch('delete_dataset', {'name': 'broken'})
        self.assertTrue(self.store.path('broken').exists())

    def test_delete_refuses_unsent_and_conflicted_local_changes(self):
        from flowledger.sync import SyncState
        sync = SyncState(self.store.repo)
        self.store.repo.sync = sync
        sync.configure(dataset='shared', client='phone')
        self.add()
        self.assertTrue(sync.status()['pending'])
        for conflict in (None, '{"message":"Changed on another device"}'):
            self.store.repo.conn.execute('UPDATE sync_outbox SET conflict=?', (conflict,))
            self.store.repo.conn.commit()
            with self.assertRaisesRegex(ValueError, 'Sync this file'):
                self.store.dispatch('delete_dataset', {'name': 'flow'})
            self.assertEqual(len(self.store.repo.list_flows()), 1)
            self.assertTrue(self.store.path('flow').exists())
        self.store.dispatch('new_dataset', {'name': 'other'})
        with self.assertRaisesRegex(ValueError, 'Sync this file'):
            self.store.dispatch('delete_dataset', {'name': 'flow'})
        self.assertEqual(self.store.dataset, 'other.sqlite3')

    def test_delete_failure_reopens_the_current_file_without_data_loss(self):
        original = self.add()
        with patch('mobile.bridge.Path.unlink', side_effect=PermissionError('blocked')):
            with self.assertRaises(PermissionError):
                self.store.dispatch('delete_dataset', {'name': 'flow'})
        self.assertEqual(self.store.dataset, 'flow.sqlite3')
        self.assertEqual(self.store.repo.get_flow(original['flow_id'])['name'], 'Test')
        real_unlink = Path.unlink
        def block_sidecars(path, *args, **kwargs):
            if str(path).endswith('-wal'):
                raise PermissionError('blocked sidecar')
            return real_unlink(path, *args, **kwargs)
        with patch('mobile.bridge.Path.unlink', block_sidecars):
            self.store.dispatch('delete_dataset', {'name': 'flow'})
        self.assertEqual(self.store.dispatch('init', {})['state']['dataset'], '')
        self.assertFalse(self.store.path('flow').exists())

    def test_missing_remembered_file_is_not_recreated(self):
        self.add()
        self.store.close()
        self.store=MobileStore(self.store.root)
        with self.assertRaisesRegex(ValueError,'no longer available'):
            self.store.dispatch('init',{'dataset':'missing.sqlite3'})
        self.assertFalse((self.store.root/'missing.sqlite3').exists())
        self.store.dispatch('init',{'dataset':'flow.sqlite3'})
        self.assertEqual(len(self.store.repo.list_flows()),1)
        for key in ['../escape','a'*63,'A'*64,'']:
            with self.assertRaisesRegex(ValueError,'Invalid document'):
                self.store.dispatch('open_document',dict(key=key,name='flow.sqlite3'))

    def test_camp_example_and_file_switch(self):
        self.add()
        self.store.dispatch('camp',{})
        self.assertEqual(len(self.store.repo.list_flows()),60)
        self.assertEqual(self.store.state()['vocabulary']['units'],['L','Wh'])
        self.assertTrue(self.store.repo.load_map_positions())
        self.store.dispatch('open_dataset',{'name':'flow.sqlite3'})
        self.assertEqual(len(self.store.repo.list_flows()),1)


if __name__ == '__main__':unittest.main()
