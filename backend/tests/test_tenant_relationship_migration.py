"""SQLite tests for Stage 4 same-company relationship guards."""
import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
MIGRATIONS_PATH = ROOT / 'erp' / 'migrations' / '__init__.py'
SPEC = importlib.util.spec_from_file_location('erp_migrations_stage4', MIGRATIONS_PATH)
MIGRATIONS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATIONS)
apply_migrations = MIGRATIONS.apply_migrations
SCHEMA = ROOT / 'erp' / 'schema.sql'


class TenantRelationshipMigrationTests(unittest.TestCase):
    def make_connection(self, target_version=4):
        conn = sqlite3.connect(':memory:', isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(SCHEMA.read_text(encoding='utf-8'))
        conn.execute('INSERT INTO schema_version(version) VALUES(1)')
        conn.execute(
            'INSERT INTO roles(name,label,permissions,is_system) VALUES(?,?,?,1)',
            ('admin', 'Administrator', '["*"]'),
        )
        conn.execute(
            'INSERT INTO users(email,name,password_hash,role) VALUES(?,?,?,?)',
            ('owner@example.test', 'Owner', 'test-hash', 'admin'),
        )
        self.assertEqual(apply_migrations(conn, target_version=target_version), target_version)
        return conn

    def _drop_multi_company_write_gates(self, conn, tables):
        for table in tables:
            for operation in ('insert', 'update', 'delete'):
                conn.execute(
                    f'DROP TRIGGER IF EXISTS "trg_tenant_{table}_block_multi_{operation}"'
                )

    def _create_two_company_category_fixture(self, conn):
        company_a = conn.execute('SELECT id FROM companies ORDER BY id LIMIT 1').fetchone()[0]
        company_b = conn.execute(
            "INSERT INTO companies(name) VALUES('Company B') RETURNING id"
        ).fetchone()[0]
        self._drop_multi_company_write_gates(conn, ('categories', 'products'))
        category_a = conn.execute(
            'INSERT INTO categories(name,company_id) VALUES(?,?) RETURNING id',
            ('Category A', company_a),
        ).fetchone()[0]
        category_b = conn.execute(
            'INSERT INTO categories(name,company_id) VALUES(?,?) RETURNING id',
            ('Category B', company_b),
        ).fetchone()[0]
        return company_a, company_b, category_a, category_b

    def test_stage4_creates_expected_relation_guards_and_indexes(self):
        conn = self.make_connection(target_version=4)
        try:
            relationships = MIGRATIONS._tenant_foreign_key_groups(conn)
            relation_triggers = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_rel_%'"
            ).fetchone()[0]
            relation_indexes = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_tenant_rel_%'"
            ).fetchone()[0]
            self.assertEqual(len(relationships), 55)
            self.assertEqual(relation_triggers, 110)
            self.assertEqual(relation_indexes, 55)
            self.assertIsNotNone(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_tenant_audit_log_company_ts'"
            ).fetchone())
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 4)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        finally:
            conn.close()

    def test_insert_rejects_child_referencing_parent_from_another_company(self):
        conn = self.make_connection(target_version=4)
        try:
            company_a, company_b, category_a, category_b = self._create_two_company_category_fixture(conn)
            # Same-company insert remains valid.
            conn.execute(
                'INSERT INTO products(sku,name,category_id,company_id) VALUES(?,?,?,?)',
                ('SKU-A1', 'Product A1', category_a, company_a),
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'cross_company_reference'):
                conn.execute(
                    'INSERT INTO products(sku,name,category_id,company_id) VALUES(?,?,?,?)',
                    ('SKU-A2', 'Product A2', category_b, company_a),
                )
        finally:
            conn.close()

    def test_update_rejects_switching_reference_to_another_company(self):
        conn = self.make_connection(target_version=4)
        try:
            company_a, company_b, category_a, category_b = self._create_two_company_category_fixture(conn)
            conn.execute(
                'INSERT INTO products(sku,name,category_id,company_id) VALUES(?,?,?,?)',
                ('SKU-A1', 'Product A1', category_a, company_a),
            )
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'cross_company_reference'):
                conn.execute(
                    'UPDATE products SET category_id=? WHERE sku=?',
                    (category_b, 'SKU-A1'),
                )
            self.assertEqual(
                conn.execute('SELECT category_id FROM products WHERE sku=?', ('SKU-A1',)).fetchone()[0],
                category_a,
            )
        finally:
            conn.close()

    def test_v4_refuses_existing_cross_company_links_and_rolls_back(self):
        conn = self.make_connection(target_version=3)
        try:
            company_a, company_b, category_a, category_b = self._create_two_company_category_fixture(conn)
            conn.execute(
                'INSERT INTO products(sku,name,category_id,company_id) VALUES(?,?,?,?)',
                ('SKU-BAD', 'Cross Company Product', category_b, company_a),
            )
            with self.assertRaisesRegex(RuntimeError, 'cross-company relationship'):
                apply_migrations(conn, target_version=4)
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_version').fetchone()[0], 3)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_rel_%'"
            ).fetchone()[0], 0)
        finally:
            conn.close()

    def test_stage4_is_idempotent(self):
        conn = self.make_connection(target_version=4)
        try:
            self.assertEqual(apply_migrations(conn, target_version=4), 4)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_tenant_rel_%'"
            ).fetchone()[0], 110)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name LIKE 'idx_tenant_rel_%'"
            ).fetchone()[0], 55)
        finally:
            conn.close()


if __name__ == '__main__':
    unittest.main()
