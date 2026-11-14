"""Stage 9 service contracts using the actual service modules and a small SQLite adapter.

Flask-free execution keeps the tenant-scope checks runnable in constrained CI images.
The production services are loaded directly; only Flask globals and the DB adapter are stubbed.
"""
import importlib.util
import json
import sqlite3
import sys
import types
import unittest
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP

ERP_ROOT = Path(__file__).parents[1] / 'erp'


class FakeG(dict):
    pass


class Stage9ServiceScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {}
        names = [
            'flask', 'erp_stage9_test', 'erp_stage9_test.db', 'erp_stage9_test.errors',
            'erp_stage9_test.money', 'erp_stage9_test.util', 'erp_stage9_test.services',
        ]
        for name in names:
            cls.saved[name] = sys.modules.get(name)

        flask = types.ModuleType('flask')
        flask.g = FakeG()
        flask._request_context = True
        flask.has_request_context = lambda: bool(flask._request_context)
        sys.modules['flask'] = flask
        cls.flask = flask

        package = types.ModuleType('erp_stage9_test')
        package.__path__ = [str(ERP_ROOT)]
        sys.modules[package.__name__] = package
        cls.package = package

        db = types.ModuleType('erp_stage9_test.db')
        db.conn = None
        db.q = lambda sql, params=(): [dict(row) for row in db.conn.execute(sql, params).fetchall()]
        db.one = lambda sql, params=(): (dict(row) if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.scalar = lambda sql, params=(): (row[0] if (row := db.conn.execute(sql, params).fetchone()) else None)
        db.execute = lambda sql, params=(): db.conn.execute(sql, params).lastrowid
        sys.modules[db.__name__] = db
        package.db = db
        cls.db = db

        errors = types.ModuleType('erp_stage9_test.errors')
        class ApiError(Exception):
            def __init__(self, code, message=None, status=400, params=None, details=None):
                super().__init__(message or code)
                self.code, self.status, self.params, self.details = code, status, params or {}, details
        errors.ApiError = ApiError
        sys.modules[errors.__name__] = errors
        package.errors = errors
        cls.ApiError = ApiError

        money = types.ModuleType('erp_stage9_test.money')
        money.round_half_up = lambda value: int(Decimal(str(value)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        sys.modules[money.__name__] = money
        package.money = money

        util = types.ModuleType('erp_stage9_test.util')
        util.parse_date = lambda value, *args, **kwargs: str(value)[:10]
        util.today = lambda: '2026-11-13'
        sys.modules[util.__name__] = util
        package.util = util

        services = types.ModuleType('erp_stage9_test.services')
        services.__path__ = [str(ERP_ROOT / 'services')]
        sys.modules[services.__name__] = services
        package.services = services

        cls.modules = {}
        for short in ('tenancy', 'sequences', 'settings', 'accounting', 'inventory'):
            name = f'erp_stage9_test.services.{short}'
            spec = importlib.util.spec_from_file_location(name, ERP_ROOT / 'services' / f'{short}.py')
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            setattr(services, short, module)
            spec.loader.exec_module(module)
            cls.modules[short] = module

    @classmethod
    def tearDownClass(cls):
        for name, old in cls.saved.items():
            if old is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = old

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript('''
            CREATE TABLE companies(id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'active');
            CREATE TABLE settings(company_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                                  PRIMARY KEY(company_id,key));
            CREATE TABLE sequences(company_id INTEGER NOT NULL, name TEXT NOT NULL,
                                   prefix TEXT NOT NULL, next_number INTEGER NOT NULL, padding INTEGER NOT NULL,
                                   PRIMARY KEY(company_id,name));
            CREATE TABLE products(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, name TEXT NOT NULL, sku TEXT NOT NULL DEFAULT '',
                                  unit TEXT NOT NULL DEFAULT 'pcs', kind TEXT NOT NULL, cost_cents INTEGER NOT NULL DEFAULT 0,
                                  min_stock REAL NOT NULL DEFAULT 0, is_active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE warehouses(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL,
                                    name TEXT NOT NULL, is_active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE stock_levels(product_id INTEGER NOT NULL, warehouse_id INTEGER NOT NULL,
                                      qty REAL NOT NULL, value_cents INTEGER NOT NULL, company_id INTEGER NOT NULL,
                                      UNIQUE(company_id,product_id,warehouse_id));
            CREATE TABLE stock_moves(id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, warehouse_id INTEGER NOT NULL,
                                     qty REAL NOT NULL, value_cents INTEGER NOT NULL, move_type TEXT NOT NULL,
                                     ref_type TEXT, ref_id INTEGER, date TEXT NOT NULL, note TEXT, lot_no TEXT,
                                     expiry_date TEXT, user_id INTEGER, company_id INTEGER NOT NULL);
            CREATE TABLE stock_counts(id INTEGER PRIMARY KEY AUTOINCREMENT, warehouse_id INTEGER NOT NULL,
                                      date TEXT NOT NULL, note TEXT, created_by INTEGER, status TEXT NOT NULL DEFAULT 'draft',
                                      number TEXT, posted_at TEXT, company_id INTEGER NOT NULL);
            CREATE TABLE stock_count_lines(id INTEGER PRIMARY KEY AUTOINCREMENT, count_id INTEGER NOT NULL,
                                           product_id INTEGER NOT NULL, system_qty REAL NOT NULL, counted_qty REAL,
                                           company_id INTEGER NOT NULL);
            CREATE TABLE accounts(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL, code TEXT NOT NULL,
                                  name TEXT NOT NULL, type TEXT NOT NULL, subtype TEXT, is_active INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE journal_entries(id INTEGER PRIMARY KEY AUTOINCREMENT, number TEXT NOT NULL, date TEXT NOT NULL,
                                         memo TEXT, ref_type TEXT, ref_id INTEGER, reversal_of INTEGER,
                                         created_by INTEGER, company_id INTEGER NOT NULL);
            CREATE TABLE journal_lines(id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER NOT NULL,
                                       account_id INTEGER NOT NULL, debit_cents INTEGER NOT NULL,
                                       credit_cents INTEGER NOT NULL, partner_id INTEGER, cost_center_id INTEGER,
                                       description TEXT, company_id INTEGER NOT NULL);
            CREATE TABLE partners(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL);
            CREATE TABLE cost_centers(id INTEGER PRIMARY KEY, company_id INTEGER NOT NULL);
        ''')
        self.conn.executemany('INSERT INTO companies(id,status) VALUES(?,?)', [(1, 'active'), (2, 'active')])
        self.conn.executemany(
            'INSERT INTO settings(company_id,key,value) VALUES(?,?,?)',
            [(1, 'currency', json.dumps('LYD')), (2, 'currency', json.dumps('USD')),
             (1, 'allow_negative_stock', json.dumps(False)), (2, 'allow_negative_stock', json.dumps(True))])
        self.conn.executemany(
            'INSERT INTO products(id,company_id,name,kind,cost_cents) VALUES(?,?,?,?,?)',
            [(11, 1, 'A Product', 'storable', 100), (22, 2, 'B Product', 'storable', 200)])
        self.conn.executemany(
            'INSERT INTO warehouses(id,company_id,name,is_active) VALUES(?,?,?,1)',
            [(101, 1, 'A Warehouse'), (202, 2, 'B Warehouse')])
        self.conn.executemany(
            'INSERT INTO stock_levels(product_id,warehouse_id,qty,value_cents,company_id) VALUES(?,?,?,?,?)',
            [(11, 101, 5, 500, 1), (22, 202, 8, 1600, 2)])
        self.conn.executemany(
            'INSERT INTO accounts(id,company_id,code,name,type,subtype,is_active) VALUES(?,?,?,?,?,?,1)',
            [(1001, 1, '1000', 'A Cash', 'asset', 'cash'),
             (1002, 1, '4000', 'A Sales', 'revenue', None),
             (2001, 2, '1000', 'B Cash', 'asset', 'cash'),
             (2002, 2, '4000', 'B Sales', 'revenue', None)])
        self.conn.execute(
            'INSERT INTO sequences(company_id,name,prefix,next_number,padding) VALUES(?,?,?,?,?)',
            (1, 'journal', 'A-JE-', 1, 4))
        self.db.conn = self.conn
        self.flask.g.clear()
        self.flask.g['company'] = {'id': 1, 'name': 'Company A'}
        self.flask._request_context = True

    def tearDown(self):
        self.conn.close()
        self.db.conn = None

    def test_current_company_rejects_client_context_mismatch(self):
        with self.assertRaises(self.ApiError) as raised:
            self.modules['tenancy'].current_company_id(2)
        self.assertEqual(raised.exception.code, 'company_context_mismatch')
        self.assertEqual(self.modules['tenancy'].current_company_id(), 1)

    def test_settings_and_cache_are_company_scoped(self):
        settings = self.modules['settings']
        self.assertEqual(settings.get_settings()['currency'], 'LYD')
        # Simulate a verified switch by updating the server-owned context and clearing request cache.
        self.flask.g['company'] = {'id': 2, 'name': 'Company B'}
        self.flask.g.pop('_settings_by_company', None)
        self.assertEqual(settings.get_settings()['currency'], 'USD')

    def test_settings_write_changes_only_the_active_company(self):
        settings = self.modules['settings']
        settings.save_settings({'currency': 'EUR'})
        a = self.conn.execute("SELECT value FROM settings WHERE company_id=1 AND key='currency'").fetchone()[0]
        b = self.conn.execute("SELECT value FROM settings WHERE company_id=2 AND key='currency'").fetchone()[0]
        self.assertEqual(json.loads(a), 'EUR')
        self.assertEqual(json.loads(b), 'USD')

        with self.assertRaises(self.ApiError) as raised:
            settings.save_settings({'default_warehouse_id': 202})
        self.assertEqual(raised.exception.code, 'invalid_warehouse')

    def test_inventory_lookup_and_level_are_scoped(self):
        inventory = self.modules['inventory']
        self.assertEqual(inventory.get_product(11)['name'], 'A Product')
        self.assertEqual(inventory.level(11, 101), (5.0, 500))
        with self.assertRaises(self.ApiError) as raised:
            inventory.get_product(22)
        self.assertEqual(raised.exception.code, 'not_found')
        with self.assertRaises(self.ApiError):
            inventory.get_warehouse(202)

    def test_inventory_receive_rejects_cross_company_warehouse_before_write(self):
        inventory = self.modules['inventory']
        with self.assertRaises(self.ApiError) as raised:
            inventory.receive(11, 202, 1, 100, 'purchase', '2026-11-13')
        self.assertEqual(raised.exception.code, 'not_found')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM stock_moves').fetchone()[0], 0)
        inventory.receive(11, 101, 2, 240, 'purchase', '2026-11-13')
        move = self.conn.execute('SELECT company_id,product_id,warehouse_id FROM stock_moves').fetchone()
        self.assertEqual(tuple(move), (1, 11, 101))
        self.assertEqual(self.conn.execute('SELECT qty FROM stock_levels WHERE product_id=11').fetchone()[0], 7.0)

    def test_account_totals_and_balance_only_use_current_company(self):
        self.conn.execute('INSERT INTO journal_entries(number,date,memo,company_id) VALUES(?,?,?,?)',
                          ('A-1', '2026-11-13', 'A only', 1))
        entry_id = self.conn.execute('SELECT id FROM journal_entries').fetchone()[0]
        self.conn.executemany(
            'INSERT INTO journal_lines(entry_id,account_id,debit_cents,credit_cents,company_id) VALUES(?,?,?,?,?)',
            [(entry_id, 1001, 500, 0, 1), (entry_id, 1002, 0, 500, 1)])
        accounting = self.modules['accounting']
        rows = accounting.account_totals()
        self.assertEqual({r['id'] for r in rows}, {1001, 1002})
        self.assertEqual(accounting.balance_of('1000'), 500)
        self.assertNotIn(2001, {r['id'] for r in rows})

    def test_accounting_posting_assigns_company_and_rejects_foreign_account(self):
        accounting = self.modules['accounting']
        # The active company's journal sequence is used without sharing another tenant's numbering.
        entry_id = accounting.post_entry('2026-11-13', 'A entry', [
            {'account': 1001, 'debit': 100, 'credit': 0},
            {'account': 1002, 'debit': 0, 'credit': 100},
        ])
        row = self.conn.execute('SELECT number,company_id FROM journal_entries WHERE id=?', (entry_id,)).fetchone()
        self.assertEqual(tuple(row), ('A-JE-0001', 1))
        with self.assertRaises(self.ApiError) as raised:
            accounting.post_entry('2026-11-13', 'bad entry', [
                {'account': 2001, 'debit': 100, 'credit': 0},
                {'account': 1002, 'debit': 0, 'credit': 100},
            ])
        self.assertEqual(raised.exception.code, 'invalid_account')

    def test_sequences_generate_company_scoped_numbers_after_stage10(self):
        self.conn.execute('INSERT INTO sequences(company_id,name,prefix,next_number,padding) VALUES(?,?,?,?,?)',
                          (2, 'invoice', 'B-INV-', 10, 4))
        self.assertEqual(self.modules['sequences'].next_number('invoice'), 'INV-000001')
        self.assertEqual(self.conn.execute(
            "SELECT next_number FROM sequences WHERE company_id=2 AND name='invoice'"
        ).fetchone()[0], 10)
        self.flask.g['company'] = {'id': 2, 'name': 'Company B'}
        self.assertEqual(self.modules['sequences'].next_number('invoice'), 'B-INV-0010')
        rows = {r['company_id']: r['next_number'] for r in self.conn.execute(
            "SELECT company_id,next_number FROM sequences WHERE name='invoice'"
        ).fetchall()}
        self.assertEqual(rows, {1: 2, 2: 11})

    def test_stock_count_creation_and_reads_are_company_scoped(self):
        inventory = self.modules['inventory']
        count_a = inventory.create_count(101, '2026-11-13', product_ids=[11, 22], note='A count')
        self.assertEqual(inventory.get_count(count_a)['company_id'], 1)
        lines = self.conn.execute('SELECT id,product_id,company_id FROM stock_count_lines WHERE count_id=?',
                                  (count_a,)).fetchall()
        self.assertEqual([(row['product_id'], row['company_id']) for row in lines], [(11, 1)])

        self.flask.g['company'] = {'id': 2, 'name': 'Company B'}
        count_b = inventory.create_count(202, '2026-11-13', product_ids=[11, 22], note='B count')
        self.assertEqual(inventory.get_count(count_b)['company_id'], 2)

        self.flask.g['company'] = {'id': 1, 'name': 'Company A'}
        with self.assertRaises(self.ApiError) as raised:
            inventory.get_count(count_b)
        self.assertEqual(raised.exception.code, 'not_found')
        inventory.update_count(count_a, [{'id': lines[0]['id'], 'counted_qty': 6}])
        updated = self.conn.execute('SELECT counted_qty FROM stock_count_lines WHERE id=?',
                                    (lines[0]['id'],)).fetchone()[0]
        self.assertEqual(updated, 6)

    def test_inventory_low_stock_is_company_scoped(self):
        self.conn.execute('UPDATE stock_levels SET qty=0 WHERE product_id=11')
        rows = self.modules['inventory'].low_stock()
        self.assertEqual([row['id'] for row in rows], [11])


if __name__ == '__main__':
    unittest.main()
