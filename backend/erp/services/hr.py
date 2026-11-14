"""HR: salary advances and payroll runs posted to the general ledger."""
from .. import audit, db
from ..errors import ApiError
from ..money import to_cents
from ..util import parse_date, today, valid_period
from . import accounting, sequences, settings, tenancy


def _cid(company_id=None):
    """Resolve tenant context from the authenticated request or explicit job context."""
    return tenancy.current_company_id(company_id)


def _employee(eid, active_only=True, company_id=None):
    cid = _cid(company_id)
    e = db.one('SELECT * FROM employees WHERE id=? AND company_id=?', (eid, cid))
    if not e:
        raise ApiError('not_found', 'Employee not found.', 404)
    if active_only and e['status'] != 'active':
        raise ApiError('inactive_employee', f"{e['name']} is not active.", 422)
    return e


def add_advance(employee_id, amount_cents, account_id, date=None, note=None, company_id=None):
    cid = _cid(company_id)
    emp = _employee(employee_id, company_id=cid)
    if amount_cents <= 0:
        raise ApiError('invalid_amount', 'Amount must be greater than zero.', 422)
    account_id = accounting.cash_account(int(account_id), cid) if account_id else accounting.cash_account(
        accounting.resolve_account('cash', company_id=cid), cid)
    date = parse_date(date or today())
    settings.check_period_open(date, cid)
    aid = db.execute('INSERT INTO advances(company_id,employee_id,amount_cents,date,account_id,note) '
                     'VALUES(?,?,?,?,?,?)', (cid, employee_id, amount_cents, date, account_id, note))
    jid = accounting.post_entry(date, f"Salary advance - {emp['name']}", [
        {'account': 'advances', 'debit': amount_cents, 'description': emp['name']},
        {'account': account_id, 'credit': amount_cents}], 'advance', aid, company_id=cid)
    db.execute('UPDATE advances SET journal_entry_id=? WHERE id=? AND company_id=?', (jid, aid, cid))
    db.execute('UPDATE employees SET advance_balance_cents = advance_balance_cents + ? '
               'WHERE id=? AND company_id=?', (amount_cents, employee_id, cid))
    audit.log('advance', 'employee', employee_id, {'amount': amount_cents / 100})
    return aid


def payroll_preview(month, company_id=None):
    cid = _cid(company_id)
    month = valid_period(month)
    return {'month': month, 'exists': bool(db.scalar(
        "SELECT COUNT(*) FROM payroll_runs WHERE month=? AND status='posted' AND company_id=?", (month, cid))),
        'employees': db.q("SELECT id, name, position, salary_cents, advance_balance_cents FROM employees "
                           "WHERE status='active' AND company_id=? ORDER BY name", (cid,))}


def run_payroll(month, date, account_id, items=None, company_id=None):
    cid = _cid(company_id)
    month = valid_period(month)
    if db.scalar("SELECT COUNT(*) FROM payroll_runs WHERE month=? AND status='posted' AND company_id=?", (month, cid)):
        raise ApiError('payroll_exists', f'Payroll for {month} already exists.', 409, params={'month': month})
    date = parse_date(date or today())
    settings.check_period_open(date, cid)
    account_id = accounting.cash_account(int(account_id), cid) if account_id else accounting.cash_account(
        accounting.resolve_account('bank', company_id=cid), cid)
    if not items:
        items = [{'employee_id': e['id']} for e in db.q(
            "SELECT id FROM employees WHERE status='active' AND company_id=?", (cid,))]
    if not items:
        raise ApiError('no_employees', 'There are no active employees.', 422)
    rows, seen = [], set()
    for it in items:
        emp = _employee(int(it['employee_id']), company_id=cid)
        if emp['id'] in seen:
            raise ApiError('duplicate_employee', 'An employee appears twice.', 422)
        seen.add(emp['id'])
        bonus, ded, adv = (to_cents(it.get(k)) for k in ('bonus', 'deduction', 'advance_deduction'))
        if min(bonus, ded, adv) < 0:
            raise ApiError('invalid_amount', 'Amounts cannot be negative.', 422)
        if adv > emp['advance_balance_cents']:
            raise ApiError('advance_exceeds_balance', f"{emp['name']}: deduction exceeds the advance balance.", 422,
                           params={'employee': emp['name'], 'balance': emp['advance_balance_cents'] / 100})
        net = emp['salary_cents'] + bonus - ded - adv
        if net < 0:
            raise ApiError('negative_net_pay', f"{emp['name']}: net pay would be negative.", 422,
                           params={'employee': emp['name']})
        rows.append((emp, bonus, ded, adv, net))
    number = sequences.next_number('payroll', cid)
    total_net = sum(r[4] for r in rows)
    uid = None
    try:
        from flask import g, has_request_context
        if has_request_context() and g.get('user'):
            uid = g.user['id']
    except ImportError:
        pass
    run_id = db.execute('INSERT INTO payroll_runs(company_id,number,month,date,account_id,total_net_cents,created_by) '
                        'VALUES(?,?,?,?,?,?,?)', (cid, number, month, date, account_id, total_net, uid))
    for emp, bonus, ded, adv, net in rows:
        db.execute('INSERT INTO payroll_items(company_id,run_id,employee_id,salary_cents,bonus_cents,deduction_cents,'
                   'advance_deduction_cents,net_cents) VALUES(?,?,?,?,?,?,?,?)',
                   (cid, run_id, emp['id'], emp['salary_cents'], bonus, ded, adv, net))
        if adv:
            db.execute('UPDATE employees SET advance_balance_cents = advance_balance_cents - ? '
                       'WHERE id=? AND company_id=?', (adv, emp['id'], cid))
    expense = sum(r[0]['salary_cents'] + r[1] - r[2] for r in rows)
    advances = sum(r[3] for r in rows)
    jid = accounting.post_entry(date, f'Payroll {month} ({number})', [
        {'account': 'salaries', 'debit': expense}, {'account': 'advances', 'credit': advances},
        {'account': account_id, 'credit': total_net}], 'payroll', run_id, company_id=cid)
    db.execute('UPDATE payroll_runs SET journal_entry_id=? WHERE id=? AND company_id=?', (jid, run_id, cid))
    audit.log('payroll', 'payroll_run', run_id, {'month': month, 'net': total_net / 100})
    return run_id


def get_run(run_id, company_id=None):
    cid = _cid(company_id)
    run = db.one('SELECT * FROM payroll_runs WHERE id=? AND company_id=?', (run_id, cid))
    if not run:
        raise ApiError('not_found', 'Payroll run not found.', 404)
    run['items'] = db.q('SELECT i.*, e.name AS employee_name FROM payroll_items i '
                        'JOIN employees e ON e.id=i.employee_id AND e.company_id=i.company_id '
                        'WHERE i.run_id=? AND i.company_id=? ORDER BY e.name', (run_id, cid))
    return run


def cancel_payroll(run_id, company_id=None):
    cid = _cid(company_id)
    run = get_run(run_id, cid)
    if run['status'] != 'posted':
        raise ApiError('already_cancelled', 'Payroll already cancelled.', 409)
    settings.check_period_open(run['date'], cid)
    accounting.reverse_entry(run['journal_entry_id'], memo=f"Cancellation of payroll {run['number']}", company_id=cid)
    for it in run['items']:
        if it['advance_deduction_cents']:
            db.execute('UPDATE employees SET advance_balance_cents = advance_balance_cents + ? '
                       'WHERE id=? AND company_id=?', (it['advance_deduction_cents'], it['employee_id'], cid))
    db.execute("UPDATE payroll_runs SET status='cancelled' WHERE id=? AND company_id=?", (run_id, cid))
    audit.log('cancel_payroll', 'payroll_run', run_id, {'number': run['number']})
