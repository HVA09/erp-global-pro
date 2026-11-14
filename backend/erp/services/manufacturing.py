"""Bills of materials and production orders (materials consumed at average cost, labour/overhead absorbed)."""
from decimal import Decimal as D

from .. import audit, db
from ..errors import ApiError
from ..money import round_half_up, to_cents
from ..util import parse_date, today
from . import accounting, inventory, sequences, settings, tenancy


def _cid(company_id=None):
    return tenancy.current_company_id(company_id)


def get_bom(bom_id, company_id=None):
    cid = _cid(company_id)
    b = db.one('SELECT b.*, p.name AS product_name, p.sku FROM boms b '
               'JOIN products p ON p.id=b.product_id AND p.company_id=b.company_id '
               'WHERE b.id=? AND b.company_id=?', (bom_id, cid))
    if not b:
        raise ApiError('not_found', 'Bill of materials not found.', 404)
    comps = db.q('SELECT c.*, p.name AS product_name, p.sku, p.unit, p.cost_cents, '
                 'COALESCE((SELECT SUM(s.value_cents) FROM stock_levels s '
                 'WHERE s.product_id=c.product_id AND s.company_id=c.company_id),0) AS sv, '
                 'COALESCE((SELECT SUM(s.qty) FROM stock_levels s '
                 'WHERE s.product_id=c.product_id AND s.company_id=c.company_id),0) AS sq '
                 'FROM bom_components c JOIN products p ON p.id=c.product_id AND p.company_id=c.company_id '
                 'WHERE c.bom_id=? AND c.company_id=? ORDER BY c.id', (bom_id, cid))
    material = 0
    for c in comps:
        unit = D(c['sv']) / D(str(c['sq'])) if c['sq'] > 1e-9 and c['sv'] > 0 else D(c['cost_cents'])
        c['unit_cost_cents'] = round_half_up(unit)
        c['line_cost_cents'] = round_half_up(D(str(c['qty'])) * unit)
        material += c['line_cost_cents']
        for k in ('sv', 'sq', 'cost_cents'):
            c.pop(k)
    b['components'] = comps
    b['material_cost_cents'] = material
    b['unit_cost_cents'] = material + b['labor_cost_cents'] + b['overhead_cost_cents']
    return b


def save_bom(data, bom_id=None, company_id=None):
    cid = _cid(company_id)
    name = (data.get('name') or '').strip()
    if not name:
        raise ApiError('validation', 'Name is required.', 422, details={'name': 'required'})
    product = inventory.get_product(int(data.get('product_id') or 0), cid)
    if not inventory.is_stock(product):
        raise ApiError('not_stock_item', 'The finished product must be a stock item.', 422)
    comps = data.get('components') or []
    if not comps:
        raise ApiError('no_components', 'Add at least one component.', 422)
    seen, clean = set(), []
    for c in comps:
        cp = inventory.get_product(int(c.get('product_id') or 0), cid)
        qty = float(c.get('qty') or 0)
        if not inventory.is_stock(cp) or cp['id'] == product['id'] or cp['id'] in seen or qty <= 0:
            raise ApiError('invalid_component', f"Invalid component: {cp['name']}", 422, params={'product': cp['name']})
        seen.add(cp['id'])
        clean.append((cp['id'], qty))
    labor, overhead = to_cents(data.get('labor_cost')), to_cents(data.get('overhead_cost'))
    active = 1 if data.get('is_active', True) else 0
    if bom_id:
        get_bom(bom_id, cid)
        db.execute('UPDATE boms SET name=?, product_id=?, labor_cost_cents=?, overhead_cost_cents=?, is_active=? '
                   'WHERE id=? AND company_id=?', (name, product['id'], labor, overhead, active, bom_id, cid))
        db.execute('DELETE FROM bom_components WHERE bom_id=? AND company_id=?', (bom_id, cid))
    else:
        bom_id = db.execute('INSERT INTO boms(company_id,name,product_id,labor_cost_cents,overhead_cost_cents,is_active) '
                            'VALUES(?,?,?,?,?,?)', (cid, name, product['id'], labor, overhead, active))
    for pid, qty in clean:
        db.execute('INSERT INTO bom_components(company_id,bom_id,product_id,qty) VALUES(?,?,?,?)',
                   (cid, bom_id, pid, qty))
    return bom_id


def produce(bom_id, qty, warehouse_id, date=None, company_id=None):
    cid = _cid(company_id)
    bom = get_bom(bom_id, cid)
    if not bom['is_active']:
        raise ApiError('inactive_bom', 'This bill of materials is inactive.', 422)
    qty = round(float(qty), 6)
    if qty <= 0:
        raise ApiError('invalid_quantity', 'Quantity must be greater than zero.', 422)
    inventory.get_warehouse(warehouse_id, cid)
    date = parse_date(date or today())
    settings.check_period_open(date, cid)
    number = sequences.next_number('production', cid)
    uid = None
    try:
        from flask import g, has_request_context
        if has_request_context() and g.get('user'):
            uid = g.user['id']
    except ImportError:
        pass
    oid = db.execute('INSERT INTO production_orders(company_id,number,bom_id,product_id,warehouse_id,qty,date,created_by) '
                     'VALUES(?,?,?,?,?,?,?,?)', (cid, number, bom_id, bom['product_id'], warehouse_id, qty, date, uid))
    material = 0
    for c in bom['components']:
        material += inventory.issue(c['product_id'], warehouse_id, round(c['qty'] * qty, 6), 'production_out', date,
                                    'production', oid, number, company_id=cid)
    labor = round_half_up(D(bom['labor_cost_cents']) * D(str(qty)))
    overhead = round_half_up(D(bom['overhead_cost_cents']) * D(str(qty)))
    total = material + labor + overhead
    inventory.receive(bom['product_id'], warehouse_id, qty, total, 'production_in', date, 'production', oid, number,
                       company_id=cid)
    jid = accounting.post_entry(date, f'Production {number}: {bom["product_name"]} x {qty:g}', [
        {'account': 'inventory', 'debit': total, 'description': 'Finished goods'},
        {'account': 'inventory', 'credit': material, 'description': 'Raw materials consumed'},
        {'account': 'absorbed', 'credit': labor + overhead, 'description': 'Labour & overhead absorbed'}],
        'production', oid, company_id=cid)
    db.execute('UPDATE production_orders SET material_cost_cents=?, labor_cost_cents=?, overhead_cost_cents=?, '
               'total_cost_cents=?, journal_entry_id=? WHERE id=? AND company_id=?',
               (material, labor, overhead, total, jid, oid, cid))
    audit.log('produce', 'production_order', oid, {'number': number, 'qty': qty, 'cost': total / 100})
    return oid


def cancel_production(order_id, company_id=None):
    cid = _cid(company_id)
    o = db.one('SELECT * FROM production_orders WHERE id=? AND company_id=?', (order_id, cid))
    if not o:
        raise ApiError('not_found', 'Production order not found.', 404)
    if o['status'] != 'done':
        raise ApiError('already_cancelled', 'Production order already cancelled.', 409)
    settings.check_period_open(o['date'], cid)
    inventory.reverse_moves('production', order_id, o['date'], f"Cancel {o['number']}", company_id=cid)
    accounting.reverse_entry(o['journal_entry_id'], memo=f"Cancellation of {o['number']}", company_id=cid)
    db.execute("UPDATE production_orders SET status='cancelled' WHERE id=? AND company_id=?", (order_id, cid))
    audit.log('cancel_production', 'production_order', order_id, {'number': o['number']})
