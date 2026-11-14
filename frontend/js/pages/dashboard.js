import { api } from '../core/api.js';
import { can } from '../core/state.js';
import { t } from '../core/i18n.js';
import { fmtMoney, fmtNum, shortDate } from '../core/fmt.js';
import { barChart, hbars, html, kpi, mount, statusChip, table } from '../core/ui.js';

export default {
  async render(root) {
    const d = await api.get('/reports/dashboard');
    const a = d.alerts;
    const alerts = [
      a.overdue_invoices && { href: '#/sales', text: t('dash.overdue', { n: a.overdue_invoices, amount: fmtMoney(a.overdue_amount) }) },
      d.low_stock_count && { href: '#/inventory/levels', text: t('dash.low_stock', { n: d.low_stock_count }) },
      a.expiring_lots && { href: '#/inventory/lots', text: t('dash.expiring', { n: a.expiring_lots }) },
      a.pending_leaves && { href: '#/hr/leave', text: t('dash.leaves', { n: a.pending_leaves }) },
      a.open_maintenance && { href: '#/maintenance', text: t('dash.maintenance', { n: a.open_maintenance }) },
      a.overdue_maintenance && { href: '#/maintenance/schedules', text: t('dash.maint_due', { n: a.overdue_maintenance }) },
    ].filter(Boolean);
    const docLink = (r) => (r.doc_type === 'invoice' ? '#/sales' : '#/purchases');
    mount(root, html`
      <div class="page-head"><h1>${t('nav.dashboard')}</h1><span class="muted">${new Date().toLocaleDateString('en-GB')}</span></div>
      ${alerts.length ? html`<div class="alerts">${alerts.map((x) => html`<a class="alert" href="${x.href}">⚠ ${x.text}</a>`)}</div>` : ''}
      <div class="kpis">
        ${kpi({ label: t('dash.sales_today'), value: fmtMoney(d.sales_today) })}
        ${kpi({ label: t('dash.sales_month'), value: fmtMoney(d.sales_month), sub: t('dash.invoices_n', { n: d.invoices_month }) })}
        ${kpi({ label: t('dash.profit_month'), value: fmtMoney(d.profit_month), tone: d.profit_month >= 0 ? 'ok' : 'bad' })}
        ${kpi({ label: t('dash.cash'), value: fmtMoney(d.cash) })}
        ${kpi({ label: t('dash.receivables'), value: fmtMoney(d.receivables), tone: d.receivables > 0 ? 'warn' : '' })}
        ${kpi({ label: t('dash.payables'), value: fmtMoney(d.payables) })}
        ${kpi({ label: t('dash.stock_value'), value: fmtMoney(d.stock_value) })}
        ${kpi({ label: t('dash.purchases_month'), value: fmtMoney(d.purchases_month) })}
      </div>
      <div class="dash-grid">
        <div class="card"><div class="card-head"><h3>${t('dash.sales_14')}</h3></div>
          <div class="card-pad">${barChart(d.sales_chart.map((x) => ({ label: x.date, short: shortDate(x.date), value: x.net })), { fmt: fmtMoney })}</div></div>
        <div class="card"><div class="card-head"><h3>${t('dash.top_products')}</h3></div>
          <div class="card-pad">${hbars(d.top_products.map((p) => ({ label: p.name, value: p.revenue, text: fmtMoney(p.revenue) })))}</div></div>
      </div>
      <div class="card"><div class="card-head"><h3>${t('dash.recent')}</h3></div>
        ${table({ rows: d.recent_documents, columns: [
          { label: t('f.number'), render: (r) => html`<a href="${docLink(r)}">${r.number}</a>` },
          { label: t('f.date'), key: 'date' }, { label: t('f.partner'), key: 'partner_name' },
          { label: t('f.total'), align: 'end', render: (r) => fmtMoney(r.total) },
          { label: t('f.payment_status'), render: (r) => statusChip(r.payment_status) }] })}</div>`);
  },
};
