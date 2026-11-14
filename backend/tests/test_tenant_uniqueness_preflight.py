"""Read-only tests for the Stage 5 tenant uniqueness preflight utility."""
import importlib.util
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / 'scripts' / 'tenant_uniqueness_preflight.py'
SPEC = importlib.util.spec_from_file_location('tenant_uniqueness_preflight', SCRIPT)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREFLIGHT)


class TenantUniquenessPreflightTests(unittest.TestCase):
    def make_database(self, include_all_rules=True):
        temp = tempfile.TemporaryDirectory()
        path = Path(temp.name) / 'preflight.db'
        conn = sqlite3.connect(path)
        conn.execute('CREATE TABLE companies(id INTEGER PRIMARY KEY, name TEXT NOT NULL)')
        conn.executemany('INSERT INTO companies(id,name) VALUES(?,?)', [(1, 'Company A'), (2, 'Company B')])
        if include_all_rules:
            table_columns = {}
            for rule in PREFLIGHT.RULES:
                table_columns.setdefault(rule['table'], set()).update(rule['columns'])
            for table, names in table_columns.items():
                columns = ['company_id INTEGER NOT NULL']
                for column in sorted(names):
                    if column in ('employee_id', 'asset_id', 'product_id', 'warehouse_id'):
                        columns.append(f'"{column}" INTEGER')
                    else:
                        columns.append(f'"{column}" TEXT')
                if table == 'payroll_runs':
                    columns.append('status TEXT NOT NULL')
                conn.execute(f'CREATE TABLE "{table}" ({", ".join(columns)})')
        conn.execute('CREATE TABLE schema_version(version INTEGER NOT NULL)')
        conn.execute('INSERT INTO schema_version(version) VALUES(4)')
        conn.commit()
        conn.close()
        return temp, path

    def test_detects_duplicates_within_company_but_not_between_companies(self):
        temp, path = self.make_database()
        try:
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO accounts(company_id,code) VALUES(1,'1000')")
            conn.execute("INSERT INTO accounts(company_id,code) VALUES(1,'1000')")
            conn.execute("INSERT INTO accounts(company_id,code) VALUES(2,'1000')")
            conn.commit()
            conn.close()

            report = PREFLIGHT.run_preflight(path)
            account_rule = next(r for r in report['rules'] if r['id'] == 'accounts_code')
            self.assertEqual(account_rule['status'], 'duplicates_found')
            self.assertEqual(account_rule['duplicate_groups'], 1)
            self.assertEqual(account_rule['affected_rows'], 2)
            self.assertEqual(len(account_rule['samples'][0]['sample_rowids']), 2)
            self.assertNotIn('key_values', account_rule['samples'][0])
            self.assertEqual(report['schema_version'], 4)
            self.assertTrue(report['read_only'])
        finally:
            temp.cleanup()

    def test_partial_predicates_match_legacy_business_rules(self):
        temp, path = self.make_database()
        try:
            conn = sqlite3.connect(path)
            conn.executemany(
                'INSERT INTO payroll_runs(company_id,month,status) VALUES(?,?,?)',
                [
                    (1, '2026-10', 'posted'),
                    (1, '2026-10', 'posted'),
                    (1, '2026-11', 'draft'),
                    (1, '2026-11', 'draft'),
                ],
            )
            conn.executemany(
                'INSERT INTO products(company_id,sku,barcode) VALUES(?,?,?)',
                [
                    (1, 'A', None),
                    (1, 'B', None),
                    (1, 'C', 'BC-1'),
                    (1, 'D', 'BC-1'),
                ],
            )
            conn.executemany(
                'INSERT INTO trade_documents(company_id,doc_type,number) VALUES(?,?,?)',
                [
                    (1, 'invoice', None),
                    (1, 'invoice', None),
                    (1, 'invoice', 'INV-1'),
                    (1, 'invoice', 'INV-1'),
                ],
            )
            conn.commit()
            conn.close()

            report = PREFLIGHT.run_preflight(path, include_values=True)
            by_id = {r['id']: r for r in report['rules']}
            self.assertEqual(by_id['payroll_runs_posted_month']['duplicate_groups'], 1)
            self.assertEqual(by_id['products_barcode_nonnull']['duplicate_groups'], 1)
            self.assertEqual(by_id['trade_documents_type_number_nonnull']['duplicate_groups'], 1)
            self.assertIn('key_values', by_id['products_barcode_nonnull']['samples'][0])
        finally:
            temp.cleanup()

    def test_reports_missing_columns_without_guessing(self):
        temp, path = self.make_database(include_all_rules=False)
        try:
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE accounts(code TEXT)')
            conn.commit()
            conn.close()

            report = PREFLIGHT.run_preflight(path)
            account_rule = next(r for r in report['rules'] if r['id'] == 'accounts_code')
            self.assertEqual(account_rule['status'], 'missing_columns')
            self.assertEqual(account_rule['missing_columns'], ['company_id'])
            self.assertGreater(report['summary']['rules_unavailable'], 0)
        finally:
            temp.cleanup()

    def test_reports_rows_without_company_ownership(self):
        temp, path = self.make_database(include_all_rules=False)
        try:
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE accounts(company_id INTEGER, code TEXT)')
            conn.execute("INSERT INTO accounts(company_id,code) VALUES(NULL,'ORPHAN')")
            conn.commit()
            conn.close()

            report = PREFLIGHT.run_preflight(path)
            account_rule = next(r for r in report['rules'] if r['id'] == 'accounts_code')
            self.assertEqual(account_rule['status'], 'ownership_missing')
            self.assertEqual(account_rule['unowned_rows'], 1)
            self.assertEqual(report['summary']['unowned_rows'], 1)
        finally:
            temp.cleanup()

    def test_missing_partial_predicate_column_is_reported(self):
        temp, path = self.make_database(include_all_rules=False)
        try:
            conn = sqlite3.connect(path)
            conn.execute('CREATE TABLE payroll_runs(company_id INTEGER, month TEXT)')
            conn.commit()
            conn.close()

            report = PREFLIGHT.run_preflight(path)
            payroll_rule = next(r for r in report['rules'] if r['id'] == 'payroll_runs_posted_month')
            self.assertEqual(payroll_rule['status'], 'missing_columns')
            self.assertEqual(payroll_rule['missing_columns'], ['status'])
        finally:
            temp.cleanup()

    def test_database_is_opened_query_only_and_unchanged(self):
        temp, path = self.make_database()
        try:
            conn = sqlite3.connect(path)
            conn.execute("INSERT INTO accounts(company_id,code) VALUES(1,'2000')")
            conn.commit()
            before = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
            conn.close()

            report = PREFLIGHT.run_preflight(path)
            self.assertTrue(report['read_only'])

            ro = PREFLIGHT._open_readonly(path)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    ro.execute("INSERT INTO accounts(company_id,code) VALUES(1,'3000')")
            finally:
                ro.close()

            conn = sqlite3.connect(path)
            after = conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
            conn.close()
            self.assertEqual(before, after)
        finally:
            temp.cleanup()

    def test_empty_or_null_business_key_values_are_not_treated_as_duplicates_when_null(self):
        temp, path = self.make_database()
        try:
            conn = sqlite3.connect(path)
            conn.executemany(
                'INSERT INTO accounts(company_id,code) VALUES(?,?)',
                [(1, None), (1, None), (1, ''), (1, '')],
            )
            conn.commit()
            conn.close()

            report = PREFLIGHT.run_preflight(path)
            account_rule = next(r for r in report['rules'] if r['id'] == 'accounts_code')
            self.assertEqual(account_rule['duplicate_groups'], 1)
            self.assertEqual(account_rule['affected_rows'], 2)
        finally:
            temp.cleanup()

    def test_cli_returns_duplicate_exit_code_and_writes_json_report(self):
        temp, path = self.make_database()
        try:
            conn = sqlite3.connect(path)
            conn.executemany(
                'INSERT INTO accounts(company_id,code) VALUES(?,?)',
                [(1, 'DUP'), (1, 'DUP')],
            )
            conn.commit()
            conn.close()
            output = Path(temp.name) / 'report.json'
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(path), '--fail-on-duplicates', '--output', str(output)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertTrue(output.exists())
            report = __import__('json').loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['summary']['duplicate_groups'], 1)
        finally:
            temp.cleanup()

    def test_cli_strict_schema_exit_code(self):
        temp, path = self.make_database(include_all_rules=False)
        try:
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), str(path), '--strict-schema'],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        finally:
            temp.cleanup()


if __name__ == '__main__':
    unittest.main()
