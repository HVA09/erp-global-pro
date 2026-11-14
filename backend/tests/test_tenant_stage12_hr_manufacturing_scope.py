"""Stage 12 tenant tests for HR/payroll and manufacturing services.

The tests execute the real service modules against an isolated SQLite database.
Only request globals and external accounting/inventory side effects are stubbed.
"""
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


class Stage12HrManufacturingScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {}
        prefix = 'erp_stage12_test'
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.errors', f'{prefix}.audit',
            f'{prefix}.money', f'{prefix}.util', f'{prefix}.services',
        ]
        for short in ('tenancy', 'settings', 'sequences', 'accounting', 'inventory', 'hr', 'manufacturing'):
            names.append(f'{prefix}.services.{short}')
        for name in names:
            cls.saved[name] = sys.modules.get(name)

        flask = types.ModuleType('flask')
        flask.g = FakeG()
        flask._request_context = True
        flask.has_request_context = lambda: bool(flask._request_context)
        sys.modules['flask'] = flask
        cls.flask = flask

        package = types.ModuleType(prefix)
        package.__path__ = [str(ERP_ROOT)]
        sys.modules[prefix] = package
        cls.package = package

        db = types.ModuleType(f'{prefix}.db')
        db.conn = None
        db.q = lambda sql, params=(): [dict(row) for row in db.conn.execute(sql, params).fetchall()]
        db.one = lambda sql, params=(): (dict(row) if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.scalar = lambda sql, params=(): (row[0] if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.execute = lambda sql, params=(): db.conn.execute(sql, params).lastrowid
        sys.modules[db.__name__] = db
        package.db = db
        cls.db = db

        errors = types.ModuleType(f'{prefix}.errors')
        class ApiError(Exception):
            def __init__(self, code, message=None, status=400, params=None, details=None):
                super().__init__(message or code)
                self.code, self.status, self.params, self.details = code, status, params or {}, details
        errors.ApiError = ApiError
        sys.modules[errors.__name__] = errors
        package.errors = errors
        cls.ApiError = ApiError

        audit = types.ModuleType(f'{prefix}.audit')
        audit.log = lambda *args, **kwargs: None
        sys.modules[audit.__name__] = audit
        package.audit = audit

        money = types.ModuleType(f'{prefix}.money')
        money.to_cents = lambda value: int(round(float(value or 0) * 100))
        money.round_half_up = lambda value: int(round(float(value)))
        sys.modules[money.__name__] = money
        package.money = money

        util = types.ModuleType(f'{prefix}.util')
        util.parse_date = lambda value, *args, **kwargs: value or '2026-11-13'
        util.today = lambda: '2026-11-13'
        util.valid_period = lambda value: value
        sys.modules[util.__name__] = util
        package.util = util

        services = types.ModuleType(f'{prefix}.services')
        services.__path__ = [str(ERP_ROOT / 'services')]
        sys.modules[services.__name__] = services
        package.services = services

        spec = importlib.util.spec_from_file_location(f'{prefix}.services.tenancy', ERP_ROOT / 'services' / 'tenancy.py')
        tenancy = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = tenancy
        services.tenancy = tenancy
        spec.loader.exec_module(tenancy)
        cls.tenancy = tenancy

        settings = types.ModuleType(f'{prefix}.services.settings')
        settings.calls = []
        settings.check_period_open = lambda date, company_id=None: settings.calls.append((date, company_id))
        sys.modules[settings.__name__] = settings
        services.settings = settings
        cls.settings = settings

        sequences = types.ModuleType(f'{prefix}.services.sequences')
        sequences.calls = []
        def next_number(name, company_id=None):
            sequences.calls.append((name, company_id))
            return f'{name.upper()}-{company_id}-000001'
        sequences.next_number = next_number
        sys.modules[sequences.__name__] = sequences
        services.sequences = sequences
        cls.sequences = sequences

        accounting = types.ModuleType(f'{prefix}.services.accounting')
        accounting.post_calls = []
        accounting.reverse_calls = []
        def cash_account(account_id, company_id=None):
            row = db.one('SELECT id FROM accounts WHERE id=? AND company_id=? AND is_active=1',
                         (account_id, company_id))
            if not row:
                raise ApiError('invalid_cash_account', 'foreign cash account', 422)
            return row['id']
        def resolve_account(ref, allow_inactive=False, company_id=None):
            row = db.one('SELECT id FROM accounts WHERE (id=? OR code=?) AND company_id=? AND is_active=1',
                         (ref if isinstance(ref, int) else -1, str(ref), company_id))
            if not row:
                raise ApiError('invalid_account', 'foreign account', 422)
            return row['id']
        accounting.cash_account = cash_account
        accounting.resolve_account = resolve_account
        def post_entry(*args, **kwargs):
            accounting.post_calls.append({'args': args, 'kwargs': kwargs})
            return 9000 + len(accounting.post_calls)
        def reverse_entry(*args, **kwargs):
            accounting.reverse_calls.append({'args': args, 'kwargs': kwargs})
            return 9100 + len(accounting.reverse_calls)
        accounting.post_entry = post_entry
        accounting.reverse_entry = reverse_entry
        sys.modules[accounting.__name__] = accounting
        services.accounting = accounting
        cls.accounting = accounting

        inventory = types.ModuleType(f'{prefix}.services.inventory')
        inventory.STOCK_KINDS = ('storable', 'raw', 'spare')
        inventory.calls = []
        inventory.is_stock = lambda product: bool(product and product.get('kind') in inventory.STOCK_KINDS)
        def get_product(pid, company_id=None):
            row = db.one('SELECT * FROM products WHERE id=? AND company_id=?', (pid, company_id))
            if not row:
                raise ApiError('not_found', 'Product not found.', 404)
            return row
        def get_warehouse(wid, company_id=None):
            row = db.one('SELECT * FROM warehouses WHERE id=? AND company_id=?', (wid, company_id))
            if not row:
                raise ApiError('not_found', 'Warehouse not found.', 404)
            return row
        inventory.get_product = get_product
        inventory.get_warehouse = get_warehouse
        inventory.issue = lambda *args, company_id=None, **kwargs: inventory.calls.append(('issue', company_id, args)) or 500
        inventory.receive = lambda *args, company_id=None, **kwargs: inventory.calls.append(('receive', company_id, args))
        inventory.reverse_moves = lambda *args, company_id=None, **kwargs: inventory.calls.append(('reverse', company_id, args))
        sys.modules[inventory.__name__] = inventory
        services.inventory = inventory
        cls.inventory = inventory

        for short in ('hr', 'manufacturing'):
            spec = importlib.util.spec_from_file_location(f'{prefix}.services.{short}', ERP_ROOT / 'services' / f'{short}.py')
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            services.__dict__[short] = mod
            spec.loader.exec_module(mod)
            setattr(cls, short, mod)

        cls.prefix = prefix

    @classmethod
    def tearDownClass(cls):
        prefix = cls.prefix
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.errors', f'{prefix}.audit',
            f'{prefix}.money', f'{prefix}.util', f'{prefix}.services',
        ]
        for short in ('tenancy', 'settings', 'sequences', 'accounting', 'inventory', 'hr', 'manufacturing'):
            names.append(f'{prefix}.services.{short}')
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
            CREATE TABLE companies(id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active');
            CREATE TABLE accounts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, code TEXT, subtype TEXT, is_active INTEGER DEFAULT 1);
            CREATE TABLE employees(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT, position TEXT, salary_cents INTEGER NOT NULL DEFAULT 0,
                advance_balance_cents INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active'
            );
            CREATE TABLE advances(
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, employee_id INTEGER NOT NULL,
                amount_cents INTEGER NOT NULL, date TEXT, account_id INTEGER, note TEXT, journal_entry_id INTEGER
            );
            CREATE TABLE payroll_runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, number TEXT, month TEXT, date TEXT,
                account_id INTEGER, total_net_cents INTEGER DEFAULT 0, status TEXT DEFAULT 'posted',
                journal_entry_id INTEGER, created_by INTEGER
            );
            CREATE TABLE payroll_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, run_id INTEGER NOT NULL,
                employee_id INTEGER NOT NULL, salary_cents INTEGER, bonus_cents INTEGER, deduction_cents INTEGER,
                advance_deduction_cents INTEGER, net_cents INTEGER
            );
            CREATE TABLE products(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT, sku TEXT, unit TEXT,
                                  kind TEXT, cost_cents INTEGER DEFAULT 0);
            CREATE TABLE warehouses(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT);
            CREATE TABLE stock_levels(id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
                                      product_id INTEGER NOT NULL, qty REAL, value_cents INTEGER);
            CREATE TABLE boms(id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, name TEXT,
                              product_id INTEGER NOT NULL, labor_cost_cents INTEGER DEFAULT 0,
                              overhead_cost_cents INTEGER DEFAULT 0, is_active INTEGER DEFAULT 1);
            CREATE TABLE bom_components(id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL,
                                        bom_id INTEGER NOT NULL, product_id INTEGER NOT NULL, qty REAL NOT NULL);
            CREATE TABLE production_orders(
                id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NOT NULL, number TEXT, bom_id INTEGER,
                product_id INTEGER, warehouse_id INTEGER, qty REAL, date TEXT, status TEXT DEFAULT 'done',
                material_cost_cents INTEGER DEFAULT 0, labor_cost_cents INTEGER DEFAULT 0,
                overhead_cost_cents INTEGER DEFAULT 0, total_cost_cents INTEGER DEFAULT 0,
                journal_entry_id INTEGER, created_by INTEGER
            );
        ''')
        self.conn.executemany('INSERT INTO companies(id,status) VALUES(?,?)', [(1, 'active'), (2, 'active')])
        self.conn.executemany('INSERT INTO accounts(id,company_id,code,subtype) VALUES(?,?,?,?)', [
            (101, 1, 'cash', 'cash'), (102, 1, 'bank', 'bank'),
            (201, 2, 'cash', 'cash'), (202, 2, 'bank', 'bank')])
        self.conn.executemany('INSERT INTO employees(id,company_id,name,salary_cents,advance_balance_cents) VALUES(?,?,?,?,?)', [
            (11, 1, 'Alpha employee', 100000, 25000), (22, 2, 'Beta employee', 200000, 30000)])
        self.conn.executemany('INSERT INTO products(id,company_id,name,sku,unit,kind,cost_cents) VALUES(?,?,?,?,?,?,?)', [
            (111, 1, 'Alpha finished', 'A-F', 'pcs', 'storable', 0),
            (112, 1, 'Alpha raw', 'A-R', 'pcs', 'raw', 1000),
            (222, 2, 'Beta raw', 'B-R', 'pcs', 'raw', 2000)])
        self.conn.executemany('INSERT INTO warehouses(id,company_id,name) VALUES(?,?,?)', [
            (101, 1, 'Alpha warehouse'), (202, 2, 'Beta warehouse')])
        self.conn.executemany('INSERT INTO stock_levels(company_id,product_id,qty,value_cents) VALUES(?,?,?,?)', [
            (1, 112, 10, 10000), (2, 112, 100, 999999)])
        self.conn.execute('INSERT INTO boms(company_id,name,product_id,labor_cost_cents,overhead_cost_cents,is_active) VALUES(1,?,?,?,?,1)',
                          ('Alpha BOM', 111, 100, 50))
        self.conn.execute('INSERT INTO bom_components(company_id,bom_id,product_id,qty) VALUES(1,1,112,2)')
        self.conn.execute('INSERT INTO boms(company_id,name,product_id,labor_cost_cents,overhead_cost_cents,is_active) VALUES(2,?,?,?,?,1)',
                          ('Beta BOM', 222, 100, 50))
        self.conn.commit()
        self.db.conn = self.conn
        self.flask.g.clear()
        self.flask.g.update({'company': {'id': 1}, 'user': {'id': 9}})
        self.flask._request_context = True
        self.accounting.post_calls.clear()
        self.accounting.reverse_calls.clear()
        self.inventory.calls.clear()
        self.sequences.calls.clear()
        self.settings.calls.clear()

    def tearDown(self):
        self.conn.close()

    def use_company(self, company_id):
        self.flask.g['company'] = {'id': company_id}

    def test_foreign_employee_cannot_receive_advance(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.hr.add_advance(22, 1000, 101)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM advances'), 0)

    def test_advance_stamps_company_and_posts_journal_for_same_company(self):
        aid = self.hr.add_advance(11, 5000, 101)
        row = self.db.one('SELECT * FROM advances WHERE id=?', (aid,))
        self.assertEqual(row['company_id'], 1)
        self.assertEqual(self.db.scalar('SELECT advance_balance_cents FROM employees WHERE id=11'), 30000)
        self.assertEqual(self.db.scalar('SELECT advance_balance_cents FROM employees WHERE id=22'), 30000)
        self.assertEqual(self.accounting.post_calls[-1]['kwargs']['company_id'], 1)

    def test_payroll_preview_only_lists_active_company_employees_and_runs(self):
        self.db.execute("INSERT INTO payroll_runs(company_id,month,date,status) VALUES(2,'2026-11','2026-11-13','posted')")
        preview = self.hr.payroll_preview('2026-11')
        self.assertFalse(preview['exists'])
        self.assertEqual([e['id'] for e in preview['employees']], [11])

    def test_run_payroll_rejects_foreign_employee(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.hr.run_payroll('2026-11', '2026-11-13', 102, [{'employee_id': 22}])
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM payroll_runs'), 0)

    def test_run_payroll_stamps_company_and_scopes_items_and_advance_balance(self):
        run_id = self.hr.run_payroll('2026-11', '2026-11-13', 102, [
            {'employee_id': 11, 'bonus': 100, 'deduction': 0, 'advance_deduction': 50}])
        run = self.db.one('SELECT * FROM payroll_runs WHERE id=?', (run_id,))
        item = self.db.one('SELECT * FROM payroll_items WHERE run_id=?', (run_id,))
        self.assertEqual(run['company_id'], 1)
        self.assertEqual(item['company_id'], 1)
        self.assertEqual(item['employee_id'], 11)
        self.assertEqual(self.db.scalar('SELECT advance_balance_cents FROM employees WHERE id=11'), 20000)
        self.assertEqual(self.db.scalar('SELECT advance_balance_cents FROM employees WHERE id=22'), 30000)
        self.assertEqual(self.sequences.calls[-1], ('payroll', 1))
        self.assertEqual(self.accounting.post_calls[-1]['kwargs']['company_id'], 1)

    def test_get_run_hides_foreign_payroll(self):
        foreign = self.db.execute("INSERT INTO payroll_runs(company_id,month,date,status) VALUES(2,'2026-10','2026-10-31','posted')")
        with self.assertRaises(self.ApiError) as ctx:
            self.hr.get_run(foreign)
        self.assertEqual(ctx.exception.status, 404)

    def test_get_run_filters_cross_company_child_items(self):
        run_id = self.db.execute("INSERT INTO payroll_runs(company_id,month,date,status) VALUES(1,'2026-10','2026-10-31','posted')")
        self.db.execute('INSERT INTO payroll_items(company_id,run_id,employee_id,salary_cents,bonus_cents,deduction_cents,advance_deduction_cents,net_cents) VALUES(2,?,?,?,?,?,?,?)',
                        (run_id, 22, 1, 0, 0, 0, 1))
        result = self.hr.get_run(run_id)
        self.assertEqual(result['items'], [])

    def test_cancel_payroll_reverses_only_with_company_and_restores_advance(self):
        run_id = self.hr.run_payroll('2026-11', '2026-11-13', 102, [
            {'employee_id': 11, 'bonus': 0, 'deduction': 0, 'advance_deduction': 100}])
        self.hr.cancel_payroll(run_id)
        self.assertEqual(self.db.scalar('SELECT advance_balance_cents FROM employees WHERE id=11'), 25000)
        self.assertEqual(self.db.scalar('SELECT advance_balance_cents FROM employees WHERE id=22'), 30000)
        self.assertEqual(self.accounting.reverse_calls[-1]['kwargs']['company_id'], 1)
        self.assertEqual(self.db.scalar('SELECT status FROM payroll_runs WHERE id=?', (run_id,)), 'cancelled')

    def test_get_bom_hides_foreign_bom_and_foreign_components(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.manufacturing.get_bom(2)
        self.assertEqual(ctx.exception.status, 404)
        bom = self.manufacturing.get_bom(1)
        self.assertEqual([c['product_id'] for c in bom['components']], [112])
        # The other company's adversarial stock_levels row for this product is ignored.
        self.assertEqual(bom['material_cost_cents'], 2000)

    def test_save_bom_rejects_foreign_component_product(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.manufacturing.save_bom({'name': 'bad', 'product_id': 111,
                                         'components': [{'product_id': 222, 'qty': 1}]})
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM boms WHERE company_id=1'), 1)

    def test_save_bom_stamps_company_on_bom_and_components(self):
        bom_id = self.manufacturing.save_bom({'name': 'Alpha new', 'product_id': 111,
                                              'components': [{'product_id': 112, 'qty': 1.5}]})
        bom = self.db.one('SELECT * FROM boms WHERE id=?', (bom_id,))
        comp = self.db.one('SELECT * FROM bom_components WHERE bom_id=?', (bom_id,))
        self.assertEqual(bom['company_id'], 1)
        self.assertEqual(comp['company_id'], 1)

    def test_produce_passes_company_to_inventory_accounting_sequence_and_order(self):
        order_id = self.manufacturing.produce(1, 2, 101, '2026-11-13')
        order = self.db.one('SELECT * FROM production_orders WHERE id=?', (order_id,))
        self.assertEqual(order['company_id'], 1)
        self.assertEqual(self.sequences.calls[-1], ('production', 1))
        self.assertEqual({call[1] for call in self.inventory.calls if call[0] in ('issue', 'receive')}, {1})
        self.assertEqual(self.accounting.post_calls[-1]['kwargs']['company_id'], 1)

    def test_produce_rejects_foreign_warehouse_and_bom(self):
        with self.assertRaises(self.ApiError):
            self.manufacturing.produce(1, 1, 202)
        with self.assertRaises(self.ApiError):
            self.manufacturing.produce(2, 1, 101)
        self.assertEqual(self.db.scalar('SELECT COUNT(*) FROM production_orders'), 0)

    def test_cancel_production_hides_foreign_order(self):
        foreign_order = self.db.execute("INSERT INTO production_orders(company_id,number,bom_id,product_id,warehouse_id,qty,date,status) VALUES(2,'B-1',2,222,202,1,'2026-11-13','done')")
        with self.assertRaises(self.ApiError) as ctx:
            self.manufacturing.cancel_production(foreign_order)
        self.assertEqual(ctx.exception.status, 404)
        self.assertEqual(self.inventory.calls, [])

    def test_cancel_production_reverses_stock_and_journal_for_company(self):
        order_id = self.db.execute("INSERT INTO production_orders(company_id,number,bom_id,product_id,warehouse_id,qty,date,status,journal_entry_id) VALUES(1,'A-1',1,111,101,1,'2026-11-13','done',777)")
        self.manufacturing.cancel_production(order_id)
        self.assertEqual(self.inventory.calls[-1][0:2], ('reverse', 1))
        self.assertEqual(self.accounting.reverse_calls[-1]['kwargs']['company_id'], 1)
        self.assertEqual(self.db.scalar('SELECT status FROM production_orders WHERE id=?', (order_id,)), 'cancelled')

    def test_explicit_company_id_mismatch_is_rejected(self):
        with self.assertRaises(self.ApiError) as ctx:
            self.hr.payroll_preview('2026-11', company_id=2)
        self.assertEqual(ctx.exception.status, 403)

    def test_hr_api_routes_keep_direct_queries_tenant_scoped(self):
        source = (ERP_ROOT / 'api' / 'hr.py').read_text(encoding='utf-8')
        self.assertIn('e.company_id=?', source)
        self.assertIn('a.company_id=?', source)
        self.assertIn('i.company_id=r.company_id', source)
        self.assertIn('ON CONFLICT(company_id,employee_id,date)', source)

    def test_manufacturing_api_routes_keep_direct_queries_tenant_scoped(self):
        source = (ERP_ROOT / 'api' / 'ops.py').read_text(encoding='utf-8')
        self.assertIn('WHERE b.company_id=?', source)
        self.assertIn('WHERE o.company_id=?', source)
        self.assertIn('p.company_id=o.company_id', source)
        self.assertIn('WHERE id=? AND company_id=?', source)


if __name__ == '__main__':
    unittest.main()
