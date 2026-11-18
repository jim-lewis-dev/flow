"""Real private-copy, direct sharing and open-editor behavior on mobile adapters."""
from contextlib import closing
from pathlib import Path
import tempfile
import unittest

from flowledger.core import Repository
from flowledger.peer_network import PeerNode
from mobile.bridge import MobileStore
from mobile.peers import _platform


class MobilePeerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = self.phone('First')
        self.b = self.phone('Second')

    def phone(self, name):
        store = MobileStore(Path(self.tmp.name) / name)
        self.addCleanup(store.close)
        store.dispatch('init', {})
        host = _platform()
        options = dict(cert_factory=lambda c, k: host.createIdentity(str(c), str(k))) if host else {}
        node = PeerNode(store.peers.directory, name=name, host='127.0.0.1', port=0,
                        discovery=False, interval=3600, **options)
        self.addCleanup(node.stop)
        store.peers._node = node
        return store

    def pair(self, requester, receiver):
        def act(store, mode, **args):
            return store.dispatch('peer_pair', dict(mode=mode, **args))['nearby']
        act(requester, 'open')
        act(receiver, 'open')
        other = receiver.peers.node().status()
        visible = act(requester, 'lookup', address=other['address'])
        self.assertIn(other['peer_id'], [d['peer_id'] for d in visible['devices']])
        outgoing = act(requester, 'request', peer_id=other['peer_id'])['request']
        incoming = act(receiver, 'poll')['request']
        self.assertEqual(outgoing['number'], incoming['number'])
        self.assertFalse(requester.peers.node().peers())
        act(receiver, 'approve', request_id=incoming['id'])
        for _ in range(3):
            act(requester, 'poll')
            act(receiver, 'poll')
            if requester.peers.node().peers() and receiver.peers.node().peers():
                break
        self.assertTrue(requester.peers.node().peers())
        self.assertTrue(receiver.peers.node().peers())

    def flow(self, store, name='Charge'):
        return store.dispatch('add_flow', {'flow':dict(date='2026-11-01',name=name,
            from_account='Battery',to_account='Laptop',amount='10',units='Wh')})['record']

    def test_two_phones_can_pair_join_and_keep_the_latest_edit(self):
        a,b=self.a,self.b
        fid=self.flow(a)['flow_id']
        shared=a.dispatch('peer_share', {'current':a.dataset})['state']
        dataset=shared['peer_file']['dataset']
        self.pair(a, b)
        catalog=b.dispatch('peer_catalog',{})['catalog']
        item=next(i for i in catalog if i['dataset']==dataset)
        local=self.flow(b,'My private entry')
        b.dispatch('peer_join',dict(item,name='flow'))
        shared_key=b.dataset
        self.assertTrue(shared_key.startswith('shared:'))
        self.assertEqual(b.dataset_name,'flow.sqlite3')
        rows=[f for f in b.state()['local_files'] if f['name']=='flow.sqlite3']
        self.assertEqual(len(rows),2)
        self.assertEqual(len({f['location'] for f in rows}),2)
        self.assertFalse(b.repo.get_flow(local['flow_id']))
        b.open('flow');self.assertEqual(b.repo.get_flow(local['flow_id'])['name'],'My private entry')
        self.assertIsNone(b.repo.get_flow(fid))
        b.open(shared_key)
        self.assertEqual(b.state()['peer_file']['dataset'],dataset)
        a.dispatch('update_flow',dict(flow={'flow_id':fid,'amount':'11'},baseline={'amount':'10'},revision=a.state()['flows'][0].get('edit_revision')))
        b.dispatch('update_flow',dict(flow={'flow_id':fid,'amount':'12'},baseline={'amount':'10'},revision=b.state()['flows'][0].get('edit_revision')))
        a.peers.node().sync_once()
        b.peers.node().sync_once()
        refresh=b.dispatch('peer_poll',{})
        self.assertIn('state',refresh)
        self.assertEqual(refresh['state']['peer_file'],{'dataset':dataset})
        self.assertEqual(refresh['state']['flows'][0]['amount'],'12')
        self.assertEqual(a.repo.get_flow(fid)['amount'],'12')
        # Save As uses this exact snapshot. A restore retains the same lineage
        # and latest values; exporting does not publish another copy.
        from flowledger import peer_store
        snapshot=b.dispatch('prepare_export',{})
        with closing(Repository(snapshot['path'])) as backup:
            self.assertEqual(peer_store.dataset(backup),dataset)
            self.assertEqual(backup.get_flow(fid)['amount'],'12')
        self.assertEqual(len(b.peers.status()['files']),1)
        with self.assertRaisesRegex(ValueError,'Unknown action'):
            b.dispatch('peer_resolve',dict(current=b.dataset,conflict='old',choice='old'))
        b.peers.node().sync_once()
        self.assertEqual(a.repo.get_flow(fid)['amount'],'12')
        self.assertEqual(a.state()['peer_file'],{'dataset':dataset})
        b.peers.foreground(False)
        self.assertFalse(b.peers.status()['running'])
        b.peers.foreground(True)
        self.assertTrue(b.peers.status()['running'])
        b.dispatch('peer_unshare',{'current':b.dataset})
        b.close();b.dispatch('init',{'dataset':shared_key})
        self.assertEqual(b.repo.get_flow(fid)['amount'],'12')
        self.assertTrue(any(row['key']==shared_key for row in b.state()['local_files']))
        self.assertFalse(any(row['key']==shared_key for row in b.peers.status()['files']))
        self.assertTrue(b.path('flow').is_file())

    def test_nearby_open_is_explicit_and_pause_cancels(self):
        from unittest.mock import Mock, patch
        peers=self.a.peers
        mock=Mock()
        view={'open':True,'devices':[],'request':None,'paired':[]}
        mock.nearby_open.return_value=view
        mock.nearby_poll.return_value=view
        def act(mode, **args):
            return peers.call('peer_pair', dict(mode=mode,**args))
        with patch.object(peers, '_node', mock), patch.object(peers, 'node', return_value=mock) as start, patch.object(peers,'status',return_value={}):
            act('close')
            with self.assertRaisesRegex(ValueError,'Open nearby'):
                act('poll')
            start.assert_not_called()
            self.assertEqual(act('open')['nearby'],view)
            self.assertEqual(act('poll')['nearby'],view)
            mock.nearby_approve.assert_not_called()
            act('request',peer_id='custard')
            mock.nearby_request.assert_called_once_with('custard')
            act('approve',request_id='r1')
            mock.nearby_approve.assert_called_once_with('r1')
            act('cancel',request_id='r2')
            mock.nearby_cancel.assert_called_once_with('r2')
            peers.foreground(False)
            act('close')
            mock.nearby_close.assert_called_once_with()
            start.assert_called_once_with()
            with self.assertRaisesRegex(ValueError,'Open nearby'):
                act('poll')
        peers._foreground=True

    def test_forget_is_local_even_if_network_cannot_start(self):
        from unittest.mock import patch
        peers = self.a.peers
        self.pair(self.a, self.b)
        identity = self.b.peers.node().status()['peer_id']
        peers._node.stop()
        peers.error = 'Custard: connection refused'
        with patch.object(peers, 'node', side_effect=OSError('network unavailable')) as start:
            result = peers.call('peer_forget', {'peer_id': identity})
        start.assert_not_called()
        self.assertEqual(result['device']['peers'], [])
        self.assertEqual(result['device']['error'], '')
        self.assertFalse(result['device']['running'])

    def test_unusual_shared_label_lives_inside_only_one_ledger(self):
        from flowledger import peer_store
        from mobile.peers import shared_path, file_label, file_key
        import uuid
        dataset=str(uuid.uuid4())
        target=shared_path(self.a.root,dataset,'Money / July')
        self.assertEqual(target.name,'ledger.sqlite3')
        with closing(Repository(target)) as backup:
            peer_store.share(backup,'Money / July',dataset_id=dataset)
        self.assertEqual(file_label(self.a.root,target),'Money / July.sqlite3')
        self.a.open(file_key(self.a.root,target))
        self.assertEqual(self.a.dataset_name,'Money / July.sqlite3')
        self.assertEqual(list(target.parent.iterdir()),[target])

    def test_picker_document_shares_as_a_new_local_copy(self):
        store=self.a
        key='d'*64
        self.flow(store)
        src=store.document_path(key)
        src.parent.mkdir(exist_ok=True)
        store.repo.backup(src)
        store.open_document(key,'flow.sqlite3')
        before=src.read_bytes()
        result=store.dispatch('peer_share',dict(current=store.dataset))
        shared_key=result['state']['dataset']
        self.assertTrue(shared_key.startswith('shared:'))
        self.assertEqual(result['state']['dataset_name'],'flow.sqlite3')
        self.assertTrue(result['state']['peer_file']['dataset'])
        self.assertEqual(src.read_bytes(),before)
        self.assertEqual(store.repo.list_flows()[0]['name'],'Charge')
        with self.assertRaisesRegex(ValueError,'Stop sharing'):
            store.delete_dataset(shared_key)
        store.dispatch('peer_unshare',{'current':store.dataset})
        self.assertTrue(store.path('flow').exists())
        store.open(shared_key);self.assertEqual(store.repo.list_flows()[0]['name'],'Charge')


if __name__=='__main__':
    unittest.main()
