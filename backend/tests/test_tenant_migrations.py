"""SQLite-only tests for the versioned multi-company identity migration."""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

# Load the standalone migration module directly so these migration checks do
# not require importing the Flask application package.
_MIGRATIONS_PATH = Path(__file__).parents[1] / 'erp' / 'migrations' / '__init__.py'
_SPEC = importlib.util.spec_from_file_location('erp_migrations_standalone', _MIGRATIONS_PATH)
_MIGRATIONS = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MIGRATIONS)
apply_migrations = _MIGRATIONS.apply_migrations


class TenantMigrationTests(unittest.TestCase):
    def make_connection(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(
            '''
            CREATE TABLE schema_version(version INTEGER NOT NULL);
            INSERT INTO schema_version(version) VALUES(1);
            CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE roles(name TEXT PRIMARY KEY);
            CREATE TABLE users(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              email TEXT NOT NULL UNIQUE,
              role TEXT NOT NULL REFERENCES roles(name),
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            '''
        )
        return conn

    def test_legacy_users_are_mapped_to_one_company_with_existing_roles(self):
        conn = self.make_connection()
        try:
            conn.executemany('INSERT INTO roles(name) VALUES(?)', [('admin',), ('sales',)])
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?)', ('company', '"Legacy Trading"'))
            conn.execute('INSERT INTO settings(key,value) VALUES(?,?)', ('currency', '"LYD"'))
            conn.execute("INSERT INTO users(email,role) VALUES('owner@example.test','admin')")
            conn.execute("INSERT INTO users(email,role) VALUES('seller@example.test','sales')")

            self.assertEqual(apply_migrations(conn, target_version=2), 2)
            company = conn.execute('SELECT name,currency FROM companies').fetchone()
            self.assertEqual(company, ('Legacy Trading', 'LYD'))
            memberships = conn.execute(
                'SELECT u.email,m.role,m.status FROM company_memberships m '
                'JOIN users u ON u.id=m.user_id ORDER BY u.email'
            ).fetchall()
            self.assertEqual(memberships, [
                ('owner@example.test', 'admin', 'active'),
                ('seller@example.test', 'sales', 'active'),
            ])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            conn.close()

    def test_empty_database_gets_identity_tables_without_fake_company(self):
        conn = self.make_connection()
        try:
            self.assertEqual(apply_migrations(conn, target_version=2), 2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM company_memberships').fetchone()[0], 0)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertIn('companies', tables)
            self.assertIn('company_memberships', tables)
        finally:
            conn.close()

    def test_migration_runs_against_the_real_legacy_schema(self):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.execute('PRAGMA foreign_keys=ON')
        schema_path = Path(__file__).parents[1] / 'erp' / 'schema.sql'
        conn.executescript(schema_path.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute(
            'INSERT INTO roles(name,label,permissions,is_system) VALUES(?,?,?,1)',
            ('admin', 'Administrator', '["*"]'),
        )
        conn.execute("INSERT INTO settings(key,value) VALUES('company','\"Legacy Full Schema\"')")
        conn.execute(
            "INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)",
            ('legacy@example.test', 'Legacy User', 'hash-placeholder', 'admin'),
        )
        try:
            self.assertEqual(apply_migrations(conn, target_version=2), 2)
            self.assertEqual(
                conn.execute(
                    'SELECT c.name,m.role,m.status FROM company_memberships m '
                    'JOIN companies c ON c.id=m.company_id'
                ).fetchone(),
                ('Legacy Full Schema', 'admin', 'active'),
            )
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            conn.close()

    def test_migration_is_idempotent(self):
        conn = self.make_connection()
        try:
            conn.execute('INSERT INTO roles(name) VALUES(?)', ('admin',))
            conn.execute("INSERT INTO users(email,role) VALUES('a@example.test','admin')")
            apply_migrations(conn, target_version=2)
            apply_migrations(conn, target_version=2)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM companies').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM company_memberships').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 2)
        finally:
            conn.close()

    def test_unknown_legacy_role_aborts_without_partial_schema_or_version(self):
        conn = self.make_connection()
        try:
            conn.execute('PRAGMA foreign_keys=OFF')
            conn.execute("INSERT INTO users(email,role) VALUES('orphan@example.test','missing-role')")
            conn.execute('PRAGMA foreign_keys=ON')
            with self.assertRaises(RuntimeError):
                apply_migrations(conn, target_version=2)
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 1)
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('companies', tables)
            self.assertNotIn('company_memberships', tables)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
