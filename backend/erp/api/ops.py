"""Manufacturing, CRM actions, maintenance actions and project summaries."""
from flask import Blueprint, jsonify, request

from .. import audit, db
from ..crud import serialize
from ..errors import ApiError
from ..security import require
from ..services import manufacturing as mfg, tenancy
from ..util import parse_date, today
from .common import body, paging

bp = Blueprint('ops', __name__, url_prefix='/api')


def _cid():
    return tenancy.current_company_id()


# ------------------------------------------------------------------ manufacturing
@bp.get('/manufacturing/boms')
@require('manufacturing.read')
def boms():
    cid = _cid()
    rows = db.q('SELECT b.id FROM boms b WHERE b.company_id=? ORDER BY b.id DESC', (cid,))
    return jsonify(items=serialize([mfg.get_bom(r['id'], cid) for r in rows]))


@bp.get('/manufacturing/boms/<int:bid>')
@require('manufacturing.read')
def bom(bid):
    return jsonify(serialize(mfg.get_bom(bid, _cid())))


@bp.post('/manufacturing/boms')
@require('manufacturing.write')
def bom_create():
    with db.transaction():
        bid = mfg.save_bom(body(), company_id=_cid())
        audit.log('create', 'bom', bid)
    return jsonify(serialize(mfg.get_bom(bid, _cid()))), 201


@bp.put('/manufacturing/boms/<int:bid>')
@require('manufacturing.write')
def bom_update(bid):
    with db.transaction():
        mfg.save_bom(body(), bid, company_id=_cid())
        audit.log('update', 'bom', bid)
    return jsonify(serialize(mfg.get_bom(bid, _cid())))


@bp.delete('/manufacturing/boms/<int:bid>')
@require('manufacturing.write')
def bom_delete(bid):
    with db.transaction():
        cid = _cid()
        mfg.get_bom(bid, cid)
        if db.scalar('SELECT COUNT(*) FROM production_orders WHERE bom_id=? AND company_id=?', (bid, cid)):
            raise ApiError('in_use', 'This bill of materials has production orders; deactivate it instead.', 409)
        db.execute('DELETE FROM boms WHERE id=? AND company_id=?', (bid, cid))
        audit.log('delete', 'bom', bid)
    return jsonify(ok=True)


@bp.get('/manufacturing/orders')
@require('manufacturing.read')
def orders():
    page, per, offset = paging()
    cid = _cid()
    total = db.scalar('SELECT COUNT(*) FROM production_orders WHERE company_id=?', (cid,))
    rows = db.q('SELECT o.*, p.name AS product_name, w.name AS warehouse_name, b.name AS bom_name '
                'FROM production_orders o JOIN products p ON p.id=o.product_id AND p.company_id=o.company_id '
                'JOIN warehouses w ON w.id=o.warehouse_id AND w.company_id=o.company_id '
                'JOIN boms b ON b.id=o.bom_id AND b.company_id=o.company_id '
                'WHERE o.company_id=? ORDER BY o.id DESC LIMIT ? OFFSET ?', (cid, per, offset))
    return jsonify(items=serialize(rows), total=total, page=page, per_page=per)


@bp.post('/manufacturing/orders')
@require('manufacturing.write')
def order_create():
    b = body()
    with db.transaction():
        cid = _cid()
        oid = mfg.produce(int(b.get('bom_id') or 0), b.get('qty'), int(b.get('warehouse_id') or 0), b.get('date'), company_id=cid)
    return jsonify(serialize(db.one('SELECT * FROM production_orders WHERE id=? AND company_id=?', (oid, cid)))), 201


@bp.post('/manufacturing/orders/<int:oid>/cancel')
@require('manufacturing.write')
def order_cancel(oid):
    with db.transaction():
        mfg.cancel_production(oid, company_id=_cid())
    return jsonify(ok=True)


# ------------------------------------------------------------------ CRM
@bp.post('/crm/leads/<int:lid>/convert')
@require('crm.write', 'customers.write')
def convert_lead(lid):
    cid = _cid()
    with db.transaction():
        # The lead identifier is untrusted input: resolve it inside the active tenant.
        lead = db.one('SELECT * FROM leads WHERE id=? AND company_id=?', (lid, cid))
        if not lead:
            raise ApiError('not_found', 'Lead not found.', 404)
        if lead['status'] == 'converted':
            raise ApiError('already_converted', 'This lead is already a customer.', 409)
        pid = db.execute(
            'INSERT INTO partners(company_id,name,is_customer,phone,email,notes) VALUES(?,?,?,?,?,?)',
            (cid, lead['name'], 1, lead['phone'], lead['email'], lead['notes']))
        db.execute("UPDATE leads SET status='converted', partner_id=?, updated_at=datetime('now') "
                   "WHERE id=? AND company_id=?", (pid, lid, cid))
        audit.log('convert', 'lead', lid, {'customer_id': pid})
    return jsonify(customer_id=pid)


@bp.get('/crm/pipeline')
@require('crm.read')
def pipeline():
    cid = _cid()
    rows = db.q("SELECT stage, COUNT(*) AS count, COALESCE(SUM(value_cents),0) AS value_cents, "
                "COALESCE(SUM(value_cents*probability/100),0) AS weighted_cents "
                "FROM opportunities WHERE company_id=? GROUP BY stage ORDER BY stage", (cid,))
    return jsonify(items=serialize(rows))


# ------------------------------------------------------------------ maintenance & projects
@bp.post('/maintenance/schedules/<int:sid>/done')
@require('maintenance.write')
def schedule_done(sid):
    cid = _cid()
    date = parse_date((body() if request.data else {}).get('date') or today())
    with db.transaction():
        if not db.one('SELECT id FROM maintenance_schedules WHERE id=? AND company_id=?', (sid, cid)):
            raise ApiError('not_found', 'Schedule not found.', 404)
        db.execute("UPDATE maintenance_schedules SET last_done=?, updated_at=datetime('now') "
                   "WHERE id=? AND company_id=?", (date, sid, cid))
        audit.log('done', 'maintenance_schedule', sid, {'date': date})
    return jsonify(ok=True)


@bp.get('/projects/<int:pid>/summary')
@require('projects.read')
def project_summary(pid):
    cid = _cid()
    p = db.one('SELECT * FROM projects WHERE id=? AND company_id=?', (pid, cid))
    if not p:
        raise ApiError('not_found', 'Project not found.', 404)
    by_emp = db.q('SELECT e.id AS employee_id, e.name, SUM(tl.hours) AS hours, '
                  'CAST(ROUND(SUM(tl.hours*e.salary_cents/176.0)) AS INTEGER) AS cost_cents FROM time_logs tl '
                  'JOIN employees e ON e.id=tl.employee_id AND e.company_id=tl.company_id '
                  'WHERE tl.project_id=? AND tl.company_id=? GROUP BY e.id, e.name ORDER BY hours DESC',
                  (pid, cid))
    tasks = db.q('SELECT status, COUNT(*) AS count FROM tasks '
                 'WHERE project_id=? AND company_id=? GROUP BY status ORDER BY status', (pid, cid))
    cost = sum(r['cost_cents'] for r in by_emp)
    return jsonify(serialize({'project': p, 'by_employee': by_emp, 'tasks': tasks, 'actual_cost_cents': cost,
                              'budget_remaining_cents': p['budget_cents'] - cost,
                              'hours': sum(r['hours'] for r in by_emp)}))


def register(app):
    app.register_blueprint(bp)
