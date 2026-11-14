"""Company-scoped business reports and dashboard queries."""
import math
from datetime import date as _date, timedelta

from .. import db
from ..util import today
from . import accounting, inventory, tenancy

_NET = "CASE d.doc_type WHEN 'invoice' THEN %s ELSE -%s END"


def _signed(col):
    return _NET % (col, col)


def _company(company_id=None):
    return tenancy.current_company_id(company_id)


def sales_net(date_from, date_to, company_id=None):
    cid = _company(company_id)
    return db.one(
        f"SELECT COALESCE(SUM({_signed('d.subtotal_cents')}),0) AS net, "
        f"COALESCE(SUM({_signed('d.total_cents')}),0) AS gross, "
        "COALESCE(SUM(CASE d.doc_type WHEN 'invoice' THEN 1 ELSE 0 END),0) AS count "
        "FROM trade_documents d WHERE d.company_id=? AND d.status='posted' "
        "AND d.doc_type IN ('invoice','credit_note') AND d.date >= ? AND d.date <= ?",
        (cid, date_from, date_to),
    )


def sales_summary(date_from, date_to, group='day', company_id=None):
    cid = _company(company_id)
    n = 10 if group == 'day' else 7
    rows = db.q(
        f"SELECT substr(d.date,1,{n}) AS period, COALESCE(SUM({_signed('d.subtotal_cents')}),0) AS net_cents, "
        f"COALESCE(SUM({_signed('d.tax_cents')}),0) AS tax_cents, "
        f"COALESCE(SUM({_signed('d.total_cents')}),0) AS total_cents, "
        "SUM(CASE d.doc_type WHEN 'invoice' THEN 1 ELSE 0 END) AS invoices "
        "FROM trade_documents d WHERE d.company_id=? AND d.status='posted' "
        "AND d.doc_type IN ('invoice','credit_note') AND d.date >= ? AND d.date <= ? "
        "GROUP BY period ORDER BY period",
        (cid, date_from, date_to),
    )
    return {'from': date_from, 'to': date_to, 'group': group, 'rows': rows,
            'total_net_cents': sum(r['net_cents'] for r in rows),
            'total_cents': sum(r['total_cents'] for r in rows)}


def top_products(date_from, date_to, limit=10, company_id=None):
    cid = _company(company_id)
    return db.q(
        f"SELECT l.product_id, COALESCE(p.name, l.description) AS name, "
        f"SUM(CASE d.doc_type WHEN 'invoice' THEN l.qty ELSE -l.qty END) AS qty, "
        f"SUM({_signed('l.net_cents')}) AS revenue_cents "
        "FROM trade_document_lines l JOIN trade_documents d "
        "ON d.id=l.document_id AND d.company_id=l.company_id "
        "LEFT JOIN products p ON p.id=l.product_id AND p.company_id=l.company_id "
        "WHERE l.company_id=? AND d.status='posted' AND d.doc_type IN ('invoice','credit_note') "
        "AND d.date >= ? AND d.date <= ? GROUP BY COALESCE(l.product_id, l.description) "
        "ORDER BY revenue_cents DESC LIMIT ?",
        (cid, date_from, date_to, limit),
    )


def top_customers(date_from, date_to, limit=10, company_id=None):
    cid = _company(company_id)
    return db.q(
        f"SELECT d.partner_id, p.name, SUM({_signed('d.subtotal_cents')}) AS revenue_cents, "
        "SUM(CASE d.doc_type WHEN 'invoice' THEN 1 ELSE 0 END) AS invoices "
        "FROM trade_documents d JOIN partners p ON p.id=d.partner_id AND p.company_id=d.company_id "
        "WHERE d.company_id=? AND d.status='posted' AND d.doc_type IN ('invoice','credit_note') "
        "AND d.date >= ? AND d.date <= ? GROUP BY d.partner_id ORDER BY revenue_cents DESC LIMIT ?",
        (cid, date_from, date_to, limit),
    )


def profitability(date_from, date_to, company_id=None):
    cid = _company(company_id)
    rows = db.q(
        f"SELECT l.product_id, COALESCE(p.name, l.description) AS name, "
        f"SUM(CASE d.doc_type WHEN 'invoice' THEN l.qty ELSE -l.qty END) AS qty, "
        f"SUM({_signed('l.net_cents')}) AS revenue_cents, "
        f"SUM({_signed('l.cost_cents')}) AS cost_cents "
        "FROM trade_document_lines l JOIN trade_documents d "
        "ON d.id=l.document_id AND d.company_id=l.company_id "
        "LEFT JOIN products p ON p.id=l.product_id AND p.company_id=l.company_id "
        "WHERE l.company_id=? AND d.status='posted' AND d.doc_type IN ('invoice','credit_note') "
        "AND d.date >= ? AND d.date <= ? GROUP BY COALESCE(l.product_id, l.description)",
        (cid, date_from, date_to),
    )
    for row in rows:
        row['profit_cents'] = row['revenue_cents'] - row['cost_cents']
        row['margin_pct'] = round(row['profit_cents'] / row['revenue_cents'] * 100, 1) if row['revenue_cents'] else 0
    rows.sort(key=lambda row: -row['profit_cents'])
    return {'from': date_from, 'to': date_to, 'rows': rows,
            'total_revenue_cents': sum(r['revenue_cents'] for r in rows),
            'total_cost_cents': sum(r['cost_cents'] for r in rows),
            'total_profit_cents': sum(r['profit_cents'] for r in rows)}


def forecast(company_id=None):
    """Simple run-rate estimate from the last 90 days (average, not machine learning)."""
    cid = _company(company_id)
    end = _date.today()
    start = (end - timedelta(days=89)).isoformat()
    first = db.scalar(
        "SELECT MIN(date) FROM trade_documents WHERE company_id=? AND status='posted' "
        "AND doc_type='invoice' AND date >= ?", (cid, start))
    if not first:
        return None
    span = max(1, (end - _date.fromisoformat(first)).days + 1)
    net = sales_net(start, end.isoformat(), cid)['net']
    daily = net / span
    return {'days': span, 'daily_avg_cents': round(daily), 'projected_30_days_cents': round(daily * 30)}


def reorder_suggestions(company_id=None):
    """Products likely to run out within 14 days, based only on this company's stock and sales."""
    cid = _company(company_id)
    start = (_date.today() - timedelta(days=59)).isoformat()
    out = []
    for row in db.q(
        "SELECT p.id, p.sku, p.name, p.unit, SUM(-m.qty) AS sold, MIN(m.date) AS first_sale, "
        "COALESCE((SELECT SUM(s.qty) FROM stock_levels s WHERE s.product_id=p.id "
        "AND s.company_id=p.company_id),0) AS stock "
        "FROM stock_moves m JOIN products p ON p.id=m.product_id AND p.company_id=m.company_id "
        "WHERE m.company_id=? AND m.move_type='sale' AND m.date >= ? AND p.is_active=1 "
        "GROUP BY p.id", (cid, start)):
        span = max(1, (_date.today() - _date.fromisoformat(row['first_sale'])).days + 1)
        velocity = row['sold'] / span
        if velocity <= 0:
            continue
        days_left = row['stock'] / velocity
        if days_left < 14:
            out.append({'product_id': row['id'], 'sku': row['sku'], 'name': row['name'], 'unit': row['unit'],
                        'stock': row['stock'], 'daily_velocity': round(velocity, 2),
                        'days_left': max(0, round(days_left)),
                        'suggested_qty': max(1, math.ceil(velocity * 30 - row['stock']))})
    return sorted(out, key=lambda item: item['days_left'])


def dashboard(company_id=None):
    cid = _company(company_id)
    t = today()
    month_start = t[:8] + '01'
    today_sales = sales_net(t, t, cid)
    month_sales = sales_net(month_start, t, cid)
    purchases = db.scalar(
        "SELECT COALESCE(SUM(CASE doc_type WHEN 'bill' THEN subtotal_cents ELSE -subtotal_cents END),0) "
        "FROM trade_documents WHERE company_id=? AND status='posted' "
        "AND doc_type IN ('bill','debit_note') AND date >= ? AND date <= ?", (cid, month_start, t))
    receivables = db.scalar(
        "SELECT COALESCE(SUM(CASE doc_type WHEN 'invoice' THEN total_cents-paid_cents "
        "ELSE -(total_cents-paid_cents) END),0) FROM trade_documents WHERE company_id=? "
        "AND status='posted' AND doc_type IN ('invoice','credit_note')", (cid,))
    payables = db.scalar(
        "SELECT COALESCE(SUM(CASE doc_type WHEN 'bill' THEN total_cents-paid_cents "
        "ELSE -(total_cents-paid_cents) END),0) FROM trade_documents WHERE company_id=? "
        "AND status='posted' AND doc_type IN ('bill','debit_note')", (cid,))
    cash = db.scalar(
        "SELECT COALESCE(SUM(l.debit_cents-l.credit_cents),0) FROM journal_lines l "
        "JOIN accounts a ON a.id=l.account_id AND a.company_id=l.company_id "
        "JOIN journal_entries e ON e.id=l.entry_id AND e.company_id=l.company_id "
        "WHERE l.company_id=? AND a.subtype IN ('cash','bank')", (cid,))
    since = (_date.today() - timedelta(days=13)).isoformat()
    by_day = {r['period']: r['net_cents'] for r in sales_summary(since, t, company_id=cid)['rows']}
    chart = [{'date': (_date.today() - timedelta(days=13 - i)).isoformat()} for i in range(14)]
    for item in chart:
        item['net_cents'] = by_day.get(item['date'], 0)
    overdue = db.one(
        "SELECT COUNT(*) AS n, COALESCE(SUM(total_cents-paid_cents),0) AS amount FROM trade_documents "
        "WHERE company_id=? AND status='posted' AND doc_type='invoice' "
        "AND payment_status != 'paid' AND due_date < ?", (cid, t))
    return {
        'sales_today_cents': today_sales['net'], 'sales_month_cents': month_sales['net'],
        'invoices_month': month_sales['count'], 'purchases_month_cents': purchases or 0,
        'profit_month_cents': accounting.income_statement(month_start, t, cid)['net_income_cents'],
        'receivables_cents': receivables or 0, 'payables_cents': payables or 0, 'cash_cents': cash or 0,
        'stock_value_cents': accounting.balance_of('inventory', company_id=cid),
        'low_stock_count': len(inventory.low_stock(cid)),
        'sales_chart': chart, 'top_products': top_products(month_start, t, 5, cid),
        'recent_documents': db.q(
            "SELECT d.id, d.doc_type, d.number, d.date, d.total_cents, d.payment_status, "
            "p.name AS partner_name FROM trade_documents d JOIN partners p "
            "ON p.id=d.partner_id AND p.company_id=d.company_id WHERE d.company_id=? "
            "AND d.status='posted' AND d.doc_type IN ('invoice','bill') ORDER BY d.id DESC LIMIT 8", (cid,)),
        'alerts': {
            'overdue_invoices': overdue['n'], 'overdue_amount_cents': overdue['amount'],
            'expiring_lots': sum(1 for lot in inventory.lots_report(cid) if lot['status'] in ('expired', 'expiring')),
            'pending_leaves': db.scalar("SELECT COUNT(*) FROM leave_requests WHERE company_id=? AND status='pending'", (cid,)),
            'open_maintenance': db.scalar("SELECT COUNT(*) FROM maintenance_requests WHERE company_id=? "
                                          "AND status IN ('open','in_progress')", (cid,)),
            'overdue_maintenance': db.scalar(
                "SELECT COUNT(*) FROM maintenance_schedules WHERE company_id=? AND "
                "date(COALESCE(last_done, date('now','-1 year')), '+'||frequency_days||' days') < date('now')", (cid,)),
        },
    }
