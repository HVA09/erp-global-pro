"""Stage 9 migration tests: company-keyed settings and tenant-oriented indexes."""
import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SCHEMA = ROOT / 'erp' / 'schema.sql'
MIGRATIONS_PATH = ROOT / 'erp' / 'migrations' / '__init__.py'

import importlib.util
SPEC = importlib.util.spec_from_file_location('erp_migrations_stage9', MIGRATIONS_PATH)
MIGRATIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATIONS)


class Stage9MigrationTests(unittest.TestCase):
    def make_connection(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute("INSERT INTO roles(name,label,permissions,is_system) VALUES('admin','Admin','[\"*\"]',1)")
        conn.execute("INSERT INTO users(email,name,password_hash,role) VALUES('owner@test.invalid','Owner','x','admin')")
        conn.execute("INSERT INTO settings(key,value) VALUES('company',?)", (json.dumps('Legacy Co'),))
        MIGRATIONS.apply_migrations(conn, target_version=4)
        return conn

    def test_v5_rebuilds_settings_primary_key_and_preserves_legacy_values(self):
        conn = self.make_connection()
        try:
            company_id = conn.execute('SELECT id FROM companies').fetchone()[0]
            conn.execute("INSERT INTO settings(company_id,key,value) VALUES(?,?,?)",
                         (company_id, 'tax', json.dumps(15)))
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=5), 5)
            info = conn.execute('PRAGMA table_info(settings)').fetchall()
            pk = [row['name'] for row in sorted((r for r in info if r['pk']), key=lambda r: r['pk'])]
            self.assertEqual(pk, ['company_id', 'key'])
            self.assertEqual(json.loads(conn.execute(
                "SELECT value FROM settings WHERE company_id=? AND key='company'", (company_id,)
            ).fetchone()[0]), 'Legacy Co')
            self.assertEqual(json.loads(conn.execute(
                "SELECT value FROM settings WHERE company_id=? AND key='tax'", (company_id,)
            ).fetchone()[0]), 15)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 5)
        finally:
            conn.close()

    def test_settings_key_can_repeat_across_companies_when_test_gate_is_removed(self):
        conn = self.make_connection()
        try:
            company_a = conn.execute('SELECT id FROM companies').fetchone()[0]
            company_b = conn.execute("INSERT INTO companies(name) VALUES('Company B') RETURNING id").fetchone()[0]
            MIGRATIONS.apply_migrations(conn, target_version=5)
            for op in ('insert', 'update', 'delete'):
                conn.execute(f'DROP TRIGGER IF EXISTS trg_tenant_settings_block_multi_{op}')
            conn.execute('INSERT INTO settings(company_id,key,value) VALUES(?,?,?)',
                         (company_a, 'currency', json.dumps('LYD')))
            conn.execute('INSERT INTO settings(company_id,key,value) VALUES(?,?,?)',
                         (company_b, 'currency', json.dumps('USD')))
            values = dict(conn.execute("SELECT company_id,value FROM settings WHERE key='currency'").fetchall())
            self.assertEqual(json.loads(values[company_a]), 'LYD')
            self.assertEqual(json.loads(values[company_b]), 'USD')
        finally:
            conn.close()

    def test_settings_owner_is_immutable_and_multi_company_writes_remain_blocked(self):
        conn = self.make_connection()
        try:
            company_a = conn.execute('SELECT id FROM companies').fetchone()[0]
            MIGRATIONS.apply_migrations(conn, target_version=5)
            conn.execute("INSERT INTO companies(name) VALUES('Company B')")
            for sql, params in (
                ("INSERT INTO settings(company_id,key,value) VALUES(?,?,?)", (company_a, 'x', '1')),
                ("UPDATE settings SET value='2' WHERE company_id=? AND key='company'", (company_a,)),
                ("DELETE FROM settings WHERE company_id=? AND key='company'", (company_a,)),
            ):
                with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, 'tenant_isolation_pending'):
                    conn.execute(sql, params)
        finally:
            conn.close()

    def test_v5_adds_tenant_indexes_for_inventory_and_ledger(self):
        conn = self.make_connection()
        try:
            MIGRATIONS.apply_migrations(conn, target_version=5)
            expected = {
                'stock_moves': 'idx_stock_moves_tenant_product_warehouse_date',
                'stock_levels': 'idx_stock_levels_tenant_product_warehouse',
                'journal_entries': 'idx_journal_entries_tenant_date',
                'journal_lines': 'idx_journal_lines_tenant_entry_account',
                'sequences': 'idx_sequences_tenant_name',
            }
            for table, index in expected.items():
                with self.subTest(table=table):
                    names = {row['name'] for row in conn.execute(f'PRAGMA index_list("{table}")')}
                    self.assertIn(index, names)
        finally:
            conn.close()

    def test_v5_is_idempotent(self):
        conn = self.make_connection()
        try:
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=5), 5)
            self.assertEqual(MIGRATIONS.apply_migrations(conn, target_version=5), 5)
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 5)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
