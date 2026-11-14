"""Stock ledger: per-warehouse moving-average cost, FEFO lots, exact tie-out with the general ledger.

Every stock movement is a row in stock_moves (signed qty and signed value in cents).
stock_levels is a running total kept in sync inside the same transaction, so
SUM(stock_moves.value) == SUM(stock_levels.value) == GL inventory account (see accounting.health_check)."""
from datetime import date as _date
from decimal import Decimal as D

from flask import g, has_request_context

from .. import db
from ..errors import ApiError
from ..money import round_half_up
from ..util import parse_date, today
from . import accounting, sequences, settings, tenancy

STOCK_KINDS = ('storable', 'raw', 'spare')
COST_UPDATE_TYPES = ('purchase', 'opening', 'production_in')
EPS = 1e-9


def get_product(pid, company_id=None):
    cid = tenancy.current_company_id(company_id)
    p = db.one('SELECT * FROM products WHERE id=? AND company_id=?', (pid, cid))
    if not p:
        raise ApiError('not_found', 'Product not found.', 404)
    return p


def is_stock(product):
    return product is not None and product['kind'] in STOCK_KINDS


def get_warehouse(wid, company_id=None):
    cid = tenancy.current_company_id(company_id)
    w = db.one('SELECT * FROM warehouses WHERE id=? AND company_id=?', (wid, cid))
    if not w:
        raise ApiError('not_found', 'Warehouse not found.', 404)
    return w


def level(pid, wid, company_id=None):
    cid = tenancy.current_company_id(company_id)
    r = db.one('SELECT qty, value_cents FROM stock_levels WHERE product_id=? AND warehouse_id=? AND company_id=?', (pid, wid, cid))
    return (r['qty'], r['value_cents']) if r else (0.0, 0)


def _apply_level(pid, wid, dq, dv, company_id):
    db.execute('INSERT INTO stock_levels(product_id,warehouse_id,qty,value_cents,company_id) VALUES(?,?,?,?,?) '
               'ON CONFLICT(product_id,warehouse_id) DO UPDATE SET qty=ROUND(qty+excluded.qty,6), '
               'value_cents=value_cents+excluded.value_cents '
               'WHERE stock_levels.company_id=excluded.company_id',
               (pid, wid, round(dq, 6), dv, company_id))


def _user_id():
    u = g.get('user') if has_request_context() else None
    return u['id'] if u else None


def _insert_move(pid, wid, qty, value, move_type, date, ref_type, ref_id, note, lot_no, expiry, company_id):
    # Confirm both master records belong to the same authenticated tenant before writing a ledger row.
    get_product(pid, company_id)
    get_warehouse(wid, company_id)
    mid = db.execute(
        'INSERT INTO stock_moves(product_id,warehouse_id,qty,value_cents,move_type,ref_type,ref_id,date,note,'
        'lot_no,expiry_date,user_id,company_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (pid, wid, round(qty, 6), value, move_type, ref_type, ref_id, date, note, lot_no or None, expiry or None,
         _user_id(), company_id))
    _apply_level(pid, wid, qty, value, company_id)
    return mid


# --------------------------------------------------------------------------- core movements
def receive(pid, wid, qty, value_cents, move_type, date, ref_type=None, ref_id=None, note=None,
            lot_no=None, expiry_date=None, company_id=None):
    """Stock in. value_cents is the total value received (>= 0)."""
    cid = tenancy.current_company_id(company_id)
    get_product(pid, cid)
    get_warehouse(wid, cid)
    qty = round(float(qty), 6)
    if qty <= EPS or value_cents < 0:
        raise ApiError('invalid_quantity', 'Quantity must be greater than zero.', 422)
    mid = _insert_move(pid, wid, qty, int(value_cents), move_type, date, ref_type, ref_id, note, lot_no, expiry_date, cid)
    if move_type in COST_UPDATE_TYPES and value_cents > 0:
        db.execute('UPDATE products SET cost_cents=? WHERE id=? AND company_id=?',
                   (round_half_up(D(value_cents) / D(str(qty))), pid, cid))
    return mid


def allocate_lots(pid, wid, qty, lot_no=None, company_id=None):
    """FEFO allocation -> [(lot_no, expiry_date, qty)] summing to qty (untracked stock is used last)."""
    cid = tenancy.current_company_id(company_id)
    get_product(pid, cid)
    get_warehouse(wid, cid)
    layers = db.q(
        'SELECT lot_no, expiry_date, ROUND(SUM(qty),6) AS q FROM stock_moves WHERE product_id=? AND warehouse_id=? '
        'AND company_id=? AND lot_no IS NOT NULL GROUP BY lot_no, expiry_date HAVING q > 0 '
        'ORDER BY (expiry_date IS NULL), expiry_date, lot_no', (pid, wid, cid))
    parts, need = [], qty
    if lot_no:
        layers = [l for l in layers if l['lot_no'] == lot_no]
        avail = sum(l['q'] for l in layers)
        if avail + EPS < qty:
            raise ApiError('insufficient_lot', f'Lot {lot_no} has only {avail} available.', 409,
                           params={'lot': lot_no, 'available': avail})
    for l in layers:
        take = min(need, l['q'])
        parts.append((l['lot_no'], l['expiry_date'], take))
        need = round(need - take, 6)
        if need <= EPS:
            break
    if need > EPS:
        parts.append((None, None, need))
    return parts


def issue_detail(pid, wid, qty, move_type, date, ref_type=None, ref_id=None, note=None, lot_no=None, company_id=None):
    """Stock out at moving-average cost. Returns [{lot_no, expiry_date, qty, value_cents}] (values positive)."""
    cid = tenancy.current_company_id(company_id)
    qty = round(float(qty), 6)
    if qty <= EPS:
        raise ApiError('invalid_quantity', 'Quantity must be greater than zero.', 422)
    product = get_product(pid, cid)
    get_warehouse(wid, cid)
    lq, lv = level(pid, wid, cid)
    if qty > lq + EPS and not settings.get_settings(cid)['allow_negative_stock']:
        raise ApiError('insufficient_stock', f"Not enough stock for {product['name']}: {lq:g} available, {qty:g} requested.",
                       409, params={'product': product['name'], 'available': lq, 'requested': qty})
    if lq > EPS:
        if qty >= lq - EPS:
            total = lv
            if qty - lq > EPS:
                total += round_half_up(D(str(qty - lq)) * D(product['cost_cents']))
        else:
            total = round_half_up(D(lv) * D(str(qty)) / D(str(lq)))
    else:
        total = round_half_up(D(str(qty)) * D(product['cost_cents']))
    parts = allocate_lots(pid, wid, qty, lot_no, cid)
    out, remaining = [], total
    for i, (lot, exp, q) in enumerate(parts):
        v = remaining if i == len(parts) - 1 else round_half_up(D(total) * D(str(q)) / D(str(qty)))
        _insert_move(pid, wid, -q, -v, move_type, date, ref_type, ref_id, note, lot, exp, cid)
        remaining -= v
        out.append({'lot_no': lot, 'expiry_date': exp, 'qty': q, 'value_cents': v})
    return out


def issue(pid, wid, qty, move_type, date, ref_type=None, ref_id=None, note=None, lot_no=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    return sum(p['value_cents'] for p in issue_detail(pid, wid, qty, move_type, date, ref_type, ref_id, note, lot_no, cid))


def reverse_moves(ref_type, ref_id, date, note=None, company_id=None):
    """Mirror every move of a document (used when cancelling). Values are reversed exactly."""
    cid = tenancy.current_company_id(company_id)
    if db.scalar("SELECT COUNT(*) FROM stock_moves WHERE company_id=? AND ref_type=? AND ref_id=? AND move_type='reversal'",
                 (cid, ref_type, ref_id)):
        raise ApiError('already_reversed', 'Stock movements already reversed.', 409)
    allow_neg = settings.get_settings(cid)['allow_negative_stock']
    for m in db.q("SELECT * FROM stock_moves WHERE company_id=? AND ref_type=? AND ref_id=? ORDER BY id",
                  (cid, ref_type, ref_id)):
        if m['qty'] < 0:
            pass  # returning stock to the shelf is always possible
        else:
            lq, _ = level(m['product_id'], m['warehouse_id'], cid)
            if lq + (-m['qty']) < -EPS and not allow_neg:
                p = get_product(m['product_id'], cid)
                raise ApiError('cannot_reverse_stock', f"Cannot cancel: {p['name']} was already consumed or sold.",
                               409, params={'product': p['name']})
        _insert_move(m['product_id'], m['warehouse_id'], -m['qty'], -m['value_cents'], 'reversal', date,
                     ref_type, ref_id, note or f"Reversal of move {m['id']}", m['lot_no'], m['expiry_date'], cid)


# --------------------------------------------------------------------------- user operations
def _require_stock_product(pid, company_id=None):
    cid = tenancy.current_company_id(company_id)
    p = get_product(pid, cid)
    if not is_stock(p):
        raise ApiError('not_stock_item', f"{p['name']} is a service and has no stock.", 422)
    return p


def adjust(pid, wid, qty_delta, date=None, note=None, unit_cost_cents=None, lot_no=None, expiry_date=None,
           opening=False, company_id=None):
    cid = tenancy.current_company_id(company_id)
    p = _require_stock_product(pid, cid)
    get_warehouse(wid, cid)
    date = parse_date(date or today())
    settings.check_period_open(date, cid)
    qty_delta = round(float(qty_delta), 6)
    if abs(qty_delta) <= EPS:
        raise ApiError('invalid_quantity', 'Quantity cannot be zero.', 422)
    memo = f"{'Opening stock' if opening else 'Stock adjustment'}: {p['name']} ({qty_delta:+g})"
    if qty_delta > 0:
        if unit_cost_cents is None:
            lq, lv = level(pid, wid, cid)
            unit = D(lv) / D(str(lq)) if lq > EPS and lv > 0 else D(p['cost_cents'])
        else:
            unit = D(int(unit_cost_cents))
        value = round_half_up(D(str(qty_delta)) * unit)
        mid = receive(pid, wid, qty_delta, value, 'opening' if opening else 'adjust', date, 'adjustment', None,
                      note, lot_no, expiry_date, cid)
        if value > 0:
            accounting.post_entry(date, memo, [
                {'account': 'inventory', 'debit': value},
                {'account': 'opening' if opening else 'inventory_adj', 'credit': value}], 'stock_adjust', mid, company_id=cid)
    else:
        value = issue(pid, wid, -qty_delta, 'adjust', date, 'adjustment', None, note, lot_no, cid)
        if value > 0:
            accounting.post_entry(date, memo, [
                {'account': 'inventory_adj', 'debit': value}, {'account': 'inventory', 'credit': value}],
                'stock_adjust', None, company_id=cid)
    return {'product_id': pid, 'warehouse_id': wid, 'qty': qty_delta, 'value_cents': value}


def opening_stock(pid, wid, qty, unit_cost_cents, date=None, note=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    return adjust(pid, wid, qty, date, note or 'Opening balance', unit_cost_cents, opening=True, company_id=cid)


def transfer(pid, from_wid, to_wid, qty, date=None, note=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    _require_stock_product(pid, cid)
    if int(from_wid) == int(to_wid):
        raise ApiError('same_warehouse', 'Source and destination must differ.', 422)
    get_warehouse(from_wid, cid), get_warehouse(to_wid, cid)
    date = parse_date(date or today())
    settings.check_period_open(date, cid)
    parts = issue_detail(pid, from_wid, qty, 'transfer_out', date, 'transfer', None, note, company_id=cid)
    for part in parts:
        receive(pid, to_wid, part['qty'], part['value_cents'], 'transfer_in', date, 'transfer', None, note,
                part['lot_no'], part['expiry_date'], cid)
    return {'qty': float(qty), 'value_cents': sum(p['value_cents'] for p in parts)}


# --------------------------------------------------------------------------- stock counts
def create_count(warehouse_id, date=None, product_ids=None, note=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    get_warehouse(warehouse_id, cid)
    date = parse_date(date or today())
    count_id = db.execute(
        'INSERT INTO stock_counts(warehouse_id,date,note,created_by,company_id) VALUES(?,?,?,?,?)',
        (warehouse_id, date, note, _user_id(), cid))
    if product_ids:
        marks = ','.join('?' * len(product_ids))
        prods = db.q(
            f"SELECT id FROM products WHERE id IN ({marks}) AND company_id=? AND kind IN ('storable','raw','spare')",
            (*tuple(product_ids), cid))
    else:
        prods = db.q("SELECT p.id FROM products p WHERE p.company_id=? AND p.is_active=1 "
                     "AND p.kind IN ('storable','raw','spare') ORDER BY p.name", (cid,))
    for product in prods:
        db.execute('INSERT INTO stock_count_lines(count_id,product_id,system_qty,company_id) VALUES(?,?,?,?)',
                   (count_id, product['id'], level(product['id'], warehouse_id, cid)[0], cid))
    return count_id


def get_count(count_id, company_id=None):
    cid = tenancy.current_company_id(company_id)
    count = db.one(
        'SELECT c.*, w.name AS warehouse_name FROM stock_counts c '
        'JOIN warehouses w ON w.id=c.warehouse_id AND w.company_id=c.company_id '
        'WHERE c.id=? AND c.company_id=?', (count_id, cid))
    if not count:
        raise ApiError('not_found', 'Stock count not found.', 404)
    count['lines'] = db.q(
        'SELECT l.*, p.name AS product_name, p.sku, p.unit FROM stock_count_lines l '
        'JOIN products p ON p.id=l.product_id AND p.company_id=l.company_id '
        'WHERE l.count_id=? AND l.company_id=? ORDER BY p.name', (count_id, cid))
    return count


def update_count(count_id, lines, company_id=None):
    cid = tenancy.current_company_id(company_id)
    count = db.one('SELECT * FROM stock_counts WHERE id=? AND company_id=?', (count_id, cid))
    if not count:
        raise ApiError('not_found', 'Stock count not found.', 404)
    if count['status'] != 'draft':
        raise ApiError('not_draft', 'Only draft counts can be edited.', 409)
    for line in lines or []:
        counted = line.get('counted_qty')
        counted = None if counted in (None, '') else round(float(counted), 6)
        if counted is not None and counted < 0:
            raise ApiError('invalid_quantity', 'Counted quantity cannot be negative.', 422)
        db.execute('UPDATE stock_count_lines SET counted_qty=? '
                   'WHERE id=? AND count_id=? AND company_id=?',
                   (counted, line['id'], count_id, cid))


def post_count(count_id, company_id=None):
    cid = tenancy.current_company_id(company_id)
    count = db.one('SELECT * FROM stock_counts WHERE id=? AND company_id=?', (count_id, cid))
    if not count:
        raise ApiError('not_found', 'Stock count not found.', 404)
    if count['status'] != 'draft':
        raise ApiError('not_draft', 'This count is already posted.', 409)
    number = sequences.next_number('stock_count', cid)
    adjustments = 0
    lines = db.q('SELECT * FROM stock_count_lines WHERE count_id=? AND company_id=? AND counted_qty IS NOT NULL',
                 (count_id, cid))
    for line in lines:
        current = level(line['product_id'], count['warehouse_id'], cid)[0]
        diff = round(line['counted_qty'] - current, 6)
        if abs(diff) > EPS:
            adjust(line['product_id'], count['warehouse_id'], diff, count['date'],
                   f'Count {number}', company_id=cid)
            adjustments += 1
    db.execute("UPDATE stock_counts SET status='posted', number=?, posted_at=datetime('now') "
               'WHERE id=? AND company_id=?', (number, count_id, cid))
    return {'number': number, 'adjustments': adjustments}


# --------------------------------------------------------------------------- reports
def valuation(as_of=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    # Aggregates are isolated by tenant before grouping to avoid cross-company totals.
    rows = db.q(
        'SELECT * FROM (SELECT p.id AS product_id, p.sku, p.name, p.unit, p.kind, ROUND(SUM(m.qty),6) AS qty, '
        'SUM(m.value_cents) AS value_cents FROM stock_moves m JOIN products p '
        'ON p.id=m.product_id AND p.company_id=m.company_id '
        'WHERE m.company_id=? AND m.date <= ? GROUP BY p.id) '
        'WHERE ABS(qty) > 0.000001 OR value_cents != 0 ORDER BY name',
        (cid, as_of or '9999-12-31'))
    for row in rows:
        row['avg_cost_cents'] = round_half_up(D(row['value_cents']) / D(str(row['qty']))) if abs(row['qty']) > EPS else 0
    total = sum(row['value_cents'] for row in rows)
    gl = accounting.balance_of('inventory', as_of, cid)
    return {'as_of': as_of, 'rows': rows, 'total_value_cents': total, 'gl_balance_cents': gl,
            'matches_gl': total == gl}


def lots_report(company_id=None):
    cid = tenancy.current_company_id(company_id)
    rows = db.q(
        'SELECT * FROM (SELECT p.id AS product_id, p.name AS product_name, p.sku, w.id AS warehouse_id, '
        'w.name AS warehouse_name, m.lot_no, m.expiry_date, ROUND(SUM(m.qty),6) AS qty FROM stock_moves m '
        'JOIN products p ON p.id=m.product_id AND p.company_id=m.company_id '
        'JOIN warehouses w ON w.id=m.warehouse_id AND w.company_id=m.company_id '
        'WHERE m.company_id=? AND m.lot_no IS NOT NULL '
        'GROUP BY p.id, p.name, p.sku, w.id, w.name, m.product_id, m.warehouse_id, m.lot_no, m.expiry_date) '
        'WHERE qty > 0.000001 ORDER BY (expiry_date IS NULL), expiry_date, product_name', (cid,))
    now = _date.today()
    for row in rows:
        if row['expiry_date']:
            days = (_date.fromisoformat(row['expiry_date']) - now).days
            row['days_to_expiry'] = days
            row['status'] = 'expired' if days < 0 else 'expiring' if days <= 30 else 'ok'
        else:
            row['days_to_expiry'], row['status'] = None, 'ok'
    return rows


def low_stock(company_id=None):
    cid = tenancy.current_company_id(company_id)
    return db.q(
        "SELECT p.id, p.sku, p.name, p.unit, p.min_stock, COALESCE(SUM(s.qty),0) AS stock_qty FROM products p "
        "LEFT JOIN stock_levels s ON s.product_id=p.id AND s.company_id=p.company_id "
        "WHERE p.company_id=? AND p.is_active=1 AND p.kind IN ('storable','raw','spare') "
        "GROUP BY p.id HAVING stock_qty <= p.min_stock ORDER BY stock_qty - p.min_stock, p.name", (cid,))
