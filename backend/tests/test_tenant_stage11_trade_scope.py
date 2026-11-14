"""Stage 11 tests for tenant-aware trade documents, payments, statements and returns.

Tests load the real trade service against an in-memory SQLite schema and only stub
Flask's request globals plus downstream accounting/inventory side effects.
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


class Stage11TradeScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {}
        prefix = 'erp_stage11_test'
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.errors', f'{prefix}.audit',
            f'{prefix}.money', f'{prefix}.util', f'{prefix}.services',
        ]
        for short in ('tenancy', 'settings', 'sequences', 'accounting', 'inventory', 'trade'):
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
        money.line_amounts = lambda qty, price, discount, tax: (
            int(round(qty * price * (1 - discount / 100))),
            int(round(qty * price * (1 - discount / 100) * tax / 100)),
            int(round(qty * price * (1 - discount / 100) * (1 + tax / 100))),
        )
        money.round_half_up = lambda value: int(round(float(value)))
        money.to_cents = lambda value: int(round(float(value) * 100))
        sys.modules[money.__name__] = money
        package.money = money

        util = types.ModuleType(f'{prefix}.util')
        util.add_days = lambda value, days: value
        util.parse_date = lambda value, *args, **kwargs: value or '2026-11-13'
        util.today = lambda: '2026-11-13'
        sys.modules[util.__name__] = util
        package.util = util

        services = types.ModuleType(f'{prefix}.services')
        services.__path__ = [str(ERP_ROOT / 'services')]
        sys.modules[services.__name__] = services
        package.services = services

        # Load the real tenancy service; it uses the real fake-DB adapter above.
        spec = importlib.util.spec_from_file_location(f'{prefix}.services.tenancy', ERP_ROOT / 'services' / 'tenancy.py')
        tenancy = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = tenancy
        services.tenancy = tenancy
        spec.loader.exec_module(tenancy)
        cls.tenancy = tenancy

        settings = types.ModuleType(f'{prefix}.services.settings')
        settings.get_settings = lambda company_id=None: {'tax': 0, 'lock_date': ''}
        settings.default_warehouse_id = lambda company_id=None: 101 if int(company_id or 1) == 1 else 202
        settings.check_period_open = lambda date, company_id=None: None
        sys.modules[settings.__name__] = settings
        services.settings = settings
        cls.settings = settings

        sequences = types.ModuleType(f'{prefix}.services.sequences')
        sequences.next_number = lambda name, company_id=None: f'{name.upper()}-{int(company_id or 1)}-000001'
        sys.modules[sequences.__name__] = sequences
        services.sequences = sequences
        cls.sequences = sequences

        accounting = types.ModuleType(f'{prefix}.services.accounting')
        accounting.cash_account = lambda account_id, company_id=None: account_id
        accounting.resolve_account = lambda ref, company_id=None: (int(ref) if isinstance(ref, int) else (1001 if int(company_id or 1) == 1 else 2001))
        accounting.post_entry = lambda *args, **kwargs: 9001
        accounting.reverse_entry = lambda *args, **kwargs: 9002
        sys.modules[accounting.__name__] = accounting
        services.accounting = accounting
        cls.accounting = accounting

        inventory = types.ModuleType(f'{prefix}.services.inventory')
        inventory.STOCK_KINDS = ('storable', 'raw', 'spare')
        inventory.is_stock = lambda product: bool(product and product.get('kind') in inventory.STOCK_KINDS)
        inventory.get_warehouse = lambda wid, company_id=None: cls.db.one(
            'SELECT * FROM warehouses WHERE id=? AND company_id=?', (wid, int(company_id or 1))) or (_ for _ in ()).throw(ApiError('not_found', 'Warehouse not found.', 404))
        sys.modules[inventory.__name__] = inventory
        services.inventory = inventory
        cls.inventory = inventory

        spec = importlib.util.spec_from_file_location(f'{prefix}.services.trade', ERP_ROOT / 'services' / 'trade.py')
        trade = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = trade
        services.trade = trade
        spec.loader.exec_module(trade)
        cls.trade = trade

    @classmethod
    def tearDownClass(cls):
        prefix = 'erp_stage11_test'
        names = [
            'flask', prefix, f'{prefix}.db', f'{prefix}.errors', f'{prefix}.audit',
            f'{prefix}.money', f'{prefix}.util', f'{prefix}.services',
        ]
        for short in ('tenancy', 'settings', 'sequences', 'accounting', 'inventory', 'trade'):
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
            CREATE TABLE partners(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL,
                tax_number TEXT, is_customer INTEGER NOT NULL DEFAULT 0, is_supplier INTEGER NOT NULL DEFAULT 0,
                is_active INTEGER NOT NULL DEFAULT 1, payment_terms_days INTEGER NOT NULL DEFAULT 0,
                credit_limit_cents INTEGER NOT NULL DEFAULT 0, is_system INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE warehouses(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL);
            CREATE TABLE products(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL, sku TEXT, unit TEXT,
                kind TEXT, price_cents INTEGER DEFAULT 0, cost_cents INTEGER DEFAULT 0, tax_rate REAL
            );
            CREATE TABLE accounts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT, is_active INTEGER DEFAULT 1, subtype TEXT);
            CREATE TABLE trade_documents(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, doc_type TEXT NOT NULL, number TEXT,
                partner_id INTEGER NOT NULL, warehouse_id INTEGER, date TEXT NOT NULL, due_date TEXT,
                status TEXT NOT NULL DEFAULT 'draft', payment_status TEXT, subtotal_cents INTEGER NOT NULL DEFAULT 0,
                tax_cents INTEGER NOT NULL DEFAULT 0, total_cents INTEGER NOT NULL DEFAULT 0,
                paid_cents INTEGER NOT NULL DEFAULT 0, origin_id INTEGER, source TEXT, notes TEXT,
                journal_entry_id INTEGER, created_by INTEGER, posted_at TEXT, cancelled_at TEXT
            );
            CREATE TABLE trade_document_lines(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, document_id INTEGER NOT NULL,
                product_id INTEGER, account_id INTEGER, description TEXT, qty REAL, unit_price_cents INTEGER,
                discount_pct REAL, tax_rate REAL, net_cents INTEGER, tax_cents INTEGER, total_cents INTEGER,
                cost_cents INTEGER, lot_no TEXT, expiry_date TEXT
            );
            CREATE TABLE payments(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, number TEXT, direction TEXT, partner_id INTEGER,
                document_id INTEGER, account_id INTEGER, amount_cents INTEGER, date TEXT, method TEXT,
                reference TEXT, note TEXT, status TEXT NOT NULL DEFAULT 'posted', journal_entry_id INTEGER,
                created_by INTEGER
            );
            CREATE TABLE credit_applications(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, credit_id INTEGER, target_id INTEGER,
                amount_cents INTEGER, date TEXT, created_by INTEGER
            );
            CREATE TABLE stock_moves(
                id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, ref_type TEXT, ref_id INTEGER,
                product_id INTEGER, qty REAL, value_cents INTEGER, move_type TEXT
            );
        ''')
        self.conn.executemany('INSERT INTO companies(id,status) VALUES(?,?)', [(1, 'active'), (2, 'active')])
        self.conn.executemany(
            'INSERT INTO partners(id,company_id,name,tax_number,is_customer,is_supplier) VALUES(?,?,?,?,?,?)',
            [(11, 1, 'Alpha Customer', 'TAX-A', 1, 0), (22, 2, 'Beta Customer', 'TAX-B', 1, 0)])
        self.conn.executemany('INSERT INTO warehouses(id,company_id,name) VALUES(?,?,?)',
                              [(101, 1, 'Alpha WH'), (202, 2, 'Beta WH')])
        self.conn.executemany('INSERT INTO products(id,company_id,name,sku,unit,kind,price_cents,cost_cents,tax_rate) VALUES(?,?,?,?,?,?,?,?,?)',
                              [(111, 1, 'Alpha Product', 'A-1', 'pcs', 'storable', 500, 300, 0),
                               (222, 2, 'Beta Product', 'B-1', 'pcs', 'storable', 700, 400, 0)])
        self.conn.executemany('INSERT INTO accounts(id,company_id,name,is_active,subtype) VALUES(?,?,?,?,?)',
                              [(1001, 1, 'Alpha Cash', 1, 'cash'), (1002, 1, 'Alpha Sales', 1, None),
                               (2001, 2, 'Beta Cash', 1, 'cash'), (2002, 2, 'Beta Sales', 1, None)])
        self.conn.executemany(
            'INSERT INTO trade_documents(id,company_id,doc_type,number,partner_id,warehouse_id,date,due_date,status,payment_status,total_cents,paid_cents) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            [(100, 1, 'invoice', 'A-INV-1', 11, 101, '2026-11-01', '2026-11-30', 'posted', 'unpaid', 10000, 0),
             (200, 2, 'invoice', 'B-INV-1', 22, 202, '2026-11-02', '2026-11-30', 'posted', 'unpaid', 20000, 0)])
        self.conn.executemany(
            'INSERT INTO trade_document_lines(id,company_id,document_id,product_id,account_id,description,qty,unit_price_cents,discount_pct,tax_rate,net_cents,tax_cents,total_cents) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(1001, 1, 100, 111, 1002, 'A line', 2, 5000, 0, 0, 10000, 0, 10000),
             (2001, 2, 200, 222, 2002, 'B line', 4, 5000, 0, 0, 20000, 0, 20000)])
        self.conn.executemany(
            'INSERT INTO payments(id,company_id,number,direction,partner_id,document_id,account_id,amount_cents,date,method) VALUES(?,?,?,?,?,?,?,?,?,?)',
            [(10001, 1, 'A-RCT-1', 'in', 11, 100, 1001, 2000, '2026-11-03', 'cash'),
             (20001, 2, 'B-RCT-1', 'in', 22, 200, 2001, 3000, '2026-11-04', 'cash')])
        self.db.conn = self.conn
        self.flask.g.clear()
        self.flask.g.update({'company': {'id': 1, 'name': 'Alpha'}, 'user': {'id': 5}})
        self.flask._request_context = True

    def tearDown(self):
        self.conn.close()
        self.db.conn = None

    def switch_company(self, company_id):
        self.flask.g['company'] = {'id': company_id, 'name': f'Company {company_id}'}

    def test_fetch_and_get_doc_reject_foreign_document(self):
        self.assertEqual(self.trade.get_doc(100)['company_id'], 1)
        with self.assertRaises(self.ApiError) as raised:
            self.trade.get_doc(200)
        self.assertEqual(raised.exception.code, 'not_found')

    def test_get_doc_only_returns_same_company_children(self):
        # Plant deliberately inconsistent child rows; service query must never expose them.
        self.conn.execute(
            'INSERT INTO trade_document_lines(id,company_id,document_id,description,qty) VALUES(?,?,?,?,?)',
            (1002, 2, 100, 'foreign injected line', 99))
        result = self.trade.get_doc(100)
        self.assertEqual([r['id'] for r in result['lines']], [1001])
        self.assertEqual([r['id'] for r in result['payments']], [10001])
        self.assertEqual(result['applications'], [])

    def test_list_docs_only_returns_active_company_documents(self):
        result = self.trade.list_docs('invoice', {})
        self.assertEqual([r['id'] for r in result['items']], [100])
        self.assertEqual(result['total'], 1)

    def test_list_payments_only_returns_active_company_payments(self):
        result = self.trade.list_payments({})
        self.assertEqual([r['id'] for r in result['items']], [10001])
        self.assertEqual(result['total'], 1)

    def test_partner_statement_and_ageing_are_company_scoped(self):
        statement = self.trade.partner_statement(11)
        self.assertEqual(statement['partner']['name'], 'Alpha Customer')
        self.assertEqual(statement['closing_cents'], 8000)
        aged = self.trade.ageing('customer', '2026-11-13')
        self.assertEqual(len(aged['rows']), 1)
        self.assertEqual(aged['rows'][0]['partner_id'], 11)

    def test_foreign_partner_cannot_be_used_to_create_document(self):
        with self.assertRaises(self.ApiError) as raised:
            self.trade.save_draft('invoice', {
                'partner_id': 22, 'warehouse_id': 101,
                'lines': [{'description': 'Service', 'qty': 1, 'unit_price': 5}],
            })
        self.assertEqual(raised.exception.code, 'invalid_partner')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM trade_documents').fetchone()[0], 2)

    def test_foreign_product_and_account_references_are_rejected(self):
        with self.assertRaises(self.ApiError) as product_error:
            self.trade.save_draft('invoice', {
                'partner_id': 11, 'warehouse_id': 101,
                'lines': [{'product_id': 222, 'qty': 1}],
            })
        self.assertEqual(product_error.exception.code, 'invalid_product')
        with self.assertRaises(self.ApiError) as account_error:
            self.trade.save_draft('invoice', {
                'partner_id': 11, 'warehouse_id': 101,
                'lines': [{'description': 'Service', 'qty': 1, 'unit_price': 5, 'account_id': 2002}],
            })
        self.assertEqual(account_error.exception.code, 'invalid_account')

    def test_save_draft_stamps_server_company_id_and_line_company_id(self):
        did = self.trade.save_draft('invoice', {
            'company_id': 2,  # untrusted body value must be ignored
            'partner_id': 11, 'warehouse_id': 101,
            'lines': [{'product_id': 111, 'qty': 2}],
        })
        doc = self.conn.execute('SELECT company_id, partner_id FROM trade_documents WHERE id=?', (did,)).fetchone()
        line = self.conn.execute('SELECT company_id, product_id FROM trade_document_lines WHERE document_id=?', (did,)).fetchone()
        self.assertEqual(tuple(doc), (1, 11))
        self.assertEqual(tuple(line), (1, 111))

    def test_foreign_payment_and_credit_application_ids_are_not_accessible(self):
        with self.assertRaises(self.ApiError) as pay_error:
            self.trade.cancel_payment(20001)
        self.assertEqual(pay_error.exception.code, 'not_found')
        with self.assertRaises(self.ApiError) as app_error:
            self.trade.undo_credit_application(700)
        self.assertEqual(app_error.exception.code, 'not_found')

    def test_cross_company_credit_application_is_rejected(self):
        with self.assertRaises(self.ApiError) as raised:
            self.trade.apply_credit(100, 200, 1000)
        self.assertEqual(raised.exception.code, 'not_found')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM credit_applications').fetchone()[0], 0)


    def test_posting_uses_same_company_for_document_and_journal(self):
        self.conn.execute(
            'INSERT INTO products(id,company_id,name,sku,unit,kind,price_cents,cost_cents,tax_rate) VALUES(?,?,?,?,?,?,?,?,?)',
            (333, 1, 'Alpha Service', 'A-SVC', 'service', 'service', 5000, 0, 0))
        did = self.trade.save_draft('invoice', {
            'partner_id': 11, 'warehouse_id': None,
            'lines': [{'product_id': 333, 'qty': 1}],
        })
        journal_context = {}
        self.accounting.post_entry = lambda *args, **kwargs: journal_context.update(kwargs) or 9901
        number = self.trade.post(did, 'invoice')
        row = self.conn.execute('SELECT company_id,status,number,journal_entry_id FROM trade_documents WHERE id=?', (did,)).fetchone()
        self.assertEqual(tuple(row), (1, 'posted', number, 9901))
        self.assertEqual(journal_context.get('company_id'), 1)

    def test_payment_creation_and_journal_are_scoped_to_company(self):
        context = {}
        self.accounting.post_entry = lambda *args, **kwargs: context.update(kwargs) or 9902
        payment_id = self.trade.add_payment(100, 2500, 1001, date='2026-11-10', method='cash')
        payment = self.conn.execute('SELECT company_id,document_id,account_id,amount_cents FROM payments WHERE id=?', (payment_id,)).fetchone()
        doc = self.conn.execute('SELECT paid_cents,payment_status FROM trade_documents WHERE id=100').fetchone()
        self.assertEqual(tuple(payment), (1, 100, 1001, 2500))
        self.assertEqual(tuple(doc), (4500, 'partial'))
        self.assertEqual(context.get('company_id'), 1)

    def test_walkin_customer_is_created_per_company(self):
        alpha = self.trade.walkin_customer_id()
        self.switch_company(2)
        beta = self.trade.walkin_customer_id()
        rows = self.conn.execute('SELECT company_id FROM partners WHERE id IN (?,?) ORDER BY company_id', (alpha, beta)).fetchall()
        self.assertEqual([r[0] for r in rows], [1, 2])

    def test_explicit_company_mismatch_is_rejected(self):
        with self.assertRaises(self.ApiError) as raised:
            self.trade.get_doc(200, company_id=2)
        self.assertEqual(raised.exception.code, 'company_context_mismatch')


if __name__ == '__main__':
    unittest.main()
