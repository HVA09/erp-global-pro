"""Sales & purchase documents (invoice, credit note, bill, debit note): drafts, posting to stock and
the general ledger, payments, credit application, cancellation, POS checkout, statements and ageing."""
from datetime import date as _date
from decimal import Decimal as D

from .. import audit, db
from ..errors import ApiError
from ..money import line_amounts, round_half_up, to_cents
from ..util import add_days, parse_date, today
from . import accounting, inventory, sequences, settings, tenancy


def _cid(company_id=None):
    """Resolve a trusted active-company context for every trade operation."""
    return tenancy.current_company_id(company_id)

DOC_TYPES = {
    'invoice': {'side': 'sales', 'partner_flag': 'is_customer', 'label': 'Invoice'},
    'credit_note': {'side': 'sales', 'partner_flag': 'is_customer', 'label': 'Credit note'},
    'bill': {'side': 'purchases', 'partner_flag': 'is_supplier', 'label': 'Bill'},
    'debit_note': {'side': 'purchases', 'partner_flag': 'is_supplier', 'label': 'Debit note'},
}
RETURN_TYPE = {'invoice': 'credit_note', 'bill': 'debit_note'}
# doc_type -> (payment direction, control account, cash account is debited?)
PAY_RULES = {'invoice': ('in', 'receivable', True), 'credit_note': ('out', 'receivable', False),
             'bill': ('out', 'payable', False), 'debit_note': ('in', 'payable', True)}


# --------------------------------------------------------------------------- reading
def _fetch(doc_id, doc_type=None, company_id=None):
    cid = _cid(company_id)
    doc = db.one(
        'SELECT d.*, p.name AS partner_name, p.tax_number AS partner_tax_number, w.name AS warehouse_name, '
        'o.number AS origin_number FROM trade_documents d '
        'JOIN partners p ON p.id=d.partner_id AND p.company_id=d.company_id '
        'LEFT JOIN warehouses w ON w.id=d.warehouse_id AND w.company_id=d.company_id '
        'LEFT JOIN trade_documents o ON o.id=d.origin_id AND o.company_id=d.company_id '
        'WHERE d.id=? AND d.company_id=?', (doc_id, cid))
    if not doc or (doc_type and doc['doc_type'] != doc_type):
        raise ApiError('not_found', 'Document not found.', 404)
    return doc


def get_doc(doc_id, doc_type=None, company_id=None):
    cid = _cid(company_id)
    doc = _fetch(doc_id, doc_type, cid)
    doc['lines'] = db.q(
        'SELECT l.*, p.name AS product_name, p.sku, p.unit, a.name AS account_name '
        'FROM trade_document_lines l '
        'LEFT JOIN products p ON p.id=l.product_id AND p.company_id=l.company_id '
        'LEFT JOIN accounts a ON a.id=l.account_id AND a.company_id=l.company_id '
        'WHERE l.document_id=? AND l.company_id=? ORDER BY l.id', (doc_id, cid))
    doc['payments'] = db.q(
        'SELECT pay.*, a.name AS account_name FROM payments pay '
        'JOIN accounts a ON a.id=pay.account_id AND a.company_id=pay.company_id '
        'WHERE pay.document_id=? AND pay.company_id=? ORDER BY pay.id', (doc_id, cid))
    doc['applications'] = db.q(
        'SELECT c.*, cd.number AS credit_number, td.number AS target_number FROM credit_applications c '
        'JOIN trade_documents cd ON cd.id=c.credit_id AND cd.company_id=c.company_id '
        'JOIN trade_documents td ON td.id=c.target_id AND td.company_id=c.company_id '
        'WHERE (c.credit_id=? OR c.target_id=?) AND c.company_id=? ORDER BY c.id', (doc_id, doc_id, cid))
    doc['outstanding_cents'] = doc['total_cents'] - doc['paid_cents'] if doc['status'] == 'posted' else 0
    return doc


def list_docs(doc_type, args):
    cid = _cid()
    where, params = ['d.doc_type=?', 'd.company_id=?'], [doc_type, cid]
    for key in ('status', 'payment_status', 'partner_id', 'source'):
        if args.get(key):
            where.append(f'd.{key}=?')
            params.append(args[key])
    if args.get('date_from'):
        where.append('d.date >= ?'), params.append(args['date_from'])
    if args.get('date_to'):
        where.append('d.date <= ?'), params.append(args['date_to'])
    if args.get('overdue'):
        where.append("d.status='posted' AND d.payment_status != 'paid' AND d.due_date < ?"), params.append(today())
    if args.get('q'):
        where.append('(d.number LIKE ? OR p.name LIKE ?)')
        params += [f"%{args['q']}%"] * 2
    base = ('FROM trade_documents d JOIN partners p ON p.id=d.partner_id AND p.company_id=d.company_id LEFT JOIN trade_documents o ON o.id=d.origin_id AND o.company_id=d.company_id '
            'WHERE ' + ' AND '.join(where))
    total = db.scalar(f'SELECT COUNT(*) {base}', params)
    page, per = max(1, int(args.get('page') or 1)), min(200, max(1, int(args.get('per_page') or 25)))
    rows = db.q(f"SELECT d.*, p.name AS partner_name, o.number AS origin_number, "
                f"CASE WHEN d.status='posted' THEN d.total_cents-d.paid_cents ELSE 0 END AS outstanding_cents "
                f"{base} ORDER BY d.date DESC, d.id DESC LIMIT ? OFFSET ?", (*params, per, (page - 1) * per))
    return {'items': rows, 'total': total, 'page': page, 'per_page': per}


# --------------------------------------------------------------------------- drafts
def _parse_lines(doc_type, raw, company_id=None):
    cid = _cid(company_id)
    if not isinstance(raw, list) or not raw:
        raise ApiError('no_lines', 'Add at least one line.', 422)
    side = DOC_TYPES[doc_type]['side']
    default_tax = settings.get_settings(cid)['tax']
    out = []
    for i, ln in enumerate(raw, 1):
        prm = {'line': i}
        product = None
        if ln.get('product_id'):
            product = db.one('SELECT * FROM products WHERE id=? AND company_id=?', (int(ln['product_id']), cid))
            if not product:
                raise ApiError('invalid_product', f'Line {i}: unknown product.', 422, params=prm)
        try:
            qty = round(float(ln.get('qty')), 6)
        except (TypeError, ValueError):
            qty = 0
        if qty <= 0:
            raise ApiError('invalid_quantity', f'Line {i}: quantity must be greater than zero.', 422, params=prm)
        if ln.get('unit_price_cents') is not None:
            price = int(ln['unit_price_cents'])
        elif ln.get('unit_price') not in (None, ''):
            price = to_cents(ln['unit_price'])
        else:
            price = (product['price_cents'] if side == 'sales' else product['cost_cents']) if product else 0
        try:
            disc = float(ln.get('discount_pct') or 0)
            if ln.get('tax_rate') not in (None, ''):
                tax_rate = float(ln['tax_rate'])
            else:
                tax_rate = product['tax_rate'] if product and product['tax_rate'] is not None else default_tax
        except (TypeError, ValueError):
            raise ApiError('invalid_value', f'Line {i}: invalid number.', 422, params=prm)
        if price < 0 or not 0 <= disc <= 100 or not 0 <= tax_rate <= 100:
            raise ApiError('invalid_value', f'Line {i}: value out of range.', 422, params=prm)
        desc = (ln.get('description') or '').strip() or (product['name'] if product else '')
        if not desc:
            raise ApiError('description_required', f'Line {i}: add a product or a description.', 422, params=prm)
        account_id = int(ln['account_id']) if ln.get('account_id') else None
        if account_id and not db.one('SELECT id FROM accounts WHERE id=? AND company_id=? AND is_active=1', (account_id, cid)):
            raise ApiError('invalid_account', f'Line {i}: unknown account.', 422, params=prm)
        net, tax, total = line_amounts(qty, price, disc, tax_rate)
        out.append({'product_id': product['id'] if product else None, 'account_id': account_id, 'description': desc,
                    'qty': qty, 'unit_price_cents': price, 'discount_pct': disc, 'tax_rate': tax_rate,
                    'net_cents': net, 'tax_cents': tax, 'total_cents': total,
                    'lot_no': (ln.get('lot_no') or '').strip() or None, 'expiry_date': ln.get('expiry_date') or None,
                    '_stock': inventory.is_stock(product)})
    return out


def save_draft(doc_type, data, doc_id=None, source=None, company_id=None):
    cid = _cid(company_id)
    meta = DOC_TYPES[doc_type]
    partner = db.one('SELECT * FROM partners WHERE id=? AND company_id=?',
                     (data.get('partner_id'), cid)) if data.get('partner_id') else None
    if not partner:
        raise ApiError('invalid_partner', 'Select a customer/supplier.', 422, details={'partner_id': 'required'})
    if not partner[meta['partner_flag']]:
        raise ApiError('invalid_partner_type', 'This contact cannot be used on this document type.', 422)
    if not partner['is_active']:
        raise ApiError('inactive_partner', 'This contact is inactive.', 422)
    date = parse_date(data.get('date') or today())
    due = parse_date(data.get('due_date'), 'due_date', required=False) or add_days(date, partner['payment_terms_days'])
    lines = _parse_lines(doc_type, data.get('lines'), cid)
    wid = int(data['warehouse_id']) if data.get('warehouse_id') else None
    if wid:
        inventory.get_warehouse(wid)
    elif any(l['_stock'] for l in lines):
        wid = settings.default_warehouse_id(cid)
    origin_id = data.get('origin_id') or None
    if origin_id:
        origin = _fetch(int(origin_id), company_id=cid)
        if RETURN_TYPE.get(origin['doc_type']) != doc_type or origin['partner_id'] != partner['id']:
            raise ApiError('invalid_origin', 'The referenced document does not match.', 422)
    subtotal, tax = sum(l['net_cents'] for l in lines), sum(l['tax_cents'] for l in lines)
    notes = (data.get('notes') or '').strip() or None
    if doc_id:
        doc = _fetch(doc_id, doc_type, cid)
        if doc['status'] != 'draft':
            raise ApiError('not_draft', 'Only draft documents can be edited.', 409)
        db.execute('UPDATE trade_documents SET partner_id=?, warehouse_id=?, date=?, due_date=?, subtotal_cents=?, '
                   'tax_cents=?, total_cents=?, origin_id=?, notes=? WHERE id=? AND company_id=?',
                   (partner['id'], wid, date, due, subtotal, tax, subtotal + tax, origin_id, notes, doc_id, cid))
        db.execute('DELETE FROM trade_document_lines WHERE document_id=? AND company_id=?', (doc_id, cid))
    else:
        uid = None
        from flask import g, has_request_context
        if has_request_context() and g.get('user'):
            uid = g.user['id']
        doc_id = db.execute('INSERT INTO trade_documents(company_id,doc_type,partner_id,warehouse_id,date,due_date,'
                            'subtotal_cents,tax_cents,total_cents,origin_id,source,notes,created_by) '
                            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                            (cid, doc_type, partner['id'], wid, date, due, subtotal, tax, subtotal + tax, origin_id,
                             source or 'manual', notes, uid))
    for l in lines:
        db.execute('INSERT INTO trade_document_lines(company_id,document_id,product_id,account_id,description,qty,'
                   'unit_price_cents,discount_pct,tax_rate,net_cents,tax_cents,total_cents,lot_no,expiry_date) '
                   'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (cid, doc_id, l['product_id'], l['account_id'], l['description'], l['qty'], l['unit_price_cents'],
                    l['discount_pct'], l['tax_rate'], l['net_cents'], l['tax_cents'], l['total_cents'],
                    l['lot_no'], l['expiry_date']))
    return doc_id


def delete_draft(doc_id, doc_type, company_id=None):
    cid = _cid(company_id)
    doc = _fetch(doc_id, doc_type, cid)
    if doc['status'] != 'draft':
        raise ApiError('not_draft', 'Only draft documents can be deleted; cancel posted ones instead.', 409)
    db.execute('DELETE FROM trade_documents WHERE id=? AND company_id=?', (doc_id, cid))
    return doc


def create_return_from(origin_id, lines_spec=None, company_id=None):
    cid = _cid(company_id)
    origin = get_doc(origin_id, company_id=cid)
    new_type = RETURN_TYPE.get(origin['doc_type'])
    if not new_type or origin['status'] != 'posted':
        raise ApiError('cannot_return', 'Only posted invoices and bills can be returned.', 422)
    returned = {r['product_id']: r['q'] for r in db.q(
        "SELECT l.product_id, SUM(l.qty) q FROM trade_document_lines l JOIN trade_documents d "
        "ON d.id=l.document_id AND d.company_id=l.company_id "
        "WHERE d.origin_id=? AND d.company_id=? AND l.company_id=? AND d.status='posted' "
        "AND l.product_id IS NOT NULL GROUP BY l.product_id", (origin_id, cid, cid))}
    wanted = {int(s['line_id']): float(s.get('qty') or 0) for s in lines_spec} if lines_spec else None
    lines = []
    for ln in origin['lines']:
        if wanted is not None and ln['id'] not in wanted:
            continue
        remaining = ln['qty'] - (returned.get(ln['product_id'], 0) if ln['product_id'] else 0)
        qty = min(wanted[ln['id']], ln['qty']) if wanted is not None else ln['qty']
        if ln['product_id']:
            qty = min(qty, remaining)
        if qty <= 1e-9:
            continue
        lines.append({'product_id': ln['product_id'], 'account_id': ln['account_id'], 'description': ln['description'],
                      'qty': qty, 'unit_price_cents': ln['unit_price_cents'], 'discount_pct': ln['discount_pct'],
                      'tax_rate': ln['tax_rate'], 'lot_no': ln['lot_no'], 'expiry_date': ln['expiry_date']})
    if not lines:
        raise ApiError('nothing_to_return', 'Nothing left to return on this document.', 422)
    return save_draft(new_type, {'partner_id': origin['partner_id'], 'warehouse_id': origin['warehouse_id'],
                                 'date': today(), 'origin_id': origin_id, 'lines': lines,
                                 'notes': f"Return of {origin['number']}"}, company_id=cid)


# --------------------------------------------------------------------------- balances
def receivable_balance(partner_id, company_id=None):
    cid = _cid(company_id)
    if not db.one('SELECT id FROM partners WHERE id=? AND company_id=?', (partner_id, cid)):
        raise ApiError('not_found', 'Contact not found.', 404)
    return db.scalar("SELECT COALESCE(SUM(CASE doc_type WHEN 'invoice' THEN total_cents-paid_cents "
                     "ELSE -(total_cents-paid_cents) END),0) FROM trade_documents WHERE partner_id=? "
                     "AND status='posted' AND doc_type IN ('invoice','credit_note') AND company_id=?", (partner_id, cid))


def payable_balance(partner_id, company_id=None):
    cid = _cid(company_id)
    if not db.one('SELECT id FROM partners WHERE id=? AND company_id=?', (partner_id, cid)):
        raise ApiError('not_found', 'Contact not found.', 404)
    return db.scalar("SELECT COALESCE(SUM(CASE doc_type WHEN 'bill' THEN total_cents-paid_cents "
                     "ELSE -(total_cents-paid_cents) END),0) FROM trade_documents WHERE partner_id=? "
                     "AND status='posted' AND doc_type IN ('bill','debit_note') AND company_id=?", (partner_id, cid))


# --------------------------------------------------------------------------- posting
def _entry(doc, number, lines):
    lines = [l for l in lines if l.get('debit') or l.get('credit')]
    if len(lines) < 2:
        return None  # zero-value document: nothing to book
    return accounting.post_entry(doc['date'], f"{DOC_TYPES[doc['doc_type']]['label']} {number}", lines,
                                 'document', doc['id'], company_id=doc['company_id'])


def _origin_unit_cost(origin_id, product_id, company_id=None):
    cid = _cid(company_id)
    if not origin_id:
        return None
    r = db.one("SELECT SUM(-qty) q, SUM(-value_cents) v FROM stock_moves WHERE ref_type='trade_document' AND ref_id=? "
               "AND product_id=? AND qty < 0 AND move_type='sale' AND company_id=?", (origin_id, product_id, cid))
    return D(r['v']) / D(str(r['q'])) if r and r['q'] and r['q'] > 0 else None


def _check_returns(doc, lines, company_id=None):
    cid = _cid(company_id)
    origin = doc['origin_id']
    if not origin:
        return
    orig = {r['product_id']: r['q'] for r in db.q(
        'SELECT product_id, SUM(qty) q FROM trade_document_lines WHERE document_id=? AND company_id=? AND product_id IS NOT NULL '
        'GROUP BY product_id', (origin, cid))}
    done = {r['product_id']: r['q'] for r in db.q(
        "SELECT l.product_id, SUM(l.qty) q FROM trade_document_lines l JOIN trade_documents d ON d.id=l.document_id AND d.company_id=l.company_id "
        "WHERE d.origin_id=? AND d.company_id=? AND l.company_id=? AND d.status='posted' AND d.id != ? "
        "AND l.product_id IS NOT NULL GROUP BY l.product_id",
        (origin, cid, cid, doc['id']))}
    now = {}
    for l in lines:
        if l['product_id']:
            now[l['product_id']] = now.get(l['product_id'], 0) + l['qty']
    for pid, q in now.items():
        if q + done.get(pid, 0) > orig.get(pid, 0) + 1e-9:
            raise ApiError('return_exceeds_document', 'Returned quantity exceeds the original document.', 422)


def _add(d, key, cents):
    d[key] = d.get(key, 0) + cents


def _post_invoice(doc, lines, wh, number):
    cogs, revenue = 0, {}
    for ln in lines:
        if ln['product_id'] and ln['kind'] in inventory.STOCK_KINDS:
            value = inventory.issue(ln['product_id'], wh, ln['qty'], 'sale', doc['date'], 'trade_document', doc['id'],
                                    number, ln['lot_no'], company_id=doc['company_id'])
            db.execute('UPDATE trade_document_lines SET cost_cents=? WHERE id=? AND company_id=?', (value, ln['id'], doc['company_id']))
            cogs += value
        _add(revenue, ln['account_id'] or 'sales', ln['net_cents'])
    jl = [{'account': 'receivable', 'debit': doc['total_cents'], 'partner_id': doc['partner_id']}]
    jl += [{'account': a, 'credit': v} for a, v in revenue.items()]
    jl += [{'account': 'vat_out', 'credit': doc['tax_cents']},
           {'account': 'cogs', 'debit': cogs}, {'account': 'inventory', 'credit': cogs}]
    return _entry(doc, number, jl)


def _post_credit_note(doc, lines, wh, number):
    _check_returns(doc, lines, doc['company_id'])
    back, revenue = 0, {}
    for ln in lines:
        if ln['product_id'] and ln['kind'] in inventory.STOCK_KINDS:
            unit = _origin_unit_cost(doc['origin_id'], ln['product_id'], doc['company_id'])
            if unit is None:
                lq, lv = inventory.level(ln['product_id'], wh, doc['company_id'])
                unit = D(lv) / D(str(lq)) if lq > 1e-9 and lv > 0 else D(
                    inventory.get_product(ln['product_id'], doc['company_id'])['cost_cents'])
            value = round_half_up(D(str(ln['qty'])) * unit)
            inventory.receive(ln['product_id'], wh, ln['qty'], value, 'return_in', doc['date'], 'trade_document',
                              doc['id'], number, ln['lot_no'], ln['expiry_date'], company_id=doc['company_id'])
            db.execute('UPDATE trade_document_lines SET cost_cents=? WHERE id=? AND company_id=?', (value, ln['id'], doc['company_id']))
            back += value
        _add(revenue, ln['account_id'] or 'sales_returns', ln['net_cents'])
    jl = [{'account': 'receivable', 'credit': doc['total_cents'], 'partner_id': doc['partner_id']}]
    jl += [{'account': a, 'debit': v} for a, v in revenue.items()]
    jl += [{'account': 'vat_out', 'debit': doc['tax_cents']},
           {'account': 'inventory', 'debit': back}, {'account': 'cogs', 'credit': back}]
    return _entry(doc, number, jl)


def _post_bill(doc, lines, wh, number):
    stock_total, expenses = 0, {}
    for ln in lines:
        if ln['product_id'] and ln['kind'] in inventory.STOCK_KINDS:
            inventory.receive(ln['product_id'], wh, ln['qty'], ln['net_cents'], 'purchase', doc['date'],
                              'trade_document', doc['id'], number, ln['lot_no'], ln['expiry_date'], company_id=doc['company_id'])
            stock_total += ln['net_cents']
        else:
            _add(expenses, ln['account_id'] or 'general_expense', ln['net_cents'])
    jl = [{'account': 'inventory', 'debit': stock_total}]
    jl += [{'account': a, 'debit': v} for a, v in expenses.items()]
    jl += [{'account': 'vat_in', 'debit': doc['tax_cents']},
           {'account': 'payable', 'credit': doc['total_cents'], 'partner_id': doc['partner_id']}]
    return _entry(doc, number, jl)


def _post_debit_note(doc, lines, wh, number):
    _check_returns(doc, lines, doc['company_id'])
    value_total, diff_total, others = 0, 0, {}
    for ln in lines:
        if ln['product_id'] and ln['kind'] in inventory.STOCK_KINDS:
            value = inventory.issue(ln['product_id'], wh, ln['qty'], 'return_out', doc['date'], 'trade_document',
                                    doc['id'], number, ln['lot_no'], company_id=doc['company_id'])
            db.execute('UPDATE trade_document_lines SET cost_cents=? WHERE id=? AND company_id=?', (value, ln['id'], doc['company_id']))
            value_total += value
            diff_total += ln['net_cents'] - value
        else:
            _add(others, ln['account_id'] or 'general_expense', ln['net_cents'])
    jl = [{'account': 'payable', 'debit': doc['total_cents'], 'partner_id': doc['partner_id']},
          {'account': 'inventory', 'credit': value_total}, {'account': 'vat_in', 'credit': doc['tax_cents']}]
    jl += [{'account': a, 'credit': v} for a, v in others.items()]
    jl.append({'account': 'inventory_adj', 'credit': diff_total} if diff_total > 0
              else {'account': 'inventory_adj', 'debit': -diff_total})
    return _entry(doc, number, jl)


_HANDLERS = {'invoice': _post_invoice, 'credit_note': _post_credit_note, 'bill': _post_bill,
             'debit_note': _post_debit_note}


def post(doc_id, doc_type=None, company_id=None):
    cid = _cid(company_id)
    doc = _fetch(doc_id, doc_type, cid)
    if doc['status'] != 'draft':
        raise ApiError('not_draft', 'Only draft documents can be posted.', 409)
    settings.check_period_open(doc['date'], cid)
    lines = db.q('SELECT l.*, p.kind FROM trade_document_lines l LEFT JOIN products p '
                 'ON p.id=l.product_id AND p.company_id=l.company_id '
                 'WHERE l.document_id=? AND l.company_id=? ORDER BY l.id', (doc_id, cid))
    if not lines:
        raise ApiError('no_lines', 'Add at least one line.', 422)
    partner = db.one('SELECT * FROM partners WHERE id=? AND company_id=?', (doc['partner_id'], cid))
    if not partner['is_active']:
        raise ApiError('inactive_partner', 'This contact is inactive.', 422)
    wh = doc['warehouse_id']
    if any(l['kind'] in inventory.STOCK_KINDS for l in lines) and not wh:
        wh = settings.default_warehouse_id(cid)
        if not wh:
            raise ApiError('warehouse_required', 'Create a warehouse first.', 422)
        db.execute('UPDATE trade_documents SET warehouse_id=? WHERE id=? AND company_id=?', (wh, doc_id, cid))
    if doc['doc_type'] == 'invoice' and partner['credit_limit_cents'] > 0:
        balance = receivable_balance(partner['id'], cid)
        if balance + doc['total_cents'] > partner['credit_limit_cents']:
            raise ApiError('credit_limit_exceeded', 'Credit limit exceeded for this customer.', 409,
                           params={'limit': partner['credit_limit_cents'] / 100, 'balance': balance / 100,
                                   'amount': doc['total_cents'] / 100})
    number = sequences.next_number(doc['doc_type'], cid)
    entry_id = _HANDLERS[doc['doc_type']](doc, lines, wh, number)
    db.execute("UPDATE trade_documents SET status='posted', number=?, journal_entry_id=?, posted_at=datetime('now'), "
               "paid_cents=0, payment_status='unpaid' WHERE id=? AND company_id=?", (number, entry_id, doc_id, cid))
    audit.log('post', doc['doc_type'], doc_id, {'number': number, 'total': doc['total_cents'] / 100})
    return number


def cancel(doc_id, doc_type=None, company_id=None):
    cid = _cid(company_id)
    doc = _fetch(doc_id, doc_type, cid)
    if doc['status'] != 'posted':
        raise ApiError('not_posted', 'Only posted documents can be cancelled.', 409)
    settings.check_period_open(doc['date'], cid)
    if db.scalar("SELECT COUNT(*) FROM payments WHERE document_id=? AND company_id=? AND status='posted'", (doc_id, cid)) or \
            db.scalar('SELECT COUNT(*) FROM credit_applications WHERE (credit_id=? OR target_id=?) AND company_id=?', (doc_id, doc_id, cid)):
        raise ApiError('has_payments', 'Cancel its payments/credit applications first.', 409)
    if db.scalar("SELECT COUNT(*) FROM trade_documents WHERE origin_id=? AND company_id=? AND status='posted'", (doc_id, cid)):
        raise ApiError('has_credit_notes', 'Cancel the related credit/debit notes first.', 409)
    inventory.reverse_moves('trade_document', doc_id, doc['date'], f"Cancel {doc['number']}", company_id=cid)
    if doc['journal_entry_id']:
        accounting.reverse_entry(doc['journal_entry_id'], memo=f"Cancellation of {doc['number']}", company_id=cid)
    db.execute("UPDATE trade_documents SET status='cancelled', cancelled_at=datetime('now') WHERE id=? AND company_id=?", (doc_id, cid))
    audit.log('cancel', doc['doc_type'], doc_id, {'number': doc['number']})


# --------------------------------------------------------------------------- payments
def recompute_paid(doc_id, company_id=None):
    cid = _cid(company_id)
    total = db.scalar('SELECT total_cents FROM trade_documents WHERE id=? AND company_id=?', (doc_id, cid))
    if total is None:
        raise ApiError('not_found', 'Document not found.', 404)
    paid = db.scalar("SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE document_id=? AND company_id=? AND status='posted'",
                     (doc_id, cid)) + db.scalar('SELECT COALESCE(SUM(amount_cents),0) FROM credit_applications '
                                            'WHERE (credit_id=? OR target_id=?) AND company_id=?', (doc_id, doc_id, cid))
    status = 'paid' if paid >= total and total > 0 else 'partial' if paid > 0 else 'unpaid'
    db.execute('UPDATE trade_documents SET paid_cents=?, payment_status=? WHERE id=? AND company_id=?', (paid, status, doc_id, cid))


def add_payment(doc_id, amount_cents, account_id, date=None, method=None, reference=None, note=None, company_id=None):
    cid = _cid(company_id)
    doc = _fetch(doc_id, company_id=cid)
    if doc['status'] != 'posted':
        raise ApiError('not_posted', 'Post the document before registering payments.', 409)
    outstanding = doc['total_cents'] - doc['paid_cents']
    amount_cents = int(amount_cents)
    if amount_cents <= 0:
        raise ApiError('invalid_amount', 'Amount must be greater than zero.', 422)
    if amount_cents > outstanding:
        raise ApiError('payment_exceeds_balance', 'Payment exceeds the outstanding balance.', 422,
                       params={'outstanding': outstanding / 100})
    direction, control, cash_debit = PAY_RULES[doc['doc_type']]
    account_id = accounting.cash_account(int(account_id), cid) if account_id else accounting.cash_account(
        accounting.resolve_account('cash', company_id=cid), cid)
    date = parse_date(date or today())
    settings.check_period_open(date, cid)
    number = sequences.next_number('payment_in' if direction == 'in' else 'payment_out', cid)
    from flask import g, has_request_context
    uid = g.user['id'] if has_request_context() and g.get('user') else None
    pid = db.execute('INSERT INTO payments(company_id,number,direction,partner_id,document_id,account_id,amount_cents,date,method,'
                     'reference,note,created_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (cid, number, direction, doc['partner_id'], doc_id, account_id, amount_cents, date, method or 'cash',
                      reference, note, uid))
    cash = {'account': account_id}
    ctl = {'account': control, 'partner_id': doc['partner_id']}
    if cash_debit:
        cash['debit'], ctl['credit'] = amount_cents, amount_cents
    else:
        cash['credit'], ctl['debit'] = amount_cents, amount_cents
    jid = accounting.post_entry(date, f"Payment {number} - {doc['number']}", [cash, ctl], 'payment', pid, company_id=cid)
    db.execute('UPDATE payments SET journal_entry_id=? WHERE id=? AND company_id=?', (jid, pid, cid))
    recompute_paid(doc_id, cid)
    audit.log('payment', doc['doc_type'], doc_id, {'number': number, 'amount': amount_cents / 100})
    return pid


def cancel_payment(payment_id, company_id=None):
    cid = _cid(company_id)
    pay = db.one('SELECT * FROM payments WHERE id=? AND company_id=?', (payment_id, cid))
    if not pay:
        raise ApiError('not_found', 'Payment not found.', 404)
    if pay['status'] != 'posted':
        raise ApiError('already_cancelled', 'Payment already cancelled.', 409)
    settings.check_period_open(pay['date'], cid)
    accounting.reverse_entry(pay['journal_entry_id'], memo=f"Cancellation of payment {pay['number']}", company_id=cid)
    db.execute("UPDATE payments SET status='cancelled' WHERE id=? AND company_id=?", (payment_id, cid))
    recompute_paid(pay['document_id'], cid)
    audit.log('cancel_payment', 'payment', payment_id, {'number': pay['number']})


def apply_credit(credit_id, target_id, amount_cents, date=None, company_id=None):
    cid = _cid(company_id)
    credit, target = _fetch(credit_id, company_id=cid), _fetch(target_id, company_id=cid)
    if (credit['doc_type'], target['doc_type']) not in (('credit_note', 'invoice'), ('debit_note', 'bill')):
        raise ApiError('invalid_credit_pair', 'A credit note can only be applied to an invoice (debit note to a bill).', 422)
    if credit['status'] != 'posted' or target['status'] != 'posted' or credit['partner_id'] != target['partner_id']:
        raise ApiError('invalid_credit_pair', 'Both documents must be posted and belong to the same contact.', 422)
    amount_cents = int(amount_cents)
    limit = min(credit['total_cents'] - credit['paid_cents'], target['total_cents'] - target['paid_cents'])
    if amount_cents <= 0 or amount_cents > limit:
        raise ApiError('payment_exceeds_balance', 'Amount exceeds the available balance.', 422,
                       params={'outstanding': limit / 100})
    date = parse_date(date or today())
    from flask import g, has_request_context
    uid = g.user['id'] if has_request_context() and g.get('user') else None
    app_id = db.execute('INSERT INTO credit_applications(company_id,credit_id,target_id,amount_cents,date,created_by) '
                        'VALUES(?,?,?,?,?,?)', (cid, credit_id, target_id, amount_cents, date, uid))
    recompute_paid(credit_id, cid), recompute_paid(target_id, cid)
    audit.log('apply_credit', credit['doc_type'], credit_id, {'target': target['number'], 'amount': amount_cents / 100})
    return app_id


def undo_credit_application(app_id, company_id=None):
    cid = _cid(company_id)
    app = db.one('SELECT * FROM credit_applications WHERE id=? AND company_id=?', (app_id, cid))
    if not app:
        raise ApiError('not_found', 'Application not found.', 404)
    db.execute('DELETE FROM credit_applications WHERE id=? AND company_id=?', (app_id, cid))
    recompute_paid(app['credit_id'], cid), recompute_paid(app['target_id'], cid)


def list_payments(args, company_id=None):
    cid = _cid(company_id)
    where, params = ['pay.company_id=?'], [cid]
    for key in ('direction', 'partner_id', 'status', 'document_id'):
        if args.get(key):
            where.append(f'pay.{key}=?'), params.append(args[key])
    if args.get('date_from'):
        where.append('pay.date >= ?'), params.append(args['date_from'])
    if args.get('date_to'):
        where.append('pay.date <= ?'), params.append(args['date_to'])
    base = ('FROM payments pay JOIN partners p ON p.id=pay.partner_id AND p.company_id=pay.company_id JOIN trade_documents d ON d.id=pay.document_id AND d.company_id=pay.company_id '
            'JOIN accounts a ON a.id=pay.account_id AND a.company_id=pay.company_id WHERE ' + ' AND '.join(where))
    total = db.scalar(f'SELECT COUNT(*) {base}', params)
    page, per = max(1, int(args.get('page') or 1)), min(200, max(1, int(args.get('per_page') or 25)))
    rows = db.q(f'SELECT pay.*, p.name AS partner_name, d.number AS document_number, d.doc_type, a.name AS account_name '
                f'{base} ORDER BY pay.date DESC, pay.id DESC LIMIT ? OFFSET ?', (*params, per, (page - 1) * per))
    return {'items': rows, 'total': total, 'page': page, 'per_page': per}


# --------------------------------------------------------------------------- POS
def walkin_customer_id(company_id=None):
    cid = _cid(company_id)
    row = db.one("SELECT id FROM partners WHERE company_id=? AND is_system=1 AND is_customer=1 ORDER BY id LIMIT 1", (cid,))
    if row:
        return row['id']
    return db.execute("INSERT INTO partners(company_id,name,is_customer,is_system) VALUES(?, 'Walk-in Customer',1,1)", (cid,))


def pos_checkout(data, company_id=None):
    cid = _cid(company_id)
    lines = data.get('lines')
    if not isinstance(lines, list) or not lines:
        raise ApiError('no_lines', 'The cart is empty.', 422)
    partner_id = data.get('partner_id') or walkin_customer_id(cid)
    doc_id = save_draft('invoice', {'partner_id': partner_id, 'date': today(), 'warehouse_id': data.get('warehouse_id'),
                                    'lines': lines, 'notes': data.get('notes')}, source='pos', company_id=cid)
    post(doc_id, 'invoice', cid)
    doc = _fetch(doc_id, company_id=cid)
    method = data.get('method') or 'cash'
    paid = to_cents(data['paid']) if data.get('paid') not in (None, '') else doc['total_cents']
    paid = min(paid, doc['total_cents'])
    if paid > 0:
        account = data.get('account_id') or accounting.resolve_account('cash' if method == 'cash' else 'bank', company_id=cid)
        add_payment(doc_id, paid, account, today(), method, company_id=cid)
    return doc_id


# --------------------------------------------------------------------------- statements & ageing
def partner_statement(partner_id, side=None, date_from=None, date_to=None, company_id=None):
    cid = _cid(company_id)
    partner = db.one('SELECT * FROM partners WHERE id=? AND company_id=?', (partner_id, cid))
    if not partner:
        raise ApiError('not_found', 'Contact not found.', 404)
    side = side or ('customer' if partner['is_customer'] else 'supplier')
    pos_type, neg_type = ('invoice', 'credit_note') if side == 'customer' else ('bill', 'debit_note')
    events = []
    for d in db.q("SELECT id, doc_type, number, date, total_cents FROM trade_documents WHERE partner_id=? "
                  "AND status='posted' AND doc_type IN (?,?) AND company_id=?", (partner_id, pos_type, neg_type, cid)):
        events.append({'date': d['date'], 'order': 0, 'id': d['id'], 'type': d['doc_type'], 'number': d['number'],
                       'amount_cents': d['total_cents'] if d['doc_type'] == pos_type else -d['total_cents']})
    for p in db.q("SELECT pay.id, pay.number, pay.date, pay.amount_cents, d.doc_type FROM payments pay "
                  "JOIN trade_documents d ON d.id=pay.document_id AND d.company_id=pay.company_id "
                  "WHERE pay.partner_id=? AND pay.company_id=? AND pay.status='posted' AND d.doc_type IN (?,?)",
                  (partner_id, cid, pos_type, neg_type)):
        events.append({'date': p['date'], 'order': 1, 'id': p['id'], 'type': 'payment', 'number': p['number'],
                       'amount_cents': -p['amount_cents'] if p['doc_type'] == pos_type else p['amount_cents']})
    events.sort(key=lambda e: (e['date'], e['order'], e['id']))
    f, t = date_from or '0000-01-01', date_to or '9999-12-31'
    opening = sum(e['amount_cents'] for e in events if e['date'] < f)
    running, rows = opening, []
    for e in events:
        if f <= e['date'] <= t:
            running += e['amount_cents']
            rows.append({**e, 'balance_cents': running})
    return {'partner': {'id': partner['id'], 'name': partner['name']}, 'side': side,
            'opening_cents': opening, 'rows': rows, 'closing_cents': running}


def ageing(side='customer', as_of=None, company_id=None):
    cid = _cid(company_id)
    as_of = as_of or today()
    types = ('invoice', 'credit_note') if side == 'customer' else ('bill', 'debit_note')
    docs = db.q("SELECT d.id, d.doc_type, d.number, d.date, d.due_date, d.total_cents-d.paid_cents AS open_cents, "
                "d.partner_id, p.name AS partner_name FROM trade_documents d JOIN partners p ON p.id=d.partner_id "
                "WHERE d.status='posted' AND d.doc_type IN (?,?) AND d.total_cents > d.paid_cents AND d.date <= ? "
                "AND d.company_id=? AND p.company_id=d.company_id",
                (*types, as_of, cid))
    keys = ('current', 'd1_30', 'd31_60', 'd61_90', 'd90_plus')
    by_partner = {}
    for d in docs:
        amount = d['open_cents'] if d['doc_type'] == types[0] else -d['open_cents']
        days = (_date.fromisoformat(as_of) - _date.fromisoformat(d['due_date'] or d['date'])).days
        bucket = 'current' if days <= 0 else 'd1_30' if days <= 30 else 'd31_60' if days <= 60 else \
            'd61_90' if days <= 90 else 'd90_plus'
        row = by_partner.setdefault(d['partner_id'], {'partner_id': d['partner_id'], 'partner_name': d['partner_name'],
                                                      **{k + '_cents': 0 for k in keys}, 'total_cents': 0})
        row[bucket + '_cents'] += amount
        row['total_cents'] += amount
    rows = sorted(by_partner.values(), key=lambda r: -r['total_cents'])
    totals = {k + '_cents': sum(r[k + '_cents'] for r in rows) for k in keys}
    totals['total_cents'] = sum(r['total_cents'] for r in rows)
    return {'as_of': as_of, 'side': side, 'rows': rows, 'totals': totals}
