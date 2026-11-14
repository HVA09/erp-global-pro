// Accounting: journal, chart of accounts, ledger and the financial statements, assets, health check.
import { api } from '../core/api.js';
import { can } from '../core/state.js';
import { errMsg, t } from '../core/i18n.js';
import { fmtMoney, fmtNum, monthStart, today } from '../core/fmt.js';
import { attrs, bind, confirmDialog, debounce, field, html, icon, mount, notifyError, openModal, pager, printHtml, raw,
  readForm, statusChip, submitForm, table, toast } from '../core/ui.js';
import { clearRefCache, loadRef, refOptions, resourceView, tabsPage } from '../core/resource.js';

const TYPES = ['asset', 'liability', 'equity', 'revenue', 'expense'];
const REFS = ['document', 'payment', 'manual', 'stock_adjust', 'payroll', 'advance', 'production', 'asset', 'depreciation'];
const natural = (a) => (['asset', 'expense'].includes(a.type) ? a.net : -a.net);
const accLabel = (a) => `${a.code} — ${a.name}`;

/** Generic report shell: date controls + print + a body rendered from fetched data. */
export function report(container, { range = true, asOf = false, init = {}, extra, fetch, view, title }) {
  const S = { from: monthStart(), to: today(), as_of: today(), ...init };
  async function load() {
    const data = await fetch(S);
    mount(container, html`<div class="toolbar">
      ${range ? html`<label class="row">${t('common.from')} <input type="date" value="${S.from}" data-change="f" data-name="from" style="width:auto"></label>
        <label class="row">${t('common.to')} <input type="date" value="${S.to}" data-change="f" data-name="to" style="width:auto"></label>` : ''}
      ${asOf ? html`<label class="row">${t('rep.as_of')} <input type="date" value="${S.as_of}" data-change="f" data-name="as_of" style="width:auto"></label>` : ''}
      ${extra ? extra(S, data) : ''}<span class="grow"></span><button class="btn" data-action="print">${icon('printer')} ${t('common.print')}</button></div>
      <div class="card" id="rep">${view(data, S)}</div>`);
  }
  bind(container, {
    change: { f: (el) => { S[el.dataset.name] = el.value; return load(); } },
    click: { print: () => printHtml(`<h1>${t(title)}</h1>${container.querySelector('#rep').innerHTML}`, t(title)) },
  });
  return load();
}
const statement = (rows) => html`<table class="table"><tbody>${rows.map((r) => (r.head
  ? html`<tr class="total"><td colspan="2">${r.head}</td></tr>`
  : html`<tr class="${r.total ? 'total' : ''}"><td>${r.label}</td><td class="num">${fmtMoney(r.amount)}</td></tr>`))}</tbody></table>`;

// ---------------------------------------------------------------- journal
async function journalTab(container) {
  const S = { q: '', ref_type: '', date_from: '', date_to: '', page: 1 };
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = await api.get('/accounting/entries', { ...S, per_page: 25 });
    mount(root, html`<div class="toolbar"><input class="search" type="search" placeholder="${t('common.search')}" value="${S.q}" data-input="search">
      <select data-change="f" data-name="ref_type" style="width:auto"><option value="">${t('acc.all_sources')}</option>${REFS.map((r) => html`<option value="${r}"${attrs({ selected: S.ref_type === r })}>${t('ref.' + r)}</option>`)}</select>
      <input type="date" value="${S.date_from}" data-change="f" data-name="date_from" style="width:auto"><input type="date" value="${S.date_to}" data-change="f" data-name="date_to" style="width:auto"><span class="grow"></span>
      ${can('accounting.write') ? html`<button class="btn primary" data-action="new">${icon('plus')} ${t('acc.new_entry')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, rowAttr: (r) => attrs({ class: 'click', 'data-action': 'open', 'data-id': r.id }), columns: [
        { label: t('f.number'), key: 'number' }, { label: t('f.date'), key: 'date' }, { label: t('f.memo'), key: 'memo' },
        { label: t('f.source'), render: (r) => t('ref.' + (r.ref_type || 'manual')) }, { label: t('f.amount'), align: 'end', render: (r) => fmtMoney(r.total) },
        { label: t('f.status'), render: (r) => (r.reversal_of ? html`<span class="chip">${t('acc.reversal')}</span>` : statusChip(r.status)) }] })}${pager(d)}</div>`);
    const s = root.querySelector('input[type=search]'); if (S.focus) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  bind(root, {
    click: { page: (el) => { S.page = Number(el.dataset.page); S.focus = false; return load(); }, new: () => openEntryForm(load), open: (el) => openEntry(el.dataset.id, load) },
    change: { f: (el) => { S[el.dataset.name] = el.value; S.page = 1; S.focus = false; return load(); } },
    input: { search: debounce((el) => { S.q = el.value.trim(); S.page = 1; S.focus = true; load().catch(notifyError); }, 300) },
  });
  await load();
}

export async function openEntry(id, done) {
  const m = openModal({ title: '…', size: 'lg', body: html`<div class="loading"><div class="spinner"></div></div>` });
  const e = await api.get(`/accounting/entries/${id}`);
  m.setTitle(`${t('acc.entry')} ${e.number}`);
  m.setBody(html`<div class="kv"><div><small>${t('f.date')}</small>${e.date}</div><div><small>${t('f.source')}</small>${t('ref.' + (e.ref_type || 'manual'))}</div><div><small>${t('f.status')}</small>${e.reversal_of ? t('acc.reversal') : t('status.' + e.status)}</div></div>
    ${e.memo ? html`<p>${e.memo}</p>` : ''}${table({ rows: e.lines, footer: html`<tr class="total"><td colspan="3">${t('f.total')}</td><td class="num">${fmtMoney(e.total)}</td><td class="num">${fmtMoney(e.total)}</td></tr>`, columns: [
      { label: t('f.account'), render: (l) => `${l.account_code} — ${l.account_name}` }, { label: t('f.partner'), render: (l) => l.partner_name || '' }, { label: t('f.description'), render: (l) => l.description || '' },
      { label: t('acc.debit'), align: 'end', render: (l) => (l.debit ? fmtMoney(l.debit) : '') }, { label: t('acc.credit'), align: 'end', render: (l) => (l.credit ? fmtMoney(l.credit) : '') }] })}`);
  if (e.ref_type === 'manual' && e.status === 'posted' && !e.reversal_of && can('accounting.write')) {
    m.setFooter(html`<button class="btn danger" data-action="reverse">${t('acc.reverse')}</button>`);
    bind(m.el, { click: { reverse: async () => {
      if (!(await confirmDialog(t('acc.confirm_reverse'), { danger: true }))) return;
      await api.post(`/accounting/entries/${id}/reverse`, {}); m.close(); toast(t('common.saved'), 'ok'); done?.();
    } } });
  }
}

async function openEntryForm(done) {
  const accounts = (await loadRef({ resource: 'accounts', params: { is_active: 'true' } }));
  const lines = [{ account_id: '', debit: '', credit: '', description: '' }, { account_id: '', debit: '', credit: '', description: '' }];
  const id = 'je' + Date.now();
  const opts = refOptions(accounts, accLabel);
  const rows = () => html`<table class="lines"><thead><tr><th>${t('f.account')}</th><th>${t('f.description')}</th><th>${t('acc.debit')}</th><th>${t('acc.credit')}</th><th></th></tr></thead><tbody>
    ${lines.map((l, i) => html`<tr><td style="min-width:240px"><select data-change="acct" data-i="${i}">${opts.map((o) => html`<option value="${o.value}"${attrs({ selected: String(o.value) === String(l.account_id) })}>${o.label}</option>`)}</select></td>
      <td><input value="${l.description}" data-input="line" data-i="${i}" data-f="description"></td>
      <td><input class="w-price" type="number" step="0.01" min="0" value="${l.debit}" data-input="line" data-i="${i}" data-f="debit"></td>
      <td><input class="w-price" type="number" step="0.01" min="0" value="${l.credit}" data-input="line" data-i="${i}" data-f="credit"></td>
      <td><button type="button" class="icon-btn" data-action="rm" data-i="${i}">${icon('x')}</button></td></tr>`)}</tbody></table>`;
  const m = openModal({ title: t('acc.new_entry'), size: 'lg',
    body: html`<form id="${id}" data-submit="save"><div class="grid g2" style="margin-bottom:12px">${field({ name: 'date', label: t('f.date'), type: 'date', value: today(), required: true })}${field({ name: 'memo', label: t('f.memo') })}</div>
      <div id="lines">${rows()}</div><button type="button" class="btn sm" data-action="add" style="margin-top:8px">${icon('plus')} ${t('doc.add_line')}</button><div class="totals" id="tot"></div></form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}" id="je-save">${t('common.save')}</button>` });
  const sum = (k) => lines.reduce((s, l) => s + (Number(l[k]) || 0), 0);
  function recalc() {
    const d = Math.round(sum('debit') * 100), c = Math.round(sum('credit') * 100);
    mount(m.el.querySelector('#tot'), html`<div><span>${t('acc.debit')}</span><b>${fmtMoney(d / 100)}</b></div><div><span>${t('acc.credit')}</span><b>${fmtMoney(c / 100)}</b></div>
      <div class="grand"><span>${t('acc.difference')}</span><span class="${d === c && d > 0 ? 'ok-text' : 'bad-text'}">${fmtMoney((d - c) / 100)}</span></div>`);
    m.el.querySelector('#je-save').disabled = !(d === c && d > 0);
  }
  const redraw = () => { mount(m.el.querySelector('#lines'), rows()); recalc(); };
  bind(m.el, {
    click: { add: () => { lines.push({ account_id: '', debit: '', credit: '', description: '' }); redraw(); }, rm: (el) => { if (lines.length > 2) { lines.splice(Number(el.dataset.i), 1); redraw(); } } },
    change: { acct: (el) => { lines[Number(el.dataset.i)].account_id = el.value; } },
    input: { line: (el) => { lines[Number(el.dataset.i)][el.dataset.f] = el.value; if (el.dataset.f !== 'description') recalc(); } },
    submit: { save: (form) => submitForm(form, async () => {
      const v = readForm(form);
      await api.post('/accounting/entries', { date: v.date, memo: v.memo, lines: lines.filter((l) => l.account_id || l.debit || l.credit) });
      m.close(); toast(t('common.saved'), 'ok'); done?.();
    }) },
  });
  recalc();
}

// ---------------------------------------------------------------- reports
const ledgerTab = async (container) => {
  const accounts = await loadRef({ resource: 'accounts' });
  return report(container, { title: 'acc.ledger', init: { account_id: accounts[0]?.id || '' },
    extra: (S) => html`<select data-change="f" data-name="account_id" style="width:auto;max-width:320px">${accounts.map((a) => html`<option value="${a.id}"${attrs({ selected: String(a.id) === String(S.account_id) })}>${accLabel(a)}</option>`)}</select>`,
    fetch: (S) => api.get('/accounting/ledger', { account_id: S.account_id, from: S.from, to: S.to }),
    view: (d) => table({ rows: d.rows, footer: html`<tr class="total"><td colspan="3">${t('acc.closing')}</td><td class="num">${fmtMoney(d.total_debit)}</td><td class="num">${fmtMoney(d.total_credit)}</td><td class="num">${fmtMoney(d.closing_balance)}</td></tr>`,
      columns: [{ label: t('f.date'), key: 'date' }, { label: t('f.number'), key: 'number' }, { label: t('f.memo'), render: (r) => r.memo || r.description || '' },
        { label: t('acc.debit'), align: 'end', render: (r) => (r.debit ? fmtMoney(r.debit) : '') }, { label: t('acc.credit'), align: 'end', render: (r) => (r.credit ? fmtMoney(r.credit) : '') },
        { label: t('f.balance'), align: 'end', render: (r) => fmtMoney(r.balance) }] }) });
};
const trialTab = (c) => report(c, { title: 'acc.trial', range: false, asOf: true, fetch: (S) => api.get('/accounting/trial-balance', { as_of: S.as_of }),
  view: (d) => html`${table({ rows: d.rows, footer: html`<tr class="total"><td colspan="2">${t('f.total')}</td><td class="num">${fmtMoney(d.total_debit)}</td><td class="num">${fmtMoney(d.total_credit)}</td></tr>`,
    columns: [{ label: t('f.code'), key: 'code' }, { label: t('f.name'), key: 'name' }, { label: t('acc.debit'), align: 'end', render: (r) => (r.debit ? fmtMoney(r.debit) : '') }, { label: t('acc.credit'), align: 'end', render: (r) => (r.credit ? fmtMoney(r.credit) : '') }] })}
    <div class="card-pad">${d.balanced ? html`<span class="chip chip-ok">${t('acc.balanced')}</span>` : html`<span class="chip chip-bad">${t('acc.unbalanced')}</span>`}</div>` });
const incomeTab = (c) => report(c, { title: 'acc.income', fetch: (S) => api.get('/accounting/income-statement', { from: S.from, to: S.to }),
  view: (d) => statement([{ head: t('acc.revenue') }, ...d.revenue.map((r) => ({ label: `${r.code} ${r.name}`, amount: r.amount })), { label: t('acc.total_revenue'), amount: d.total_revenue, total: true },
    { head: t('acc.cogs') }, ...d.cogs.map((r) => ({ label: `${r.code} ${r.name}`, amount: r.amount })), { label: t('acc.gross_profit'), amount: d.gross_profit, total: true },
    { head: t('acc.expenses') }, ...d.expenses.map((r) => ({ label: `${r.code} ${r.name}`, amount: r.amount })), { label: t('acc.total_expenses'), amount: d.total_expenses, total: true },
    { label: t('acc.net_income'), amount: d.net_income, total: true }]) });
const balanceTab = (c) => report(c, { title: 'acc.balance', range: false, asOf: true, fetch: (S) => api.get('/accounting/balance-sheet', { as_of: S.as_of }),
  view: (d) => html`${statement([{ head: t('acc.assets') }, ...d.assets.map((r) => ({ label: `${r.code} ${r.name}`, amount: r.amount })), { label: t('acc.total_assets'), amount: d.total_assets, total: true },
    { head: t('acc.liabilities') }, ...d.liabilities.map((r) => ({ label: `${r.code} ${r.name}`, amount: r.amount })), { label: t('acc.total_liabilities'), amount: d.total_liabilities, total: true },
    { head: t('acc.equity') }, ...d.equity.map((r) => ({ label: `${r.code} ${r.name}`, amount: r.amount })), { label: t('acc.current_earnings'), amount: d.earnings },
    { label: t('acc.total_equity'), amount: d.total_equity, total: true }, { label: t('acc.total_le'), amount: d.total_liabilities_equity, total: true }])}
    <div class="card-pad">${d.balanced ? html`<span class="chip chip-ok">${t('acc.balanced')}</span>` : html`<span class="chip chip-bad">${t('acc.unbalanced')}</span>`}</div>` });
const cashTab = (c) => report(c, { title: 'acc.cashflow', fetch: (S) => api.get('/accounting/cash-flow', { from: S.from, to: S.to }),
  view: (d) => html`${table({ rows: d.rows, footer: html`<tr class="total"><td colspan="2">${t('acc.opening_cash')}</td><td class="num" colspan="2">${fmtMoney(d.opening)}</td></tr><tr class="total"><td colspan="2">${t('acc.net_change')}</td><td class="num" colspan="2">${fmtMoney(d.net_change)}</td></tr><tr class="total"><td colspan="2">${t('acc.closing_cash')}</td><td class="num" colspan="2">${fmtMoney(d.closing)}</td></tr>`,
    columns: [{ label: t('acc.activity'), render: (r) => t('cf.' + r.category) }, { label: t('f.source'), render: (r) => t('ref.' + r.ref_type) }, { label: t('acc.inflow'), align: 'end', render: (r) => fmtMoney(r.inflow) }, { label: t('acc.outflow'), align: 'end', render: (r) => fmtMoney(r.outflow) }] })}` });
const taxTab = (c) => report(c, { title: 'acc.tax', fetch: (S) => api.get('/accounting/tax-summary', { from: S.from, to: S.to }),
  view: (d) => html`${table({ rows: d.rows, columns: [{ label: t('f.type'), render: (r) => t('doc.' + r.doc_type) }, { label: t('f.tax_rate'), align: 'end', render: (r) => r.tax_rate + '%' }, { label: t('f.subtotal'), align: 'end', render: (r) => fmtMoney(r.net) }, { label: t('f.tax'), align: 'end', render: (r) => fmtMoney(r.tax) }] })}
    <div class="card-pad"><div class="totals"><div><span>${t('acc.output_tax')}</span><b>${fmtMoney(d.output_tax)}</b></div><div><span>${t('acc.input_tax')}</span><b>${fmtMoney(d.input_tax)}</b></div><div class="grand"><span>${t('acc.net_tax')}</span><span>${fmtMoney(d.net_payable)}</span></div></div></div>` });
const ageingTab = (c) => report(c, { title: 'acc.ageing', range: false, asOf: true, init: { side: 'customer' },
  extra: (S) => html`<select data-change="f" data-name="side" style="width:auto"><option value="customer"${attrs({ selected: S.side === 'customer' })}>${t('acc.receivable')}</option><option value="supplier"${attrs({ selected: S.side === 'supplier' })}>${t('acc.payable')}</option></select>`,
  fetch: (S) => api.get('/reports/ageing', { side: S.side, as_of: S.as_of }),
  view: (d) => table({ rows: d.rows, footer: html`<tr class="total"><td>${t('f.total')}</td>${['current', 'd1_30', 'd31_60', 'd61_90', 'd90_plus', 'total'].map((k) => html`<td class="num">${fmtMoney(d.totals[k])}</td>`)}</tr>`,
    columns: [{ label: t('f.partner'), key: 'partner_name' }, ...['current', 'd1_30', 'd31_60', 'd61_90', 'd90_plus', 'total'].map((k) => ({ label: t('age.' + k), align: 'end', render: (r) => fmtMoney(r[k]) }))] }) });

// ---------------------------------------------------------------- fixed assets & health
async function assetsTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  const write = can('accounting.write');
  async function load() {
    const d = await api.get('/accounting/assets');
    mount(root, html`<div class="toolbar"><span class="grow"></span>${write ? html`<button class="btn" data-action="dep-all">${t('acc.depreciate_all')}</button><button class="btn primary" data-action="new">${icon('plus')} ${t('common.add')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.name'), key: 'name' }, { label: t('f.purchase_date'), key: 'purchase_date' }, { label: t('f.cost'), align: 'end', render: (r) => fmtMoney(r.cost) },
        { label: t('acc.monthly_dep'), align: 'end', render: (r) => fmtMoney(r.monthly) }, { label: t('acc.accumulated'), align: 'end', render: (r) => fmtMoney(r.accumulated) },
        { label: t('acc.book_value'), align: 'end', render: (r) => fmtMoney(r.book_value) }, { label: t('f.status'), render: (r) => statusChip(r.status === 'active' ? 'active' : 'fully_depreciated') },
        { label: '', cls: 'actions', render: (r) => (write && r.status === 'active' ? html`<button class="btn sm" data-action="dep" data-id="${r.id}">${t('acc.depreciate')}</button>` : '') }] })}</div>`);
  }
  const askMonth = async (title) => new Promise((resolve) => {
    const m = openModal({ title, size: 'sm', body: html`<form id="pm" data-submit="ok">${field({ name: 'period', label: t('f.period'), type: 'month', value: today().slice(0, 7), required: true })}</form>`,
      footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="pm">${t('common.ok')}</button>`, onClose: () => resolve(null) });
    bind(m.el, { submit: { ok: (form) => { const v = readForm(form).period; m.close(); resolve(v); } } });
  });
  bind(root, { click: {
    new: () => openAssetForm(load),
    dep: async (el) => { const p = await askMonth(t('acc.depreciate')); if (p) { await api.post(`/accounting/assets/${el.dataset.id}/depreciate`, { period: p }); toast(t('common.saved'), 'ok'); await load(); } },
    'dep-all': async () => { const p = await askMonth(t('acc.depreciate_all')); if (p) { const r = await api.post('/accounting/assets/depreciate-all', { period: p }); toast(t('acc.dep_done', { n: r.posted }), 'ok'); await load(); } },
  } });
  await load();
}
async function openAssetForm(done) {
  const accts = (await api.get('/payment-accounts')).items;
  const id = 'as' + Date.now();
  const m = openModal({ title: t('f.asset'), size: 'md', body: html`<form id="${id}" class="grid g2" data-submit="save">
    ${field({ name: 'name', label: t('f.name'), required: true, cls: 'full' })}${field({ name: 'cost', label: t('f.cost'), type: 'money', required: true })}${field({ name: 'salvage', label: t('acc.salvage'), type: 'money', value: 0 })}
    ${field({ name: 'useful_life_months', label: t('acc.life_months'), type: 'number', required: true, value: 60 })}${field({ name: 'purchase_date', label: t('f.purchase_date'), type: 'date', value: today() })}
    ${field({ name: 'account_id', label: t('acc.funding'), type: 'select', hint: t('acc.funding_hint'), cls: 'full', options: [{ value: '', label: t('acc.funding_opening') }, ...accts.map((a) => ({ value: a.id, label: a.name }))] })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { await api.post('/accounting/assets', readForm(form)); m.close(); toast(t('common.saved'), 'ok'); done(); }) } });
}

async function healthTab(container) {
  async function run() {
    const d = await api.get('/accounting/health');
    mount(container, html`<div class="card card-pad"><div class="row" style="margin-bottom:10px"><h3 class="grow">${t('acc.health')}</h3>${d.ok ? html`<span class="chip chip-ok">${t('acc.health_ok')}</span>` : html`<span class="chip chip-bad">${t('acc.health_bad')}</span>`}<button class="btn" data-action="run">${t('acc.run_check')}</button></div>
      ${d.checks.map((c) => html`<div class="check-row"><span class="${c.ok ? 'ok-text' : 'bad-text'}" style="font-weight:800">${c.ok ? '✓' : '✗'}</span><span class="grow">${t('health.' + c.key)}</span>${c.ok ? '' : html`<span class="muted">${Object.entries(c.params).map(([k, v]) => `${k}: ${v}`).join(' · ')}</span>`}</div>`)}</div>`);
  }
  bind(container, { click: { run } });
  await run();
}

const accountsTab = resourceView({ name: 'accounts', perm: 'accounting', singular: 'f.account', perPage: 100, defaults: { type: 'expense', is_active: true },
  columns: [{ key: 'code' }, { key: 'name' }, { key: 'type', render: (r) => t('acc.type.' + r.type) }, { key: 'subtype', render: (r) => (r.subtype ? t('acc.sub.' + r.subtype) : '') },
    { key: 'balance', label: 'f.balance', money: true, render: (r) => fmtMoney(natural(r)) }, { key: 'is_active', bool: true, label: 'f.is_active' }],
  fields: [{ name: 'code', required: true }, { name: 'name', required: true }, { name: 'type', options: TYPES.map((x) => ({ value: x, i18n: 'acc.type.' + x })) },
    { name: 'subtype', options: [{ value: '', label: '—' }, { value: 'cash', i18n: 'acc.sub.cash' }, { value: 'bank', i18n: 'acc.sub.bank' }, { value: 'operating', i18n: 'acc.sub.operating' }] },
    { name: 'parent_id', label: 'f.parent', ref: { resource: 'accounts' } }, { name: 'is_active', type: 'checkbox' }],
  filters: [{ name: 'type', options: TYPES.map((x) => ({ value: x, i18n: 'acc.type.' + x })) }] });

export default {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.accounting')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'accounting', [
      { key: 'journal', label: 'acc.journal', perm: 'accounting.read', render: journalTab },
      { key: 'accounts', label: 'acc.chart', perm: 'accounting.read', render: accountsTab },
      { key: 'ledger', label: 'acc.ledger', perm: 'accounting.read', render: ledgerTab },
      { key: 'trial', label: 'acc.trial', perm: 'accounting.read', render: trialTab },
      { key: 'income', label: 'acc.income', perm: 'accounting.read', render: incomeTab },
      { key: 'balance', label: 'acc.balance', perm: 'accounting.read', render: balanceTab },
      { key: 'cashflow', label: 'acc.cashflow', perm: 'accounting.read', render: cashTab },
      { key: 'tax', label: 'acc.tax', perm: 'accounting.read', render: taxTab },
      { key: 'ageing', label: 'acc.ageing', perm: 'accounting.read', render: ageingTab },
      { key: 'assets', label: 'acc.assets_tab', perm: 'accounting.read', render: assetsTab },
      { key: 'costcenters', label: 'acc.cost_centers', perm: 'accounting.read', render: resourceView({ name: 'cost_centers', perm: 'accounting', singular: 'acc.cost_center', columns: [{ key: 'name' }, { key: 'is_active', bool: true, label: 'f.is_active' }], fields: [{ name: 'name', required: true }, { name: 'is_active', type: 'checkbox' }], defaults: { is_active: true } }) },
      { key: 'health', label: 'acc.health', perm: 'accounting.read', render: healthTab },
    ]);
  },
};
