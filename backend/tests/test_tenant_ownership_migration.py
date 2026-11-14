"""SQLite-only tests for Stage 3 company ownership migration (no Flask dependency)."""
import importlib.util
import json
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MIGRATIONS_PATH = ROOT / 'erp' / 'migrations' / '__init__.py'
SPEC = importlib.util.spec_from_file_location('erp_migrations_stage3', MIGRATIONS_PATH)
MIGRATIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATIONS)
apply_migrations = MIGRATIONS.apply_migrations
TENANT_TABLES = MIGRATIONS.TENANT_TABLES
SCHEMA = ROOT / 'erp' / 'schema.sql'


class TenantOwnershipMigrationTests(unittest.TestCase):
    def make_full_connection(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute(
            'INSERT INTO roles(name,label,permissions,is_system) VALUES(?,?,?,1)',
            ('admin', 'Administrator', '["*"]'),
        )
        return conn

    def add_legacy_user_and_migrate_identity(self, conn):
        conn.execute("INSERT INTO settings(key,value) VALUES('company',?)", (json.dumps('Legacy Org'),))
        conn.execute("INSERT INTO settings(key,value) VALUES('currency',?)", (json.dumps('LYD'),))
        conn.execute(
            'INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',
            ('owner@example.test', 'Owner', 'test-hash', 'admin'),
        )
        self.assertEqual(apply_migrations(conn, target_version=2), 2)

    def test_all_tenant_tables_receive_non_null_company_owner_and_index(self):
        conn = self.make_full_connection()
        try:
            self.add_legacy_user_and_migrate_identity(conn)
            company_id = conn.execute('SELECT id FROM companies').fetchone()[0]
            conn.execute("INSERT INTO categories(name) VALUES('Old category')")
            category_id = conn.execute('SELECT id FROM categories').fetchone()[0]
            conn.execute(
                'INSERT INTO products(sku,name,category_id) VALUES(?,?,?)',
                ('OLD-1', 'Old product', category_id),
            )
            conn.execute(
                'INSERT INTO audit_log(user_id,user_email,action,entity) VALUES(?,?,?,?)',
                (1, 'owner@example.test', 'legacy', 'system'),
            )

            self.assertEqual(apply_migrations(conn, target_version=3), 3)
            for table in TENANT_TABLES:
                columns = {r['name']: r for r in conn.execute(f'PRAGMA table_info("{table}")')}
                self.assertIn('company_id', columns, table)
                self.assertEqual(columns['company_id']['notnull'], 1, table)
                self.assertEqual(columns['company_id']['dflt_value'], str(company_id), table)
                indexes = {r['name'] for r in conn.execute(f'PRAGMA index_list("{table}")')}
                self.assertIn(f'idx_tenant_{table}_company_id', indexes, table)
                foreign_keys = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
                self.assertTrue(any(fk['table'] == 'companies' and fk['from'] == 'company_id'
                                    for fk in foreign_keys), table)

            self.assertEqual(conn.execute('SELECT company_id FROM categories WHERE id=?', (category_id,)).fetchone()[0], company_id)
            self.assertEqual(conn.execute('SELECT company_id FROM products WHERE sku=?', ('OLD-1',)).fetchone()[0], company_id)
            self.assertEqual(conn.execute('SELECT company_id FROM audit_log').fetchone()[0], company_id)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 3)
        finally:
            conn.close()

    def test_new_single_company_rows_inherit_bootstrap_company(self):
        conn = self.make_full_connection()
        try:
            self.add_legacy_user_and_migrate_identity(conn)
            company_id = conn.execute('SELECT id FROM companies').fetchone()[0]
            apply_migrations(conn)
            conn.execute("INSERT INTO categories(name) VALUES('New category')")
            conn.execute("INSERT INTO accounts(code,name,type,subtype) VALUES('A100','Cash','asset','cash')")
            self.assertEqual(
                conn.execute('SELECT company_id FROM categories WHERE name=?', ('New category',)).fetchone()[0],
                company_id,
            )
            self.assertEqual(
                conn.execute('SELECT company_id FROM accounts WHERE code=?', ('A100',)).fetchone()[0],
                company_id,
            )
        finally:
            conn.close()

    def test_tenant_ownership_cannot_be_changed_after_creation(self):
        conn = self.make_full_connection()
        try:
            self.add_legacy_user_and_migrate_identity(conn)
            apply_migrations(conn)
            conn.execute("INSERT INTO categories(name) VALUES('Protected')")
            conn.execute('PRAGMA foreign_keys=OFF')
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'company_id is immutable'):
                conn.execute('UPDATE categories SET company_id=999 WHERE name=?', ('Protected',))
            conn.execute('PRAGMA foreign_keys=ON')
        finally:
            conn.close()

    def test_database_blocks_tenant_writes_when_second_company_exists(self):
        conn = self.make_full_connection()
        try:
            self.add_legacy_user_and_migrate_identity(conn)
            apply_migrations(conn)
            conn.execute("INSERT INTO categories(name) VALUES('Company One')")
            conn.execute("INSERT INTO companies(name) VALUES('Company Two')")
            for sql in (
                "INSERT INTO categories(name) VALUES('Blocked')",
                "UPDATE categories SET name='Blocked' WHERE name='Company One'",
                "DELETE FROM categories WHERE name='Company One'",
            ):
                with self.subTest(sql=sql), self.assertRaisesRegex(sqlite3.IntegrityError, 'tenant_isolation_pending'):
                    conn.execute(sql)
            # System-level audit events remain possible while business data is locked.
            conn.execute("INSERT INTO audit_log(action,entity) VALUES('login_failed','user')")
            self.assertIsNone(conn.execute(
                "SELECT company_id FROM audit_log WHERE action='login_failed'"
            ).fetchone()[0])
        finally:
            conn.close()

    def test_fresh_database_gets_bootstrap_company_for_first_run_setup(self):
        conn = self.make_full_connection()
        try:
            self.assertEqual(apply_migrations(conn, target_version=3), 3)
            rows = conn.execute('SELECT id,name,status FROM companies').fetchall()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]['name'], 'My Company')
            self.assertEqual(rows[0]['status'], 'active')
        finally:
            conn.close()

    def test_migration_refuses_to_guess_when_multiple_companies_exist(self):
        conn = self.make_full_connection()
        try:
            self.assertEqual(apply_migrations(conn, target_version=2), 2)
            conn.execute("INSERT INTO companies(name) VALUES('Company A')")
            conn.execute("INSERT INTO companies(name) VALUES('Company B')")
            with self.assertRaisesRegex(RuntimeError, 'cannot infer ownership'):
                apply_migrations(conn)
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 2)
            columns = {r['name'] for r in conn.execute('PRAGMA table_info(categories)')}
            self.assertNotIn('company_id', columns)
        finally:
            conn.close()

    def test_audit_log_is_hybrid_and_allows_global_events(self):
        conn = self.make_full_connection()
        try:
            self.assertEqual(apply_migrations(conn, target_version=3), 3)
            info = {r['name']: r for r in conn.execute('PRAGMA table_info(audit_log)')}
            self.assertEqual(info['company_id']['notnull'], 0)
            conn.execute("INSERT INTO audit_log(action,entity) VALUES('system_start','system')")
            self.assertIsNone(conn.execute(
                "SELECT company_id FROM audit_log WHERE action='system_start'"
            ).fetchone()[0])
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
