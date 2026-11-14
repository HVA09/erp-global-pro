"""Company-scoped CSV / Excel export and import for master data."""
import csv
import io
from datetime import date, datetime

from flask import Blueprint, Response, g, request

from .. import audit, db
from ..crud import serialize
from ..errors import ApiError
from ..security import has_perm, require
from .. import resources as res
from .common import body

bp = Blueprint('data', __name__, url_prefix='/api/data')

# Every query is constrained by a trusted company id supplied by export().
EXPORTS = {
    'products': ('products.read', ['sku', 'barcode', 'name', 'category', 'kind', 'unit', 'price', 'cost', 'tax_rate',
                                   'min_stock', 'stock_qty'],
                 "SELECT p.sku, p.barcode, p.name, c.name AS category, p.kind, p.unit, p.price_cents, p.cost_cents, "
                 "p.tax_rate, p.min_stock, COALESCE((SELECT SUM(qty) FROM stock_levels s "
                 "WHERE s.product_id=p.id AND s.company_id=p.company_id),0) AS stock_qty "
                 "FROM products p LEFT JOIN categories c ON c.id=p.category_id AND c.company_id=p.company_id "
                 "WHERE p.company_id=? ORDER BY p.name"),
    'customers': ('customers.read', ['name', 'phone', 'email', 'address', 'city', 'country', 'tax_number',
                                     'credit_limit', 'payment_terms_days'],
                  "SELECT name, phone, email, address, city, country, tax_number, credit_limit_cents, "
                  "payment_terms_days FROM partners WHERE company_id=? AND is_customer=1 AND is_system=0 ORDER BY name"),
    'suppliers': ('suppliers.read', ['name', 'phone', 'email', 'address', 'city', 'country', 'tax_number',
                                     'credit_limit', 'payment_terms_days'],
                  "SELECT name, phone, email, address, city, country, tax_number, credit_limit_cents, "
                  "payment_terms_days FROM partners WHERE company_id=? AND is_supplier=1 ORDER BY name"),
    'invoices': ('sales.read', ['number', 'date', 'due_date', 'customer', 'status', 'payment_status', 'subtotal',
                                'tax', 'total', 'paid'],
                 "SELECT d.number, d.date, d.due_date, p.name AS customer, d.status, d.payment_status, "
                 "d.subtotal_cents, d.tax_cents, d.total_cents, d.paid_cents FROM trade_documents d "
                 "JOIN partners p ON p.id=d.partner_id AND p.company_id=d.company_id "
                 "WHERE d.company_id=? AND d.doc_type='invoice' ORDER BY d.date DESC, d.id DESC"),
    'bills': ('purchases.read', ['number', 'date', 'due_date', 'supplier', 'status', 'payment_status', 'subtotal',
                                 'tax', 'total', 'paid'],
              "SELECT d.number, d.date, d.due_date, p.name AS supplier, d.status, d.payment_status, "
              "d.subtotal_cents, d.tax_cents, d.total_cents, d.paid_cents FROM trade_documents d "
              "JOIN partners p ON p.id=d.partner_id AND p.company_id=d.company_id "
              "WHERE d.company_id=? AND d.doc_type='bill' ORDER BY d.date DESC, d.id DESC"),
    'journal': ('accounting.read', ['entry', 'date', 'memo', 'account_code', 'account_name', 'debit', 'credit'],
                "SELECT e.number AS entry, e.date, e.memo, a.code AS account_code, a.name AS account_name, "
                "l.debit_cents, l.credit_cents FROM journal_lines l "
                "JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id "
                "JOIN accounts a ON a.id=l.account_id AND a.company_id=l.company_id "
                "WHERE l.company_id=? ORDER BY e.date, e.id, l.id"),
    'employees': ('hr.read', ['name', 'department', 'position', 'salary', 'phone', 'email', 'hire_date', 'status'],
                  "SELECT e.name, d.name AS department, e.position, e.salary_cents, e.phone, e.email, e.hire_date, "
                  "e.status FROM employees e LEFT JOIN departments d "
                  "ON d.id=e.department_id AND d.company_id=e.company_id "
                  "WHERE e.company_id=? ORDER BY e.name"),
    'stock': ('inventory.read', ['sku', 'product', 'warehouse', 'qty', 'value'],
              "SELECT p.sku, p.name AS product, w.name AS warehouse, s.qty, s.value_cents FROM stock_levels s "
              "JOIN products p ON p.id=s.product_id AND p.company_id=s.company_id "
              "JOIN warehouses w ON w.id=s.warehouse_id AND w.company_id=s.company_id "
              "WHERE s.company_id=? AND s.qty != 0 ORDER BY p.name, w.name"),
}


def _safe(v):
    """Neutralise spreadsheet formula injection (cells starting with = + - @)."""
    if isinstance(v, str) and v[:1] in ('=', '+', '-', '@') and not v.replace('.', '', 1).lstrip('+-').isdigit():
        return "'" + v
    return v


def _company_id():
    company = g.get('company') or {}
    try:
        cid = int(company['id'])
    except (KeyError, TypeError, ValueError):
        raise ApiError('active_company_required', 'An active company is required.', 409)
    if cid <= 0:
        raise ApiError('active_company_required', 'An active company is required.', 409)
    return cid


@bp.get('/export/<name>')
@require('data.export')
def export(name):
    if name not in EXPORTS:
        raise ApiError('not_found', 'Unknown export.', 404)
    perm, cols, sql = EXPORTS[name]
    if not has_perm(g.user['permissions'], perm):
        raise ApiError('forbidden', f'Missing permission: {perm}', 403, params={'permission': perm})
    cid = _company_id()
    rows = serialize(db.q(sql, (cid,)))
    audit.log('export', name, None, {'rows': len(rows)})
    fmt = request.args.get('format', 'csv')
    if fmt == 'xlsx':
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = name[:30]
        ws.append(cols)
        for row in rows:
            ws.append([_safe(row.get(col)) for col in cols])
        out = io.BytesIO()
        wb.save(out)
        return Response(out.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                        headers={'Content-Disposition': f'attachment; filename={name}.xlsx'})
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(cols)
    for row in rows:
        writer.writerow([_safe(row.get(col)) for col in cols])
    return Response('\ufeff' + out.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': f'attachment; filename={name}.csv'})


# --------------------------------------------------------------------------- import
def _cell(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v.strip() if isinstance(v, str) else v


def _read_rows(upload):
    name = (upload.filename or '').lower()
    raw = upload.read()
    if name.endswith('.xlsx'):
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(raw), read_only=True, data_only=True).active
        table = [[_cell(c) for c in row] for row in ws.iter_rows(values_only=True)]
    elif name.endswith('.csv'):
        table = [[_cell(c) for c in row] for row in csv.reader(io.StringIO(raw.decode('utf-8-sig')))]
    else:
        raise ApiError('invalid_file', 'Upload a .csv or .xlsx file.', 422)
    if not table:
        raise ApiError('invalid_file', 'The file is empty.', 422)
    headers = [str(h or '').strip().lower() for h in table[0]]
    rows = [dict(zip(headers, row)) for row in table[1:] if any(c not in (None, '') for c in row)]
    if len(rows) > 5000:
        raise ApiError('too_many_rows', 'Maximum 5000 rows per import.', 422)
    return rows


def _import_partners(rows, flag, resource, dry, company_id):
    created = updated = 0
    errors = []
    for i, row in enumerate(rows, 2):
        try:
            existing = db.one(
                'SELECT * FROM partners WHERE company_id=? AND lower(name)=lower(?) AND is_system=0',
                (company_id, str(row.get('name') or '').strip()),
            )
            data = resource._parse(row, creating=existing is None, company_id=company_id)
            if existing:
                data = {key: value for key, value in data.items()
                        if row.get(key.replace('_cents', '')) not in (None, '')}
                data[flag] = 1
                if not dry:
                    sets = ','.join(f'{column}=?' for column in data)
                    db.execute(f"UPDATE partners SET {sets}, updated_at=datetime('now') "
                               "WHERE id=? AND company_id=?", (*data.values(), existing['id'], company_id))
                updated += 1
            else:
                data[flag] = 1
                data['company_id'] = company_id
                if not dry:
                    cols = list(data)
                    db.execute(f"INSERT INTO partners({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                               [data[column] for column in cols])
                created += 1
        except ApiError as exc:
            errors.append({'row': i, 'errors': exc.details or {'_': exc.code}})
    return created, updated, errors


def _import_products(rows, dry, company_id):
    created = updated = 0
    errors = []
    for i, row in enumerate(rows, 2):
        try:
            sku = str(row.get('sku') or '').strip()
            existing = db.one('SELECT * FROM products WHERE company_id=? AND sku=?', (company_id, sku)) if sku else None
            data = res.PRODUCTS._parse(row, creating=existing is None, company_id=company_id)
            cat = str(row.get('category') or '').strip()
            if cat:
                found = db.one('SELECT id FROM categories WHERE company_id=? AND lower(name)=lower(?)',
                               (company_id, cat))
                if found:
                    data['category_id'] = found['id']
                elif not dry:
                    data['category_id'] = db.execute(
                        'INSERT INTO categories(company_id,name) VALUES(?,?)', (company_id, cat))
            if existing:
                data = {key: value for key, value in data.items()
                        if key == 'category_id' or row.get(key.replace('_cents', '')) not in (None, '')}
                if data and not dry:
                    sets = ','.join(f'{column}=?' for column in data)
                    db.execute(f"UPDATE products SET {sets}, updated_at=datetime('now') "
                               "WHERE id=? AND company_id=?", (*data.values(), existing['id'], company_id))
                updated += 1
            else:
                data['company_id'] = company_id
                if not dry:
                    cols = list(data)
                    rid = db.execute(f"INSERT INTO products({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                                     [data[column] for column in cols])
                    res._product_after_save(rid, data, None, row)
                created += 1
        except ApiError as exc:
            errors.append({'row': i, 'errors': exc.details or {'_': exc.code}})
    return created, updated, errors


@bp.post('/import/<name>')
@require('data.import')
def import_(name):
    targets = {'products': 'products.write', 'customers': 'customers.write', 'suppliers': 'suppliers.write'}
    if name not in targets:
        raise ApiError('not_found', 'Unknown import target.', 404)
    if not has_perm(g.user['permissions'], targets[name]):
        raise ApiError('forbidden', f'Missing permission: {targets[name]}', 403, params={'permission': targets[name]})
    cid = _company_id()
    upload = request.files.get('file')
    if not upload:
        raise ApiError('invalid_file', 'Choose a file to import.', 422)
    dry = request.form.get('dry_run') in ('1', 'true')
    rows = _read_rows(upload)
    with db.transaction():
        if name == 'products':
            created, updated, errors = _import_products(rows, dry, cid)
        elif name == 'customers':
            created, updated, errors = _import_partners(rows, 'is_customer', res.CUSTOMERS, dry, cid)
        else:
            created, updated, errors = _import_partners(rows, 'is_supplier', res.SUPPLIERS, dry, cid)
        if errors:
            raise ApiError('import_failed', f'{len(errors)} row(s) have errors; nothing was imported.', 422,
                           details=errors[:50])
        if not dry:
            audit.log('import', name, None, {'created': created, 'updated': updated})
    return {'dry_run': dry, 'created': created, 'updated': updated, 'rows': len(rows)}


def register(app):
    app.register_blueprint(bp)
