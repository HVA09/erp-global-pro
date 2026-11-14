"""Base data (roles, chart of accounts, sequences) and an optional demo company."""
import json
import random
from datetime import date, timedelta

from . import db
from .services import sequences

ROLES = {
    'admin': ('Administrator', ['*']),
    'accountant': ('Accountant', ['dashboard.read', 'accounting.*', 'sales.*', 'purchases.*', 'payments.*',
                                  'customers.*', 'suppliers.*', 'products.read', 'inventory.read', 'hr.read',
                                  'reports.read', 'documents.*', 'data.*']),
    'sales': ('Sales', ['dashboard.read', 'pos.*', 'sales.read', 'sales.write', 'customers.*', 'products.read',
                        'inventory.read', 'crm.*', 'reports.read', 'documents.read']),
    'warehouse': ('Warehouse', ['dashboard.read', 'inventory.*', 'products.*', 'purchases.read', 'purchases.write',
                                'suppliers.read', 'manufacturing.*', 'maintenance.*', 'documents.read', 'reports.read']),
    'hr': ('HR', ['dashboard.read', 'hr.*', 'projects.*', 'documents.read', 'reports.read']),
    'viewer': ('Viewer', ['dashboard.read', 'sales.read', 'purchases.read', 'customers.read', 'suppliers.read',
                          'products.read', 'inventory.read', 'accounting.read', 'reports.read', 'crm.read', 'hr.read',
                          'projects.read', 'manufacturing.read', 'maintenance.read', 'documents.read']),
}

# code, name, type, subtype, system
CHART = [
    ('1000', 'Cash on Hand', 'asset', 'cash', 1), ('1010', 'Bank Account', 'asset', 'bank', 1),
    ('1100', 'Accounts Receivable', 'asset', 'receivable', 1), ('1150', 'Employee Advances', 'asset', 'advances', 1),
    ('1200', 'Inventory', 'asset', 'inventory', 1), ('1300', 'VAT Receivable (Input Tax)', 'asset', 'tax_receivable', 1),
    ('1500', 'Fixed Assets', 'asset', 'fixed_asset', 1), ('1590', 'Accumulated Depreciation', 'asset', 'accum_depr', 1),
    ('2000', 'Accounts Payable', 'liability', 'payable', 1), ('2100', 'VAT Payable (Output Tax)', 'liability', 'tax_payable', 1),
    ('3000', "Owner's Capital", 'equity', 'capital', 1), ('3900', 'Opening Balance Equity', 'equity', 'opening', 1),
    ('4000', 'Sales Revenue', 'revenue', 'sales', 1), ('4100', 'Sales Returns & Allowances', 'revenue', 'sales_returns', 1),
    ('4900', 'Other Income', 'revenue', 'other_income', 1),
    ('5000', 'Cost of Goods Sold', 'expense', 'cogs', 1), ('5200', 'Salaries & Wages', 'expense', 'payroll', 1),
    ('5300', 'Depreciation Expense', 'expense', 'depreciation', 1), ('5400', 'Inventory Adjustments', 'expense', 'inventory_adj', 1),
    ('5500', 'General & Administrative', 'expense', 'operating', 1), ('5600', 'Maintenance & Repairs', 'expense', 'operating', 1),
    ('5700', 'Marketing & Advertising', 'expense', 'operating', 0), ('5800', 'Rent & Utilities', 'expense', 'operating', 0),
    ('5900', 'Production Costs Absorbed', 'expense', 'absorbed', 1),
]


def ensure_base_data():
    for name, (label, perms) in ROLES.items():
        db.execute('INSERT OR IGNORE INTO roles(name,label,permissions,is_system) VALUES(?,?,?,1)',
                   (name, label, json.dumps(perms)))
    # Roles are global identity metadata. Tenant-owned seed data is installed
    # only when exactly one company exists; multi-company operation remains
    # blocked until all services are tenant-aware.
    if db.scalar('SELECT COUNT(*) FROM companies') != 1:
        return
    company_id = db.scalar("SELECT id FROM companies WHERE status='active' ORDER BY id LIMIT 1")
    for name, (prefix, padding) in sequences.DEFAULT_SEQUENCES.items():
        db.execute('INSERT OR IGNORE INTO sequences(company_id,name,prefix,next_number,padding) VALUES(?,?,?,1,?)',
                   (company_id, name, prefix, padding))
    for code, name, typ, sub, system in CHART:
        db.execute('INSERT OR IGNORE INTO accounts(code,name,type,subtype,is_system) VALUES(?,?,?,?,?)',
                   (code, name, typ, sub, system))
    if not db.scalar('SELECT COUNT(*) FROM warehouses'):
        db.execute("INSERT INTO warehouses(code,name) VALUES('MAIN','Main Warehouse')")
    from .services import trade
    trade.walkin_customer_id()


def demo_allowed():
    return not db.scalar('SELECT COUNT(*) FROM products') and not db.scalar('SELECT COUNT(*) FROM trade_documents')


def seed_demo():
    """Create a realistic demo company by calling the real services (so every entry is balanced)."""
    from .errors import ApiError
    from .services import accounting, assets, hr, inventory, manufacturing, settings, trade
    rng = random.Random(42)
    t = date.today()
    d = lambda n: (t - timedelta(days=n)).isoformat()
    ex = db.execute
    acc = accounting.resolve_account
    settings.save_settings({'company': 'Demo Trading Co.', 'tax': 15, 'tax_number': '300012345600003',
                            'currency': 'USD', 'address': '12 Market Street', 'phone': '+1 555 0100',
                            'email': 'info@demo.example'})
    accounting.post_entry(d(60), 'Owner capital contribution', [
        {'account': 'bank', 'debit': 20000000}, {'account': 'cash', 'debit': 500000},
        {'account': 'equity', 'credit': 20500000}], 'manual')

    cat = {n: ex('INSERT INTO categories(name) VALUES(?)', (n,)) for n in
           ('Electronics', 'Accessories', 'Raw Materials', 'Services', 'Spare Parts')}
    sup = [ex('INSERT INTO partners(name,is_supplier,phone,email,city,payment_terms_days) VALUES(?,?,?,?,?,?)',
              (n, 1, ph, em, c, 30)) for n, ph, em, c in (
        ('Global Tech Distribution', '+1 555 0111', 'sales@globaltech.example', 'Dubai'),
        ('Prime Components Ltd', '+1 555 0112', 'orders@primecomp.example', 'Istanbul'),
        ('Office & Facilities Co', '+1 555 0113', 'billing@officefac.example', 'Cairo'))]
    cus = [ex('INSERT INTO partners(name,is_customer,phone,email,city,credit_limit_cents,payment_terms_days) '
              'VALUES(?,?,?,?,?,?,?)', (n, 1, ph, em, c, lim, terms)) for n, ph, em, c, lim, terms in (
        ('Al Noor Trading', '+1 555 0201', 'info@alnoor.example', 'Riyadh', 5000000, 30),
        ('Horizon Electronics', '+1 555 0202', 'buy@horizon.example', 'Jeddah', 3000000, 15),
        ('BlueSky Offices', '+1 555 0203', 'it@bluesky.example', 'Dubai', 0, 30),
        ('City Computers', '+1 555 0204', 'sales@citycomp.example', 'Amman', 2000000, 0),
        ('Delta Solutions', '+1 555 0205', 'hello@delta.example', 'Cairo', 1500000, 30))]

    def product(sku, name, kind, price, cost, cat_name, barcode=None, min_stock=0, unit='pcs'):
        return ex('INSERT INTO products(sku,barcode,name,category_id,kind,unit,price_cents,cost_cents,min_stock) '
                  'VALUES(?,?,?,?,?,?,?,?,?)', (sku, barcode, name, cat[cat_name], kind, unit, price, cost, min_stock))
    P = {
        'laptop': product('SKU-1001', 'Pro Laptop 15"', 'storable', 120000, 85000, 'Electronics', '6290001000011', 5),
        'mouse': product('SKU-1002', 'Wireless Mouse', 'storable', 2500, 1200, 'Accessories', '6290001000028', 20),
        'keyboard': product('SKU-1003', 'Mechanical Keyboard', 'storable', 8000, 4500, 'Accessories', '6290001000035', 10),
        'monitor': product('SKU-1004', '27" Monitor', 'storable', 32000, 21000, 'Electronics', '6290001000042', 5),
        'cable': product('SKU-1005', 'USB-C Cable 1m', 'storable', 900, 300, 'Accessories', '6290001000059', 50),
        'housing': product('SKU-2001', 'Plastic Housing', 'raw', 0, 200, 'Raw Materials', None, 50),
        'board': product('SKU-2002', 'Circuit Board', 'raw', 0, 600, 'Raw Materials', None, 50),
        'hub': product('SKU-2003', 'Smart Hub (assembled)', 'storable', 5900, 0, 'Electronics', '6290001000066', 10),
        'install': product('SRV-3001', 'Installation Service', 'service', 5000, 0, 'Services'),
        'toner': product('SKU-4001', 'Printer Toner (spare)', 'spare', 5500, 3000, 'Spare Parts', None, 5),
    }
    bank, cash = acc('bank'), acc('cash')

    def doc(kind, partner, when, lines, pay=None, pay_account=None):
        doc_id = trade.save_draft(kind, {'partner_id': partner, 'date': when, 'lines': lines})
        trade.post(doc_id, kind)
        if pay:
            total = db.scalar('SELECT total_cents FROM trade_documents WHERE id=?', (doc_id,))
            trade.add_payment(doc_id, total if pay == 'full' else int(total * pay), pay_account or bank, when,
                              'transfer')
        return doc_id

    doc('bill', sup[0], d(50), [
        {'product_id': P['laptop'], 'qty': 20, 'unit_price': 850}, {'product_id': P['monitor'], 'qty': 15, 'unit_price': 210},
        {'product_id': P['keyboard'], 'qty': 40, 'unit_price': 45}], pay='full')
    doc('bill', sup[1], d(45), [
        {'product_id': P['mouse'], 'qty': 100, 'unit_price': 12}, {'product_id': P['cable'], 'qty': 300, 'unit_price': 3},
        {'product_id': P['housing'], 'qty': 200, 'unit_price': 2}, {'product_id': P['board'], 'qty': 200, 'unit_price': 6},
        {'product_id': P['toner'], 'qty': 6, 'unit_price': 30, 'lot_no': 'T-A', 'expiry_date': d(-20)},
        {'product_id': P['toner'], 'qty': 10, 'unit_price': 30, 'lot_no': 'T-B', 'expiry_date': d(-400)}], pay=0.5)
    doc('bill', sup[2], d(5), [{'description': 'Office rent', 'qty': 1, 'unit_price': 1500, 'tax_rate': 0,
                                'account_id': acc('5800')}])

    for offset in sorted((rng.choice([rng.randint(0, 30), rng.randint(0, 12)]) for _ in range(46)), reverse=True):
        partner = rng.choice(cus)
        lines = [{'product_id': rng.choice([P['laptop'], P['mouse'], P['keyboard'], P['monitor'], P['cable']]),
                  'qty': rng.randint(1, 3)} for _ in range(rng.randint(1, 3))]
        if rng.random() < 0.3:
            lines.append({'product_id': P['install'], 'qty': 1})
        try:
            with db.transaction():
                pay = rng.choice(['full', 'full', 0.4, None])
                doc('invoice', partner, d(offset), lines, pay, cash if rng.random() < 0.3 else bank)
        except ApiError:
            continue
    for i in range(6):
        try:
            with db.transaction():
                doc_id = trade.save_draft('invoice', {'partner_id': trade.walkin_customer_id(), 'date': d(i),
                                                      'lines': [{'product_id': rng.choice([P['mouse'], P['cable']]),
                                                                 'qty': rng.randint(1, 4)}]}, source='pos')
                trade.post(doc_id, 'invoice')
                trade.add_payment(doc_id, db.scalar('SELECT total_cents FROM trade_documents WHERE id=?', (doc_id,)),
                                  cash, d(i), 'cash')
        except ApiError:
            continue
    first = db.one("SELECT id FROM trade_documents WHERE doc_type='invoice' AND status='posted' AND partner_id!=? "
                   "AND payment_status='paid' ORDER BY id LIMIT 1", (trade.walkin_customer_id(),))
    if first:
        cn = trade.create_return_from(first['id'])
        ex('UPDATE trade_documents SET date=? WHERE id=?', (d(3), cn))
        trade.post(cn, 'credit_note')

    manufacturing_bom = manufacturing.save_bom({
        'name': 'Smart Hub standard', 'product_id': P['hub'], 'labor_cost': 3, 'overhead_cost': 1,
        'components': [{'product_id': P['housing'], 'qty': 1}, {'product_id': P['board'], 'qty': 1}]})
    manufacturing.produce(manufacturing_bom, 50, db.scalar('SELECT id FROM warehouses ORDER BY id LIMIT 1'), d(20))
    branch = ex("INSERT INTO warehouses(code,name) VALUES('BR1','Branch Warehouse')")
    inventory.transfer(P['mouse'], db.scalar('SELECT id FROM warehouses ORDER BY id LIMIT 1'), branch, 10, d(10),
                       'Stock for branch')

    depts = {n: ex('INSERT INTO departments(name) VALUES(?)', (n,)) for n in ('Sales', 'Operations', 'Administration')}
    emps = [ex('INSERT INTO employees(name,department_id,position,salary_cents,phone,hire_date) VALUES(?,?,?,?,?,?)',
               (n, depts[dp], pos, sal, ph, d(400 + i * 30))) for i, (n, dp, pos, sal, ph) in enumerate((
        ('Omar Hassan', 'Sales', 'Sales Manager', 450000, '+1 555 0301'),
        ('Layla Ahmed', 'Sales', 'Sales Representative', 280000, '+1 555 0302'),
        ('Khaled Saeed', 'Operations', 'Warehouse Keeper', 240000, '+1 555 0303'),
        ('Sara Ali', 'Administration', 'Accountant', 380000, '+1 555 0304'),
        ('Youssef Nabil', 'Operations', 'Technician', 260000, '+1 555 0305')))]
    for e in emps:
        for k in range(5):
            ex('INSERT INTO attendance(employee_id,date,status) VALUES(?,?,?)',
               (e, d(k), rng.choice(['present', 'present', 'present', 'late'])))
    ex('INSERT INTO leave_requests(employee_id,type,start_date,end_date,note) VALUES(?,?,?,?,?)',
       (emps[1], 'annual', d(-7), d(-10), 'Family trip'))
    hr.add_advance(emps[2], 50000, cash, d(12), 'Personal advance')
    prev = (t.replace(day=1) - timedelta(days=1))
    hr.run_payroll(prev.strftime('%Y-%m'), prev.isoformat(), bank, [
        {'employee_id': emps[2], 'advance_deduction': 250}, {'employee_id': emps[0], 'bonus': 300},
        {'employee_id': emps[1]}, {'employee_id': emps[3]}, {'employee_id': emps[4]}])

    for n, ph, src, st in (('Fatima Al-Zahra', '+1 555 0401', 'Website', 'new'), ('Tarek Mansour', '+1 555 0402', 'Referral', 'contacted'),
                           ('Nadia Karim', '+1 555 0403', 'Exhibition', 'qualified'), ('Hamza Idris', '+1 555 0404', 'Social media', 'new')):
        ex('INSERT INTO leads(name,phone,source,status) VALUES(?,?,?,?)', (n, ph, src, st))
    for title, partner, val, stage, prob, close in (
            ('Office refresh - 30 laptops', cus[2], 3600000, 'negotiation', 60, d(-15)),
            ('Monitor bulk order', cus[0], 960000, 'proposal', 40, d(-25)),
            ('Smart Hub distribution', cus[1], 1500000, 'prospecting', 15, d(-45)),
            ('Accessories contract', cus[3], 400000, 'won', 100, d(2))):
        ex('INSERT INTO opportunities(title,partner_id,value_cents,stage,probability,expected_close) VALUES(?,?,?,?,?,?)',
           (title, partner, val, stage, prob, close))
    for typ, who, when, notes, done in (('call', 'Horizon Electronics', d(1), 'Discuss monthly volume', 1),
                                        ('meeting', 'BlueSky Offices', d(-2), 'Present laptop proposal', 0),
                                        ('email', 'Delta Solutions', d(0), 'Send price list', 0)):
        ex('INSERT INTO activities(type,contact_name,date,notes,done) VALUES(?,?,?,?,?)', (typ, who, when, notes, done))
    ex('INSERT INTO campaigns(name,channel,start_date,end_date,budget_cents) VALUES(?,?,?,?,?)',
       ('Spring Promotion', 'Email + Social', d(20), d(-10), 300000))

    projects = [ex('INSERT INTO projects(name,partner_id,start_date,end_date,budget_cents,status) VALUES(?,?,?,?,?,?)', v)
                for v in (('Office network rollout', cus[2], d(30), d(-30), 2500000, 'active'),
                          ('Warehouse automation study', None, d(10), d(-60), 800000, 'planned'))]
    tasks = [ex('INSERT INTO tasks(project_id,title,assignee_id,due_date,priority,status) VALUES(?,?,?,?,?,?)', v)
             for v in ((projects[0], 'Site survey', emps[4], d(20), 'high', 'done'),
                       (projects[0], 'Install switches', emps[4], d(-5), 'high', 'doing'),
                       (projects[0], 'Client training', emps[0], d(-20), 'medium', 'todo'),
                       (projects[1], 'Requirements workshop', emps[3], d(-10), 'medium', 'todo'))]
    for emp, proj, task, hrs, when in ((emps[4], projects[0], tasks[0], 16, d(22)), (emps[4], projects[0], tasks[1], 12, d(3)),
                                       (emps[0], projects[0], tasks[2], 4, d(1))):
        ex('INSERT INTO time_logs(employee_id,project_id,task_id,hours,date) VALUES(?,?,?,?,?)',
           (emp, proj, task, hrs, when))

    for asset, issue, pr, st in (('Delivery Van', 'Brake inspection', 'high', 'open'),
                                 ('Warehouse Forklift', 'Hydraulic leak', 'critical', 'in_progress'),
                                 ('Office Printer', 'Paper jam', 'low', 'resolved')):
        ex('INSERT INTO maintenance_requests(asset_name,issue,priority,status,reported_date) VALUES(?,?,?,?,?)',
           (asset, issue, pr, st, d(3)))
    for asset, task, freq, last in (('Delivery Van', 'Oil change', 90, d(100)), ('Warehouse Forklift', 'Safety inspection', 30, d(10)),
                                    ('Air conditioning', 'Filter cleaning', 60, d(20))):
        ex('INSERT INTO maintenance_schedules(asset_name,task,frequency_days,last_done) VALUES(?,?,?,?)',
           (asset, task, freq, last))

    van = assets.create_asset({'name': 'Delivery Van', 'cost': 18000, 'salvage': 3000, 'useful_life_months': 60,
                               'purchase_date': d(70), 'account_id': bank})
    for back in (2, 1):
        month = (t.replace(day=1) - timedelta(days=30 * (back - 1) + (1 if back == 1 else 0))).strftime('%Y-%m')
        try:
            with db.transaction():
                assets.depreciate(van, month)
        except ApiError:
            pass
    ex('INSERT INTO files(title,category,related_to,link,note,date) VALUES(?,?,?,?,?,?)',
       ('Trade license', 'Legal', 'Company', 'https://example.com/license.pdf', 'Renew yearly', d(100)))
    ex("INSERT INTO cost_centers(name) VALUES('Head Office')")
    ex("INSERT INTO cost_centers(name) VALUES('Warehouse')")
