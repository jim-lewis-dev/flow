"""Old account and forecast fields disappear without losing saved user data."""
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from flowledger.core import Repository, ledger_balance_at, _legacy_quantity
from tests.test_optional_times import V1_SCHEMA, FLOW_FIELDS


class AccountMigrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def make_legacy(self, version=2):
        path = self.root / f"legacy-v{version}.sqlite3"
        with sqlite3.connect(path) as conn:
            conn.executescript(V1_SCHEMA)
            conn.execute("ALTER TABLE flows ADD COLUMN time_specified INTEGER NOT NULL DEFAULT 0")
            conn.execute("UPDATE flows SET time_specified=1")
            conn.execute("UPDATE accounts SET archived=1 WHERE account_key='shop'")
            conn.execute("INSERT INTO accounts VALUES('unknown','Unknown',NULL,'2026-10-05 15:00:00',0,1)")
            conn.execute("UPDATE flows SET notes=? WHERE name='Lunch'", ("First line\nSecond line: café",))
            conn.execute("INSERT INTO tags VALUES('trip','Trip')")
            conn.execute("INSERT INTO flow_tags VALUES('00000000-0000-0000-0000-000000000001','trip',0)")
            conn.execute("INSERT INTO flows VALUES('00000000-0000-0000-0000-000000000003',"
                         "'2026-10-04 00:00:00','cash','shop',300,'Planned','Normal plan','','uncategorized','baseline',0)")
            conn.execute("INSERT INTO scenarios VALUES('alternative','Alternative')")
            conn.execute("INSERT INTO scenario_exclusions VALUES('alternative','00000000-0000-0000-0000-000000000002')")
            conn.execute("INSERT INTO flows VALUES('00000000-0000-0000-0000-000000000004',"
                         "'2026-10-02 13:14:15','cash','shop',99900,'Planned','Hypothetical',"
                         "'Do not apply to real ledger','uncategorized','alternative',1)")
            conn.execute("INSERT INTO flow_tags VALUES('00000000-0000-0000-0000-000000000004','trip',0)")
            if version == 3:
                conn.execute("ALTER TABLE accounts DROP COLUMN mine")
                conn.execute("ALTER TABLE accounts DROP COLUMN archived")
            conn.execute(f"PRAGMA user_version={version}")
        return path

    def make_v6(self):
        path = self.root / "v6.sqlite3"
        repo = Repository(":memory:")
        try:
            repo.save_account("A", "12.345", "2026-10-01", "kg")
            repo.save_flow(dict(date="2026-10-02 12:34:56", from_account="A", to_account="B",
                                amount="0.125", units="kg", tags=["First", "Second"], notes="Keep\nnotes"))
            statements = "\n".join(repo.conn.iterdump()).replace(
                "    FOREIGN KEY(from_key, units)",
                "    CHECK(from_key <> to_key),\n    FOREIGN KEY(from_key, units)")
            with sqlite3.connect(path) as conn:
                conn.executescript(statements)
                conn.execute("PRAGMA application_id = 1179406167")
                conn.execute("PRAGMA user_version = 6")
        finally:
            repo.close()
        return path

    def test_v6_upgrade_preserves_every_record_and_allows_equal_endpoints_without_backup(self):
        path = self.make_v6()
        with sqlite3.connect(path) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE flows SET to_key=from_key")
            tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
        repo = Repository(path)
        try:
            for table, rows in before.items():
                self.assertEqual([tuple(row) for row in repo.conn.execute(f"SELECT * FROM {table}")], rows)
            self.assertEqual(repo.conn.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertIsNone(repo.migration_backup_path)
            saved = repo.save_flow(dict(date="2026-10-03", from_account="A", to_account="a",
                                       amount="-3", units="kg"))
            self.assertEqual((saved["from_account"], saved["to_account"]), ("A", "A"))
            self.assertEqual(repo.conn.execute("PRAGMA foreign_key_check").fetchall(), [])
        finally:
            repo.close()
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_failed_v6_upgrade_rolls_back_and_v6_restore_leaves_source_unchanged(self):
        path = self.make_v6()
        with sqlite3.connect(path) as conn:
            before = list(conn.iterdump())
        validate = Repository._validate_database
        def fail(conn, **kwargs):
            if conn.execute("PRAGMA user_version").fetchone()[0] == 7:
                raise ValueError("Migration test failure")
            return validate(conn, **kwargs)
        with patch.object(Repository, "_validate_database", side_effect=fail):
            with self.assertRaisesRegex(ValueError, "Migration test failure"):
                Repository(path)
        with sqlite3.connect(path) as conn:
            self.assertEqual(list(conn.iterdump()), before)
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 6)
        self.assertEqual(list(self.root.iterdir()), [path])
        digest = hashlib.sha256(path.read_bytes()).digest()
        repo = Repository(self.root / "restored.sqlite3")
        try:
            repo.restore(path)
            self.assertEqual(repo.conn.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertEqual(repo.list_flows()[0]["tags"], ["First", "Second"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
        finally:
            repo.close()

    @staticmethod
    def snapshot(conn):
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: [tuple(row) for row in conn.execute(f"SELECT * FROM {name} ORDER BY 1")]
                for name in tables}

    def test_new_accounts_have_only_anchor_fields_and_no_hidden_visibility(self):
        repo = Repository(self.root / "new.sqlite3")
        self.addCleanup(repo.close)
        saved = repo.save_account(" Cash ", "10", "2026-10-01")
        self.assertEqual(saved, {"name": "Cash", "units": "USD", "opening_amount": "10",
                                 "opening_at": "2026-10-01 00:00:00"})
        flow = repo.save_flow({"from_account": "Cash", "to_account": "Shop", "amount": "1", "units": "USD"})
        self.assertEqual([row["name"] for row in repo.list_accounts()], ["Cash", "Shop"])
        self.assertIsNone(repo.get_account("Shop")["opening_amount"])
        self.assertIsNone(ledger_balance_at(repo.get_account("Shop"), repo.list_flows(), flow["date"]))
        self.assertEqual(set(json.loads(repo.history()[-2]["after_json"])), set(saved))
        self.assertEqual({row[1] for row in repo.conn.execute("PRAGMA table_info(accounts)")},
                         {"account_key", "name"})
        self.assertEqual({row[1] for row in repo.conn.execute("PRAGMA table_info(account_balances)")},
                         {"account_key", "units", "opening_amount", "opening_at"})

    def test_v2_and_v3_upgrade_keep_actual_flows_and_recover_hypothetical_collections(self):
        for version in (2,3):
            with self.subTest(version=version):
                path = self.make_legacy(version)
                with sqlite3.connect(path) as conn:
                    before = self.snapshot(conn)
                repo = Repository(path)
                try:
                    after = self.snapshot(repo.conn)
                    self.assertEqual(set(after), {'accounts','account_balances','flows','categories','tags','flow_tags','audit'})
                    self.assertEqual(after['accounts'],[row[:2] for row in before['accounts']])
                    self.assertEqual(after['account_balances'], [(row[0], 'USD', None if row[2] is None else _legacy_quantity(row[2]), row[3]) for row in before['accounts']])
                    self.assertEqual(after['audit'],before['audit'])
                    self.assertEqual(after['categories'],before['categories'])
                    self.assertEqual(after['tags'],before['tags'])
                    actual = repo.list_flows()
                    self.assertEqual([flow['date'] for flow in actual],
                                     ['2026-10-02 00:00:00','2026-10-02 12:34:56','2026-10-04'])
                    self.assertTrue(all(set(flow)==FLOW_FIELDS for flow in actual))
                    self.assertEqual(actual[0]['notes'],'First line\nSecond line: café')
                    self.assertEqual(actual[0]['tags'],['Trip'])
                    self.assertEqual(actual[2]['status'],'Planned')
                    self.assertEqual([flow['flow_id'] for flow in actual], [
                        '00000000-0000-0000-0000-000000000001',
                        '00000000-0000-0000-0000-000000000002',
                        '00000000-0000-0000-0000-000000000003'])
                    self.assertIsNone(repo.get_account('Unknown')['opening_amount'])
                    self.assertEqual(ledger_balance_at(repo.get_account('Cash'),actual,'2026-10-03'),88)
                    self.assertEqual(ledger_balance_at(repo.get_account('Cash'),actual,'2026-10-03',('Complete','Pending','Planned')),86)
                    self.assertEqual(repo.conn.execute('PRAGMA foreign_keys').fetchone()[0],1)
                    self.assertEqual(repo.conn.execute('PRAGMA user_version').fetchone()[0],7)
                    self.assertIsNone(repo.conn.execute('PRAGMA foreign_key_check').fetchone())
                    with self.assertRaises(sqlite3.IntegrityError):
                        repo.conn.execute("DELETE FROM accounts WHERE account_key='cash'")
                    with sqlite3.connect(repo.migration_backup_path) as conn:
                        self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],version)
                        self.assertEqual(self.snapshot(conn),before)
                    recovered = json.loads(Path(repo.migration_forecasts_path).read_text())
                    self.assertEqual(len(recovered),1)
                    self.assertEqual(recovered[0]['name'],'Alternative')
                    self.assertEqual(recovered[0]['excluded_flow_ids'],['00000000-0000-0000-0000-000000000002'])
                    hypothetical = recovered[0]['flows'][0]
                    self.assertEqual(set(hypothetical),FLOW_FIELDS)
                    self.assertEqual(hypothetical['date'],'2026-10-02 13:14:15')
                    self.assertEqual(hypothetical['amount'],'999')
                    self.assertEqual(hypothetical['units'],'USD')
                    self.assertEqual(hypothetical['tags'],['Trip'])
                    self.assertEqual(hypothetical['notes'],'Do not apply to real ledger')
                    self.assertIsNone(repo.get_flow(hypothetical['flow_id']))
                finally:
                    repo.close()
                reopened = Repository(path)
                self.assertIsNone(reopened.migration_backup_path)
                reopened.close()
                self.assertEqual(len(list(self.root.glob(f'legacy-v{version}.sqlite3.pre-v7-*.bak'))),1)

    def test_failed_upgrade_rolls_back_all_tables_and_version(self):
        path = self.make_legacy()
        with sqlite3.connect(path) as conn:
            before = self.snapshot(conn)
        validate = Repository._validate_database
        def fail_on_new_schema(conn, **kwargs):
            if conn.execute('PRAGMA user_version').fetchone()[0] == 7:
                raise ValueError('simulated migration failure')
            validate(conn, **kwargs)
        with patch.object(Repository,'_validate_database',side_effect=fail_on_new_schema):
            with self.assertRaisesRegex(ValueError,'simulated'):
                Repository(path)
        with sqlite3.connect(path) as conn:
            self.assertEqual(conn.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(self.snapshot(conn),before)
        self.assertEqual(len(list(self.root.glob('*.pre-v7-*.bak'))),1)

    def test_restore_v3_preserves_source_forecasts_and_current_data_safety_copy(self):
        path = self.make_legacy(3)
        original_hash = hashlib.sha256(path.read_bytes()).digest()
        repo = Repository(self.root / 'active.sqlite3')
        self.addCleanup(repo.close)
        current = repo.save_account('Existing',12345)
        repo.restore(path)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),original_hash)
        self.assertEqual([row['name'] for row in repo.list_accounts()],['Cash','Shop','Unknown'])
        self.assertEqual(repo.conn.execute('PRAGMA user_version').fetchone()[0],7)
        self.assertEqual(repo.conn.execute('PRAGMA foreign_keys').fetchone()[0],1)
        self.assertEqual(len(repo.list_flows()),3)
        recovered = json.loads(Path(repo.migration_forecasts_path).read_text())
        self.assertEqual(recovered[0]['flows'][0]['name'],'Hypothetical')
        self.assertEqual(repo.migration_backup_path,str(path))
        backups = list(self.root.glob('active.sqlite3.pre-restore-*.bak'))
        self.assertEqual(len(backups),1)
        old = Repository(backups[0])
        self.addCleanup(old.close)
        self.assertEqual(old.list_accounts(),[current])


if __name__ == '__main__':
    unittest.main()
