// Products & inventory: stock levels, movements, adjustments/transfers, physical counts, lots, valuation.
import { api } from '../core/api.js';
import { can } from '../core/state.js';
import { t } from '../core/i18n.js';
import { fmtMoney, fmtNum, today } from '../core/fmt.js';
import { attrs, bind, confirmDialog, debounce, field, html, icon, mount, notifyError, openModal, pager, readForm, statusChip, submitForm, table, toast } from '../core/ui.js';
import { clearRefCache, loadRef, refOptions, resourceView, tabsPage } from '../core/resource.js';

const KIND_OPTS = ['storable', 'raw', 'spare', 'service'].map((k) => ({ value: k, i18n: 'kind.' + k }));
const MOVE_TYPES = ['purchase', 'sale', 'return_in', 'return_out', 'adjust', 'opening', 'transfer_in', 'transfer_out', 'production_in', 'production_out', 'reversal'];

const productsTab = resourceView({
  name: 'products', perm: 'products', singular: 'f.product', modalSize: 'lg', defaults: { kind: 'storable', unit: 'pcs', is_active: true, price: 0, cost: 0, min_stock: 0 },
  columns: [{ key: 'sku' }, { key: 'name' }, { key: 'category_name', label: 'f.category' }, { key: 'kind', label: 'f.kind', render: (r) => t('kind.' + r.kind) },
    { key: 'price', money: true }, { key: 'cost', money: true },
    { key: 'stock_qty', qty: true, label: 'f.stock', render: (r) => (r.kind === 'service' ? '—' : html`<span class="${r.stock_qty <= r.min_stock ? 'bad-text' : ''}">${fmtNum(r.stock_qty)}</span>`) },
    { key: 'is_active', bool: true, label: 'f.is_active' }],
  fields: [{ name: 'sku', required: true }, { name: 'barcode' }, { name: 'name', required: true, full: true },
    { name: 'category_id', label: 'f.category', ref: { resource: 'categories' } }, { name: 'kind', options: KIND_OPTS }, { name: 'unit' },
    { name: 'price', type: 'money' }, { name: 'cost', type: 'money' }, { name: 'tax_rate', type: 'number', hint: 'f.tax_rate_hint' }, { name: 'min_stock', type: 'number' },
    { name: 'notes', type: 'textarea', full: true }, { name: 'is_active', type: 'checkbox' },
    { name: 'opening_qty', type: 'number', createOnly: true, hint: 'f.opening_hint' }, { name: 'opening_cost', type: 'money', createOnly: true },
    { name: 'warehouse_id', label: 'f.warehouse', ref: { resource: 'warehouses' }, createOnly: true, optional: true }],
  filters: [{ name: 'category_id', ref: { resource: 'categories' } }, { name: 'kind', options: KIND_OPTS }],
});

export const products = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.products')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'products', [
      { key: 'items', label: 'nav.products', perm: 'products.read', render: productsTab },
      { key: 'categories', label: 'f.categories', perm: 'products.read', render: resourceView({ name: 'categories', perm: 'products', singular: 'f.category', columns: [{ key: 'name' }], fields: [{ name: 'name', required: true }] }) },
    ]);
  },
};

// ---------------------------------------------------------------- stock levels
async function stockProducts() { return (await loadRef({ resource: 'products', params: { is_active: 'true' } })).filter((p) => p.kind !== 'service'); }

async function levelsTab(container) {
  const S = { q: '', warehouse_id: '', low: false, page: 1 };
  const warehouses = await loadRef({ resource: 'warehouses' });
  const root = document.createElement('div'); container.replaceChildren(root);
  const write = can('inventory.write');
  async function load() {
    const d = await api.get('/inventory/levels', { q: S.q, warehouse_id: S.warehouse_id, low: S.low ? 1 : '', page: S.page, per_page: 30 });
    mount(root, html`<div class="toolbar"><input class="search" type="search" placeholder="${t('common.search')}" value="${S.q}" data-input="search">
      <select data-change="wh" style="width:auto">${refOptions(warehouses).map((o) => html`<option value="${o.value}"${attrs({ selected: String(o.value) === String(S.warehouse_id) })}>${o.value === '' ? t('inv.all_warehouses') : o.label}</option>`)}</select>
      <label class="row" style="gap:6px"><input type="checkbox" data-change="low"${attrs({ checked: S.low })}> ${t('inv.low_only')}</label><span class="grow"></span>
      ${write ? html`<button class="btn" data-action="transfer">${icon('transfer')} ${t('inv.transfer')}</button><button class="btn primary" data-action="adjust">${icon('plus')} ${t('inv.adjust')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.sku'), key: 'sku' }, { label: t('f.product'), key: 'product_name' }, { label: t('f.warehouse'), key: 'warehouse_name' },
        { label: t('f.qty'), align: 'end', render: (r) => html`<span class="${r.qty <= r.min_stock ? 'bad-text' : ''}">${fmtNum(r.qty)} ${r.unit}</span>` },
        { label: t('f.min_stock'), align: 'end', render: (r) => fmtNum(r.min_stock) }, { label: t('f.avg_cost'), align: 'end', render: (r) => fmtMoney(r.avg_cost) }, { label: t('f.value'), align: 'end', render: (r) => fmtMoney(r.value) }] })}${pager(d)}</div>`);
    const s = root.querySelector('input[type=search]'); if (S.focus) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  bind(root, {
    click: { page: (el) => { S.page = Number(el.dataset.page); S.focus = false; return load(); }, adjust: () => openAdjust(load), transfer: () => openTransfer(load) },
    change: { wh: (el) => { S.warehouse_id = el.value; S.page = 1; S.focus = false; return load(); }, low: (el) => { S.low = el.checked; S.page = 1; S.focus = false; return load(); } },
    input: { search: debounce((el) => { S.q = el.value.trim(); S.page = 1; S.focus = true; load().catch(notifyError); }, 300) },
  });
  await load();
}

async function openAdjust(done) {
  const [prods, whs] = [await stockProducts(), await loadRef({ resource: 'warehouses', params: { is_active: 'true' } })];
  const id = 'adj' + Date.now();
  const m = openModal({ title: t('inv.adjust'), size: 'md',
    body: html`<form id="${id}" class="grid g2" data-submit="save">
      ${field({ name: 'product_id', label: t('f.product'), type: 'select', required: true, options: refOptions(prods, (p) => `${p.sku} — ${p.name}`, false), cls: 'full' })}
      ${field({ name: 'warehouse_id', label: t('f.warehouse'), type: 'select', required: true, options: refOptions(whs, 'name', false) })}
      ${field({ name: 'direction', label: t('inv.direction'), type: 'select', options: [{ value: 'in', label: t('inv.dir_in') }, { value: 'out', label: t('inv.dir_out') }] })}
      ${field({ name: 'qty', label: t('f.qty'), type: 'number', required: true, attrs: { min: '0.001' } })}
      ${field({ name: 'unit_cost', label: t('f.unit_cost'), type: 'money', hint: t('inv.cost_hint') })}
      ${field({ name: 'lot_no', label: t('f.lot_no') })}${field({ name: 'expiry_date', label: t('f.expiry_date'), type: 'date' })}
      ${field({ name: 'note', label: t('f.note'), cls: 'full' })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => {
    const v = readForm(form);
    await api.post('/inventory/adjust', { product_id: v.product_id, warehouse_id: v.warehouse_id, qty: v.direction === 'out' ? -Math.abs(v.qty) : Math.abs(v.qty), unit_cost: v.unit_cost, lot_no: v.lot_no, expiry_date: v.expiry_date, note: v.note });
    m.close(); toast(t('common.saved'), 'ok'); done();
  }) } });
}

async function openTransfer(done) {
  const [prods, whs] = [await stockProducts(), await loadRef({ resource: 'warehouses', params: { is_active: 'true' } })];
  const id = 'trf' + Date.now();
  const m = openModal({ title: t('inv.transfer'), size: 'md',
    body: html`<form id="${id}" class="grid g2" data-submit="save">
      ${field({ name: 'product_id', label: t('f.product'), type: 'select', required: true, options: refOptions(prods, (p) => `${p.sku} — ${p.name}`, false), cls: 'full' })}
      ${field({ name: 'from_warehouse_id', label: t('inv.from'), type: 'select', required: true, options: refOptions(whs, 'name', false) })}
      ${field({ name: 'to_warehouse_id', label: t('inv.to'), type: 'select', required: true, options: refOptions(whs, 'name', false), value: whs[1]?.id })}
      ${field({ name: 'qty', label: t('f.qty'), type: 'number', required: true, attrs: { min: '0.001' } })}${field({ name: 'note', label: t('f.note') })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { await api.post('/inventory/transfer', readForm(form)); m.close(); toast(t('common.saved'), 'ok'); done(); }) } });
}

// ---------------------------------------------------------------- movements
async function movesTab(container) {
  const S = { q: '', move_type: '', warehouse_id: '', date_from: '', date_to: '', page: 1 };
  const warehouses = await loadRef({ resource: 'warehouses' });
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = await api.get('/inventory/moves', { ...S, per_page: 30 });
    mount(root, html`<div class="toolbar"><input class="search" type="search" placeholder="${t('common.search')}" value="${S.q}" data-input="search">
      <select data-change="f" data-name="move_type" style="width:auto"><option value="">${t('inv.all_types')}</option>${MOVE_TYPES.map((m) => html`<option value="${m}"${attrs({ selected: S.move_type === m })}>${t('move.' + m)}</option>`)}</select>
      <select data-change="f" data-name="warehouse_id" style="width:auto">${refOptions(warehouses).map((o) => html`<option value="${o.value}"${attrs({ selected: String(o.value) === String(S.warehouse_id) })}>${o.value === '' ? t('inv.all_warehouses') : o.label}</option>`)}</select>
      <input type="date" value="${S.date_from}" data-change="f" data-name="date_from" style="width:auto"><input type="date" value="${S.date_to}" data-change="f" data-name="date_to" style="width:auto"></div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.date'), key: 'date' }, { label: t('f.product'), render: (r) => `${r.product_name}` }, { label: t('f.warehouse'), key: 'warehouse_name' },
        { label: t('f.type'), render: (r) => t('move.' + r.move_type) }, { label: t('f.qty'), align: 'end', render: (r) => html`<b class="${r.qty < 0 ? 'bad-text' : 'ok-text'}">${r.qty > 0 ? '+' : ''}${fmtNum(r.qty)}</b>` },
        { label: t('f.value'), align: 'end', render: (r) => fmtMoney(r.value) }, { label: t('f.lot_no'), render: (r) => r.lot_no || '' }, { label: t('f.note'), render: (r) => r.note || '' }] })}${pager(d)}</div>`);
    const s = root.querySelector('input[type=search]'); if (S.focus) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  bind(root, {
    click: { page: (el) => { S.page = Number(el.dataset.page); S.focus = false; return load(); } },
    change: { f: (el) => { S[el.dataset.name] = el.value; S.page = 1; S.focus = false; return load(); } },
    input: { search: debounce((el) => { S.q = el.value.trim(); S.page = 1; S.focus = true; load().catch(notifyError); }, 300) },
  });
  await load();
}

// ---------------------------------------------------------------- counts
async function countsTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  const write = can('inventory.write');
  async function load() {
    const d = await api.get('/inventory/counts');
    mount(root, html`<div class="toolbar"><span class="grow"></span>${write ? html`<button class="btn primary" data-action="new">${icon('plus')} ${t('inv.new_count')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, rowAttr: (r) => attrs({ class: 'click', 'data-action': 'open', 'data-id': r.id }), columns: [
        { label: t('f.number'), render: (r) => r.number || `${t('status.draft')} #${r.id}` }, { label: t('f.date'), key: 'date' }, { label: t('f.warehouse'), key: 'warehouse_name' },
        { label: t('inv.lines'), align: 'end', key: 'lines' }, { label: t('f.status'), render: (r) => statusChip(r.status === 'posted' ? 'posted' : 'draft') }] })}</div>`);
  }
  bind(root, { click: { new: () => openNewCount(load), open: (el) => openCount(el.dataset.id, load) } });
  await load();
}

async function openNewCount(done) {
  const whs = await loadRef({ resource: 'warehouses', params: { is_active: 'true' } });
  const id = 'nc' + Date.now();
  const m = openModal({ title: t('inv.new_count'), size: 'sm',
    body: html`<form id="${id}" class="grid" data-submit="save">${field({ name: 'warehouse_id', label: t('f.warehouse'), type: 'select', required: true, options: refOptions(whs, 'name', false) })}
      ${field({ name: 'date', label: t('f.date'), type: 'date', value: today() })}${field({ name: 'note', label: t('f.note') })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { const c = await api.post('/inventory/counts', readForm(form)); m.close(); done(); openCount(c.id, done); }) } });
}

async function openCount(cid, done) {
  const m = openModal({ title: '…', size: 'lg', body: html`<div class="loading"><div class="spinner"></div></div>` });
  let c;
  async function refresh() {
    c = await api.get(`/inventory/counts/${cid}`);
    const draft = c.status === 'draft' && can('inventory.write');
    m.setTitle(`${t('inv.count')} ${c.number || '#' + c.id} — ${c.warehouse_name}`);
    m.setBody(table({ rows: c.lines, columns: [{ label: t('f.sku'), key: 'sku' }, { label: t('f.product'), key: 'product_name' }, { label: t('inv.system_qty'), align: 'end', render: (l) => fmtNum(l.system_qty) },
      { label: t('inv.counted_qty'), align: 'end', render: (l) => (draft ? html`<input type="number" step="any" min="0" style="width:100px" value="${l.counted_qty ?? ''}" data-input="count" data-id="${l.id}">` : (l.counted_qty ?? '—')) },
      { label: t('inv.difference'), align: 'end', render: (l) => (l.counted_qty === null || l.counted_qty === undefined ? '' : html`<b class="${l.counted_qty - l.system_qty < 0 ? 'bad-text' : l.counted_qty - l.system_qty > 0 ? 'ok-text' : ''}">${fmtNum(l.counted_qty - l.system_qty)}</b>`) }] }));
    m.setFooter(draft ? html`<button class="btn" data-action="save">${t('common.save')}</button><button class="btn primary" data-action="post">${t('inv.post_count')}</button>` : html``);
  }
  const collect = () => [...m.el.querySelectorAll('[data-id][data-input=count]')].map((i) => ({ id: Number(i.dataset.id), counted_qty: i.value }));
  bind(m.el, { click: {
    save: async () => { await api.put(`/inventory/counts/${cid}`, { lines: collect() }); toast(t('common.saved'), 'ok'); await refresh(); },
    post: async () => {
      if (!(await confirmDialog(t('inv.confirm_post')))) return;
      await api.put(`/inventory/counts/${cid}`, { lines: collect() });
      const r = await api.post(`/inventory/counts/${cid}/post`);
      toast(t('inv.count_posted', { n: r.adjustments }), 'ok'); done?.(); await refresh();
    } } });
  await refresh();
}

// ---------------------------------------------------------------- lots & valuation
async function lotsTab(container) {
  const d = await api.get('/inventory/lots');
  mount(container, html`<div class="card">${table({ rows: d.items, empty: t('inv.no_lots'), columns: [{ label: t('f.product'), render: (r) => `${r.product_name}` }, { label: t('f.warehouse'), key: 'warehouse_name' },
    { label: t('f.lot_no'), key: 'lot_no' }, { label: t('f.expiry_date'), render: (r) => r.expiry_date || '—' }, { label: t('f.qty'), align: 'end', render: (r) => fmtNum(r.qty) },
    { label: t('inv.days_left'), align: 'end', render: (r) => (r.days_to_expiry === null ? '—' : r.days_to_expiry) }, { label: t('f.status'), render: (r) => statusChip(r.status) }] })}</div>`);
}

async function valuationTab(container) {
  let asOf = '';
  async function load() {
    const d = await api.get('/inventory/valuation', { as_of: asOf });
    mount(container, html`<div class="toolbar"><label class="row">${t('rep.as_of')} <input type="date" value="${asOf}" data-change="asof" style="width:auto"></label><span class="grow"></span>
      ${d.matches_gl ? html`<span class="chip chip-ok">${t('inv.matches_gl')}</span>` : html`<span class="chip chip-bad">${t('inv.mismatch_gl')}</span>`}</div>
      <div class="card">${table({ rows: d.rows, footer: html`<tr class="total"><td colspan="3">${t('f.total')}</td><td class="num">${fmtMoney(d.total_value)}</td></tr>`, columns: [{ label: t('f.sku'), key: 'sku' }, { label: t('f.product'), key: 'name' },
        { label: t('f.qty'), align: 'end', render: (r) => `${fmtNum(r.qty)} ${r.unit}` }, { label: t('f.value'), align: 'end', render: (r) => fmtMoney(r.value) }] })}</div>`);
  }
  bind(container, { change: { asof: (el) => { asOf = el.value; return load(); } } });
  await load();
}

export const inventory = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.inventory')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'inventory', [
      { key: 'levels', label: 'inv.levels', perm: 'inventory.read', render: levelsTab },
      { key: 'moves', label: 'inv.moves', perm: 'inventory.read', render: movesTab },
      { key: 'counts', label: 'inv.counts', perm: 'inventory.read', render: countsTab },
      { key: 'lots', label: 'inv.lots', perm: 'inventory.read', render: lotsTab },
      { key: 'valuation', label: 'inv.valuation', perm: 'inventory.read', render: valuationTab },
      { key: 'warehouses', label: 'inv.warehouses', perm: 'inventory.read', render: resourceView({ name: 'warehouses', perm: 'inventory', singular: 'f.warehouse', columns: [{ key: 'code' }, { key: 'name' }, { key: 'is_active', bool: true, label: 'f.is_active' }], fields: [{ name: 'code', required: true }, { name: 'name', required: true }, { name: 'is_active', type: 'checkbox' }], defaults: { is_active: true } }) },
    ]);
  },
};
