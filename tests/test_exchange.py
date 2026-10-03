"""Contract tests for non-mutating previews and all-or-nothing data exchange."""

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from flowledger.core import Repository
from flowledger.exchange import EXPORT_FIELDS, commit_import, export_flows, preview_import, read_import


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Repository(Path(self.directory.name) / "test.sqlite3")
        self.addCleanup(self.repo.close)

    def row(self, **changes):
        result = {"date": "2026-10-02", "from_account": "AmEx", "to_account": "Joe's Diner",
                  "amount": "12.34", "status": "Complete", "name": "Lunch", "notes": "Burger, fries, tip",
                  "category": "Dining", "tags": ["Trip", "Family"]}
        result.update(changes)
        return result

    def save(self, **changes):
        return self.repo.save_flow(self.row(**changes))

    def path(self, name):
        return Path(self.directory.name) / name

    def snapshot(self):
        return list(self.repo.conn.iterdump())

    def test_preview_normalizes_but_never_writes(self):
        before = self.snapshot()
        result = preview_import(self.repo, [self.row(date="2026-10-02T13:14:15", tags="Trip | trip | Family")])
        self.assertEqual(result[0]["state"], "New")
        self.assertEqual(result[0]["data"]["date"], "2026-10-02 13:14:15")
        self.assertEqual(result[0]["data"]["tags"], ["Trip", "Family"])
        self.assertEqual(result[0]["data"]["amount_cents"], 1234)
        self.assertEqual(self.snapshot(), before)

    def test_required_fields_and_invalid_rows_report_each_error(self):
        rows = [None, ["not", "a row"], {}, self.row(from_account=""), self.row(to_account=" amex ")]
        result = preview_import(self.repo, rows)
        self.assertEqual([item["state"] for item in result], ["Error"] * len(rows))
        self.assertEqual([item["row"] for item in result], list(range(1, len(rows) + 1)))

    def test_malformed_amounts_dates_tags_and_status(self):
        changes = [
            {"amount": "12.345"}, {"amount": "NaN"}, {"amount": "Infinity"},
            {"amount": "0"}, {"amount": "-3.00"}, {"amount": True}, {"amount": 12.34},
            {"amount": "1e4"}, {"amount": "92233720368547758.08"},
            {"date": "2026-02-30"}, {"date": "2026-10-02 25:00"},
            {"date": "2026-10-02T12:00:00Z"}, {"date": "2026-10-02 12:00:00.5"},
            {"tags": [1]}, {"tags": [None]}, {"tags": {}}, {"tags": [["nested"]]},
            {"tags": '["broken"'}, {"tags": "[1, 2]"}, {"status": "Done"},
            {"flow_id": "not-a-uuid"}, {"amount_cents": 1235},
            {"from_account": "Bad\x00Account"},
        ]
        for change in changes:
            with self.subTest(change=change):
                self.assertEqual(preview_import(self.repo, [self.row(**change)])[0]["state"], "Error")

    def test_aliases_defaults_and_date_only(self):
        result = preview_import(self.repo, [{"Date": "2026-10-02", "From": " Cash ",
                                           "To": " Shop ", "Amount": "5.10"}])[0]
        self.assertEqual(result["state"], "New")
        data = result["data"]
        self.assertEqual(data["date"], "2026-10-02")
        self.assertEqual(data["name"], "Cash -> Shop")
        self.assertEqual(data["status"], "Complete")
        self.assertEqual(data["category"], "Uncategorized")
        self.assertEqual(data["tags"], [])
        bad = self.row(**{"from": "Conflicting Account"})
        self.assertEqual(preview_import(self.repo, [bad])[0]["state"], "Error")

    def test_duplicate_id_is_safe_skip_even_with_normalized_labels(self):
        saved = self.save()
        history = self.repo.history()
        row = {**saved, "from_account": " amex ", "to_account": " joe's diner ",
               "category": " dining ", "tags": ["family", "TRIP", "trip"]}
        result = preview_import(self.repo, [row])
        self.assertEqual(result[0]["state"], "Duplicate")
        self.assertEqual(commit_import(self.repo, result), {"imported": 0, "skipped": 1})
        self.assertEqual(len(self.repo.list_flows()), 1)
        self.assertEqual(self.repo.history(), history)

    def test_existing_id_conflict_aborts_entire_batch(self):
        saved = self.save()
        before = self.snapshot()
        result = preview_import(self.repo, [self.row(name="Another flow"),
                                           {**saved, "amount_cents": 2000}])
        self.assertEqual([item["state"] for item in result], ["New", "Conflict"])
        with self.assertRaises(ValueError):
            commit_import(self.repo, result)
        self.assertEqual(self.snapshot(), before)

    def test_same_batch_duplicate_id_imports_once(self):
        row = self.row(flow_id=str(uuid.uuid4()))
        result = preview_import(self.repo, [row, dict(row)])
        self.assertEqual([item["state"] for item in result], ["New", "Duplicate"])
        self.assertEqual(commit_import(self.repo, result), {"imported": 1, "skipped": 1})
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_same_batch_conflicting_id_aborts(self):
        row = self.row(flow_id=str(uuid.uuid4()))
        result = preview_import(self.repo, [row, {**row, "amount": "3.00"}])
        self.assertEqual([item["state"] for item in result], ["New", "Conflict"])
        with self.assertRaises(ValueError):
            commit_import(self.repo, result)
        self.assertEqual(self.repo.list_flows(), [])
        self.assertEqual(self.repo.list_accounts(), [])

    def test_legitimate_identical_rows_need_acceptance(self):
        result = preview_import(self.repo, [self.row(), self.row()])
        self.assertEqual([item["state"] for item in result], ["New", "Similar"])
        self.assertEqual(commit_import(self.repo, result), {"imported": 1, "skipped": 1})
        next_preview = preview_import(self.repo, [self.row(flow_id=str(uuid.uuid4()))])
        self.assertEqual(next_preview[0]["state"], "Similar")
        self.assertEqual(commit_import(self.repo, next_preview, include_similar=True),
                         {"imported": 1, "skipped": 0})
        self.assertEqual(len(self.repo.list_flows()), 2)
        self.assertEqual(commit_import(self.repo, preview_import(self.repo, [self.row(), self.row()]),
                                       include_similar=True), {"imported": 2, "skipped": 0})

    def test_error_aborts_without_creating_suggestions(self):
        before = self.snapshot()
        result = preview_import(self.repo, [self.row(), self.row(amount="broken")])
        with self.assertRaises(ValueError):
            commit_import(self.repo, result)
        self.assertEqual(self.snapshot(), before)

    def test_runtime_failure_rolls_back_flows_accounts_and_audit(self):
        before = self.snapshot()
        result = preview_import(self.repo, [self.row(), self.row(name="Second", to_account="Boat store")])
        real_save = self.repo.save_flow
        count = 0
        def failing_save(data):
            nonlocal count
            count += 1
            if count == 2:
                raise sqlite3.IntegrityError("simulated write failure")
            return real_save(data)
        with patch.object(self.repo, "save_flow", side_effect=failing_save):
            with self.assertRaises(sqlite3.IntegrityError):
                commit_import(self.repo, result)
        self.assertEqual(self.snapshot(), before)

    def test_stale_preview_catches_new_id_conflict(self):
        row = self.row(flow_id=str(uuid.uuid4()))
        result = preview_import(self.repo, [row])
        self.repo.save_flow({**row, "amount": "99.00"})
        before = self.snapshot()
        with self.assertRaises(ValueError):
            commit_import(self.repo, result)
        self.assertEqual(self.snapshot(), before)

    def test_stale_preview_catches_new_similar_content(self):
        result = preview_import(self.repo, [self.row()])
        self.save()
        self.assertEqual(commit_import(self.repo, result), {"imported": 0, "skipped": 1})
        self.assertEqual(len(self.repo.list_flows()), 1)

    def test_json_and_csv_roundtrip_preserves_exact_values_and_ids(self):
        saved = self.save(amount="123456789012345.67", date="2026-10-02 12:34:56",
                          notes='Comma, newline\n"quoted" café', tags=["Boat | trailer", "Road trip"])
        for suffix in (".json", ".csv"):
            with self.subTest(suffix=suffix):
                target = self.path("export" + suffix)
                self.assertEqual(export_flows(self.repo, target), 1)
                rows = read_import(target)
                self.assertEqual(rows[0]["amount"], "123456789012345.67")
                self.assertEqual(rows[0]["flow_id"], saved["flow_id"])
                result = preview_import(self.repo, rows)
                self.assertEqual(result[0]["state"], "Duplicate")
                self.assertEqual(result[0]["data"], saved)

    def test_csv_tags_pipe_and_json(self):
        path = self.path("input.csv")
        path.write_text('date,from,to,amount,tags\n2026-10-02,Cash,Shop,4.20,Boat|Trip\n', encoding="utf-8")
        result = preview_import(self.repo, read_import(path))
        self.assertEqual(result[0]["data"]["tags"], ["Boat", "Trip"])
        path.write_text('date,from,to,amount,tags\n2026-10-02,Cash,Shop,4.20,"[""Boat"", ""Trip""]"\n', encoding="utf-8")
        self.assertEqual(preview_import(self.repo, read_import(path))[0]["data"]["tags"], ["Boat", "Trip"])

    def test_json_object_wrapper_and_exact_number_literals(self):
        path = self.path("input.json")
        path.write_text('{"flows":[{"date":"2026-10-02","from":"Cash","to":"Shop","amount":12.34}]}',
                        encoding="utf-8")
        result = preview_import(self.repo, read_import(path))
        self.assertEqual(result[0]["data"]["amount_cents"], 1234)

    def test_file_structure_errors(self):
        cases = {"bad.json": '{broken', "object.json": '{"other":[]}',
                 "nan.json": '[{"amount":NaN}]', "empty.csv": '',
                 "duplicate.csv": 'date,from,from_account,to,amount\n',
                 "file.txt": 'anything'}
        for name, content in cases.items():
            with self.subTest(name=name):
                path = self.path(name)
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_import(path)

    def test_unknown_fields_are_rejected_without_writes(self):
        before = self.snapshot()
        for extra in ({"scenario": "Baseline"}, {"scenario": "Buy a boat"},
                      {"time_specified": False}, {"time_specified": ""},
                      {"invented_field": "anything"}):
            with self.subTest(extra=extra):
                result = preview_import(self.repo, [self.row(), self.row(**extra)])
                self.assertEqual(result[1]["state"], "Error")
                self.assertIn("Unsupported fields", result[1]["message"])
                self.assertIn(next(iter(extra)), result[1]["message"])
                with self.assertRaises(ValueError):
                    commit_import(self.repo, result)
                self.assertEqual(self.snapshot(), before)

    def test_legacy_csv_and_json_fields_are_rejected(self):
        csv_path = self.path("legacy.csv")
        csv_path.write_text("date,from,to,amount,scenario,time_specified\n"
                            "2026-10-02,Cash,Shop,4.20,Baseline,false\n", encoding="utf-8")
        json_path = self.path("legacy.json")
        json_path.write_text(json.dumps([self.row(scenario="Buy a boat")]), encoding="utf-8")
        for path in (csv_path, json_path):
            with self.subTest(path=path):
                result = preview_import(self.repo, read_import(path))
                self.assertEqual(result[0]["state"], "Error")
                self.assertIn("scenario", result[0]["message"])
        self.assertEqual(self.repo.list_flows(), [])

    def test_export_contains_exactly_nine_fields_plus_id_and_all_statuses(self):
        expected_fields = {"flow_id", "date", "from_account", "to_account", "amount",
                           "status", "name", "notes", "category", "tags"}
        self.assertEqual(set(EXPORT_FIELDS), expected_fields)
        for status in ("Complete", "Pending", "Planned", "Void"):
            self.save(status=status, name=status)
        for suffix in (".json", ".csv"):
            path = self.path("all_flows" + suffix)
            self.assertEqual(export_flows(self.repo, path), 4)
            rows = read_import(path)
            self.assertEqual({row["status"] for row in rows},
                             {"Complete", "Pending", "Planned", "Void"})
            self.assertTrue(all(set(row) == expected_fields for row in rows))

    def test_optional_time_roundtrip_preserves_date_only_and_explicit_midnight(self):
        dates = ["2026-10-02", "2026-10-02 00:00:00", "2026-10-02 12:34:56"]
        for index, date in enumerate(dates):
            self.save(date=date, name=f"Flow {index}")
        for suffix in (".json", ".csv"):
            with self.subTest(suffix=suffix):
                path = self.path("dates" + suffix)
                export_flows(self.repo, path)
                rows = read_import(path)
                self.assertEqual(sorted(row["date"] for row in rows), dates)
                result = preview_import(self.repo, rows)
                self.assertTrue(all(item["state"] == "Duplicate" for item in result))
                other = Repository(self.path("fresh" + suffix + ".sqlite3"))
                self.addCleanup(other.close)
                commit_import(other, preview_import(other, rows))
                self.assertEqual(sorted(flow["date"] for flow in other.list_flows()), dates)

    def test_same_id_with_changed_time_precision_is_conflict(self):
        saved = self.save(date="2026-10-02")
        result = preview_import(self.repo, [{**saved, "date": "2026-10-02 00:00:00"}])
        self.assertEqual(result[0]["state"], "Conflict")
        with self.assertRaises(ValueError):
            commit_import(self.repo, result)
        self.assertEqual(self.repo.get_flow(saved["flow_id"])["date"], "2026-10-02")

    def test_empty_batches_and_empty_export(self):
        self.assertEqual(commit_import(self.repo, []), {"imported": 0, "skipped": 0})
        for suffix in (".json", ".csv"):
            path = self.path("empty" + suffix)
            self.assertEqual(export_flows(self.repo, path), 0)
            self.assertEqual(read_import(path), [])


if __name__ == "__main__":
    unittest.main()
