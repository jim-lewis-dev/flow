"""Optional date precision, exact-second arithmetic and old-data recovery."""
from datetime import date, datetime
from decimal import Decimal
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from flowledger.core import Repository, ledger_balance_at, ledger_timeline
from flowledger.exchange import commit_import, export_flows, preview_import, read_import

V1_SCHEMA = """
PRAGMA application_id=1179406167;
PRAGMA user_version=1;
CREATE TABLE accounts(account_key TEXT PRIMARY KEY,name TEXT,opening_cents INTEGER,opening_at TEXT,mine INTEGER,archived INTEGER);
CREATE TABLE categories(category_key TEXT PRIMARY KEY,name TEXT);
CREATE TABLE tags(tag_key TEXT PRIMARY KEY,name TEXT);
CREATE TABLE scenarios(scenario_key TEXT PRIMARY KEY,name TEXT);
CREATE TABLE flows(flow_id TEXT PRIMARY KEY,date TEXT,from_key TEXT REFERENCES accounts(account_key),to_key TEXT REFERENCES accounts(account_key),amount_cents INTEGER,status TEXT,name TEXT,notes TEXT,category_key TEXT REFERENCES categories(category_key),scenario_key TEXT REFERENCES scenarios(scenario_key));
CREATE TABLE flow_tags(flow_id TEXT REFERENCES flows(flow_id),tag_key TEXT REFERENCES tags(tag_key),position INTEGER);
CREATE TABLE scenario_exclusions(scenario_key TEXT REFERENCES scenarios(scenario_key),flow_id TEXT REFERENCES flows(flow_id));
CREATE TABLE audit(audit_id INTEGER PRIMARY KEY,changed_at TEXT,entity TEXT,entity_key TEXT,action TEXT,before_json TEXT,after_json TEXT);
INSERT INTO accounts VALUES('cash','Cash',10000,'2026-10-01 00:00:00',1,0);
INSERT INTO accounts VALUES('shop','Shop',0,'2026-10-01 00:00:00',0,0);
INSERT INTO categories VALUES('uncategorized','Uncategorized');
INSERT INTO scenarios VALUES('baseline','Baseline');
INSERT INTO flows VALUES('00000000-0000-0000-0000-000000000001','2026-10-02 00:00:00','cash','shop',1200,'Complete','Lunch','','uncategorized','baseline');
INSERT INTO flows VALUES('00000000-0000-0000-0000-000000000002','2026-10-02 12:34:56','cash','shop',200,'Pending','Snack','','uncategorized','baseline');
INSERT INTO audit VALUES(1,'2026-10-02 00:00:00','flow','00000000-0000-0000-0000-000000000001','create',NULL,'{"name":"Lunch"}');
"""


FLOW_FIELDS = {"date", "from_account", "to_account", "units", 'amount', "status",
               "name", "notes", "category", "tags", "flow_id"}

class OptionalTimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.repo = Repository(self.root / 'active.sqlite3')
        self.addCleanup(self.repo.close)

    def flow(self, **changes):
        return self.repo.save_flow({"units": "USD", 'date':'2026-10-02','from_account':'Cash',
                                   'to_account':'Shop','amount':'12.00',**changes})

    def make_v1(self):
        path = self.root / 'legacy.sqlite3'
        with sqlite3.connect(path) as conn:
            conn.executescript(V1_SCHEMA)
        return path

    def test_date_only_and_datetime_inputs_retain_precision_without_extra_fields(self):
        for value, expected in (('2026-10-02','2026-10-02'),(date(2026,10,2),'2026-10-02'),
                                ('2026-10-02 00:00','2026-10-02 00:00:00'),
                                (datetime(2026,10,2),'2026-10-02 00:00:00'),
                                ('2026-10-02T12:34:56','2026-10-02 12:34:56')):
            with self.subTest(value=value):
                saved = self.flow(date=value)
                self.assertEqual(saved['date'],expected)
                self.assertEqual(set(saved),FLOW_FIELDS)
        default = self.repo.save_flow({"units": "USD", 'from_account':'Cash','to_account':'Shop','amount':'1'})
        self.assertEqual(default['date'],date.today().isoformat())

    def test_edit_and_void_preserve_date_only_without_duplicate_effect(self):
        self.repo.save_account('Cash','100','2026-10-01')
        saved = self.flow()
        updated = self.repo.save_flow({"units": "USD", **saved,'status':'Pending','amount':'15'})
        self.assertEqual(updated['date'],'2026-10-02')
        updated = self.repo.save_flow({**updated,'status':'Complete'})
        self.assertEqual(len(self.repo.list_flows()),1)
        self.assertEqual(ledger_balance_at(self.repo.get_account('Cash'),self.repo.list_flows(),'2026-10-03'),Decimal("85"))
        self.assertEqual(self.repo.void_flow(updated['flow_id'])['date'],'2026-10-02')
        self.assertEqual(ledger_balance_at(self.repo.get_account('Cash'),self.repo.list_flows(),'2026-10-03'),Decimal("100"))

    def test_exact_midnight_date_only_and_second_ordering_share_calculation_timestamps(self):
        self.repo.save_account('Cash','100','2026-10-02')
        # The explicit midnight has the smaller ID despite being entered last.
        date_only = self.flow(flow_id='00000000-0000-0000-0000-000000000002')
        later = self.flow(date='2026-10-02 00:00:01',amount='3',flow_id='00000000-0000-0000-0000-000000000003')
        midnight = self.flow(date='2026-10-02 00:00',amount='1',flow_id='00000000-0000-0000-0000-000000000001')
        self.assertEqual(self.repo.list_flows(),[midnight,date_only,later])
        account = self.repo.get_account('Cash')
        self.assertEqual(ledger_balance_at(account,self.repo.list_flows(),'2026-10-02'),Decimal("87"))
        self.assertEqual(ledger_balance_at(account,self.repo.list_flows(),'2026-10-02 00:00:01'),Decimal("84"))
        self.assertEqual(ledger_timeline([account],self.repo.list_flows(),'2026-10-01 23:59:59','2026-10-02 00:00:02')['Cash', 'USD'],
                         [('2026-10-01 23:59:59',Decimal("100")),('2026-10-02 00:00:00',Decimal("87")),
                          ('2026-10-02 00:00:01',Decimal("84")),('2026-10-02 00:00:02',Decimal("84"))])

    def test_removed_fields_are_rejected_without_mutation(self):
        for values in ({'time_specified':False},{'time_specified':True},{'scenario':'Baseline'}, {'extra':'value'}):
            with self.subTest(values=values), self.assertRaisesRegex(ValueError,'Unknown flow fields'):
                self.flow(**values)
        self.assertEqual(self.repo.list_flows(),[])
        self.assertEqual(self.repo.list_accounts(),[])

    def test_v1_migration_preserves_balances_history_and_original_backup(self):
        path = self.make_v1()
        with sqlite3.connect(path) as conn:
            dates = conn.execute('SELECT date FROM flows ORDER BY date').fetchall()
            audit = conn.execute('SELECT * FROM audit').fetchall()
        repo = Repository(path)
        try:
            self.assertEqual(repo.conn.execute('PRAGMA user_version').fetchone()[0],7)
            self.assertEqual([row['date'] for row in repo.list_flows()],['2026-10-02','2026-10-02 12:34:56'])
            self.assertTrue(all(set(row)==FLOW_FIELDS for row in repo.list_flows()))
            self.assertEqual([tuple(row) for row in repo.conn.execute('SELECT * FROM audit')],audit)
            self.assertEqual(ledger_balance_at(repo.get_account('Cash'),repo.list_flows(),'2026-10-03'),Decimal("88"))
            self.assertEqual(ledger_balance_at(repo.get_account('Cash'),repo.list_flows(),'2026-10-03',('Complete','Pending')),Decimal("86"))
            backup = Path(repo.migration_backup_path)
            self.assertTrue(backup.is_file())
            with sqlite3.connect(backup) as conn:
                self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],1)
                self.assertEqual(conn.execute('SELECT date FROM flows ORDER BY date').fetchall(),dates)
        finally:
            repo.close()
        reopened = Repository(path)
        self.assertIsNone(reopened.migration_backup_path)
        reopened.close()
        self.assertEqual(len(list(self.root.glob('legacy.sqlite3.pre-v7-*.bak'))),1)

    def test_failed_migration_keeps_legacy_data_and_pre_upgrade_snapshot(self):
        path = self.make_v1()
        real_validate = Repository._validate_database
        def reject_upgrade(conn, **kwargs):
            if conn.execute('PRAGMA user_version').fetchone()[0] == 7:
                raise ValueError('simulated migration validation failure')
            real_validate(conn, **kwargs)
        with patch.object(Repository,'_validate_database',side_effect=reject_upgrade):
            with self.assertRaisesRegex(ValueError,'simulated'):
                Repository(path)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(conn.execute('SELECT count(*) FROM flows').fetchone()[0],2)
        self.assertEqual(len(list(self.root.glob('legacy.sqlite3.pre-v7-*.bak'))),1)

    def test_restore_v1_migrates_private_copy_and_keeps_source_unchanged(self):
        path = self.make_v1()
        source_hash = hashlib.sha256(path.read_bytes()).digest()
        active = self.flow(name='Current data')
        self.repo.restore(path)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),source_hash)
        self.assertEqual(self.repo.conn.execute('PRAGMA user_version').fetchone()[0],7)
        self.assertEqual([row['date'] for row in self.repo.list_flows()],['2026-10-02','2026-10-02 12:34:56'])
        safety = list(self.root.glob('active.sqlite3.pre-restore-*.bak'))
        self.assertEqual(len(safety),1)
        old_active = Repository(safety[0])
        try:
            self.assertEqual(old_active.list_flows(),[active])
        finally:
            old_active.close()

    def test_json_csv_roundtrip_preserves_date_only_explicit_midnight_and_seconds(self):
        saved = [self.flow(),self.flow(date='2026-10-02 00:00'),self.flow(date='2026-10-02 12:34:56')]
        for suffix in ('json','csv'):
            with self.subTest(suffix=suffix):
                target = self.root / ('roundtrip.' + suffix)
                export_flows(self.repo,target)
                preview = preview_import(self.repo,read_import(target))
                self.assertEqual([row['state'] for row in preview],['Duplicate']*3)
                self.assertEqual({row['data']['flow_id']:row['data'] for row in preview},
                                 {row['flow_id']:row for row in saved})
                self.assertEqual(commit_import(self.repo,preview),{'imported':0,'skipped':3})

if __name__ == '__main__':
    unittest.main()
