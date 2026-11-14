"""Focused, Flask-free contract tests for tenant-scoped generic CRUD.

The production ERP package depends on Flask. These tests load the actual CRUD
module against small Flask/DB adapters so the critical SQL-scope contract can
run in minimal environments too.
"""
import contextlib
import importlib.util
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace


class FakeG(dict):
    pass


class TenantCrudScopingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved_modules = {}
        for name in ('flask', 'erp_crud_stage7_test', 'erp_crud_stage7_test.audit',
                     'erp_crud_stage7_test.db', 'erp_crud_stage7_test.errors',
                     'erp_crud_stage7_test.money', 'erp_crud_stage7_test.security'):
            cls.saved_modules[name] = sys.modules.get(name)

        flask = types.ModuleType('flask')
        flask.g = FakeG()
        flask.request = SimpleNamespace(args={}, get_json=lambda silent=False: None)
        flask.jsonify = lambda *args, **kwargs: args[0] if args else kwargs
        flask.Blueprint = lambda *args, **kwargs: SimpleNamespace(add_url_rule=lambda *a, **k: None)
        sys.modules['flask'] = flask

        package = types.ModuleType('erp_crud_stage7_test')
        package.__path__ = []
        sys.modules[package.__name__] = package

        audit = types.ModuleType('erp_crud_stage7_test.audit')
        audit.events = []
        audit.log = lambda *args, **kwargs: audit.events.append((args, kwargs))

        db = types.ModuleType('erp_crud_stage7_test.db')
        db.conn = None
        db.q = lambda sql, params=(): [dict(r) for r in db.conn.execute(sql, params).fetchall()]
        db.one = lambda sql, params=(): (dict(r) if (r := db.conn.execute(sql, params).fetchone()) else None)
        db.scalar = lambda sql, params=(): (r[0] if (r := db.conn.execute(sql, params).fetchone()) else None)
        db.execute = lambda sql, params=(): db.conn.execute(sql, params).lastrowid
        db.table_columns = lambda table: {r['name'] for r in db.conn.execute(f'PRAGMA table_info("{table}")')}

        @contextlib.contextmanager
        def transaction():
            db.conn.execute('SAVEPOINT crud_test_tx')
            try:
                yield db.conn
            except BaseException:
                db.conn.execute('ROLLBACK TO crud_test_tx')
                db.conn.execute('RELEASE crud_test_tx')
                raise
            else:
                db.conn.execute('RELEASE crud_test_tx')
        db.transaction = transaction

        errors = types.ModuleType('erp_crud_stage7_test.errors')

        class ApiError(Exception):
            def __init__(self, code, message=None, status=400, details=None, **kwargs):
                self.code, self.status, self.details = code, status, details
                super().__init__(message or code)
        errors.ApiError = ApiError

        money = types.ModuleType('erp_crud_stage7_test.money')
        money.from_cents = lambda value: (value / 100) if value is not None else None
        money.to_cents = lambda value: int(round(float(value) * 100))

        security = types.ModuleType('erp_crud_stage7_test.security')
        security.require = lambda *perms: (lambda fn: fn)

        for mod in (audit, db, errors, money, security):
            sys.modules[mod.__name__] = mod
            setattr(package, mod.__name__.rsplit('.', 1)[-1], mod)

        path = Path(__file__).parents[1] / 'erp' / 'crud.py'
        spec = importlib.util.spec_from_file_location('erp_crud_stage7_test.crud', path)
        cls.crud = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.crud
        spec.loader.exec_module(cls.crud)
        cls.ApiError = ApiError
        cls.flask = flask
        cls.db = db
        cls.audit = audit

    @classmethod
    def tearDownClass(cls):
        for name, old in cls.saved_modules.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE categories (
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL
            );
            CREATE TABLE products (
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
                name TEXT NOT NULL, category_id INTEGER, is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT
            );
            INSERT INTO categories(id,company_id,name) VALUES
                (101,1,'A Category'), (202,2,'B Category');
            INSERT INTO products(id,company_id,name,category_id,is_active) VALUES
                (11,1,'A Product',101,1), (22,2,'B Product',202,1);
        ''')
        self.db.conn = self.conn
        self.flask.g.clear()
        self.flask.request.args = {}
        self.flask.request.get_json = lambda silent=False: None
        self.audit.events.clear()
        self.resource = self.crud.Resource(
            'products', 'products',
            [self.crud.F('name', required=True), self.crud.F('category_id', 'ref', ref='categories')],
            'products',
            select=('SELECT t.*, c.name AS category_name FROM products t '
                    'LEFT JOIN categories c ON c.id=t.category_id'),
            where='t.is_active=1', search=('t.name',), order='t.name ASC'
        )
        self.set_company(1)

    def tearDown(self):
        self.conn.close()
        self.db.conn = None

    def set_company(self, company_id):
        self.flask.g['company'] = {'id': company_id, 'name': f'Company {company_id}'}

    def test_list_and_count_only_include_active_company(self):
        result = self.resource.list_view()
        self.assertEqual(result['total'], 1)
        self.assertEqual([r['id'] for r in result['items']], [11])

    def test_get_cannot_read_another_company_record_by_id(self):
        with self.assertRaises(self.ApiError) as raised:
            self.resource.get_view(22)
        self.assertEqual(raised.exception.code, 'not_found')
        self.assertEqual(raised.exception.status, 404)

    def test_update_cannot_modify_another_company_record(self):
        self.flask.request.get_json = lambda silent=False: {'name': 'Compromised'}
        with self.assertRaises(self.ApiError) as raised:
            self.resource.update_view(22)
        self.assertEqual(raised.exception.code, 'not_found')
        name = self.conn.execute('SELECT name FROM products WHERE id=22').fetchone()[0]
        self.assertEqual(name, 'B Product')

    def test_delete_cannot_remove_another_company_record(self):
        with self.assertRaises(self.ApiError) as raised:
            self.resource.delete_view(22)
        self.assertEqual(raised.exception.code, 'not_found')
        count = self.conn.execute('SELECT COUNT(*) FROM products WHERE id=22').fetchone()[0]
        self.assertEqual(count, 1)

    def test_create_uses_server_company_id(self):
        self.flask.request.get_json = lambda silent=False: {'name': 'A New Product', 'category_id': 101}
        result, status = self.resource.create_view()
        self.assertEqual(status, 201)
        self.assertEqual(result['company_id'], 1)
        self.assertEqual(result['name'], 'A New Product')

    def test_create_rejects_client_supplied_company_id(self):
        self.flask.request.get_json = lambda silent=False: {
            'name': 'Spoofed', 'category_id': 101, 'company_id': 2
        }
        with self.assertRaises(self.ApiError) as raised:
            self.resource.create_view()
        self.assertEqual(raised.exception.code, 'tenant_field_forbidden')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products').fetchone()[0], 2)

    def test_update_rejects_client_supplied_company_id(self):
        self.flask.request.get_json = lambda silent=False: {'company_id': 2, 'name': 'Spoofed'}
        with self.assertRaises(self.ApiError) as raised:
            self.resource.update_view(11)
        self.assertEqual(raised.exception.code, 'tenant_field_forbidden')
        self.assertEqual(self.conn.execute('SELECT company_id FROM products WHERE id=11').fetchone()[0], 1)

    def test_current_company_can_update_and_delete_its_own_record(self):
        self.flask.request.get_json = lambda silent=False: {'name': 'A Updated'}
        updated = self.resource.update_view(11)
        self.assertEqual(updated['name'], 'A Updated')
        deleted = self.resource.delete_view(11)
        self.assertEqual(deleted, {'ok': True})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM products WHERE id=11').fetchone()[0], 0)

    def test_ref_validation_rejects_foreign_company_parent(self):
        self.flask.request.get_json = lambda silent=False: {'name': 'Bad Child', 'category_id': 202}
        with self.assertRaises(self.ApiError) as raised:
            self.resource.create_view()
        self.assertEqual(raised.exception.code, 'validation')
        self.assertEqual(raised.exception.details, {'category_id': 'invalid_ref'})

    def test_missing_company_context_fails_closed(self):
        self.flask.g.pop('company', None)
        with self.assertRaises(self.ApiError) as raised:
            self.resource.list_view()
        self.assertEqual(raised.exception.code, 'active_company_required')

    def test_missing_company_column_fails_closed(self):
        self.conn.execute('CREATE TABLE legacy_rows(id INTEGER PRIMARY KEY, name TEXT)')
        legacy = self.crud.Resource('legacy_rows', 'legacy_rows', [self.crud.F('name')], 'test')
        with self.assertRaises(self.ApiError) as raised:
            legacy.list_view()
        self.assertEqual(raised.exception.code, 'tenant_scope_unavailable')


if __name__ == '__main__':
    unittest.main()
