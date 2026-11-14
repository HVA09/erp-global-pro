// Business reports: sales, products, customers, forecast & reorder, partner statements.
import { api } from '../core/api.js';
import { t } from '../core/i18n.js';
import { fmtMoney, fmtNum, shortDate } from '../core/fmt.js';
import { attrs, barChart, hbars, html, kpi, mount, openModal, printHtml, icon, statusChip, table } from '../core/ui.js';
import { loadRef, tabsPage } from '../core/resource.js';
import { report } from './accounting.js';

const salesTab = (c) => report(c, { title: 'rep.sales', init: { group: 'day' },
  extra: (S) => html`<select data-change="f" data-name="group" style="width:auto"><option value="day"${attrs({ selected: S.group === 'day' })}>${t('rep.by_day')}</option><option value="month"${attrs({ selected: S.group === 'month' })}>${t('rep.by_month')}</option></select>`,
  fetch: (S) => api.get('/reports/sales-summary', { from: S.from, to: S.to, group: S.group }),
  view: (d) => html`<div class="card-pad">${barChart(d.rows.map((r) => ({ label: r.period, short: shortDate(r.period), value: r.net })), { fmt: fmtMoney })}</div>
    ${table({ rows: d.rows, footer: html`<tr class="total"><td colspan="2">${t('f.total')}</td><td class="num">${fmtMoney(d.total_net)}</td><td></td><td class="num">${fmtMoney(d.total)}</td></tr>`,
      columns: [{ label: t('f.period'), key: 'period' }, { label: t('rep.invoices'), align: 'end', render: (r) => r.invoices }, { label: t('f.subtotal'), align: 'end', render: (r) => fmtMoney(r.net) },
        { label: t('f.tax'), align: 'end', render: (r) => fmtMoney(r.tax) }, { label: t('f.total'), align: 'end', render: (r) => fmtMoney(r.total) }] })}` });

const productsTab = (c) => report(c, { title: 'rep.products',
  fetch: async (S) => ({ top: (await api.get('/reports/top-products', { from: S.from, to: S.to, limit: 10 })).items, profit: await api.get('/reports/profitability', { from: S.from, to: S.to }) }),
  view: (d) => html`<div class="card-pad"><h3>${t('dash.top_products')}</h3>${hbars(d.top.map((p) => ({ label: p.name, value: p.revenue, text: fmtMoney(p.revenue) })))}</div>
    <div class="card-head"><h3>${t('rep.profitability')}</h3></div>${table({ rows: d.profit.rows, footer: html`<tr class="total"><td colspan="2">${t('f.total')}</td><td class="num">${fmtMoney(d.profit.total_revenue)}</td><td class="num">${fmtMoney(d.profit.total_cost)}</td><td class="num">${fmtMoney(d.profit.total_profit)}</td><td></td></tr>`,
      columns: [{ label: t('f.product'), key: 'name' }, { label: t('f.qty'), align: 'end', render: (r) => fmtNum(r.qty) }, { label: t('rep.revenue'), align: 'end', render: (r) => fmtMoney(r.revenue) },
        { label: t('rep.cost'), align: 'end', render: (r) => fmtMoney(r.cost) }, { label: t('rep.profit'), align: 'end', render: (r) => html`<b class="${r.profit < 0 ? 'bad-text' : 'ok-text'}">${fmtMoney(r.profit)}</b>` },
        { label: t('rep.margin'), align: 'end', render: (r) => r.margin_pct + '%' }] })}` });

const customersTab = (c) => report(c, { title: 'rep.customers', fetch: (S) => api.get('/reports/top-customers', { from: S.from, to: S.to, limit: 15 }),
  view: (d) => table({ rows: d.items, columns: [{ label: t('f.customer'), key: 'name' }, { label: t('rep.invoices'), align: 'end', render: (r) => r.invoices }, { label: t('rep.revenue'), align: 'end', render: (r) => fmtMoney(r.revenue) }] }) });

async function forecastTab(container) {
  const [f, r] = await Promise.all([api.get('/reports/forecast'), api.get('/reports/reorder')]);
  mount(container, html`<div class="kpis">${f.projected_30_days === undefined ? html`<div class="card card-pad muted">${t('rep.no_forecast')}</div>`
      : html`${kpi({ label: t('rep.daily_avg'), value: fmtMoney(f.daily_avg), sub: t('rep.based_on', { n: f.days }) })}${kpi({ label: t('rep.projected_30'), value: fmtMoney(f.projected_30_days), tone: 'ok', sub: t('rep.forecast_note') })}`}</div>
    <div class="card"><div class="card-head"><h3>${t('rep.reorder')}</h3></div>${table({ rows: r.items, empty: t('rep.reorder_empty'), columns: [{ label: t('f.sku'), key: 'sku' }, { label: t('f.product'), key: 'name' },
      { label: t('f.stock'), align: 'end', render: (x) => fmtNum(x.stock) }, { label: t('rep.velocity'), align: 'end', render: (x) => fmtNum(x.daily_velocity, 2) + '/d' },
      { label: t('rep.days_left'), align: 'end', render: (x) => html`<b class="${x.days_left <= 3 ? 'bad-text' : 'warn-text'}">${x.days_left}</b>` }, { label: t('rep.suggested'), align: 'end', render: (x) => html`<b>${fmtNum(x.suggested_qty)}</b>` }] })}</div>`);
}

export async function openStatement(partnerId, side) {
  const m = openModal({ title: t('rep.statement'), size: 'lg', body: html`<div class="loading"><div class="spinner"></div></div>` });
  const d = await api.get('/reports/statement', { partner_id: partnerId, side });
  m.setTitle(`${t('rep.statement')} — ${d.partner.name}`);
  const type = (r) => (r.type === 'payment' ? t('nav.payments') : t('doc.' + r.type));
  const body = html`<div class="kv"><div><small>${t('rep.opening')}</small>${fmtMoney(d.opening)}</div><div><small>${t('rep.closing')}</small><b>${fmtMoney(d.closing)}</b></div></div>
    ${table({ rows: d.rows, columns: [{ label: t('f.date'), key: 'date' }, { label: t('f.type'), render: type }, { label: t('f.number'), key: 'number' },
      { label: t('f.amount'), align: 'end', render: (r) => html`<span class="${r.amount < 0 ? 'ok-text' : ''}">${fmtMoney(r.amount)}</span>` }, { label: t('f.balance'), align: 'end', render: (r) => fmtMoney(r.balance) }] })}`;
  m.setBody(body);
  m.setFooter(html`<button class="btn" data-action="print">${icon('printer')} ${t('common.print')}</button>`);
  m.el.querySelector('[data-action=print]').addEventListener('click', () => printHtml(`<h1>${t('rep.statement')} — ${d.partner.name}</h1>${m.body.innerHTML}`, t('rep.statement')));
}

async function statementsTab(container) {
  const [cus, sup] = await Promise.all([loadRef({ resource: 'customers' }), loadRef({ resource: 'suppliers' })]);
  mount(container, html`<div class="card card-pad" style="max-width:520px"><div class="grid"><label class="field"><span class="field-label">${t('f.partner')}</span><select id="pid">
    <optgroup label="${t('nav.customers')}">${cus.map((p) => html`<option value="c:${p.id}">${p.name}</option>`)}</optgroup><optgroup label="${t('nav.suppliers')}">${sup.map((p) => html`<option value="s:${p.id}">${p.name}</option>`)}</optgroup></select></label>
    <button class="btn primary" id="go">${t('rep.statement')}</button></div></div>`);
  container.querySelector('#go').addEventListener('click', () => {
    const [kind, id] = container.querySelector('#pid').value.split(':');
    openStatement(id, kind === 'c' ? 'customer' : 'supplier');
  });
}

export default {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.reports')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'reports', [
      { key: 'sales', label: 'rep.sales', perm: 'reports.read', render: salesTab },
      { key: 'products', label: 'rep.products', perm: 'reports.read', render: productsTab },
      { key: 'customers', label: 'rep.customers', perm: 'reports.read', render: customersTab },
      { key: 'forecast', label: 'rep.forecast', perm: 'reports.read', render: forecastTab },
      { key: 'statements', label: 'rep.statement', perm: 'reports.read', render: statementsTab },
    ]);
  },
};
