"""Dates keep their precision while omitted components receive safe defaults."""
import tempfile
import unittest
from pathlib import Path

from flowledger.core import normalize_date, normalize_flow_date
from mobile.bridge import MobileStore


class DateEntryTests(unittest.TestCase):
    def test_partial_dates_and_times(self):
        cases = {
            '2026': '2026-01-01',
            '2026-11': '2026-11-01',
            '2026-2-3': '2026-02-03',
            '2026 9': '2026-01-01 09:00:00',
            '2026-11 9': '2026-11-01 09:00:00',
            '2026-11 00:00:00': '2026-11-01 00:00:00',
            '2026-11T9:4': '2026-11-01 09:04:00',
            ' 2026-12-30 9:4:2 ': '2026-12-30 09:04:02',
            '2028-2-29': '2028-02-29',
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(normalize_flow_date(value), expected)
                self.assertEqual(normalize_date(value), expected if ' ' in expected else expected + ' 00:00:00')

    def test_invalid_components_are_not_silently_corrected(self):
        for value in ('', '11', '2026-', '2026-0', '2026-13', '2026-2-29',
                      '2026-11-31', '2026-11 24', '2026-11 9:60',
                      '2026-11 9:3:60', '2026-11 9:', '2026-11 9.5',
                      '2026-11-1T9:30Z', '2026-11-1 9:30+01:00'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_date(value)

    def test_partial_date_with_time_across_views(self):
        with tempfile.TemporaryDirectory() as directory:
            store = MobileStore(Path(directory))
            try:
                store.dispatch('init', {})
                entry = dict(date='2026-11 9', name='Breakfast', from_account='Pantry',
                             to_account='Breakfast', amount='2', units='eggs',
                             status='Complete', category='Food', tags=[], notes='')
                result = store.dispatch('add_flow', {'flow': entry})
                self.assertEqual(result['record']['date'], '2026-11-01 09:00:00')
                store.dispatch('save_anchor', dict(name='Pantry', units='eggs',
                               opening_at='2026-11 8', opening_amount='12'))
                state = store.dispatch('view', {'as_of': '2026-11 9'})['state']
                pantry = next(a for a in state['accounts'] if a['name'] == 'Pantry')
                self.assertEqual(pantry['balance'], '10')
                args = dict(pairs=[['Pantry', 'eggs']], start='2026-11 8', end='2026-11 8:59')
                graph = store.dispatch('graph', args)
                self.assertEqual(graph['end'], '2026-11-01 08:59:00')
                self.assertEqual(graph['series'][0]['points'][-1][1], '12')
                args['end'] = '2026-11'
                self.assertEqual(store.dispatch('graph', args)['series'][0]['points'][-1][1], '10')
                selection = dict(units='eggs', start='2026-11', end='2026-11 8:59')
                self.assertEqual(store.dispatch('experimental', selection)['groups'], [])
                selection['end'] = '2026-11'
                self.assertEqual(len(store.dispatch('experimental', selection)['groups']), 1)
            finally:
                store.close()


if __name__ == '__main__':
    unittest.main()
