"""Backend test-suite (python -m unittest discover -s tests -v).
Covers security, RBAC, double-entry integrity, stock valuation, documents, payroll, manufacturing and a
randomised stress test that must always leave the books balanced."""
import io
import os
import random
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta

from erp import create_app, db, security
from erp.errors import ApiError
from erp.services import accounting, assets, hr, inventory, manufacturing, tenancy, trade


class Base(unittest.TestCase):
    def setUp(self):
        security._failures.clear()
        self.tmp = tempfile.mkdtemp()
        self.app = create_app({'DB_PATH': os.path.join(self.tmp, 't.sqlite3'), 'SECRET_KEY': 'test-secret-' * 4,
                               'TESTING': True})
        self.c = self.app.test_client()
        r = self.c.post('/api/auth/setup', json={'company': 'T Co', 'name': 'Admin', 'email': 'admin@t.com',
                                                 'password': 'Admin12345'})
        self.assertEqual(r.status_code, 201, r.get_data(as_text=True))
        self.token = r.json['token']

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def api(self, method, path, json=None, token=None, **kw):
        headers = {'Authorization': 'Bearer ' + (token or self.token)}
        return getattr(self.c, method)(path, json=json, headers=headers, **kw)

    def ok(self, r, status=200):
        self.assertEqual(r.status_code, status, r.get_data(as_text=True))
        return r.json

    def err(self, r, status, code=None):
        self.assertEqual(r.status_code, status, r.get_data(as_text=True))
        if code:
            self.assertEqual(r.json['error']['code'], code)
        return r.json['error']

    def product(self, sku, price=0, cost=0, kind='storable', **kw):
        return self.ok(self.api('post', '/api/products', {'sku': sku, 'name': sku, 'price': price, 'cost': cost,
                                                          'kind': kind, **kw}), 201)

    def customer(self, name='Cust', **kw):
        return self.ok(self.api('post', '/api/customers', {'name': name, **kw}), 201)

    def supplier(self, name='Supp', **kw):
        return self.ok(self.api('post', '/api/suppliers', {'name': name, **kw}), 201)

    def doc(self, path, partner, lines, post=True, **kw):
        return self.ok(self.api('post', path, {'partner_id': partner['id'], 'lines': lines, 'post': post, **kw}), 201)

    def bill(self, supplier, lines, **kw):
        return self.doc('/api/purchases/bills', supplier, lines, **kw)

    def invoice(self, customer, lines, **kw):
        return self.doc('/api/sales/invoices', customer, lines, **kw)

    def tb(self):
        rows = self.ok(self.api('get', '/api/accounting/trial-balance'))['rows']
        return {r['code']: (r['debit'], r['credit']) for r in rows}

    def health(self):
        res = self.ok(self.api('get', '/api/accounting/health'))
        self.assertTrue(res['ok'], [c for c in res['checks'] if not c['ok']])

    def account_id(self, code):
        items = self.ok(self.api('get', '/api/accounts?all=1'))['items']
        return next(a['id'] for a in items if a['code'] == code)

    def stock(self, pid):
        return self.ok(self.api('get', f'/api/products/{pid}'))['stock_qty']

    def make_user(self, role, email=None):
        email = email or f'{role}@t.com'
        self.ok(self.api('post', '/api/users', {'email': email, 'name': role, 'password': 'Passw0rd123', 'role': role}), 201)
        r = self.c.post('/api/auth/login', json={'email': email, 'password': 'Passw0rd123'})
        return self.ok(r)['token']


class MultiTenantFoundationTests(Base):
    def test_first_setup_creates_company_and_active_owner_membership(self):
        company = db.one('SELECT id,name,status FROM companies')
        self.assertIsNotNone(company)
        self.assertEqual(company['name'], 'T Co')
        self.assertEqual(company['status'], 'active')
        membership = db.one(
            'SELECT m.role,m.status,u.email FROM company_memberships m '
            'JOIN users u ON u.id=m.user_id WHERE m.company_id=?',
            (company['id'],),
        )
        self.assertEqual(membership, {'role': 'admin', 'status': 'active', 'email': 'admin@t.com'})



# ====================================================================== security & access control
class SecurityTests(Base):
    def test_every_api_route_requires_authentication(self):
        public = {'/api/health', '/api/auth/setup-status', '/api/auth/setup', '/api/auth/login'}
        checked = 0
        for rule in self.app.url_map.iter_rules():
            if not rule.rule.startswith('/api/') or rule.rule in public:
                continue
            path = rule.rule.replace('<int:rid>', '1').replace('<int:did>', '1').replace('<int:eid>', '1') \
                .replace('<int:cid>', '1').replace('<int:pid>', '1').replace('<int:aid>', '1') \
                .replace('<int:uid>', '1').replace('<int:lid>', '1').replace('<int:bid>', '1') \
                .replace('<int:oid>', '1').replace('<int:sid>', '1').replace('<int:rid>', '1').replace('<name>', 'x')
            for method in rule.methods - {'HEAD', 'OPTIONS'}:
                r = getattr(self.c, method.lower())(path)
                self.assertEqual(r.status_code, 401, f'{method} {rule.rule} is not protected')
                checked += 1
        self.assertGreater(checked, 150)

    def test_setup_only_once_and_password_policy(self):
        r = self.c.post('/api/auth/setup', json={'name': 'X', 'email': 'x@t.com', 'password': 'Passw0rd123'})
        self.err(r, 409, 'already_setup')
        self.err(self.api('post', '/api/users', {'email': 'a@t.com', 'name': 'A', 'password': 'short1', 'role': 'viewer'}),
                 422, 'weak_password')
        self.err(self.api('post', '/api/users', {'email': 'a@t.com', 'name': 'A', 'password': 'onlyletters', 'role': 'viewer'}),
                 422, 'weak_password')

    def test_login_wrong_password_and_throttle(self):
        for _ in range(5):
            self.err(self.c.post('/api/auth/login', json={'email': 'admin@t.com', 'password': 'bad'}), 401, 'invalid_credentials')
        self.err(self.c.post('/api/auth/login', json={'email': 'admin@t.com', 'password': 'Admin12345'}), 429, 'too_many_attempts')

    def test_tampered_and_deactivated_tokens(self):
        self.err(self.api('get', '/api/products', token=self.token[:-3] + 'abc'), 401)
        viewer_token = self.make_user('viewer')
        self.ok(self.api('get', '/api/products', token=viewer_token))
        uid = next(u['id'] for u in self.ok(self.api('get', '/api/users'))['items'] if u['email'] == 'viewer@t.com')
        self.ok(self.api('put', f'/api/users/{uid}', {'is_active': False}))
        self.err(self.api('get', '/api/products', token=viewer_token), 401)

    def test_role_permissions_are_enforced(self):
        viewer = self.make_user('viewer')
        self.err(self.api('post', '/api/products', {'sku': 'X', 'name': 'X'}, token=viewer), 403, 'forbidden')
        self.err(self.api('get', '/api/users', token=viewer), 403)
        self.err(self.api('get', '/api/audit', token=viewer), 403)
        sales = self.make_user('sales')
        p = self.product('S1', price=10, cost=5)
        c = self.customer('C')
        self.ok(self.api('post', '/api/purchases/bills', {'partner_id': self.supplier()['id'], 'post': True,
                         'lines': [{'product_id': p['id'], 'qty': 5, 'unit_price': 5}]}), 201)
        inv = self.ok(self.api('post', '/api/sales/invoices', {'partner_id': c['id'], 'post': True,
                      'lines': [{'product_id': p['id'], 'qty': 1}]}, token=sales), 201)
        self.err(self.api('post', f"/api/sales/invoices/{inv['id']}/cancel", token=sales), 403, 'forbidden')
        self.err(self.api('post', '/api/accounting/entries', {'lines': []}, token=sales), 403)
        self.err(self.api('put', '/api/settings', {'company': 'Hacked'}, token=sales), 403)

    def test_cannot_remove_last_admin(self):
        me = self.ok(self.api('get', '/api/users'))['items'][0]
        self.err(self.api('put', f"/api/users/{me['id']}", {'role': 'viewer'}), 409, 'last_admin')
        self.err(self.api('put', f"/api/users/{me['id']}", {'is_active': False}), 409, 'last_admin')

    def test_validation_and_duplicates(self):
        e = self.err(self.api('post', '/api/products', {'sku': '', 'price': -5}), 422, 'validation')
        self.assertEqual(e['details']['sku'], 'required')
        self.assertEqual(e['details']['name'], 'required')
        self.assertEqual(e['details']['price'], 'min')
        self.product('DUP')
        self.err(self.api('post', '/api/products', {'sku': 'DUP', 'name': 'again'}), 409, 'duplicate')
        self.err(self.api('post', '/api/customers', {'name': 'x', 'email': 'e' * 600}), 422, 'validation')


# ====================================================================== accounting & documents
class AccountingTests(Base):
    def test_average_cost_and_sale_posting(self):
        p = self.product('P1', price=300)
        s, c = self.supplier(), self.customer()
        self.bill(s, [{'product_id': p['id'], 'qty': 10, 'unit_price': 100, 'tax_rate': 0}])
        self.bill(s, [{'product_id': p['id'], 'qty': 10, 'unit_price': 200, 'tax_rate': 0}])
        inv = self.invoice(c, [{'product_id': p['id'], 'qty': 5, 'unit_price': 300, 'tax_rate': 15}])
        self.assertEqual((inv['subtotal'], inv['tax'], inv['total']), (1500.0, 225.0, 1725.0))
        self.assertEqual(inv['lines'][0]['cost'], 750.0)  # moving average (1000+2000)/20 = 150 per unit
        self.assertEqual(inv['number'], 'INV-000001')
        tb = self.tb()
        self.assertEqual(tb['1200'], (2250.0, 0))     # inventory
        self.assertEqual(tb['1100'], (1725.0, 0))     # receivable
        self.assertEqual(tb['2000'], (0, 3000.0))     # payable
        self.assertEqual(tb['4000'], (0, 1500.0))     # sales
        self.assertEqual(tb['2100'], (0, 225.0))      # output VAT
        self.assertEqual(tb['5000'], (750.0, 0))      # COGS
        self.assertEqual(self.stock(p['id']), 15)
        self.health()

    def test_failed_posting_rolls_back_everything(self):
        p = self.product('P2', price=10)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 3, 'unit_price': 5}])
        c = self.customer()
        e = self.err(self.api('post', '/api/sales/invoices', {'partner_id': c['id'], 'post': True,
                     'lines': [{'product_id': p['id'], 'qty': 100}]}), 409, 'insufficient_stock')
        self.assertEqual(e['params']['available'], 3)
        self.assertEqual(self.ok(self.api('get', '/api/sales/invoices'))['total'], 0)
        self.assertEqual(self.stock(p['id']), 3)
        self.health()

    def test_payments_status_and_limits(self):
        p = self.product('P3', price=300)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 100}])
        c = self.customer()
        inv = self.invoice(c, [{'product_id': p['id'], 'qty': 5, 'unit_price': 300, 'tax_rate': 15}])
        cash = self.account_id('1000')
        d = self.ok(self.api('post', f"/api/sales/invoices/{inv['id']}/payments", {'amount': 700, 'account_id': cash}), 201)
        self.assertEqual((d['payment_status'], d['paid'], d['outstanding']), ('partial', 700.0, 1025.0))
        self.err(self.api('post', f"/api/sales/invoices/{inv['id']}/payments", {'amount': 1100, 'account_id': cash}),
                 422, 'payment_exceeds_balance')
        self.err(self.api('post', f"/api/sales/invoices/{inv['id']}/payments", {'amount': 10, 'account_id': self.account_id('4000')}),
                 422, 'invalid_cash_account')
        d = self.ok(self.api('post', f"/api/sales/invoices/{inv['id']}/payments", {'amount': 1025, 'account_id': cash}), 201)
        self.assertEqual(d['payment_status'], 'paid')
        self.assertEqual(self.ok(self.api('get', f"/api/customers/{c['id']}"))['balance'], 0)
        self.err(self.api('post', f"/api/sales/invoices/{inv['id']}/cancel"), 409, 'has_payments')
        for pay in d['payments']:
            self.ok(self.api('post', f"/api/payments/{pay['id']}/cancel"))
        self.assertEqual(self.ok(self.api('get', f"/api/sales/invoices/{inv['id']}"))['payment_status'], 'unpaid')
        self.health()

    def test_cancel_invoice_restores_stock_and_books(self):
        p = self.product('P4', price=50)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 20}])
        before = self.tb()
        inv = self.invoice(self.customer(), [{'product_id': p['id'], 'qty': 4, 'unit_price': 50}])
        self.assertEqual(self.stock(p['id']), 6)
        d = self.ok(self.api('post', f"/api/sales/invoices/{inv['id']}/cancel"))
        self.assertEqual(d['status'], 'cancelled')
        self.assertEqual(self.stock(p['id']), 10)
        after = self.tb()
        for code in ('1100', '4000', '2100', '5000'):
            dr, cr = after.get(code, (0, 0))
            self.assertEqual(dr, cr, f'account {code} should net to zero after cancellation')
        self.assertEqual(after['1200'], before['1200'])
        self.err(self.api('post', f"/api/sales/invoices/{inv['id']}/cancel"), 409, 'not_posted')
        self.health()

    def test_credit_note_and_credit_application(self):
        p = self.product('P5', price=300)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 150}])
        c = self.customer()
        inv = self.invoice(c, [{'product_id': p['id'], 'qty': 5, 'unit_price': 300, 'tax_rate': 15}])
        line = inv['lines'][0]
        cn = self.ok(self.api('post', f"/api/sales/invoices/{inv['id']}/return", {'lines': [{'line_id': line['id'], 'qty': 2}]}), 201)
        self.assertEqual(cn['total'], 690.0)
        cn = self.ok(self.api('post', f"/api/sales/credit-notes/{cn['id']}/post"))
        self.assertEqual(self.stock(p['id']), 7)   # 10 - 5 + 2 returned
        self.ok(self.api('post', '/api/credit-applications', {'credit_id': cn['id'], 'target_id': inv['id'], 'amount': 690}), 201)
        inv2 = self.ok(self.api('get', f"/api/sales/invoices/{inv['id']}"))
        cn2 = self.ok(self.api('get', f"/api/sales/credit-notes/{cn['id']}"))
        self.assertEqual((inv2['paid'], inv2['payment_status'], cn2['payment_status']), (690.0, 'partial', 'paid'))
        self.assertEqual(self.ok(self.api('get', f"/api/customers/{c['id']}"))['balance'], 1035.0)
        # cannot return more than was sold
        bad = self.ok(self.api('post', '/api/sales/credit-notes', {'partner_id': c['id'], 'origin_id': inv['id'],
                      'lines': [{'product_id': p['id'], 'qty': 9, 'unit_price': 300}]}), 201)
        self.err(self.api('post', f"/api/sales/credit-notes/{bad['id']}/post"), 422, 'return_exceeds_document')
        self.err(self.api('post', f"/api/sales/invoices/{inv['id']}/cancel"), 409)
        self.health()

    def test_debit_note_returns_goods_to_supplier(self):
        p = self.product('P6')
        s = self.supplier()
        bill = self.bill(s, [{'product_id': p['id'], 'qty': 10, 'unit_price': 100, 'tax_rate': 0}])
        dn = self.ok(self.api('post', f"/api/purchases/bills/{bill['id']}/return",
                              {'lines': [{'line_id': bill['lines'][0]['id'], 'qty': 4}]}), 201)
        dn = self.ok(self.api('post', f"/api/purchases/debit-notes/{dn['id']}/post"))
        self.assertEqual((dn['total'], self.stock(p['id'])), (400.0, 6))
        # supplier credits a different price than our carrying cost -> difference goes to adjustments
        dn2 = self.ok(self.api('post', '/api/purchases/debit-notes', {'partner_id': s['id'], 'post': True,
                      'lines': [{'product_id': p['id'], 'qty': 1, 'unit_price': 120, 'tax_rate': 0}]}), 201)
        self.assertEqual(dn2['total'], 120.0)
        self.assertEqual(self.tb()['5400'], (0, 20.0))
        self.health()

    def test_manual_journal_reversal_and_period_lock(self):
        cash, cap = self.account_id('1000'), self.account_id('3000')
        self.err(self.api('post', '/api/accounting/entries', {'lines': [{'account_id': cash, 'debit': 100},
                 {'account_id': cap, 'credit': 90}]}), 422, 'unbalanced')
        e = self.ok(self.api('post', '/api/accounting/entries', {'memo': 'capital', 'lines': [
            {'account_id': cash, 'debit': 1000}, {'account_id': cap, 'credit': 1000}]}), 201)
        self.assertEqual(e['total'], 1000.0)
        rev = self.ok(self.api('post', f"/api/accounting/entries/{e['id']}/reverse"), 201)
        self.assertEqual(rev['reversal_of'], e['id'])
        self.assertEqual(self.tb().get('1000', (0, 0))[0], self.tb().get('1000', (0, 0))[1])
        self.err(self.api('post', f"/api/accounting/entries/{e['id']}/reverse"), 409, 'already_reversed')
        self.ok(self.api('put', '/api/settings', {'lock_date': date.today().isoformat()}))
        self.err(self.api('post', '/api/accounting/entries', {'lines': [{'account_id': cash, 'debit': 5},
                 {'account_id': cap, 'credit': 5}]}), 409, 'period_locked')
        p = self.product('PL', price=5)
        self.err(self.api('post', '/api/purchases/bills', {'partner_id': self.supplier()['id'], 'post': True,
                 'lines': [{'product_id': p['id'], 'qty': 1, 'unit_price': 1}]}), 409, 'period_locked')

    def test_posted_ledger_is_immutable(self):
        p = self.product('IM')
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 1, 'unit_price': 10}])
        with self.app.app_context():
            conn = db.get_db()
            for sql in ('UPDATE journal_lines SET debit_cents=1', 'DELETE FROM journal_lines',
                        'DELETE FROM journal_entries', "UPDATE journal_entries SET memo='x'"):
                with self.assertRaises(sqlite3.IntegrityError, msg=sql):
                    conn.execute(sql)

    def test_money_rounding_is_commercial(self):
        p = self.product('RND', price=19.99)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 5}])
        inv = self.invoice(self.customer(), [{'product_id': p['id'], 'qty': 3, 'tax_rate': 15}], post=False)
        self.assertEqual((inv['subtotal'], inv['tax'], inv['total']), (59.97, 9.0, 68.97))   # 8.9955 -> 9.00

    def test_credit_limit_blocks_invoice(self):
        p = self.product('CL', price=300)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 100}])
        c = self.customer('Limited', credit_limit=1000)
        e = self.err(self.api('post', '/api/sales/invoices', {'partner_id': c['id'], 'post': True,
                     'lines': [{'product_id': p['id'], 'qty': 5, 'unit_price': 300}]}), 409, 'credit_limit_exceeded')
        self.assertEqual(e['params']['limit'], 1000.0)

    def test_expense_bill_without_stock(self):
        rent = self.account_id('5800')
        b = self.bill(self.supplier(), [{'description': 'Rent', 'qty': 1, 'unit_price': 1000, 'tax_rate': 0, 'account_id': rent}])
        self.assertEqual(self.tb()['5800'], (1000.0, 0))
        self.assertEqual(b['status'], 'posted')
        self.health()

    def test_reports_are_consistent(self):
        p = self.product('RP', price=100)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 60, 'tax_rate': 0}])
        self.invoice(self.customer(), [{'product_id': p['id'], 'qty': 4, 'unit_price': 100, 'tax_rate': 0}])
        prof = self.ok(self.api('get', '/api/reports/profitability'))
        self.assertEqual((prof['total_revenue'], prof['total_cost'], prof['total_profit']), (400.0, 240.0, 160.0))
        inc = self.ok(self.api('get', '/api/accounting/income-statement'))
        self.assertEqual((inc['total_revenue'], inc['total_cogs'], inc['net_income']), (400.0, 240.0, 160.0))
        bs = self.ok(self.api('get', '/api/accounting/balance-sheet'))
        self.assertTrue(bs['balanced'])
        self.assertEqual(self.ok(self.api('get', '/api/reports/dashboard'))['sales_today'], 400.0)
        self.assertTrue(self.ok(self.api('get', '/api/accounting/trial-balance'))['balanced'])


# ====================================================================== inventory
class InventoryTests(Base):
    def test_fefo_lot_allocation(self):
        p = self.product('LOT', price=10)
        self.bill(self.supplier(), [
            {'product_id': p['id'], 'qty': 5, 'unit_price': 4, 'lot_no': 'LATE', 'expiry_date': '2031-01-01'},
            {'product_id': p['id'], 'qty': 5, 'unit_price': 4, 'lot_no': 'SOON', 'expiry_date': '2029-01-01'}])
        inv = self.invoice(self.customer(), [{'product_id': p['id'], 'qty': 6}])
        lots = {l['lot_no']: l['qty'] for l in self.ok(self.api('get', '/api/inventory/lots'))['items']}
        self.assertEqual(lots, {'LATE': 4.0})                    # SOON fully used first, then 1 from LATE
        moves = self.ok(self.api('get', f"/api/inventory/moves?product_id={p['id']}&move_type=sale"))['items']
        self.assertEqual(sorted((m['lot_no'], m['qty']) for m in moves), [('LATE', -1.0), ('SOON', -5.0)])
        self.ok(self.api('post', f"/api/sales/invoices/{inv['id']}/cancel"))
        lots = {l['lot_no']: l['qty'] for l in self.ok(self.api('get', '/api/inventory/lots'))['items']}
        self.assertEqual(lots, {'LATE': 5.0, 'SOON': 5.0})       # cancellation restores the exact lots
        self.health()

    def test_sold_out_items_disappear_from_valuation_and_lots(self):
        p = self.product('GONE', price=10)
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 4, 'unit_price': 3, 'lot_no': 'X', 'expiry_date': '2030-01-01'}])
        self.invoice(self.customer(), [{'product_id': p['id'], 'qty': 4}])
        val = self.ok(self.api('get', '/api/inventory/valuation'))
        self.assertEqual((val['rows'], val['total_value'], val['matches_gl']), ([], 0.0, True))
        self.assertEqual(self.ok(self.api('get', '/api/inventory/lots'))['items'], [])
        self.health()

    def test_adjust_transfer_and_opening_stock(self):
        wh2 = self.ok(self.api('post', '/api/warehouses', {'code': 'W2', 'name': 'Second'}), 201)
        p = self.product('ADJ', price=10, cost=4, opening_qty=10, opening_cost=4)
        self.assertEqual(self.stock(p['id']), 10)
        self.assertEqual(self.tb()['3900'], (0, 40.0))            # opening balance equity
        wh1 = self.ok(self.api('get', '/api/warehouses'))['items'][-1]['id'] if False else 1
        self.ok(self.api('post', '/api/inventory/adjust', {'product_id': p['id'], 'warehouse_id': 1, 'qty': -3, 'note': 'damaged'}), 201)
        self.assertEqual(self.stock(p['id']), 7)
        self.assertEqual(self.tb()['5400'], (12.0, 0))            # 3 x 4.00 written off
        self.ok(self.api('post', '/api/inventory/transfer', {'product_id': p['id'], 'from_warehouse_id': 1,
                                                             'to_warehouse_id': wh2['id'], 'qty': 2}), 201)
        levels = {l['warehouse_name']: (l['qty'], l['value']) for l in
                  self.ok(self.api('get', f"/api/inventory/levels?product_id={p['id']}"))['items']}
        self.assertEqual(levels, {'Main Warehouse': (5.0, 20.0), 'Second': (2.0, 8.0)})
        self.err(self.api('post', '/api/inventory/transfer', {'product_id': p['id'], 'from_warehouse_id': 1,
                 'to_warehouse_id': 1, 'qty': 1}), 422, 'same_warehouse')
        self.err(self.api('post', '/api/inventory/adjust', {'product_id': p['id'], 'warehouse_id': 1, 'qty': -99}), 409,
                 'insufficient_stock')
        self.assertTrue(self.ok(self.api('get', '/api/inventory/valuation'))['matches_gl'])
        self.health()

    def test_stock_count_posts_differences(self):
        p = self.product('CNT', price=10, cost=5, opening_qty=10, opening_cost=5)
        count = self.ok(self.api('post', '/api/inventory/counts', {'warehouse_id': 1, 'product_ids': [p['id']]}), 201)
        self.ok(self.api('put', f"/api/inventory/counts/{count['id']}", {'lines': [{'id': count['lines'][0]['id'], 'counted_qty': 8}]}))
        res = self.ok(self.api('post', f"/api/inventory/counts/{count['id']}/post"))
        self.assertEqual(res['adjustments'], 1)
        self.assertEqual(self.stock(p['id']), 8)
        self.assertEqual(self.tb()['5400'], (10.0, 0))
        self.err(self.api('post', f"/api/inventory/counts/{count['id']}/post"), 409, 'not_draft')
        self.health()

    def test_services_have_no_stock(self):
        s = self.product('SRV', price=50, kind='service')
        self.err(self.api('post', '/api/inventory/adjust', {'product_id': s['id'], 'warehouse_id': 1, 'qty': 5}), 422, 'not_stock_item')
        inv = self.invoice(self.customer(), [{'product_id': s['id'], 'qty': 2, 'tax_rate': 0}])
        self.assertEqual(inv['total'], 100.0)
        self.assertEqual(self.tb().get('5000', (0, 0)), (0, 0))
        self.health()


# ====================================================================== HR, manufacturing, assets, POS
class OperationsTests(Base):
    def test_payroll_with_advance_and_cancellation(self):
        emp = self.ok(self.api('post', '/api/employees', {'name': 'Sam', 'salary': 3000}), 201)
        cash, bank = self.account_id('1000'), self.account_id('1010')
        self.ok(self.api('post', '/api/hr/advances', {'employee_id': emp['id'], 'amount': 500, 'account_id': cash}), 201)
        self.assertEqual(self.tb()['1150'], (500.0, 0))
        self.err(self.api('post', '/api/hr/payroll', {'month': '2026-01', 'account_id': bank,
                 'items': [{'employee_id': emp['id'], 'advance_deduction': 900}]}), 422, 'advance_exceeds_balance')
        run = self.ok(self.api('post', '/api/hr/payroll', {'month': '2026-01', 'account_id': bank, 'date': '2026-01-31',
                      'items': [{'employee_id': emp['id'], 'bonus': 200, 'deduction': 50, 'advance_deduction': 300}]}), 201)
        self.assertEqual(run['total_net'], 2850.0)
        tb = self.tb()
        self.assertEqual((tb['5200'], tb['1150'], tb['1010']), ((3150.0, 0), (200.0, 0), (0, 2850.0)))
        self.assertEqual(self.ok(self.api('get', f"/api/employees/{emp['id']}"))['advance_balance'], 200.0)
        self.err(self.api('post', '/api/hr/payroll', {'month': '2026-01', 'account_id': bank}), 409, 'payroll_exists')
        self.ok(self.api('post', f"/api/hr/payroll/{run['id']}/cancel"))
        self.assertEqual(self.ok(self.api('get', f"/api/employees/{emp['id']}"))['advance_balance'], 500.0)
        self.assertEqual(self.tb().get('5200', (0, 0))[0], self.tb().get('5200', (0, 0))[1])
        self.health()

    def test_manufacturing_cost_and_cancellation(self):
        r1, r2 = self.product('R1', kind='raw'), self.product('R2', kind='raw')
        fin = self.product('FIN', price=40)
        self.bill(self.supplier(), [{'product_id': r1['id'], 'qty': 100, 'unit_price': 2, 'tax_rate': 0},
                                    {'product_id': r2['id'], 'qty': 100, 'unit_price': 6, 'tax_rate': 0}])
        bom = self.ok(self.api('post', '/api/manufacturing/boms', {'name': 'Std', 'product_id': fin['id'],
                      'labor_cost': 3, 'overhead_cost': 1,
                      'components': [{'product_id': r1['id'], 'qty': 1}, {'product_id': r2['id'], 'qty': 1}]}), 201)
        self.assertEqual(bom['unit_cost'], 12.0)
        order = self.ok(self.api('post', '/api/manufacturing/orders', {'bom_id': bom['id'], 'qty': 10, 'warehouse_id': 1}), 201)
        self.assertEqual((order['material_cost'], order['labor_cost'], order['overhead_cost'], order['total_cost']),
                         (80.0, 30.0, 10.0, 120.0))
        self.assertEqual((self.stock(fin['id']), self.stock(r1['id']), self.stock(r2['id'])), (10, 90, 90))
        self.assertEqual(self.tb()['5900'], (0, 40.0))
        self.err(self.api('post', '/api/manufacturing/orders', {'bom_id': bom['id'], 'qty': 1000, 'warehouse_id': 1}), 409, 'insufficient_stock')
        self.ok(self.api('post', f"/api/manufacturing/orders/{order['id']}/cancel"))
        self.assertEqual((self.stock(fin['id']), self.stock(r1['id'])), (0, 100))
        self.health()

    def test_fixed_asset_depreciation(self):
        bank = self.account_id('1010')
        a = self.ok(self.api('post', '/api/accounting/assets', {'name': 'Van', 'cost': 1000, 'salvage': 0,
                    'useful_life_months': 3, 'purchase_date': '2026-01-10', 'account_id': bank}), 201)
        self.err(self.api('post', f"/api/accounting/assets/{a['id']}/depreciate", {'period': '2025-12'}), 422, 'before_purchase')
        amounts = [self.ok(self.api('post', f"/api/accounting/assets/{a['id']}/depreciate", {'period': p}))['amount']
                   for p in ('2026-01', '2026-02', '2026-03')]
        self.assertEqual(amounts, [333.33, 333.33, 333.34])            # last instalment absorbs the rounding
        self.err(self.api('post', f"/api/accounting/assets/{a['id']}/depreciate", {'period': '2026-02'}), 409, 'already_depreciated')
        asset = self.ok(self.api('get', '/api/accounting/assets'))['items'][0]
        self.assertEqual((asset['accumulated'], asset['book_value'], asset['status']), (1000.0, 0.0, 'fully_depreciated'))
        self.err(self.api('post', f"/api/accounting/assets/{a['id']}/depreciate", {'period': '2026-04'}), 409, 'fully_depreciated')
        self.health()

    def test_pos_checkout_and_walk_in_customer(self):
        p = self.product('POS1', price=25, barcode='123456')
        self.bill(self.supplier(), [{'product_id': p['id'], 'qty': 10, 'unit_price': 10}])
        found = self.ok(self.api('get', '/api/products?barcode=123456'))['items']
        self.assertEqual(len(found), 1)
        doc = self.ok(self.api('post', '/api/pos/checkout', {'lines': [{'product_id': p['id'], 'qty': 2}],
                      'method': 'cash'}), 201)
        self.assertEqual((doc['source'], doc['status'], doc['payment_status'], doc['partner_name']),
                         ('pos', 'posted', 'paid', 'Walk-in Customer'))
        self.assertEqual(doc['total'], 57.5)          # 2 x 25 + 15% VAT
        self.assertEqual(self.stock(p['id']), 8)
        self.err(self.api('post', '/api/pos/checkout', {'lines': []}), 422, 'no_lines')
        self.health()

    def test_lead_conversion_and_project_summary(self):
        lead = self.ok(self.api('post', '/api/leads', {'name': 'Lead One', 'phone': '1'}), 201)
        self.err(self.api('put', f"/api/leads/{lead['id']}", {'status': 'converted'}), 422)
        cid = self.ok(self.api('post', f"/api/crm/leads/{lead['id']}/convert"))['customer_id']
        self.assertEqual(self.ok(self.api('get', f'/api/customers/{cid}'))['name'], 'Lead One')
        self.err(self.api('post', f"/api/crm/leads/{lead['id']}/convert"), 409, 'already_converted')
        emp = self.ok(self.api('post', '/api/employees', {'name': 'Dev', 'salary': 1760}), 201)
        proj = self.ok(self.api('post', '/api/projects', {'name': 'P', 'budget': 1000}), 201)
        self.ok(self.api('post', '/api/time_logs', {'employee_id': emp['id'], 'project_id': proj['id'], 'hours': 10, 'date': '2026-01-05'}), 201)
        s = self.ok(self.api('get', f"/api/projects/{proj['id']}/summary"))
        self.assertEqual((s['hours'], s['actual_cost'], s['budget_remaining']), (10.0, 100.0, 900.0))   # 1760/176 = 10 per hour


# ====================================================================== import / export
class DataTests(Base):
    def upload(self, path, text, name='data.csv', **form):
        return self.api('post', path, data={'file': (io.BytesIO(text.encode('utf-8')), name), **form},
                        content_type='multipart/form-data')

    def test_import_is_all_or_nothing_and_supports_dry_run(self):
        good = 'sku,name,price,cost,category,opening_qty,opening_cost\nA1,Alpha,10,5,Tools,4,5\nA2,Beta,20,8,Tools,,\n'
        bad = good + 'A3,Gamma,-1,2,Tools,,\n'
        e = self.err(self.upload('/api/data/import/products', bad), 422, 'import_failed')
        self.assertEqual(e['details'][0]['row'], 4)
        self.assertEqual(self.ok(self.api('get', '/api/products'))['total'], 0)
        dry = self.ok(self.upload('/api/data/import/products', good, dry_run='1'))
        self.assertEqual((dry['dry_run'], dry['created']), (True, 2))
        self.assertEqual(self.ok(self.api('get', '/api/products'))['total'], 0)
        done = self.ok(self.upload('/api/data/import/products', good))
        self.assertEqual((done['created'], done['updated']), (2, 0))
        again = self.ok(self.upload('/api/data/import/products', 'sku,name,price\nA1,Alpha 2,11\n'))
        self.assertEqual((again['created'], again['updated']), (0, 1))
        alpha = next(p for p in self.ok(self.api('get', '/api/products'))['items'] if p['sku'] == 'A1')
        self.assertEqual((alpha['name'], alpha['price'], alpha['stock_qty'], alpha['category_name']), ('Alpha 2', 11.0, 4.0, 'Tools'))
        self.health()

    def test_export_csv_xlsx_and_formula_injection_guard(self):
        self.product('=EVIL', price=1)
        r = self.api('get', '/api/data/export/products?format=csv')
        text = r.get_data(as_text=True)
        self.assertTrue(text.lstrip('\ufeff').startswith('sku,barcode,name'))
        self.assertIn("'=EVIL", text)
        r = self.api('get', '/api/data/export/products?format=xlsx')
        self.assertEqual(r.get_data()[:2], b'PK')
        viewer = self.make_user('viewer')
        self.err(self.api('get', '/api/data/export/products', token=viewer), 403)

    def test_customer_import_merges_by_name(self):
        self.supplier('Shared Co')
        res = self.ok(self.upload('/api/data/import/customers', 'name,phone\nShared Co,555\nNew One,777\n'))
        self.assertEqual((res['created'], res['updated']), (1, 1))
        self.assertEqual(self.ok(self.api('get', '/api/customers?q=Shared'))['items'][0]['phone'], '555')


# ====================================================================== tenant session and membership context
class TenantSessionTests(Base):
    def make_company(self, name, user_id=None, role='viewer'):
        with self.app.app_context():
            with db.transaction():
                company_id = tenancy.create_company(name, currency='LYD')
                if user_id is not None:
                    tenancy.add_membership(company_id, user_id, role, status='active')
        return company_id

    def test_single_membership_is_attached_to_session_and_listed(self):
        me = self.ok(self.api('get', '/api/auth/me'))['user']
        self.assertEqual(me['role'], 'admin')
        self.assertEqual(me['active_company']['name'], 'T Co')
        companies = self.ok(self.api('get', '/api/auth/companies'))
        self.assertEqual(len(companies['items']), 1)
        self.assertEqual(companies['active_company']['id'], me['active_company']['id'])
        self.assertTrue(companies['can_use_operations'])

    def test_switch_requires_an_active_membership_and_multi_company_ops_fail_closed(self):
        unassigned_company = self.make_company('Not My Company')
        self.err(self.api('post', '/api/auth/switch-company', {'company_id': unassigned_company}),
                 403, 'company_access_denied')
        self.err(self.api('get', '/api/products'), 503, 'tenant_isolation_pending')

    def test_membership_role_changes_with_selected_company(self):
        company_id = self.make_company('Second Company', user_id=1, role='viewer')
        switched = self.ok(self.api('post', '/api/auth/switch-company', {'company_id': company_id}))
        self.assertEqual(switched['active_company']['id'], company_id)
        me = self.ok(self.api('get', '/api/auth/me', token=switched['token']))['user']
        self.assertEqual(me['role'], 'viewer')
        self.assertEqual(me['active_company']['name'], 'Second Company')
        self.assertNotIn('*', me['permissions'])
        self.err(self.api('get', '/api/products', token=switched['token']), 503, 'tenant_isolation_pending')

    def test_client_company_header_cannot_override_signed_context(self):
        second_company = self.make_company('Second Company', user_id=1, role='viewer')
        response = self.c.get('/api/auth/me', headers={
            'Authorization': 'Bearer ' + self.token, 'X-Company-ID': str(second_company),
        })
        me = self.ok(response)['user']
        self.assertEqual(me['active_company']['id'], 1)

    def test_suspending_membership_invalidates_company_bound_token(self):
        with self.app.app_context():
            with db.transaction():
                db.execute("UPDATE company_memberships SET status='suspended' WHERE user_id=1")
        self.err(self.api('get', '/api/auth/me'), 401, 'unauthorized')

    def test_admin_user_creation_assigns_membership_in_active_company(self):
        created = self.ok(self.api('post', '/api/users', {
            'email': 'staff@example.test', 'name': 'Staff Member', 'password': 'Staff12345', 'role': 'viewer',
        }), 201)
        login = self.ok(self.c.post('/api/auth/login', json={
            'email': 'staff@example.test', 'password': 'Staff12345',
        }))
        me = self.ok(self.api('get', '/api/auth/me', token=login['token']))['user']
        self.assertEqual(me['id'], created['id'])
        self.assertEqual(me['role'], 'viewer')
        self.assertEqual(me['active_company']['name'], 'T Co')


# ====================================================================== randomised stress test
class StressTest(Base):
    def test_random_operations_always_leave_books_balanced(self):
        rng = random.Random(2026)
        wh1 = 1
        pids, raws = [], []
        for i in range(5):
            pids.append(self.product(f'F{i}', price=rng.randint(20, 90))['id'])
        for i in range(2):
            raws.append(self.product(f'RAW{i}', kind='raw', cost=3)['id'])
        wh2 = self.ok(self.api('post', '/api/warehouses', {'code': 'W2', 'name': 'W2'}), 201)['id']
        cust = [self.customer(f'C{i}', credit_limit=rng.choice([0, 0, 5000]))['id'] for i in range(3)]
        sup = [self.supplier(f'S{i}')['id'] for i in range(2)]
        cash, bank = self.account_id('1000'), self.account_id('1010')
        today = date.today()
        done = {}

        with self.app.app_context():
            fin = pids[0]
            bom = manufacturing.save_bom({'name': 'B', 'product_id': fin, 'labor_cost': 1, 'overhead_cost': 1,
                                          'components': [{'product_id': raws[0], 'qty': 2}, {'product_id': raws[1], 'qty': 1}]})
            d = lambda: (today - timedelta(days=rng.randint(0, 40))).isoformat()
            lines = lambda ids, **kw: [{'product_id': rng.choice(ids), 'qty': rng.randint(1, 15),
                                        'unit_price': rng.randint(2, 60), 'tax_rate': rng.choice([0, 5, 15]), **kw}
                                       for _ in range(rng.randint(1, 3))]

            def posted(types):
                marks = ','.join('?' * len(types))
                rows = db.q(f"SELECT * FROM trade_documents WHERE status='posted' AND doc_type IN ({marks})", types)
                return rng.choice(rows) if rows else None

            def op_bill():
                did = trade.save_draft('bill', {'partner_id': rng.choice(sup), 'date': d(), 'lines': lines(pids + raws)})
                trade.post(did, 'bill')

            def op_invoice():
                did = trade.save_draft('invoice', {'partner_id': rng.choice(cust), 'date': d(), 'lines': lines(pids)})
                trade.post(did, 'invoice')

            def op_pay():
                doc = posted(['invoice', 'bill', 'credit_note', 'debit_note'])
                if doc:
                    out = doc['total_cents'] - doc['paid_cents']
                    trade.add_payment(doc['id'], rng.choice([out, max(1, out // 2)]), rng.choice([cash, bank]), d())

            def op_cancel():
                doc = posted(['invoice', 'bill', 'credit_note', 'debit_note'])
                if doc:
                    trade.cancel(doc['id'])

            def op_cancel_payment():
                rows = db.q("SELECT id FROM payments WHERE status='posted'")
                if rows:
                    trade.cancel_payment(rng.choice(rows)['id'])

            def op_return():
                doc = posted(['invoice', 'bill'])
                if doc:
                    new = trade.create_return_from(doc['id'])
                    trade.post(new, trade.RETURN_TYPE[doc['doc_type']])

            def op_apply():
                cn = posted(['credit_note', 'debit_note'])
                if cn:
                    target = 'invoice' if cn['doc_type'] == 'credit_note' else 'bill'
                    rows = db.q("SELECT id FROM trade_documents WHERE status='posted' AND doc_type=? AND partner_id=?",
                                (target, cn['partner_id']))
                    if rows:
                        trade.apply_credit(cn['id'], rng.choice(rows)['id'], rng.randint(1, 5000))

            def op_adjust():
                inventory.adjust(rng.choice(pids + raws), rng.choice([wh1, wh2]), rng.choice([-5, -2, 3, 8]),
                                 d(), 'fuzz', lot_no=rng.choice([None, 'L1', 'L2']))

            def op_transfer():
                inventory.transfer(rng.choice(pids + raws), wh1, wh2, rng.randint(1, 6), d())

            def op_produce():
                manufacturing.produce(bom, rng.randint(1, 8), wh1, d())

            def op_cancel_production():
                rows = db.q("SELECT id FROM production_orders WHERE status='done'")
                if rows:
                    manufacturing.cancel_production(rng.choice(rows)['id'])

            def op_count():
                cid = inventory.create_count(wh1, d(), [rng.choice(pids)])
                for ln in inventory.get_count(cid)['lines']:
                    inventory.update_count(cid, [{'id': ln['id'], 'counted_qty': rng.randint(0, 20)}])
                inventory.post_count(cid)

            ops = [op_bill] * 4 + [op_invoice] * 6 + [op_pay] * 5 + [op_cancel] * 2 + [op_cancel_payment, op_return] * 2 + \
                  [op_apply, op_adjust, op_adjust, op_transfer, op_produce, op_produce, op_cancel_production, op_count]
            for _ in range(450):
                op = rng.choice(ops)
                try:
                    with db.transaction():
                        op()
                    done[op.__name__] = done.get(op.__name__, 0) + 1
                except ApiError:
                    pass          # business-rule rejection: the savepoint/transaction must leave no trace
            result = accounting.health_check()
            self.assertTrue(result['ok'], [c for c in result['checks'] if not c['ok']])
        self.assertGreater(sum(done.values()), 150, done)
        for name in ('op_bill', 'op_invoice', 'op_pay', 'op_produce'):
            self.assertGreater(done.get(name, 0), 5, f'{name} should succeed regularly: {done}')


if __name__ == '__main__':
    unittest.main()
