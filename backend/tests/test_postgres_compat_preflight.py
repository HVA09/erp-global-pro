import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from scripts.postgres_compat_preflight import build_report, operational_files


class PostgresCompatibilityPreflightTests(unittest.TestCase):
    def test_operational_file_inventory_excludes_tests(self):
        files = operational_files()
        self.assertTrue(files)
        self.assertTrue(all("tests" not in p.parts for p in files))
        self.assertTrue(any(p.name == "schema.sql" for p in files))

    def test_report_discovers_schema_version(self):
        report = build_report()
        self.assertEqual(report["database_schema_version"], 8)

    def test_report_exposes_sqlite_specific_categories(self):
        report = build_report()
        categories = report["sqlite_specific_indicators"]
        for key in ("pragma", "sqlite_master", "sqlite3_reference", "create_trigger", "lastrowid"):
            self.assertIn(key, categories)
            self.assertGreater(categories[key]["count"], 0)

    def test_question_mark_count_is_explicitly_lexical_not_bind_count(self):
        report = build_report()
        self.assertGreater(report["raw_question_mark_characters_in_python"], 0)
        self.assertIn("Do not mass-replace", report["raw_question_mark_note"])

    def test_report_states_no_postgres_runtime_tests(self):
        report = build_report()
        self.assertEqual(report["postgresql_runtime_tests"], 0)
        self.assertIn("not enabled", report["decision"])

    def test_cli_writes_valid_json_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "postgres-preflight.json"
            result = subprocess.run(
                [sys.executable, str(BACKEND / "scripts" / "postgres_compat_preflight.py"), "--output", str(output)],
                cwd=BACKEND,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            parsed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(parsed["project"], "ERP Global Pro")


if __name__ == "__main__":
    unittest.main()
