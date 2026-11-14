"""Declarative definitions of the simple record types (everything that is plain create/read/update/delete)."""
from . import db
from .crud import F, Resource
from .errors import ApiError
from .money import to_cents
from .services import file_storage, inventory, settings, tenancy
from .util import today

ACTIVE = lambda: F('is_active', 'bool', default=True)
STATUS = lambda *c, default=None: F('status', 'choice', choices=list(c), default=default or c[0])


# --------------------------------------------------------------------------- partners
def _partner_fields():
    return [F('name', required=True, maxlen=200), F('phone', maxlen=40), F('email', maxlen=200),
            F('address', maxlen=300), F('city', maxlen=100), F('country', maxlen=100), F('tax_number', maxlen=50),
            F('credit_limit', 'money', default=0), F('payment_terms_days', 'int', default=0, min=0, max=365),
            F('notes', 'text', maxlen=2000), ACTIVE()]


def _balance_select(plus, minus):
    return ("SELECT t.*, COALESCE((SELECT SUM(CASE d.doc_type WHEN '%s' THEN d.total_cents-d.paid_cents "
            "ELSE -(d.total_cents-d.paid_cents) END) FROM trade_documents d WHERE d.partner_id=t.id "
            "AND d.company_id=t.company_id AND d.status='posted' AND d.doc_type IN ('%s','%s')),0) AS balance_cents FROM partners t"
            % (plus, plus, minus))


def _partner_before_delete(row):
    if row['is_system']:
        raise ApiError('system_record', 'This record is required by the system.', 409)


CUSTOMERS = Resource('customers', 'partners', _partner_fields(), 'customers',
                     select=_balance_select('invoice', 'credit_note'), where='t.is_customer=1',
                     base_values={'is_customer': 1}, search=('t.name', 't.phone', 't.email', 't.city'),
                     filters=('is_active', 'city'), sort_extra={'balance': 'balance_cents'},
                     before_delete=_partner_before_delete)
SUPPLIERS = Resource('suppliers', 'partners', _partner_fields(), 'suppliers',
                     select=_balance_select('bill', 'debit_note'), where='t.is_supplier=1',
                     base_values={'is_supplier': 1}, search=('t.name', 't.phone', 't.email', 't.city'),
                     filters=('is_active', 'city'), sort_extra={'balance': 'balance_cents'},
                     before_delete=_partner_before_delete)


# --------------------------------------------------------------------------- products & stock
def _product_before_save(data, existing, raw):
    if existing and data.get('kind') == 'service' and existing['kind'] != 'service':
        if db.scalar('SELECT COALESCE(SUM(ABS(qty)),0) FROM stock_levels WHERE product_id=? AND company_id=?',
                       (existing['id'], existing['company_id'])) > 1e-9:
            raise ApiError('has_stock', 'Remove the stock before turning this item into a service.', 409)


def _product_after_save(rid, data, existing, raw):
    if existing is not None or raw.get('opening_qty') in (None, '', 0, '0'):
        return
    try:
        qty = float(raw['opening_qty'])
    except (TypeError, ValueError):
        raise ApiError('validation', 'Invalid opening quantity.', 422, details={'opening_qty': 'invalid_number'})
    if qty > 0 and data['kind'] != 'service':
        company_id = tenancy.current_company_id()
        wid = int(raw.get('warehouse_id') or settings.default_warehouse_id(company_id) or 0)
        cost = to_cents(raw['opening_cost']) if raw.get('opening_cost') not in (None, '') else data.get('cost_cents', 0)
        inventory.opening_stock(rid, wid, qty, cost, company_id=company_id)


PRODUCTS = Resource(
    'products', 'products',
    [F('sku', required=True, maxlen=60), F('barcode', maxlen=60), F('name', required=True, maxlen=200),
     F('category_id', 'ref', ref='categories'),
     F('kind', 'choice', choices=['storable', 'raw', 'spare', 'service'], default='storable'),
     F('unit', maxlen=20, default='pcs'), F('price', 'money', default=0), F('cost', 'money', default=0),
     F('tax_rate', 'float', min=0, max=100), F('min_stock', 'float', default=0, min=0),
     F('notes', 'text', maxlen=2000), ACTIVE()], 'products',
    select=("SELECT t.*, c.name AS category_name, "
            "COALESCE((SELECT SUM(s.qty) FROM stock_levels s WHERE s.product_id=t.id AND s.company_id=t.company_id),0) AS stock_qty, "
            "COALESCE((SELECT SUM(s.value_cents) FROM stock_levels s WHERE s.product_id=t.id AND s.company_id=t.company_id),0) AS stock_value_cents "
            "FROM products t LEFT JOIN categories c ON c.id=t.category_id AND c.company_id=t.company_id"),
    search=('t.name', 't.sku', 't.barcode'), filters=('category_id', 'kind', 'is_active', 'barcode'),
    flags={'low_stock': ("t.kind IN ('storable','raw','spare') AND "
                         "COALESCE((SELECT SUM(s.qty) FROM stock_levels s WHERE s.product_id=t.id AND s.company_id=t.company_id),0) <= t.min_stock")},
    sort_extra={'stock_qty': 'stock_qty', 'category': 'category_name'}, order='t.name ASC',
    before_save=_product_before_save, after_save=_product_after_save)

CATEGORIES = Resource('categories', 'categories', [F('name', required=True, maxlen=100)], 'products',
                      search=('t.name',), order='t.name ASC')
WAREHOUSES = Resource('warehouses', 'warehouses',
                      [F('code', required=True, maxlen=20), F('name', required=True, maxlen=100), ACTIVE()],
                      'inventory', search=('t.name', 't.code'), filters=('is_active',), order='t.id ASC')


# --------------------------------------------------------------------------- accounting masters
def _account_before_save(data, existing, raw):
    if existing and existing['is_system']:
        for k in ('code', 'type', 'subtype'):
            if k in data and data[k] != existing[k]:
                raise ApiError('system_account', 'System accounts cannot change code, type or subtype.', 409)
    sub = data.get('subtype', existing['subtype'] if existing else None)
    typ = data.get('type', existing['type'] if existing else None)
    if not (existing and existing['is_system']):
        if sub not in (None, 'cash', 'bank', 'operating') and not (existing and sub == existing['subtype']):
            raise ApiError('invalid_choice', 'Subtype must be cash, bank or operating.', 422,
                           details={'subtype': 'invalid_choice'})
        if sub in ('cash', 'bank') and typ != 'asset':
            raise ApiError('invalid_choice', 'Cash and bank accounts must be assets.', 422,
                           details={'type': 'invalid_choice'})
    if existing and 'type' in data and data['type'] != existing['type'] and db.scalar(
            'SELECT COUNT(*) FROM journal_lines WHERE account_id=? AND company_id=?', (existing['id'], existing['company_id'])):
        raise ApiError('account_has_entries', 'This account already has entries; its type cannot change.', 409)


def _account_before_delete(row):
    if row['is_system']:
        raise ApiError('system_account', 'System accounts cannot be deleted.', 409)


ACCOUNTS = Resource(
    'accounts', 'accounts',
    [F('code', required=True, maxlen=20),
     F('name', required=True, maxlen=150),
     F('type', 'choice', required=True, choices=['asset', 'liability', 'equity', 'revenue', 'expense']),
     F('subtype', maxlen=30), F('parent_id', 'ref', ref='accounts'), ACTIVE()], 'accounting',
    select=("SELECT t.*, pa.name AS parent_name, COALESCE((SELECT SUM(l.debit_cents-l.credit_cents) "
            "FROM journal_lines l WHERE l.account_id=t.id AND l.company_id=t.company_id),0) AS net_cents FROM accounts t "
            "LEFT JOIN accounts pa ON pa.id=t.parent_id AND pa.company_id=t.company_id"),
    search=('t.code', 't.name'), filters=('type', 'subtype', 'is_active'), order='t.code ASC',
    before_save=_account_before_save, before_delete=_account_before_delete)
COST_CENTERS = Resource('cost_centers', 'cost_centers', [F('name', required=True, maxlen=100), ACTIVE()],
                        'accounting', search=('t.name',), filters=('is_active',), order='t.name ASC')


# --------------------------------------------------------------------------- HR
def _leave_before_save(data, existing, raw):
    start = data.get('start_date') or (existing or {}).get('start_date')
    end = data.get('end_date') or (existing or {}).get('end_date')
    if start and end and end < start:
        raise ApiError('invalid_date_range', 'End date cannot be before the start date.', 422,
                       details={'end_date': 'invalid_date'})


DEPARTMENTS = Resource('departments', 'departments', [F('name', required=True, maxlen=100)], 'hr',
                       search=('t.name',), order='t.name ASC')
EMPLOYEES = Resource(
    'employees', 'employees',
    [F('name', required=True, maxlen=150), F('department_id', 'ref', ref='departments'), F('position', maxlen=100),
     F('salary', 'money', default=0), F('phone', maxlen=40), F('email', maxlen=200), F('hire_date', 'date'),
     STATUS('active', 'inactive')], 'hr',
    select='SELECT t.*, d.name AS department_name FROM employees t LEFT JOIN departments d ON d.id=t.department_id AND d.company_id=t.company_id',
    search=('t.name', 't.position', 't.phone'), filters=('department_id', 'status'), order='t.name ASC')
ATTENDANCE = Resource(
    'attendance', 'attendance',
    [F('employee_id', 'ref', required=True, ref='employees'), F('date', 'date', required=True),
     F('status', 'choice', required=True, choices=['present', 'late', 'absent', 'leave'])], 'hr',
    select='SELECT t.*, e.name AS employee_name FROM attendance t JOIN employees e ON e.id=t.employee_id AND e.company_id=t.company_id',
    search=('e.name',), filters=('employee_id', 'status'), date_field='date', order='t.date DESC, t.id DESC')
LEAVES = Resource(
    'leave_requests', 'leave_requests',
    [F('employee_id', 'ref', required=True, ref='employees'),
     F('type', 'choice', choices=['annual', 'sick', 'unpaid', 'other'], default='annual'),
     F('start_date', 'date', required=True), F('end_date', 'date', required=True),
     STATUS('pending', 'approved', 'rejected'), F('note', 'text', maxlen=1000)], 'hr',
    select='SELECT t.*, e.name AS employee_name FROM leave_requests t JOIN employees e ON e.id=t.employee_id AND e.company_id=t.company_id',
    search=('e.name',), filters=('employee_id', 'status', 'type'), before_save=_leave_before_save)

# --------------------------------------------------------------------------- projects
PROJECTS = Resource(
    'projects', 'projects',
    [F('name', required=True, maxlen=200), F('partner_id', 'ref', ref='partners'), F('start_date', 'date'),
     F('end_date', 'date'), F('budget', 'money', default=0), STATUS('planned', 'active', 'done', 'cancelled'),
     F('notes', 'text', maxlen=2000)], 'projects',
    select=("SELECT t.*, p.name AS partner_name, "
            "COALESCE((SELECT SUM(tl.hours) FROM time_logs tl WHERE tl.project_id=t.id AND tl.company_id=t.company_id),0) AS hours, "
            "CAST(ROUND(COALESCE((SELECT SUM(tl.hours*e.salary_cents/176.0) FROM time_logs tl "
            "JOIN employees e ON e.id=tl.employee_id AND e.company_id=tl.company_id WHERE tl.project_id=t.id AND tl.company_id=t.company_id),0)) AS INTEGER) AS actual_cost_cents, "
            "(SELECT COUNT(*) FROM tasks k WHERE k.project_id=t.id AND k.company_id=t.company_id) AS tasks_total, "
            "(SELECT COUNT(*) FROM tasks k WHERE k.project_id=t.id AND k.company_id=t.company_id AND k.status='done') AS tasks_done "
            "FROM projects t LEFT JOIN partners p ON p.id=t.partner_id AND p.company_id=t.company_id"),
    search=('t.name', 'p.name'), filters=('status',))
TASKS = Resource(
    'tasks', 'tasks',
    [F('project_id', 'ref', required=True, ref='projects'), F('title', required=True, maxlen=200),
     F('assignee_id', 'ref', ref='employees'), F('due_date', 'date'),
     F('priority', 'choice', choices=['low', 'medium', 'high'], default='medium'),
     STATUS('todo', 'doing', 'done')], 'projects',
    select=("SELECT t.*, p.name AS project_name, e.name AS assignee_name FROM tasks t "
            "JOIN projects p ON p.id=t.project_id AND p.company_id=t.company_id LEFT JOIN employees e ON e.id=t.assignee_id AND e.company_id=t.company_id"),
    search=('t.title', 'p.name'), filters=('project_id', 'status', 'assignee_id', 'priority'))
TIME_LOGS = Resource(
    'time_logs', 'time_logs',
    [F('employee_id', 'ref', required=True, ref='employees'), F('project_id', 'ref', required=True, ref='projects'),
     F('task_id', 'ref', ref='tasks'), F('hours', 'float', required=True, min=0.01, max=24),
     F('date', 'date', required=True), F('note', 'text', maxlen=500)], 'projects',
    select=("SELECT t.*, e.name AS employee_name, p.name AS project_name, k.title AS task_title FROM time_logs t "
            "JOIN employees e ON e.id=t.employee_id AND e.company_id=t.company_id JOIN projects p ON p.id=t.project_id AND p.company_id=t.company_id "
            "LEFT JOIN tasks k ON k.id=t.task_id AND k.company_id=t.company_id"),
    search=('e.name', 'p.name'), filters=('employee_id', 'project_id'), date_field='date',
    order='t.date DESC, t.id DESC')


# --------------------------------------------------------------------------- CRM
def _lead_before_save(data, existing, raw):
    if data.get('status') == 'converted' and not (existing and existing['status'] == 'converted'):
        raise ApiError('invalid_choice', 'Use the convert action to convert a lead.', 422,
                       details={'status': 'invalid_choice'})


def _opportunity_before_save(data, existing, raw):
    if 'probability' not in raw:
        if data.get('stage') == 'won':
            data['probability'] = 100
        elif data.get('stage') == 'lost':
            data['probability'] = 0


LEADS = Resource(
    'leads', 'leads',
    [F('name', required=True, maxlen=150), F('phone', maxlen=40), F('email', maxlen=200), F('source', maxlen=100),
     F('notes', 'text', maxlen=2000),
     F('status', 'choice', choices=['new', 'contacted', 'qualified', 'lost', 'converted'], default='new')], 'crm',
    search=('t.name', 't.phone', 't.email'), filters=('status',), before_save=_lead_before_save)
OPPORTUNITIES = Resource(
    'opportunities', 'opportunities',
    [F('title', required=True, maxlen=200), F('partner_id', 'ref', ref='partners'), F('contact_name', maxlen=150),
     F('value', 'money', default=0), F('expected_close', 'date'),
     F('stage', 'choice', choices=['prospecting', 'proposal', 'negotiation', 'won', 'lost'], default='prospecting'),
     F('probability', 'int', default=10, min=0, max=100)], 'crm',
    select='SELECT t.*, p.name AS partner_name FROM opportunities t LEFT JOIN partners p ON p.id=t.partner_id AND p.company_id=t.company_id',
    search=('t.title', 'p.name', 't.contact_name'), filters=('stage',), before_save=_opportunity_before_save)
ACTIVITIES = Resource(
    'activities', 'activities',
    [F('type', 'choice', choices=['call', 'meeting', 'email', 'visit', 'task'], default='call'),
     F('contact_name', maxlen=150), F('date', 'date', required=True), F('notes', 'text', maxlen=1000),
     F('done', 'bool', default=False)], 'crm', search=('t.contact_name', 't.notes'), filters=('type', 'done'),
    date_field='date', order='t.date DESC, t.id DESC')
CAMPAIGNS = Resource(
    'campaigns', 'campaigns',
    [F('name', required=True, maxlen=150), F('channel', maxlen=100), F('start_date', 'date'), F('end_date', 'date'),
     F('budget', 'money', default=0), F('notes', 'text', maxlen=1000)], 'crm', search=('t.name', 't.channel'))


# --------------------------------------------------------------------------- maintenance & documents
def _request_before_save(data, existing, raw):
    if existing is None and not data.get('reported_date'):
        data['reported_date'] = today()
    status = data.get('status') or (existing or {}).get('status')
    if status in ('resolved', 'closed') and not data.get('resolved_date') and not (existing or {}).get('resolved_date'):
        data['resolved_date'] = today()


MAINTENANCE_REQUESTS = Resource(
    'maintenance_requests', 'maintenance_requests',
    [F('asset_name', required=True, maxlen=150), F('issue', required=True, maxlen=500),
     F('priority', 'choice', choices=['low', 'medium', 'high', 'critical'], default='medium'),
     STATUS('open', 'in_progress', 'resolved', 'closed'), F('reported_date', 'date'), F('resolved_date', 'date'),
     F('cost', 'money', default=0), F('notes', 'text', maxlen=1000)], 'maintenance',
    search=('t.asset_name', 't.issue'), filters=('status', 'priority'), before_save=_request_before_save)
MAINTENANCE_SCHEDULES = Resource(
    'maintenance_schedules', 'maintenance_schedules',
    [F('asset_name', required=True, maxlen=150), F('task', required=True, maxlen=200),
     F('frequency_days', 'int', required=True, min=1, max=3650), F('last_done', 'date')], 'maintenance',
    select=("SELECT t.*, date(COALESCE(t.last_done, date(t.created_at)), '+'||t.frequency_days||' days') AS next_due, "
            "(date(COALESCE(t.last_done, date(t.created_at)), '+'||t.frequency_days||' days') < date('now')) AS overdue "
            "FROM maintenance_schedules t"),
    search=('t.asset_name', 't.task'), sort_extra={'next_due': 'next_due'}, order='next_due ASC')
def _file_metadata_before_save(data, existing, raw):
    # Legacy document links are opened by the frontend in a new tab.
    if 'link' in data and data.get('link'):
        data['link'] = file_storage.validate_legacy_link(data['link'])


FILES = Resource(
    'files', 'files',
    [F('title', required=True, maxlen=200), F('category', maxlen=100), F('related_to', maxlen=150),
     F('link', maxlen=500), F('note', 'text', maxlen=1000), F('date', 'date')], 'documents',
    search=('t.title', 't.category', 't.related_to'), filters=('category',),
    before_save=_file_metadata_before_save)

ALL = [CUSTOMERS, SUPPLIERS, PRODUCTS, CATEGORIES, WAREHOUSES, ACCOUNTS, COST_CENTERS, DEPARTMENTS, EMPLOYEES,
       ATTENDANCE, LEAVES, PROJECTS, TASKS, TIME_LOGS, LEADS, OPPORTUNITIES, ACTIVITIES, CAMPAIGNS,
       MAINTENANCE_REQUESTS, MAINTENANCE_SCHEDULES, FILES]
