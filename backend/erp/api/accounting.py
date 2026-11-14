from flask import Blueprint, jsonify, request

from .. import audit, db
from ..crud import serialize
from ..errors import ApiError
from ..security import require
from ..services import accounting as acc, assets
from ..util import today
from .common import body, date_arg, int_arg, paging

bp = Blueprint('accounting', __name__, url_prefix='/api/accounting')


@bp.get('/entries')
@require('accounting.read')
def entries():
    a = request.args
    where, params = ['1=1'], []
    if a.get('date_from'):
        where.append('e.date >= ?'), params.append(a['date_from'])
    if a.get('date_to'):
        where.append('e.date <= ?'), params.append(a['date_to'])
    if a.get('ref_type'):
        where.append('e.ref_type=?'), params.append(a['ref_type'])
    if a.get('q'):
        where.append('(e.number LIKE ? OR e.memo LIKE ?)'), params.extend([f"%{a['q']}%"] * 2)
    if a.get('account_id'):
        where.append('EXISTS (SELECT 1 FROM journal_lines l WHERE l.entry_id=e.id AND l.account_id=?)')
        params.append(a['account_id'])
    sql = ' AND '.join(where)
    page, per, offset = paging()
    total = db.scalar(f'SELECT COUNT(*) FROM journal_entries e WHERE {sql}', params)
    rows = db.q(f'SELECT e.*, (SELECT COALESCE(SUM(debit_cents),0) FROM journal_lines l WHERE l.entry_id=e.id) '
                f'AS total_cents FROM journal_entries e WHERE {sql} ORDER BY e.date DESC, e.id DESC '
                f'LIMIT ? OFFSET ?', (*params, per, offset))
    return jsonify(items=serialize(rows), total=total, page=page, per_page=per)


@bp.get('/entries/<int:eid>')
@require('accounting.read')
def entry(eid):
    return jsonify(serialize(acc.get_entry(eid)))


@bp.post('/entries')
@require('accounting.write')
def create_entry():
    with db.transaction():
        eid = acc.create_manual_entry(body())
        audit.log('create', 'journal_entry', eid)
    return jsonify(serialize(acc.get_entry(eid))), 201


@bp.post('/entries/<int:eid>/reverse')
@require('accounting.write')
def reverse(eid):
    b = body() if request.data else {}
    with db.transaction():
        e = db.one('SELECT ref_type FROM journal_entries WHERE id=?', (eid,))
        if not e:
            raise ApiError('not_found', 'Journal entry not found.', 404)
        if e['ref_type'] != 'manual':
            raise ApiError('reverse_from_source', 'Cancel this from the document that created it.', 409)
        new_id = acc.reverse_entry(eid, b.get('date'), b.get('memo'))
        audit.log('reverse', 'journal_entry', eid, {'reversal_id': new_id})
    return jsonify(serialize(acc.get_entry(new_id))), 201


@bp.get('/trial-balance')
@require('accounting.read')
def trial_balance():
    return jsonify(serialize(acc.trial_balance(date_arg('as_of'))))


@bp.get('/income-statement')
@require('accounting.read')
def income_statement():
    return jsonify(serialize(acc.income_statement(date_arg('from'), date_arg('to'))))


@bp.get('/balance-sheet')
@require('accounting.read')
def balance_sheet():
    return jsonify(serialize(acc.balance_sheet(date_arg('as_of'))))


@bp.get('/ledger')
@require('accounting.read')
def ledger():
    return jsonify(serialize(acc.general_ledger(int_arg('account_id', 0), date_arg('from'), date_arg('to'))))


@bp.get('/cash-flow')
@require('accounting.read')
def cash_flow():
    t = today()
    return jsonify(serialize(acc.cash_flow(date_arg('from', t[:8] + '01'), date_arg('to', t))))


@bp.get('/tax-summary')
@require('accounting.read')
def tax_summary():
    t = today()
    return jsonify(serialize(acc.tax_summary(date_arg('from', t[:8] + '01'), date_arg('to', t))))


@bp.get('/health')
@require('accounting.read')
def health():
    return jsonify(acc.health_check())


@bp.get('/assets')
@require('accounting.read')
def list_assets():
    return jsonify(items=serialize(assets.list_assets()))


@bp.post('/assets')
@require('accounting.write')
def create_asset():
    with db.transaction():
        aid = assets.create_asset(body())
    return jsonify(id=aid), 201


@bp.post('/assets/<int:aid>/depreciate')
@require('accounting.write')
def depreciate(aid):
    with db.transaction():
        amount = assets.depreciate(aid, body().get('period') or today()[:7])
        audit.log('depreciate', 'fixed_asset', aid, {'amount': amount / 100})
    return jsonify(amount=amount / 100)


@bp.post('/assets/depreciate-all')
@require('accounting.write')
def depreciate_all():
    res = assets.depreciate_all(body().get('period') or today()[:7])
    audit.log('depreciate_all', 'fixed_asset', None, res)
    return jsonify(res)


def register(app):
    app.register_blueprint(bp)
