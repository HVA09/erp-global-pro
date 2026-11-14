"""Double-entry accounting core: posting, reversal and financial reports."""
from flask import g, has_request_context

from .. import db
from ..errors import ApiError
from ..util import today
from . import sequences, settings, tenancy

SYSTEM_ACCOUNTS = {
    'cash': '1000', 'bank': '1010', 'receivable': '1100', 'advances': '1150', 'inventory': '1200',
    'vat_in': '1300', 'fixed_assets': '1500', 'accum_depr': '1590',
    'payable': '2000', 'vat_out': '2100',
    'equity': '3000', 'opening': '3900',
    'sales': '4000', 'sales_returns': '4100', 'other_income': '4900',
    'cogs': '5000', 'salaries': '5200', 'depreciation': '5300', 'inventory_adj': '5400',
    'general_expense': '5500', 'maintenance': '5600', 'absorbed': '5900',
}
CASH_SUBTYPES = ('cash', 'bank')


def resolve_account(ref, allow_inactive=False, company_id=None):
    """ref: account id (int), system key ('receivable') or account code ('1100') -> account id."""
    cid = tenancy.current_company_id(company_id)
    cache = g.setdefault('_acct_cache', {})
    key = (cid, ref, allow_inactive)
    if key in cache:
        return cache[key]
    if isinstance(ref, int):
        row = db.one('SELECT id, is_active FROM accounts WHERE id=? AND company_id=?', (ref, cid))
    else:
        code = SYSTEM_ACCOUNTS.get(ref, ref)
        row = db.one('SELECT id, is_active FROM accounts WHERE code=? AND company_id=?', (str(code), cid))
    if not row:
        raise ApiError('invalid_account', f'Unknown account: {ref}', 422, params={'account': str(ref)})
    if not row['is_active'] and not allow_inactive:
        raise ApiError('inactive_account', f'Account {ref} is inactive', 422, params={'account': str(ref)})
    cache[key] = row['id']
    return row['id']


def cash_account(account_id, company_id=None):
    """Validate that a payment/receipt account is an active cash or bank account."""
    cid = tenancy.current_company_id(company_id)
    row = db.one('SELECT id, subtype, is_active FROM accounts WHERE id=? AND company_id=?', (account_id, cid))
    if not row or not row['is_active'] or row['subtype'] not in CASH_SUBTYPES:
        raise ApiError('invalid_cash_account', 'Select a valid cash or bank account.', 422)
    return row['id']


def _current_user_id():
    u = g.get('user') if has_request_context() else None
    return u['id'] if u else None


def post_entry(date, memo, lines, ref_type=None, ref_id=None, reversal_of=None, check_lock=True,
               allow_inactive=False, company_id=None):
    """Create a balanced journal entry.
    lines: [{'account': key|code|id, 'debit': cents, 'credit': cents, 'partner_id', 'cost_center_id', 'description'}]
    Zero lines are dropped. Raises ApiError('unbalanced') if debits != credits."""
    cid = tenancy.current_company_id(company_id)
    if check_lock:
        settings.check_period_open(date, cid)
    clean = []
    for ln in lines:
        debit, credit = int(ln.get('debit') or 0), int(ln.get('credit') or 0)
        if debit < 0 or credit < 0:
            raise ApiError('negative_amount', 'Amounts must be positive.', 422)
        if debit and credit:
            raise ApiError('both_sides', 'A line cannot have both debit and credit.', 422)
        if not debit and not credit:
            continue
        partner_id = ln.get('partner_id')
        cost_center_id = ln.get('cost_center_id')
        if partner_id and not db.one('SELECT id FROM partners WHERE id=? AND company_id=?', (partner_id, cid)):
            raise ApiError('invalid_partner', 'Journal partner must belong to the active company.', 422)
        if cost_center_id and not db.one('SELECT id FROM cost_centers WHERE id=? AND company_id=?', (cost_center_id, cid)):
            raise ApiError('invalid_cost_center', 'Cost center must belong to the active company.', 422)
        clean.append((resolve_account(ln['account'], allow_inactive, cid), debit, credit, partner_id,
                      cost_center_id, ln.get('description')))
    if len(clean) < 2:
        raise ApiError('entry_needs_two_lines', 'A journal entry needs at least two non-zero lines.', 422)
    total_d, total_c = sum(c[1] for c in clean), sum(c[2] for c in clean)
    if total_d != total_c:
        raise ApiError('unbalanced', 'Debits must equal credits.', 422,
                       params={'debit': total_d / 100, 'credit': total_c / 100})
    number = sequences.next_number('journal', cid)
    entry_id = db.execute(
        'INSERT INTO journal_entries(number,date,memo,ref_type,ref_id,reversal_of,created_by,company_id) '
        'VALUES(?,?,?,?,?,?,?,?)',
        (number, date, memo, ref_type, ref_id, reversal_of, _current_user_id(), cid))
    for aid, d, c, pid, cc, desc in clean:
        db.execute('INSERT INTO journal_lines(entry_id,account_id,debit_cents,credit_cents,partner_id,'
                   'cost_center_id,description,company_id) VALUES(?,?,?,?,?,?,?,?)',
                   (entry_id, aid, d, c, pid, cc, desc, cid))
    return entry_id


def get_entry(entry_id, company_id=None):
    cid = tenancy.current_company_id(company_id)
    entry = db.one('SELECT * FROM journal_entries WHERE id=? AND company_id=?', (entry_id, cid))
    if not entry:
        raise ApiError('not_found', 'Journal entry not found.', 404)
    entry['lines'] = db.q(
        'SELECT l.*, a.code AS account_code, a.name AS account_name, p.name AS partner_name, c.name AS cost_center_name '
        'FROM journal_lines l JOIN accounts a ON a.id=l.account_id AND a.company_id=l.company_id '
        'LEFT JOIN partners p ON p.id=l.partner_id AND p.company_id=l.company_id '
        'LEFT JOIN cost_centers c ON c.id=l.cost_center_id AND c.company_id=l.company_id '
        'WHERE l.entry_id=? AND l.company_id=? ORDER BY l.id', (entry_id, cid))
    entry['total_cents'] = sum(l['debit_cents'] for l in entry['lines'])
    return entry


def reverse_entry(entry_id, date=None, memo=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    entry = db.one('SELECT * FROM journal_entries WHERE id=? AND company_id=?', (entry_id, cid))
    if not entry:
        raise ApiError('not_found', 'Journal entry not found.', 404)
    if entry['status'] == 'reversed':
        raise ApiError('already_reversed', 'This entry is already reversed.', 409)
    if entry['reversal_of']:
        raise ApiError('cannot_reverse_reversal', 'A reversal entry cannot be reversed again.', 409)
    lock = settings.get_settings(cid).get('lock_date')
    if date is None:
        date = entry['date'] if not (lock and entry['date'] <= lock) else today()
    lines = [{'account': l['account_id'], 'debit': l['credit_cents'], 'credit': l['debit_cents'],
              'partner_id': l['partner_id'], 'cost_center_id': l['cost_center_id'], 'description': l['description']}
             for l in db.q('SELECT * FROM journal_lines WHERE entry_id=? AND company_id=? ORDER BY id', (entry_id, cid))]
    new_id = post_entry(date, memo or f"Reversal of {entry['number']}", lines, entry['ref_type'], entry['ref_id'],
                        reversal_of=entry_id, allow_inactive=True, company_id=cid)
    db.execute("UPDATE journal_entries SET status='reversed' WHERE id=? AND company_id=?", (entry_id, cid))
    return new_id


def create_manual_entry(data, company_id=None):
    from ..util import parse_date
    from ..money import to_cents
    date = parse_date(data.get('date') or today())
    raw = data.get('lines') or []
    if not isinstance(raw, list) or len(raw) < 2:
        raise ApiError('entry_needs_two_lines', 'A journal entry needs at least two lines.', 422)
    lines = []
    for ln in raw:
        if not ln.get('account_id'):
            raise ApiError('invalid_account', 'Every line needs an account.', 422)
        lines.append({'account': int(ln['account_id']), 'debit': to_cents(ln.get('debit')),
                      'credit': to_cents(ln.get('credit')), 'partner_id': ln.get('partner_id') or None,
                      'cost_center_id': ln.get('cost_center_id') or None,
                      'description': (ln.get('description') or '').strip() or None})
    return post_entry(date, (data.get('memo') or '').strip() or None, lines, 'manual', company_id=company_id)


# --------------------------------------------------------------------------- reports
def natural(acc_type, debit, credit):
    return debit - credit if acc_type in ('asset', 'expense') else credit - debit


def account_totals(date_from=None, date_to=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    start, end = date_from or '0000-01-01', date_to or '9999-12-31'
    rows = db.q(
        'SELECT a.id, a.code, a.name, a.type, a.subtype, '
        'COALESCE(SUM(CASE WHEN e.date >= ? AND e.date <= ? THEN l.debit_cents END),0) AS debit_cents, '
        'COALESCE(SUM(CASE WHEN e.date >= ? AND e.date <= ? THEN l.credit_cents END),0) AS credit_cents '
        'FROM accounts a '
        'LEFT JOIN journal_lines l ON l.account_id=a.id AND l.company_id=a.company_id '
        'LEFT JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id AND e.company_id=a.company_id '
        'WHERE a.company_id=? GROUP BY a.id ORDER BY a.code',
        (start, end, start, end, cid))
    for row in rows:
        row['balance_cents'] = natural(row['type'], row['debit_cents'], row['credit_cents'])
    return rows


def balance_of(ref, as_of=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    account_id = resolve_account(ref, allow_inactive=True, company_id=cid)
    row = db.one(
        'SELECT a.type, COALESCE(SUM(l.debit_cents),0) d, COALESCE(SUM(l.credit_cents),0) c '
        'FROM accounts a LEFT JOIN journal_lines l ON l.account_id=a.id AND l.company_id=a.company_id '
        'LEFT JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id AND e.company_id=a.company_id '
        'WHERE a.id=? AND a.company_id=? AND (e.id IS NULL OR e.date <= ?)',
        (account_id, cid, as_of or '9999-12-31'))
    return natural(row['type'], row['d'], row['c'])


def trial_balance(as_of=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    rows = [r for r in account_totals(None, as_of, cid) if r['debit_cents'] or r['credit_cents']]
    out, total_debit, total_credit = [], 0, 0
    for row in rows:
        net = row['debit_cents'] - row['credit_cents']
        debit, credit = (net, 0) if net > 0 else (0, -net)
        total_debit, total_credit = total_debit + debit, total_credit + credit
        out.append({'id': row['id'], 'code': row['code'], 'name': row['name'], 'type': row['type'],
                    'debit_cents': debit, 'credit_cents': credit})
    return {'as_of': as_of, 'rows': out, 'total_debit_cents': total_debit,
            'total_credit_cents': total_credit, 'balanced': total_debit == total_credit}


def income_statement(date_from=None, date_to=None, company_id=None):
    rows = account_totals(date_from, date_to, company_id)
    revenue = [r for r in rows if r['type'] == 'revenue' and (r['debit_cents'] or r['credit_cents'])]
    cogs = [r for r in rows if r['type'] == 'expense' and r['subtype'] == 'cogs' and (r['debit_cents'] or r['credit_cents'])]
    expenses = [r for r in rows if r['type'] == 'expense' and r['subtype'] != 'cogs' and (r['debit_cents'] or r['credit_cents'])]
    total_revenue = sum(r['balance_cents'] for r in revenue)
    total_cogs = sum(r['balance_cents'] for r in cogs)
    total_expenses = sum(r['balance_cents'] for r in expenses)
    pick = lambda items: [{'code': r['code'], 'name': r['name'], 'amount_cents': r['balance_cents']} for r in items]
    return {'from': date_from, 'to': date_to, 'revenue': pick(revenue), 'cogs': pick(cogs),
            'expenses': pick(expenses), 'total_revenue_cents': total_revenue,
            'total_cogs_cents': total_cogs, 'gross_profit_cents': total_revenue - total_cogs,
            'total_expenses_cents': total_expenses,
            'net_income_cents': total_revenue - total_cogs - total_expenses}


def balance_sheet(as_of=None, company_id=None):
    rows = account_totals(None, as_of, company_id)
    pick = lambda kind: [{'code': r['code'], 'name': r['name'], 'amount_cents': r['balance_cents']}
                         for r in rows if r['type'] == kind and r['balance_cents'] != 0]
    assets, liabilities, equity = pick('asset'), pick('liability'), pick('equity')
    earnings = (sum(r['balance_cents'] for r in rows if r['type'] == 'revenue')
                - sum(r['balance_cents'] for r in rows if r['type'] == 'expense'))
    total_assets, total_liabilities, total_equity = (
        sum(x['amount_cents'] for x in items) for items in (assets, liabilities, equity))
    return {'as_of': as_of, 'assets': assets, 'liabilities': liabilities, 'equity': equity,
            'earnings_cents': earnings, 'total_assets_cents': total_assets,
            'total_liabilities_cents': total_liabilities,
            'total_equity_cents': total_equity + earnings,
            'total_liabilities_equity_cents': total_liabilities + total_equity + earnings,
            'balanced': total_assets == total_liabilities + total_equity + earnings}


def general_ledger(account_id, date_from=None, date_to=None, company_id=None):
    cid = tenancy.current_company_id(company_id)
    account = db.one('SELECT * FROM accounts WHERE id=? AND company_id=?', (account_id, cid))
    if not account:
        raise ApiError('not_found', 'Account not found.', 404)
    start, end = date_from or '0000-01-01', date_to or '9999-12-31'
    opening = db.one(
        'SELECT COALESCE(SUM(l.debit_cents),0) d, COALESCE(SUM(l.credit_cents),0) c '
        'FROM journal_lines l JOIN journal_entries e '
        'ON e.id=l.entry_id AND e.company_id=l.company_id '
        'WHERE l.account_id=? AND l.company_id=? AND e.date < ?',
        (account_id, cid, start))
    running = natural(account['type'], opening['d'], opening['c'])
    rows = db.q(
        'SELECT e.id AS entry_id, e.number, e.date, e.memo, l.debit_cents, l.credit_cents, '
        'l.description, p.name AS partner_name FROM journal_lines l '
        'JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id '
        'LEFT JOIN partners p ON p.id=l.partner_id AND p.company_id=l.company_id '
        'WHERE l.account_id=? AND l.company_id=? AND e.date >= ? AND e.date <= ? '
        'ORDER BY e.date, e.id, l.id', (account_id, cid, start, end))
    opening_balance = running
    for row in rows:
        running += natural(account['type'], row['debit_cents'], row['credit_cents'])
        row['balance_cents'] = running
    return {'account': {'id': account['id'], 'code': account['code'], 'name': account['name'], 'type': account['type']},
            'opening_balance_cents': opening_balance, 'rows': rows, 'closing_balance_cents': running,
            'total_debit_cents': sum(r['debit_cents'] for r in rows),
            'total_credit_cents': sum(r['credit_cents'] for r in rows)}


def cash_flow(date_from, date_to, company_id=None):
    cid = tenancy.current_company_id(company_id)
    cash_ids = [r['id'] for r in db.q(
        "SELECT id FROM accounts WHERE company_id=? AND subtype IN ('cash','bank')", (cid,))]
    if not cash_ids:
        return {'rows': [], 'opening_cents': 0, 'closing_cents': 0, 'net_change_cents': 0}
    marks = ','.join('?' * len(cash_ids))
    opening = db.scalar(
        f'SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l '
        f'JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id '
        f'WHERE l.company_id=? AND l.account_id IN ({marks}) AND e.date < ?',
        (cid, *cash_ids, date_from))
    entries = db.q(
        f'SELECT e.id, e.ref_type, SUM(l.debit_cents-l.credit_cents) AS net FROM journal_lines l '
        f'JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id '
        f'WHERE l.company_id=? AND l.account_id IN ({marks}) AND e.date >= ? AND e.date <= ? GROUP BY e.id',
        (cid, *cash_ids, date_from, date_to))
    buckets = {}
    for entry in entries:
        ref_type = entry['ref_type'] or 'manual'
        if ref_type == 'asset':
            category = 'investing'
        elif ref_type == 'manual' and db.scalar(
            "SELECT COUNT(*) FROM journal_lines l JOIN accounts a "
            "ON a.id=l.account_id AND a.company_id=l.company_id "
            "WHERE l.entry_id=? AND l.company_id=? AND a.type='equity'", (entry['id'], cid)):
            category = 'financing'
        else:
            category = 'operating'
        bucket = buckets.setdefault((category, ref_type), {
            'category': category, 'ref_type': ref_type, 'inflow_cents': 0, 'outflow_cents': 0})
        if entry['net'] >= 0:
            bucket['inflow_cents'] += entry['net']
        else:
            bucket['outflow_cents'] += -entry['net']
    rows = sorted(buckets.values(), key=lambda r: (r['category'], r['ref_type']))
    net = sum(r['inflow_cents'] - r['outflow_cents'] for r in rows)
    return {'from': date_from, 'to': date_to, 'rows': rows, 'opening_cents': opening,
            'net_change_cents': net, 'closing_cents': opening + net}


def tax_summary(date_from, date_to, company_id=None):
    cid = tenancy.current_company_id(company_id)
    rows = db.q(
        "SELECT d.doc_type, l.tax_rate, COALESCE(SUM(l.net_cents),0) AS net_cents, "
        "COALESCE(SUM(l.tax_cents),0) AS tax_cents FROM trade_document_lines l "
        "JOIN trade_documents d ON d.id=l.document_id AND d.company_id=l.company_id "
        "WHERE l.company_id=? AND d.status='posted' AND d.date >= ? AND d.date <= ? "
        "GROUP BY d.doc_type, l.tax_rate ORDER BY d.doc_type, l.tax_rate", (cid, date_from, date_to))
    sign = {'invoice': 1, 'credit_note': -1, 'bill': 1, 'debit_note': -1}
    output_tax = sum(sign[r['doc_type']] * r['tax_cents'] for r in rows
                     if r['doc_type'] in ('invoice', 'credit_note'))
    input_tax = sum(sign[r['doc_type']] * r['tax_cents'] for r in rows
                    if r['doc_type'] in ('bill', 'debit_note'))
    return {'from': date_from, 'to': date_to, 'rows': rows, 'output_tax_cents': output_tax,
            'input_tax_cents': input_tax, 'net_payable_cents': output_tax - input_tax}


def health_check(company_id=None):
    cid = tenancy.current_company_id(company_id)
    checks = []

    def add(key, ok, **params):
        checks.append({'key': key, 'ok': bool(ok), 'params': params})

    bad_entries = db.q(
        'SELECT e.id FROM journal_entries e JOIN journal_lines l '
        'ON l.entry_id=e.id AND l.company_id=e.company_id '
        'WHERE e.company_id=? GROUP BY e.id HAVING SUM(l.debit_cents) != SUM(l.credit_cents)', (cid,))
    add('entries_balanced', not bad_entries, count=len(bad_entries))
    totals = db.one('SELECT COALESCE(SUM(debit_cents),0) d, COALESCE(SUM(credit_cents),0) c '
                    'FROM journal_lines WHERE company_id=?', (cid,))
    add('trial_balance', totals['d'] == totals['c'], debit=totals['d'] / 100, credit=totals['c'] / 100)
    ar_gl = balance_of('receivable', company_id=cid)
    ar_sub = db.scalar("SELECT COALESCE(SUM(CASE doc_type WHEN 'invoice' THEN total_cents-paid_cents "
                       "ELSE -(total_cents-paid_cents) END),0) FROM trade_documents "
                       "WHERE company_id=? AND status='posted' AND doc_type IN ('invoice','credit_note')", (cid,))
    add('receivable_matches', ar_gl == ar_sub, ledger=ar_gl / 100, subledger=ar_sub / 100)
    ap_gl = balance_of('payable', company_id=cid)
    ap_sub = db.scalar("SELECT COALESCE(SUM(CASE doc_type WHEN 'bill' THEN total_cents-paid_cents "
                       "ELSE -(total_cents-paid_cents) END),0) FROM trade_documents "
                       "WHERE company_id=? AND status='posted' AND doc_type IN ('bill','debit_note')", (cid,))
    add('payable_matches', ap_gl == ap_sub, ledger=ap_gl / 100, subledger=ap_sub / 100)
    inventory_gl = balance_of('inventory', company_id=cid)
    levels = db.scalar('SELECT COALESCE(SUM(value_cents),0) FROM stock_levels WHERE company_id=?', (cid,))
    moves = db.scalar('SELECT COALESCE(SUM(value_cents),0) FROM stock_moves WHERE company_id=?', (cid,))
    add('inventory_matches', inventory_gl == levels == moves,
        ledger=inventory_gl / 100, stock=levels / 100)
    bad_levels = db.q(
        'SELECT m.product_id FROM (SELECT company_id, product_id, warehouse_id, ROUND(SUM(qty),6) q, '
        'SUM(value_cents) v FROM stock_moves WHERE company_id=? GROUP BY company_id,product_id,warehouse_id) m '
        'LEFT JOIN stock_levels s ON s.company_id=m.company_id AND s.product_id=m.product_id '
        'AND s.warehouse_id=m.warehouse_id WHERE ABS(COALESCE(s.qty,0)-m.q)>0.000001 '
        'OR COALESCE(s.value_cents,0)!=m.v', (cid,))
    add('stock_levels_match_moves', not bad_levels, count=len(bad_levels))
    bad_docs = db.q(
        'SELECT d.id FROM trade_documents d LEFT JOIN '
        '(SELECT company_id,document_id,SUM(net_cents) n,SUM(tax_cents) t FROM trade_document_lines '
        'WHERE company_id=? GROUP BY company_id,document_id) l '
        'ON l.document_id=d.id AND l.company_id=d.company_id '
        'WHERE d.company_id=? AND (d.subtotal_cents!=COALESCE(l.n,0) OR d.tax_cents!=COALESCE(l.t,0) '
        'OR d.total_cents!=d.subtotal_cents+d.tax_cents)', (cid, cid))
    add('document_totals', not bad_docs, count=len(bad_docs))
    bad_paid = db.q(
        "SELECT d.id FROM trade_documents d WHERE d.company_id=? AND d.status='posted' AND d.paid_cents != "
        "COALESCE((SELECT SUM(p.amount_cents) FROM payments p WHERE p.document_id=d.id "
        "AND p.company_id=d.company_id AND p.status='posted'),0) + "
        "COALESCE((SELECT SUM(c.amount_cents) FROM credit_applications c WHERE c.company_id=d.company_id "
        "AND (c.credit_id=d.id OR c.target_id=d.id)),0)", (cid,))
    add('payments_match_documents', not bad_paid, count=len(bad_paid))
    sheet = balance_sheet(company_id=cid)
    add('balance_sheet_equation', sheet['balanced'], assets=sheet['total_assets_cents'] / 100,
        liabilities_equity=sheet['total_liabilities_equity_cents'] / 100)
    negative = db.scalar('SELECT COUNT(*) FROM stock_levels WHERE company_id=? AND qty < -0.000001', (cid,))
    add('no_negative_stock', negative == 0, count=negative)
    return {'ok': all(check['ok'] for check in checks), 'checks': checks}
