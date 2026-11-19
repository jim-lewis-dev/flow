"""Optional date precision and exact-second arithmetic."""
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from flowledger.core import Repository, ledger_balance_at, ledger_timeline
from flowledger.exchange import commit_import, export_flows, preview_import, read_import

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

    def test_edit_and_cancel_preserve_date_only_without_duplicate_effect(self):
        self.repo.save_account('Cash','100','2026-10-01')
        saved = self.flow()
        updated = self.repo.save_flow({"units": "USD", **saved,'status':'Pending','amount':'15'})
        self.assertEqual(updated['date'],'2026-10-02')
        updated = self.repo.save_flow({**updated,'status':'Complete'})
        self.assertEqual(len(self.repo.list_flows()),1)
        self.assertEqual(ledger_balance_at(self.repo.get_account('Cash'),self.repo.list_flows(),'2026-10-03'),Decimal("85"))
        self.assertEqual(self.repo.save_flow({**updated, "status": "Canceled"})['date'],'2026-10-02')
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
