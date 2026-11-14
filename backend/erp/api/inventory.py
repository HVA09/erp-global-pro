from flask import Blueprint, jsonify, request

from .. import audit, db
from ..crud import serialize
from ..errors import ApiError
from ..money import to_cents
from ..security import require
from ..services import inventory as inv
from .common import body, date_arg, int_arg, paging

bp = Blueprint('inventory', __name__, url_prefix='/api/inventory')


@bp.get('/levels')
@require('inventory.read')
def levels():
    a = request.args
    where, params = ["p.kind IN ('storable','raw','spare')", '(s.qty != 0 OR s.value_cents != 0)'], []
    if a.get('warehouse_id'):
        where.append('s.warehouse_id=?'), params.append(a['warehouse_id'])
    if a.get('product_id'):
        where.append('s.product_id=?'), params.append(a['product_id'])
    if a.get('q'):
        where.append('(p.name LIKE ? OR p.sku LIKE ? OR p.barcode LIKE ?)'), params.extend([f"%{a['q']}%"] * 3)
    if a.get('low') in ('1', 'true'):
        where.append('s.qty <= p.min_stock')
    base = ('FROM stock_levels s JOIN products p ON p.id=s.product_id JOIN warehouses w ON w.id=s.warehouse_id '
            'WHERE ' + ' AND '.join(where))
    page, per, offset = paging()
    if a.get('all'):
        page, per, offset = 1, 5000, 0
    total = db.scalar(f'SELECT COUNT(*) {base}', params)
    rows = db.q(f'SELECT s.product_id, s.warehouse_id, p.sku, p.name AS product_name, p.unit, p.min_stock, '
                f'w.name AS warehouse_name, s.qty, s.value_cents, CASE WHEN s.qty > 0 THEN '
                f'CAST(ROUND(s.value_cents*1.0/s.qty) AS INTEGER) ELSE 0 END AS avg_cost_cents {base} '
                f'ORDER BY p.name, w.name LIMIT ? OFFSET ?', (*params, per, offset))
    return jsonify(items=serialize(rows), total=total, page=page, per_page=per)


@bp.get('/moves')
@require('inventory.read')
def moves():
    a = request.args
    where, params = ['1=1'], []
    for key in ('product_id', 'warehouse_id', 'move_type'):
        if a.get(key):
            where.append(f'm.{key}=?'), params.append(a[key])
    if a.get('date_from'):
        where.append('m.date >= ?'), params.append(a['date_from'])
    if a.get('date_to'):
        where.append('m.date <= ?'), params.append(a['date_to'])
    if a.get('q'):
        where.append('(p.name LIKE ? OR p.sku LIKE ? OR m.note LIKE ?)'), params.extend([f"%{a['q']}%"] * 3)
    base = ('FROM stock_moves m JOIN products p ON p.id=m.product_id JOIN warehouses w ON w.id=m.warehouse_id '
            'WHERE ' + ' AND '.join(where))
    page, per, offset = paging()
    total = db.scalar(f'SELECT COUNT(*) {base}', params)
    rows = db.q(f'SELECT m.*, p.name AS product_name, p.sku, p.unit, w.name AS warehouse_name {base} '
                f'ORDER BY m.date DESC, m.id DESC LIMIT ? OFFSET ?', (*params, per, offset))
    return jsonify(items=serialize(rows), total=total, page=page, per_page=per)


@bp.post('/adjust')
@require('inventory.write')
def adjust():
    b = body()
    with db.transaction():
        try:
            qty = float(b.get('qty'))
        except (TypeError, ValueError):
            raise ApiError('invalid_quantity', 'Enter a valid quantity.', 422)
        cost = to_cents(b['unit_cost']) if b.get('unit_cost') not in (None, '') else None
        res = inv.adjust(int(b.get('product_id') or 0), int(b.get('warehouse_id') or 0), qty, b.get('date'),
                         b.get('note'), cost, b.get('lot_no'), b.get('expiry_date'), opening=bool(b.get('opening')))
        audit.log('adjust', 'stock', res['product_id'], serialize(res))
    return jsonify(serialize(res)), 201


@bp.post('/transfer')
@require('inventory.write')
def transfer():
    b = body()
    with db.transaction():
        try:
            qty = float(b.get('qty'))
        except (TypeError, ValueError):
            raise ApiError('invalid_quantity', 'Enter a valid quantity.', 422)
        res = inv.transfer(int(b.get('product_id') or 0), int(b.get('from_warehouse_id') or 0),
                           int(b.get('to_warehouse_id') or 0), qty, b.get('date'), b.get('note'))
        audit.log('transfer', 'stock', b.get('product_id'), serialize(res))
    return jsonify(serialize(res)), 201


@bp.get('/counts')
@require('inventory.read')
def counts():
    return jsonify(items=db.q('SELECT c.*, w.name AS warehouse_name, (SELECT COUNT(*) FROM stock_count_lines l '
                              'WHERE l.count_id=c.id) AS lines FROM stock_counts c JOIN warehouses w '
                              'ON w.id=c.warehouse_id ORDER BY c.id DESC'))


@bp.post('/counts')
@require('inventory.write')
def create_count():
    b = body()
    with db.transaction():
        cid = inv.create_count(int(b.get('warehouse_id') or 0), b.get('date'), b.get('product_ids'), b.get('note'))
        audit.log('create', 'stock_count', cid)
    return jsonify(serialize(inv.get_count(cid))), 201


@bp.get('/counts/<int:cid>')
@require('inventory.read')
def get_count(cid):
    return jsonify(serialize(inv.get_count(cid)))


@bp.put('/counts/<int:cid>')
@require('inventory.write')
def update_count(cid):
    with db.transaction():
        inv.update_count(cid, body().get('lines'))
    return jsonify(serialize(inv.get_count(cid)))


@bp.post('/counts/<int:cid>/post')
@require('inventory.write')
def post_count(cid):
    with db.transaction():
        res = inv.post_count(cid)
        audit.log('post', 'stock_count', cid, res)
    return jsonify(res)


@bp.get('/valuation')
@require('inventory.read')
def valuation():
    return jsonify(serialize(inv.valuation(date_arg('as_of'))))


@bp.get('/lots')
@require('inventory.read')
def lots():
    return jsonify(items=serialize(inv.lots_report()))


@bp.get('/low-stock')
@require('inventory.read')
def low_stock():
    return jsonify(items=inv.low_stock())


def register(app):
    app.register_blueprint(bp)
