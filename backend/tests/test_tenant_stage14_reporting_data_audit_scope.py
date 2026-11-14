"""Stage 14 tenant isolation tests for reports, import/export and audit views.

The production Flask dependency is not installed in this offline test environment.
These tests load the real handlers/services with narrow Flask-compatible stubs while
all SQL executes against real in-memory SQLite tables containing two tenants.
"""
from contextlib import contextmanager
import importlib.util
import sqlite3
import sys
import types
import unittest
from pathlib import Path

ERP_ROOT = Path(__file__).parents[1] / 'erp'


class FakeG(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class FakeBlueprint:
    def __init__(self, *args, **kwargs):
        self.name = args[0] if args else 'test'

    def _decorator(self, *args, **kwargs):
        return lambda fn: fn

    get = _decorator
    post = _decorator
    put = _decorator
    delete = _decorator


class FakeApiError(Exception):
    def __init__(self, code, message=None, status=400, params=None, details=None):
        super().__init__(message or code)
        self.code, self.status = code, status
        self.params, self.details = params or {}, details


class Stage14Harness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {}
        cls.prefix = 'erp_stage14_test'
        prefix = cls.prefix
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.audit', f'{prefix}.crud', f'{prefix}.errors',
            f'{prefix}.security', f'{prefix}.util', f'{prefix}.resources', f'{prefix}.services',
            f'{prefix}.services.tenancy', f'{prefix}.services.accounting', f'{prefix}.services.inventory',
            f'{prefix}.services.settings', f'{prefix}.services.users', f'{prefix}.api', f'{prefix}.api.common', f'{prefix}.api.data', f'{prefix}.api.admin',
            f'{prefix}.seed',
        ]
        for name in names:
            cls.saved[name] = sys.modules.get(name)

        flask = types.ModuleType('flask')
        flask.g = FakeG(company={'id': 1}, user={'id': 7, 'permissions': ['*']})
        flask.request = FakeG(args={}, form={}, files={}, remote_addr='127.0.0.1')
        flask.Blueprint = FakeBlueprint
        flask.Response = lambda response, **kwargs: {'response': response, **kwargs}
        flask.jsonify = lambda *args, **kwargs: args[0] if args else kwargs
        flask.has_request_context = lambda: True
        flask.after_this_request = lambda fn: (lambda f: f)(fn)
        flask.current_app = FakeG(config={'DB_PATH': '/tmp/fake-stage14.sqlite'})
        flask.send_file = lambda *args, **kwargs: {'send_file': args, **kwargs}
        sys.modules['flask'] = flask
        cls.flask = flask

        package = types.ModuleType(prefix)
        package.__path__ = [str(ERP_ROOT)]
        sys.modules[prefix] = package

        db = types.ModuleType(f'{prefix}.db')
        db.conn = None
        db.q = lambda sql, params=(): [dict(row) for row in db.conn.execute(sql, params).fetchall()]
        db.one = lambda sql, params=(): (dict(row) if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.scalar = lambda sql, params=(): (row[0] if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.execute = lambda sql, params=(): db.conn.execute(sql, params).lastrowid

        @contextmanager
        def transaction():
            db.conn.execute('BEGIN')
            try:
                yield db.conn
            except BaseException:
                db.conn.rollback()
                raise
            else:
                db.conn.commit()
        db.transaction = transaction
        sys.modules[db.__name__] = db
        package.db = db
        cls.db = db

        audit = types.ModuleType(f'{prefix}.audit')
        audit.events = []
        audit.log = lambda *args, **kwargs: audit.events.append((args, kwargs))
        sys.modules[audit.__name__] = audit
        package.audit = audit
        cls.audit = audit

        crud = types.ModuleType(f'{prefix}.crud')
        def serialize(obj):
            if isinstance(obj, list):
                return [serialize(row) for row in obj]
            if isinstance(obj, dict):
                return {key[:-6] if key.endswith('_cents') else key: value / 100 if key.endswith('_cents') and value is not None else value
                        for key, value in obj.items()}
            return obj
        crud.serialize = serialize
        sys.modules[crud.__name__] = crud

        errors = types.ModuleType(f'{prefix}.errors')
        errors.ApiError = FakeApiError
        sys.modules[errors.__name__] = errors

        security = types.ModuleType(f'{prefix}.security')
        security.require = lambda *args: (lambda fn: fn)
        security.has_perm = lambda permissions, perm: True
        sys.modules[security.__name__] = security

        util = types.ModuleType(f'{prefix}.util')
        util.today = lambda: '2026-11-13'
        sys.modules[util.__name__] = util

        resources = types.ModuleType(f'{prefix}.resources')
        class FakeResource:
            def __init__(self, fields):
                self.fields = fields
            def _parse(self, row, creating, company_id):
                return {key: row[key] for key in self.fields if row.get(key) not in (None, '')}
        resources.CUSTOMERS = FakeResource(('name', 'phone'))
        resources.SUPPLIERS = FakeResource(('name', 'phone'))
        resources.PRODUCTS = FakeResource(('sku', 'name', 'price_cents'))
        resources._product_after_save = lambda *args, **kwargs: None
        sys.modules[resources.__name__] = resources
        package.resources = resources
        cls.resources = resources

        services = types.ModuleType(f'{prefix}.services')
        services.__path__ = []
        sys.modules[services.__name__] = services
        package.services = services

        tenancy = types.ModuleType(f'{prefix}.services.tenancy')
        tenancy.current_company_id = lambda company_id=None: int(company_id or flask.g['company']['id'])
        sys.modules[tenancy.__name__] = tenancy
        services.tenancy = tenancy

        accounting = types.ModuleType(f'{prefix}.services.accounting')
        accounting.income_statement = lambda start, end, cid: {'net_income_cents': cid * 100}
        accounting.balance_of = lambda account, company_id=None: int(company_id) * 1000
        sys.modules[accounting.__name__] = accounting
        services.accounting = accounting

        settings = types.ModuleType(f'{prefix}.services.settings')
        settings.get_settings = lambda company_id=None: {}
        settings.save_settings = lambda data, company_id=None: {}
        sys.modules[settings.__name__] = settings
        services.settings = settings
        users = types.ModuleType(f'{prefix}.services.users')
        sys.modules[users.__name__] = users
        services.users = users

        inventory = types.ModuleType(f'{prefix}.services.inventory')
        inventory.low_stock = lambda cid: [{'company_id': cid}]
        inventory.lots_report = lambda cid: [{'company_id': cid, 'status': 'ok'}]
        sys.modules[inventory.__name__] = inventory
        services.inventory = inventory

        seed = types.ModuleType(f'{prefix}.seed')
        seed.demo_allowed = lambda: True
        sys.modules[seed.__name__] = seed

        api = types.ModuleType(f'{prefix}.api')
        api.__path__ = [str(ERP_ROOT / 'api')]
        sys.modules[api.__name__] = api
        common = types.ModuleType(f'{prefix}.api.common')
        common.body = lambda: {}
        common.paging = lambda: (1, 100, 0)
        common.date_arg = lambda name, default=None: flask.request.args.get(name, default)
        common.int_arg = lambda name, default=0: int(flask.request.args.get(name, default))
        sys.modules[common.__name__] = common

        trade = types.ModuleType(f'{prefix}.services.trade')
        trade.partner_statement = lambda partner_id, side, date_from, date_to, cid: {'company_id': cid}
        trade.ageing = lambda side, as_of, cid: {'company_id': cid}
        sys.modules[trade.__name__] = trade
        services.trade = trade

        spec = importlib.util.spec_from_file_location(f'{prefix}.services.reports', ERP_ROOT / 'services' / 'reports.py')
        report_service = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = report_service
        spec.loader.exec_module(report_service)
        services.reports = report_service
        cls.report_service = report_service

        spec = importlib.util.spec_from_file_location(f'{prefix}.api.data', ERP_ROOT / 'api' / 'data.py')
        data_api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = data_api
        spec.loader.exec_module(data_api)
        cls.data_api = data_api

        # Load the actual admin audit handler with the same DB connection.
        spec = importlib.util.spec_from_file_location(f'{prefix}.api.admin', ERP_ROOT / 'api' / 'admin.py')
        admin_api = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = admin_api
        spec.loader.exec_module(admin_api)
        cls.admin_api = admin_api

    @classmethod
    def tearDownClass(cls):
        prefix = cls.prefix
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.audit', f'{prefix}.crud', f'{prefix}.errors',
            f'{prefix}.security', f'{prefix}.util', f'{prefix}.resources', f'{prefix}.services',
            f'{prefix}.services.tenancy', f'{prefix}.services.accounting', f'{prefix}.services.inventory',
            f'{prefix}.services.settings', f'{prefix}.services.users', f'{prefix}.api', f'{prefix}.api.common', f'{prefix}.api.data', f'{prefix}.api.admin',
            f'{prefix}.seed', f'{prefix}.services.reports', f'{prefix}.services.trade',
        ]
        for name in names:
            old = cls.saved.get(name)
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE companies(id INTEGER PRIMARY KEY, status TEXT NOT NULL);
            CREATE TABLE trade_documents(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, doc_type TEXT NOT NULL, number TEXT,
              partner_id INTEGER, date TEXT, due_date TEXT, status TEXT, payment_status TEXT,
              subtotal_cents INTEGER DEFAULT 0, tax_cents INTEGER DEFAULT 0, total_cents INTEGER DEFAULT 0,
              paid_cents INTEGER DEFAULT 0
            );
            CREATE TABLE trade_document_lines(
              id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, document_id INTEGER NOT NULL,
              product_id INTEGER, description TEXT, qty REAL, net_cents INTEGER, cost_cents INTEGER
            );
            CREATE TABLE products(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, sku TEXT, barcode TEXT,
              name TEXT, category_id INTEGER, kind TEXT, unit TEXT, price_cents INTEGER, cost_cents INTEGER,
              tax_rate REAL, min_stock REAL, is_active INTEGER DEFAULT 1);
            CREATE TABLE categories(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT);
            CREATE TABLE partners(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT, phone TEXT,
              email TEXT, address TEXT, city TEXT, country TEXT, tax_number TEXT, credit_limit_cents INTEGER,
              payment_terms_days INTEGER, is_customer INTEGER DEFAULT 0, is_supplier INTEGER DEFAULT 0,
              is_system INTEGER DEFAULT 0);
            CREATE TABLE accounts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, code TEXT, name TEXT, subtype TEXT);
            CREATE TABLE journal_entries(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, number TEXT, date TEXT, memo TEXT);
            CREATE TABLE journal_lines(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, entry_id INTEGER,
              account_id INTEGER, debit_cents INTEGER, credit_cents INTEGER);
            CREATE TABLE departments(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT);
            CREATE TABLE employees(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT, department_id INTEGER,
              position TEXT, salary_cents INTEGER, phone TEXT, email TEXT, hire_date TEXT, status TEXT);
            CREATE TABLE warehouses(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT);
            CREATE TABLE stock_levels(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, product_id INTEGER,
              warehouse_id INTEGER, qty REAL, value_cents INTEGER);
            CREATE TABLE stock_moves(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, product_id INTEGER,
              qty REAL, date TEXT, move_type TEXT);
            CREATE TABLE leave_requests(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, status TEXT);
            CREATE TABLE maintenance_requests(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, status TEXT);
            CREATE TABLE maintenance_schedules(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
              last_done TEXT, frequency_days INTEGER);
            CREATE TABLE audit_log(id INTEGER PRIMARY KEY, company_id INTEGER, ts TEXT, user_id INTEGER,
              user_email TEXT, action TEXT, entity TEXT, entity_id TEXT, details TEXT, ip TEXT);
            CREATE TABLE company_memberships(id INTEGER PRIMARY KEY, company_id INTEGER, user_id INTEGER, status TEXT);
            CREATE TABLE settings(company_id INTEGER, key TEXT, value TEXT);
            CREATE TABLE sequences(company_id INTEGER, name TEXT, prefix TEXT, next_number INTEGER, padding INTEGER);
        ''')
        self.conn.executemany('INSERT INTO companies VALUES(?,?)', [(1, 'active'), (2, 'active')])
        self.conn.executemany('INSERT INTO partners VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (11, 1, 'Alpha Customer', '111', 'a@test', '', '', '', '', 0, 0, 1, 0, 0),
            (22, 2, 'Beta Customer', '222', 'b@test', '', '', '', '', 0, 0, 1, 0, 0),
            (12, 1, 'Alpha Supplier', '112', 'as@test', '', '', '', '', 0, 0, 0, 1, 0),
            (23, 2, 'Beta Supplier', '223', 'bs@test', '', '', '', '', 0, 0, 0, 1, 0),
        ])
        self.conn.executemany('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (111, 1, 'A-1', None, 'Alpha Product', None, 'storable', 'pcs', 500, 300, 0, 0, 1),
            (222, 2, 'B-1', None, 'Beta Product', None, 'storable', 'pcs', 700, 400, 0, 0, 1),
        ])
        self.conn.executemany('INSERT INTO categories VALUES(?,?,?)', [(1, 1, 'Alpha Cat'), (2, 2, 'Beta Cat')])
        self.conn.executemany('INSERT INTO trade_documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', [
            (1, 1, 'invoice', 'INV-A', 11, '2026-11-10', '2026-11-20', 'posted', 'unpaid', 10000, 500, 10500, 0),
            (2, 2, 'invoice', 'INV-B', 22, '2026-11-10', '2026-11-20', 'posted', 'unpaid', 90000, 4500, 94500, 0),
            (3, 2, 'bill', 'BILL-B', 22, '2026-11-10', '2026-11-20', 'posted', 'unpaid', 20000, 1000, 21000, 0),
            (4, 1, 'bill', 'BILL-A', 12, '2026-11-10', '2026-11-20', 'posted', 'unpaid', 2000, 100, 2100, 0),
        ])
        self.conn.executemany('INSERT INTO trade_document_lines VALUES(?,?,?,?,?,?,?,?)', [
            (1, 1, 1, 111, None, 2, 10000, 6000), (2, 2, 2, 222, None, 5, 90000, 50000),
        ])
        self.conn.executemany('INSERT INTO accounts VALUES(?,?,?,?,?)', [
            (1001, 1, '1000', 'Alpha Cash', 'cash'), (2001, 2, '1000', 'Beta Cash', 'cash')])
        self.conn.executemany('INSERT INTO journal_entries VALUES(?,?,?,?,?)', [
            (1, 1, 'J-A', '2026-11-10', 'Alpha'), (2, 2, 'J-B', '2026-11-10', 'Beta')])
        self.conn.executemany('INSERT INTO journal_lines VALUES(?,?,?,?,?,?)', [
            (1, 1, 1, 1001, 10000, 0), (2, 2, 2, 2001, 90000, 0)])
        self.conn.executemany('INSERT INTO departments VALUES(?,?,?)', [(1, 1, 'Alpha Dept'), (2, 2, 'Beta Dept')])
        self.conn.executemany('INSERT INTO employees VALUES(?,?,?,?,?,?,?,?,?,?)', [
            (1, 1, 'Alpha Employee', 1, 'Clerk', 10000, '', '', '', 'active'),
            (2, 2, 'Beta Employee', 2, 'Director', 90000, '', '', '', 'active')])
        self.conn.executemany('INSERT INTO warehouses VALUES(?,?,?)', [(1, 1, 'Alpha WH'), (2, 2, 'Beta WH')])
        self.conn.executemany('INSERT INTO stock_levels VALUES(?,?,?,?,?,?)', [
            (1, 1, 111, 1, 2, 600), (2, 2, 222, 2, 5, 2000)])
        self.conn.executemany('INSERT INTO stock_moves VALUES(?,?,?,?,?,?)', [
            (1, 1, 111, -1, '2026-11-10', 'sale'), (2, 2, 222, -4, '2026-11-10', 'sale')])
        self.conn.executemany('INSERT INTO leave_requests VALUES(?,?,?)', [(1, 1, 'pending'), (2, 2, 'pending')])
        self.conn.executemany('INSERT INTO maintenance_requests VALUES(?,?,?)', [(1, 1, 'open'), (2, 2, 'open')])
        self.conn.executemany('INSERT INTO maintenance_schedules VALUES(?,?,?,?)', [
            (1, 1, None, 30), (2, 2, None, 30)])
        self.conn.executemany('INSERT INTO audit_log VALUES(?,?,?,?,?,?,?,?,?,?)', [
            (1, 1, '2026-11-10', 7, 'a@test', 'create', 'product', '111', '{}', '127.0.0.1'),
            (2, 2, '2026-11-10', 8, 'b@test', 'delete', 'product', '222', '{}', '127.0.0.2'),
            (3, None, '2026-11-10', 7, 'a@test', 'login', 'auth', None, '{}', '127.0.0.1'),
        ])
        self.conn.executemany('INSERT INTO company_memberships VALUES(?,?,?,?)', [
            (1, 1, 7, 'active'), (2, 2, 8, 'active')])
        self.db.conn = self.conn
        self.flask.g['company'] = {'id': 1}
        self.flask.g['user'] = {'id': 7, 'permissions': ['*']}
        self.flask.request.args = {}
        self.flask.request.form = {}
        self.flask.request.files = {}
        self.audit.events.clear()

    def tearDown(self):
        self.conn.close()

    def test_sales_reports_are_company_scoped(self):
        summary = self.report_service.sales_summary('2026-11-01', '2026-11-30', company_id=1)
        self.assertEqual(summary['total_net_cents'], 10000)
        self.assertEqual(sum(r['revenue_cents'] for r in self.report_service.top_customers(
            '2026-11-01', '2026-11-30', company_id=1)), 10000)
        self.assertEqual(sum(r['revenue_cents'] for r in self.report_service.top_products(
            '2026-11-01', '2026-11-30', company_id=1)), 10000)
        self.assertEqual(self.report_service.sales_net('2026-11-01', '2026-11-30', 1)['net'], 10000)

    def test_profitability_does_not_include_other_company_costs_or_products(self):
        report = self.report_service.profitability('2026-11-01', '2026-11-30', 1)
        self.assertEqual(len(report['rows']), 1)
        self.assertEqual(report['rows'][0]['name'], 'Alpha Product')
        self.assertEqual(report['total_profit_cents'], 4000)

    def test_dashboard_uses_company_scoped_documents_alerts_and_helpers(self):
        data = self.report_service.dashboard(1)
        self.assertEqual(data['sales_month_cents'], 10000)
        self.assertEqual(data['purchases_month_cents'], 2000)
        self.assertEqual(data['receivables_cents'], 10500)
        self.assertEqual(data['recent_documents'][0]['number'], 'BILL-A')
        self.assertEqual(data['alerts']['pending_leaves'], 1)
        self.assertEqual(data['alerts']['open_maintenance'], 1)
        self.assertEqual(data['stock_value_cents'], 1000)

    def test_dashboard_for_other_company_has_no_alpha_data(self):
        data = self.report_service.dashboard(2)
        self.assertEqual(data['sales_month_cents'], 90000)
        self.assertEqual(data['recent_documents'][0]['number'], 'BILL-B')
        self.assertEqual(data['alerts']['pending_leaves'], 1)

    def test_all_export_queries_require_and_apply_company_filter(self):
        for name, (_, _, sql) in self.data_api.EXPORTS.items():
            rows = self.db.q(sql, (1,))
            self.assertTrue(rows, name)
            encoded = str(rows)
            self.assertNotIn('Beta', encoded, name)

    def test_partner_import_does_not_update_foreign_company_match(self):
        rows = [{'name': 'Beta Customer', 'phone': 'changed'}]
        created, updated, errors = self.data_api._import_partners(
            rows, 'is_customer', self.resources.CUSTOMERS, False, 1)
        self.assertEqual((created, updated, errors), (1, 0, []))
        beta = self.db.one('SELECT phone FROM partners WHERE id=22')
        alpha = self.db.one('SELECT * FROM partners WHERE company_id=1 AND lower(name)=lower(?)', ('Beta Customer',))
        self.assertEqual(beta['phone'], '222')
        self.assertEqual(alpha['phone'], 'changed')

    def test_product_import_sku_and_category_are_company_scoped(self):
        rows = [{'sku': 'B-1', 'name': 'Company A SKU', 'price_cents': 1234, 'category': 'Beta Cat'}]
        created, updated, errors = self.data_api._import_products(rows, False, 1)
        self.assertEqual((created, updated, errors), (1, 0, []))
        company_a_product = self.db.one('SELECT * FROM products WHERE company_id=1 AND sku=?', ('B-1',))
        foreign_product = self.db.one('SELECT * FROM products WHERE id=222')
        self.assertEqual(company_a_product['name'], 'Company A SKU')
        self.assertEqual(foreign_product['name'], 'Beta Product')
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM categories WHERE company_id=1 AND name=?', ('Beta Cat',)), 1)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM categories WHERE company_id=2 AND name=?', ('Beta Cat',)), 1)

    def test_audit_listing_is_filtered_to_active_company_and_excludes_global_rows(self):
        self.flask.request.args = {}
        result = self.admin_api.audit_list()
        self.assertEqual(result['total'], 1)
        self.assertEqual([row['company_id'] for row in result['items']], [1])

    def test_audit_search_cannot_expand_outside_active_company(self):
        self.flask.request.args = {'q': 'Beta'}
        result = self.admin_api.audit_list()
        self.assertEqual(result['total'], 0)

    def test_tenant_backup_is_denied_when_any_second_company_exists(self):
        self.conn.execute("INSERT INTO companies VALUES(3,'suspended')")
        with self.assertRaises(FakeApiError) as ctx:
            self.admin_api.backup()
        self.assertEqual(ctx.exception.code, 'tenant_backup_unavailable')

    def test_global_role_mutations_are_blocked_for_multiple_companies(self):
        with self.assertRaises(FakeApiError) as ctx:
            self.admin_api.create_role()
        self.assertEqual(ctx.exception.code, 'platform_admin_required')

    def test_demo_seed_is_blocked_for_multiple_companies(self):
        with self.assertRaises(FakeApiError) as ctx:
            self.admin_api.demo_data()
        self.assertEqual(ctx.exception.code, 'platform_admin_required')

    def test_global_identity_fields_blocked_when_user_has_multiple_memberships(self):
        self.conn.execute('INSERT INTO company_memberships VALUES(3,2,7,\'active\')')
        with self.assertRaises(FakeApiError) as ctx:
            self.admin_api._guard_global_user_fields(1, 7, {'password': 'new-secret'})
        self.assertEqual(ctx.exception.code, 'global_identity_change_requires_platform_admin')

    def test_company_role_assignment_is_not_mistaken_for_global_identity_field(self):
        self.conn.execute('INSERT INTO company_memberships VALUES(3,2,7,\'active\')')
        self.admin_api._guard_global_user_fields(1, 7, {'role': 'accountant'})

    def test_system_info_does_not_expose_global_db_size_or_demo_eligibility(self):
        result = self.admin_api.system_info()
        self.assertIsNone(result['db_size'])
        self.assertFalse(result['demo_allowed'])
        self.assertEqual(result['counts']['products'], 1)


if __name__ == '__main__':
    unittest.main()
