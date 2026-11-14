"""SQLite contract tests for active membership resolution without importing Flask."""
import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path


class TenantServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Load the production tenancy module under an isolated package name and provide
        # only its documented database/error dependencies. This keeps the test runnable
        # in minimal environments where Flask is not installed.
        cls.conn = sqlite3.connect(':memory:')
        cls.conn.row_factory = sqlite3.Row
        cls.conn.executescript('''
            CREATE TABLE companies(
              id INTEGER PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL DEFAULT '',
              currency TEXT NOT NULL DEFAULT 'USD', industry TEXT NOT NULL DEFAULT 'general',
              status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE users(id INTEGER PRIMARY KEY);
            CREATE TABLE roles(name TEXT PRIMARY KEY, permissions TEXT NOT NULL);
            CREATE TABLE company_memberships(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
              role TEXT NOT NULL, status TEXT NOT NULL, invited_by INTEGER,
              joined_at TEXT, UNIQUE(company_id,user_id)
            );
            INSERT INTO users(id) VALUES(7);
            INSERT INTO roles(name,permissions) VALUES
              ('admin','["*"]'), ('viewer','["dashboard.read"]');
            INSERT INTO companies(id,name,country,currency,industry,status) VALUES
              (10,'Company A','LY','LYD','trading','active'),
              (20,'Company B','LY','LYD','services','active'),
              (30,'Company C','LY','LYD','retail','suspended');
            INSERT INTO company_memberships(id,company_id,user_id,role,status) VALUES
              (100,10,7,'admin','active'), (200,20,7,'viewer','active'),
              (300,30,7,'admin','active');
        ''')

        package = types.ModuleType('erp_tenancy_test')
        package.__path__ = []
        services = types.ModuleType('erp_tenancy_test.services')
        services.__path__ = []
        db = types.ModuleType('erp_tenancy_test.db')
        errors = types.ModuleType('erp_tenancy_test.errors')

        def q(sql, params=()):
            return [dict(row) for row in cls.conn.execute(sql, params).fetchall()]

        def one(sql, params=()):
            row = cls.conn.execute(sql, params).fetchone()
            return dict(row) if row else None

        def execute(sql, params=()):
            cur = cls.conn.execute(sql, params)
            return cur.lastrowid

        db.q, db.one, db.execute = q, one, execute

        class ApiError(Exception):
            def __init__(self, code, message=None, status=400, details=None):
                self.code, self.status, self.details = code, status, details
                super().__init__(message or code)

        errors.ApiError = ApiError
        package.db = db
        sys.modules.update({
            'erp_tenancy_test': package,
            'erp_tenancy_test.db': db,
            'erp_tenancy_test.errors': errors,
            'erp_tenancy_test.services': services,
        })
        path = Path(__file__).parents[1] / 'erp' / 'services' / 'tenancy.py'
        spec = importlib.util.spec_from_file_location('erp_tenancy_test.services.tenancy', path)
        cls.tenancy = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.tenancy
        spec.loader.exec_module(cls.tenancy)
        cls.ApiError = ApiError

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        for name in list(sys.modules):
            if name.startswith('erp_tenancy_test'):
                sys.modules.pop(name, None)

    def test_only_one_active_membership_can_be_selected_implicitly(self):
        self.assertIsNone(self.tenancy.get_default_active_membership(7))
        self.conn.execute("UPDATE company_memberships SET status='suspended' WHERE id=200")
        try:
            membership = self.tenancy.get_default_active_membership(7)
            self.assertEqual(membership['company_id'], 10)
            self.assertEqual(membership['role'], 'admin')
        finally:
            self.conn.execute("UPDATE company_memberships SET status='active' WHERE id=200")

    def test_explicit_company_resolution_uses_membership_role_and_permissions(self):
        membership = self.tenancy.get_active_membership(7, company_id=20, membership_id=200)
        self.assertEqual(membership['role'], 'viewer')
        self.assertEqual(json.loads(membership['permissions']), ['dashboard.read'])
        self.assertEqual(self.tenancy.membership_company_payload(membership)['name'], 'Company B')

    def test_suspended_company_is_not_a_valid_active_membership(self):
        self.assertIsNone(self.tenancy.get_active_membership(7, company_id=30, membership_id=300))

    def test_membership_insert_rejects_duplicate_company_user_pair(self):
        with self.assertRaises(self.ApiError) as raised:
            self.tenancy.add_membership(10, 7, 'admin')
        self.assertEqual(raised.exception.code, 'membership_exists')


if __name__ == '__main__':
    unittest.main()
