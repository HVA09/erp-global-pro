from flask import Blueprint, jsonify, request

from .. import audit, db
from ..crud import serialize
from ..errors import ApiError
from ..money import to_cents
from ..security import require
from ..services import hr, tenancy
from ..util import parse_date, today
from .common import body, paging

bp = Blueprint('hr', __name__, url_prefix='/api/hr')


def _cid():
    return tenancy.current_company_id()


@bp.get('/attendance-sheet')
@require('hr.read')
def attendance_sheet():
    date = parse_date(request.args.get('date') or today())
    cid = _cid()
    rows = db.q("SELECT e.id AS employee_id, e.name, e.position, a.status FROM employees e "
                "LEFT JOIN attendance a ON a.employee_id=e.id AND a.date=? AND a.company_id=e.company_id "
                "WHERE e.status='active' AND e.company_id=? ORDER BY e.name", (date, cid))
    return jsonify(date=date, items=rows)


@bp.put('/attendance')
@require('hr.write')
def save_attendance():
    b = body()
    date = parse_date(b.get('date') or today())
    cid = _cid()
    with db.transaction():
        for r in b.get('records') or []:
            if r.get('status') not in ('present', 'late', 'absent', 'leave'):
                raise ApiError('invalid_choice', 'Invalid attendance status.', 422)
            if not db.one('SELECT id FROM employees WHERE id=? AND company_id=?', (r.get('employee_id'), cid)):
                raise ApiError('not_found', 'Employee not found.', 404)
            db.execute('INSERT INTO attendance(company_id,employee_id,date,status) VALUES(?,?,?,?) '
                       'ON CONFLICT(company_id,employee_id,date) DO UPDATE SET status=excluded.status',
                       (cid, r['employee_id'], date, r['status']))
        audit.log('update', 'attendance', None, {'date': date, 'count': len(b.get('records') or [])})
    return jsonify(ok=True)


@bp.get('/advances')
@require('hr.read')
def advances():
    where, params = ['a.company_id=?'], [_cid()]
    if request.args.get('employee_id'):
        where.append('a.employee_id=?'), params.append(request.args['employee_id'])
    page, per, offset = paging()
    total = db.scalar(f"SELECT COUNT(*) FROM advances a WHERE {' AND '.join(where)}", params)
    rows = db.q(f"SELECT a.*, e.name AS employee_name FROM advances a JOIN employees e ON e.id=a.employee_id AND e.company_id=a.company_id "
                f"WHERE {' AND '.join(where)} ORDER BY a.date DESC, a.id DESC LIMIT ? OFFSET ?",
                (*params, per, offset))
    return jsonify(items=serialize(rows), total=total, page=page, per_page=per)


@bp.post('/advances')
@require('hr.write')
def create_advance():
    b = body()
    with db.transaction():
        aid = hr.add_advance(int(b.get('employee_id') or 0), to_cents(b.get('amount')), b.get('account_id'),
                             b.get('date'), b.get('note'))
    return jsonify(id=aid), 201


@bp.get('/payroll/preview')
@require('hr.read')
def payroll_preview():
    return jsonify(serialize(hr.payroll_preview(request.args.get('month') or today()[:7])))


@bp.get('/payroll')
@require('hr.read')
def payroll_list():
    return jsonify(items=serialize(db.q(
        'SELECT r.*, (SELECT COUNT(*) FROM payroll_items i WHERE i.run_id=r.id AND i.company_id=r.company_id) AS employees '
        'FROM payroll_runs r WHERE r.company_id=? ORDER BY r.month DESC, r.id DESC', (_cid(),))))


@bp.get('/payroll/<int:rid>')
@require('hr.read')
def payroll_get(rid):
    return jsonify(serialize(hr.get_run(rid)))


@bp.post('/payroll')
@require('hr.write')
def payroll_run():
    b = body()
    with db.transaction():
        rid = hr.run_payroll(b.get('month') or today()[:7], b.get('date'), b.get('account_id'), b.get('items'))
    return jsonify(serialize(hr.get_run(rid))), 201


@bp.post('/payroll/<int:rid>/cancel')
@require('hr.write')
def payroll_cancel(rid):
    with db.transaction():
        hr.cancel_payroll(rid)
    return jsonify(serialize(hr.get_run(rid)))


def register(app):
    app.register_blueprint(bp)
