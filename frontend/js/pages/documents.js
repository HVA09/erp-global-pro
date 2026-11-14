// Sales & purchases: invoices, credit notes, bills, debit notes with posting, payments, returns and printing.
import { api } from '../core/api.js';
import { can, canAny, state } from '../core/state.js';
import { errMsg, t } from '../core/i18n.js';
import { fmtMoney, fmtNum, lineAmounts, today } from '../core/fmt.js';
import { attrs, bind, confirmDialog, debounce, esc, field, html, icon, mount, notifyError, openModal, pager, printHtml, raw,
  readForm, statusChip, submitForm, table, toast } from '../core/ui.js';
import { clearRefCache, loadRef, refOptions, resourceView, tabsPage } from '../core/resource.js';

export const KINDS = {
  invoice: { path: '/sales/invoices', perm: 'sales', partner: 'customers', side: 'sales', title: 'doc.invoice', ret: 'credit_note' },
  credit_note: { path: '/sales/credit-notes', perm: 'sales', partner: 'customers', side: 'sales', title: 'doc.credit_note', target: 'invoice' },
  bill: { path: '/purchases/bills', perm: 'purchases', partner: 'suppliers', side: 'purchases', title: 'doc.bill', ret: 'debit_note' },
  debit_note: { path: '/purchases/debit-notes', perm: 'purchases', partner: 'suppliers', side: 'purchases', title: 'doc.debit_note', target: 'bill' },
};
const METHODS = ['cash', 'card', 'transfer', 'cheque'];
const docName = (d) => d.number || `${t('status.draft')} #${d.id}`;

// ====================================================================== list
async function docList(container, kind) {
  const K = KINDS[kind];
  const S = { page: 1, q: '', status: '', payment_status: '', date_from: '', date_to: '' };
  const root = document.createElement('div');
  container.replaceChildren(root);
  const canWrite = can(K.perm + '.write');

  async function load() {
    const d = await api.get(K.path, { ...S, per_page: 20 });
    mount(root, html`
      <div class="toolbar">
        <input class="search" type="search" placeholder="${t('common.search')}" value="${S.q}" data-input="search">
        <select data-change="filter" data-name="status" style="width:auto"><option value="">${t('f.status')}</option>${['draft', 'posted', 'cancelled'].map((s) => html`<option value="${s}"${attrs({ selected: S.status === s })}>${t('status.' + s)}</option>`)}</select>
        <select data-change="filter" data-name="payment_status" style="width:auto"><option value="">${t('f.payment_status')}</option>${['unpaid', 'partial', 'paid'].map((s) => html`<option value="${s}"${attrs({ selected: S.payment_status === s })}>${t('status.' + s)}</option>`)}</select>
        <input type="date" value="${S.date_from}" data-change="filter" data-name="date_from" style="width:auto" title="${t('common.from')}">
        <input type="date" value="${S.date_to}" data-change="filter" data-name="date_to" style="width:auto" title="${t('common.to')}">
        <span class="grow"></span>
        ${canWrite ? html`<button class="btn primary" data-action="add">${icon('plus')} ${t('common.new')}</button>` : ''}
      </div>
      <div class="card">${table({ rows: d.items, empty: t('common.empty'),
        rowAttr: (r) => attrs({ class: 'click', 'data-action': 'open', 'data-id': r.id }),
        columns: [
          { label: t('f.number'), render: (r) => html`<b>${docName(r)}</b>${r.source === 'pos' ? html` <span class="chip chip-info">POS</span>` : ''}` },
          { label: t('f.date'), key: 'date' }, { label: t('f.partner'), key: 'partner_name' },
          ...(K.target || false ? [{ label: t('f.origin'), render: (r) => r.origin_number || '' }] : []),
          { label: t('f.total'), align: 'end', render: (r) => fmtMoney(r.total) },
          { label: t('f.outstanding'), align: 'end', render: (r) => (r.status === 'posted' ? fmtMoney(r.outstanding) : '') },
          { label: t('f.status'), render: (r) => statusChip(r.status) },
          { label: t('f.payment_status'), render: (r) => (r.status === 'posted' ? statusChip(r.payment_status) : '') },
        ] })}${pager(d)}</div>`);
    const s = root.querySelector('input[type=search]');
    if (S.focus) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  bind(root, {
    click: {
      page: (el) => { S.page = Number(el.dataset.page); S.focus = false; return load(); },
      add: () => openDocForm(kind, null, load),
      open: (el) => openDoc(kind, el.dataset.id, load),
    },
    change: { filter: (el) => { S[el.dataset.name] = el.value; S.page = 1; S.focus = false; return load(); } },
    input: { search: debounce((el) => { S.q = el.value.trim(); S.page = 1; S.focus = true; load().catch(notifyError); }, 300) },
  });
  await load();
}

// ====================================================================== view / actions
function docBody(d, K) {
  return html`
    <div class="kv">
      <div><small>${t('f.partner')}</small>${d.partner_name}</div><div><small>${t('f.date')}</small>${d.date}</div>
      <div><small>${t('f.due_date')}</small>${d.due_date || '—'}</div>
      <div><small>${t('f.status')}</small>${statusChip(d.status)} ${d.status === 'posted' ? statusChip(d.payment_status) : ''}</div>
      ${d.warehouse_name ? html`<div><small>${t('f.warehouse')}</small>${d.warehouse_name}</div>` : ''}
      ${d.origin_number ? html`<div><small>${t('f.origin')}</small>${d.origin_number}</div>` : ''}
    </div>
    ${table({ rows: d.lines, columns: [
      { label: t('f.description'), render: (l) => html`${l.product_name ? html`<b>${l.product_name}</b> <span class="muted">${l.sku}</span>` : l.description}${l.lot_no ? html` <span class="chip">${l.lot_no}</span>` : ''}` },
      { label: t('f.qty'), align: 'end', render: (l) => fmtNum(l.qty) }, { label: t('f.unit_price'), align: 'end', render: (l) => fmtMoney(l.unit_price) },
      { label: t('f.discount_pct'), align: 'end', render: (l) => (l.discount_pct ? l.discount_pct + '%' : '') },
      { label: t('f.tax_rate'), align: 'end', render: (l) => l.tax_rate + '%' }, { label: t('f.total'), align: 'end', render: (l) => fmtMoney(l.total) }] })}
    <div class="totals">
      <div><span>${t('f.subtotal')}</span><b>${fmtMoney(d.subtotal)}</b></div><div><span>${t('f.tax')}</span><b>${fmtMoney(d.tax)}</b></div>
      <div class="grand"><span>${t('f.total')}</span><span>${fmtMoney(d.total)}</span></div>
      ${d.status === 'posted' ? html`<div><span>${t('f.paid')}</span><b class="ok-text">${fmtMoney(d.paid)}</b></div><div><span>${t('f.outstanding')}</span><b class="${d.outstanding > 0 ? 'bad-text' : ''}">${fmtMoney(d.outstanding)}</b></div>` : ''}
    </div>
    ${d.notes ? html`<p class="muted">${d.notes}</p>` : ''}
    ${d.payments.length ? html`<h4 style="margin:16px 0 6px">${t('nav.payments')}</h4>${table({ rows: d.payments, columns: [
      { label: t('f.number'), key: 'number' }, { label: t('f.date'), key: 'date' }, { label: t('f.method'), render: (p) => t('method.' + p.method) },
      { label: t('f.account'), key: 'account_name' }, { label: t('f.amount'), align: 'end', render: (p) => fmtMoney(p.amount) },
      { label: t('f.status'), render: (p) => statusChip(p.status === 'posted' ? 'posted' : 'cancelled') },
      { label: '', cls: 'actions', render: (p) => (p.status === 'posted' && canAny(K.perm + '.void', 'payments.write') ? html`<button class="btn sm danger" data-action="cancel-pay" data-id="${p.id}">${t('common.cancel_it')}</button>` : '') }] })}` : ''}
    ${d.applications.length ? html`<h4 style="margin:16px 0 6px">${t('doc.applications')}</h4>${table({ rows: d.applications, columns: [
      { label: t('doc.credit_doc'), key: 'credit_number' }, { label: t('doc.applied_to'), key: 'target_number' }, { label: t('f.date'), key: 'date' },
      { label: t('f.amount'), align: 'end', render: (a) => fmtMoney(a.amount) },
      { label: '', cls: 'actions', render: (a) => (canAny(K.perm + '.void', 'payments.write') ? html`<button class="btn sm danger" data-action="undo-credit" data-id="${a.id}">${t('common.cancel_it')}</button>` : '') }] })}` : ''}`;
}

function docFooter(d, kind, K) {
  const write = can(K.perm + '.write'), pay = canAny(K.perm + '.write', 'payments.write'), btn = [];
  if (d.status === 'draft' && write) {
    btn.push(html`<button class="btn danger" data-action="delete">${icon('trash')} ${t('common.delete')}</button>`, html`<button class="btn" data-action="edit">${icon('edit')} ${t('common.edit')}</button>`,
      html`<button class="btn primary" data-action="post">${icon('check')} ${t('doc.post')}</button>`);
  }
  if (d.status === 'posted') {
    btn.push(html`<button class="btn" data-action="print">${icon('printer')} ${t('common.print')}</button>`);
    if (K.ret && write) btn.push(html`<button class="btn" data-action="return">${t('doc.return')}</button>`);
    if (K.target && d.outstanding > 0 && pay) btn.push(html`<button class="btn" data-action="apply">${t('doc.apply_credit')}</button>`);
    if (d.outstanding > 0 && pay) btn.push(html`<button class="btn ok" data-action="pay">${icon('card')} ${K.target ? t('doc.refund') : t('doc.register_payment')}</button>`);
    if (can(K.perm + '.void')) btn.push(html`<button class="btn danger" data-action="cancel">${t('common.cancel_it')}</button>`);
  }
  return html`${btn}`;
}

export async function openDoc(kind, id, onChange) {
  const K = KINDS[kind];
  const m = openModal({ title: '…', size: 'lg', body: html`<div class="loading"><div class="spinner"></div></div>` });
  let doc;
  async function refresh(notify = true) {
    doc = await api.get(`${K.path}/${id}`);
    m.setTitle(`${t(K.title)} ${docName(doc)}`);
    m.setBody(docBody(doc, K));
    m.setFooter(docFooter(doc, kind, K));
    if (notify) onChange?.();
  }
  const act = async (fn, msg) => { await fn(); if (msg) toast(t(msg), 'ok'); await refresh(); };
  bind(m.el, {
    click: {
      post: () => act(() => api.post(`${K.path}/${id}/post`), 'doc.posted'),
      edit: () => { m.close(); openDocForm(kind, doc, onChange); },
      delete: async () => { if (await confirmDialog(t('common.confirm_delete'), { danger: true })) { await api.del(`${K.path}/${id}`); m.close(); onChange?.(); toast(t('common.deleted'), 'ok'); } },
      cancel: async () => { if (await confirmDialog(t('doc.confirm_cancel'), { danger: true })) await act(() => api.post(`${K.path}/${id}/cancel`), 'doc.cancelled'); },
      'cancel-pay': async (el) => { if (await confirmDialog(t('doc.confirm_cancel_payment'), { danger: true })) await act(() => api.post(`/payments/${el.dataset.id}/cancel`), 'doc.cancelled'); },
      'undo-credit': async (el) => { if (await confirmDialog(t('doc.confirm_cancel_payment'), { danger: true })) await act(() => api.del(`/credit-applications/${el.dataset.id}`), 'doc.cancelled'); },
      pay: () => openPayment(doc, K, () => refresh()),
      apply: () => openApplyCredit(doc, K, () => refresh()),
      return: () => openReturn(doc, kind, K, onChange, m),
      print: () => printDoc(doc, kind),
    },
  });
  await refresh(false);
}

async function openPayment(doc, K, done) {
  const accounts = (await api.get('/payment-accounts')).items;
  const id = 'pf' + Date.now();
  const m = openModal({
    title: t('doc.register_payment') + ' — ' + docName(doc), size: 'sm',
    body: html`<form id="${id}" class="grid" data-submit="save">
      ${field({ name: 'amount', label: t('f.amount'), type: 'money', value: doc.outstanding, required: true, attrs: { max: doc.outstanding, min: '0.01' } })}
      ${field({ name: 'date', label: t('f.date'), type: 'date', value: today(), required: true })}
      ${field({ name: 'method', label: t('f.method'), type: 'select', options: METHODS.map((x) => ({ value: x, label: t('method.' + x) })) })}
      ${field({ name: 'account_id', label: t('f.account'), type: 'select', required: true, options: accounts.map((a) => ({ value: a.id, label: `${a.code} — ${a.name}` })) })}
      ${field({ name: 'reference', label: t('f.reference') })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>`,
  });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { await api.post(`${K.path}/${doc.id}/payments`, readForm(form)); m.close(); toast(t('common.saved'), 'ok'); done(); }) } });
}

async function openApplyCredit(doc, K, done) {
  const targetKind = KINDS[K.target];
  const list = (await api.get(targetKind.path, { partner_id: doc.partner_id, status: 'posted', per_page: 200 })).items.filter((x) => x.outstanding > 0);
  if (!list.length) { toast(t('doc.no_open_docs'), 'error'); return; }
  const id = 'af' + Date.now();
  const m = openModal({
    title: t('doc.apply_credit'), size: 'sm',
    body: html`<form id="${id}" class="grid" data-submit="save">
      ${field({ name: 'target_id', label: t('doc.applied_to'), type: 'select', options: list.map((x) => ({ value: x.id, label: `${x.number} — ${fmtMoney(x.outstanding)}` })) })}
      ${field({ name: 'amount', label: t('f.amount'), type: 'money', value: Math.min(doc.outstanding, list[0].outstanding), required: true })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>`,
  });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { const v = readForm(form); await api.post('/credit-applications', { credit_id: doc.id, target_id: v.target_id, amount: v.amount }); m.close(); toast(t('common.saved'), 'ok'); done(); }) } });
}

function openReturn(doc, kind, K, onChange, parent) {
  const id = 'rf' + Date.now();
  const m = openModal({
    title: t('doc.return') + ' — ' + docName(doc), size: 'md',
    body: html`<form id="${id}" data-submit="save"><p class="muted">${t('doc.return_hint')}</p>${doc.lines.map((l) => html`
      <div class="check-row"><span class="grow">${l.product_name || l.description} <span class="muted">(${fmtNum(l.qty)})</span></span>
      <input type="number" step="any" min="0" max="${l.qty}" value="${l.product_id ? l.qty : 0}" data-line="${l.id}" style="width:100px"></div>`)}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('doc.create_return')}</button>`,
  });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => {
    const lines = [...form.querySelectorAll('[data-line]')].map((i) => ({ line_id: Number(i.dataset.line), qty: Number(i.value) })).filter((x) => x.qty > 0);
    const created = await api.post(`${K.path}/${doc.id}/return`, { lines });
    m.close(); parent.close(); onChange?.();
    openDoc(K.ret, created.id, onChange);
  }) } });
}

// ====================================================================== printing
export async function printDoc(d, kind) {
  const s = state.settings;
  let qr = '';
  if (d.status === 'posted' && (kind === 'invoice' || kind === 'credit_note')) {
    try { qr = (await api.get(`${KINDS[kind].path}/${d.id}/qr`)).svg; } catch { /* QR is optional */ }
  }
  const rows = d.lines.map((l) => `<tr><td>${esc(l.product_name || l.description)}</td><td class="num">${fmtNum(l.qty)}</td><td class="num">${fmtMoney(l.unit_price)}</td><td class="num">${l.discount_pct || 0}%</td><td class="num">${l.tax_rate}%</td><td class="num">${fmtMoney(l.total)}</td></tr>`).join('');
  const title = kind === 'invoice' && s.tax_number ? t('doc.tax_invoice') : t(KINDS[kind].title);
  printHtml(raw(`<div class="head"><div><h1>${esc(s.company)}</h1><div class="muted">${esc(s.address)} ${esc(s.phone)}<br>${s.tax_number ? t('set.tax_number') + ': ' + esc(s.tax_number) : ''}</div></div>
    <div style="text-align:end"><h1>${esc(title)}</h1><b>${esc(docName(d))}</b></div></div>
    <div class="meta"><div><b>${t('f.partner')}:</b> ${esc(d.partner_name)} ${d.partner_tax_number ? '(' + esc(d.partner_tax_number) + ')' : ''}</div><div><b>${t('f.date')}:</b> ${esc(d.date)}</div>
    <div><b>${t('f.due_date')}:</b> ${esc(d.due_date || '—')}</div>${d.origin_number ? `<div><b>${t('f.origin')}:</b> ${esc(d.origin_number)}</div>` : ''}</div>
    <table><thead><tr><th>${t('f.description')}</th><th class="num">${t('f.qty')}</th><th class="num">${t('f.unit_price')}</th><th class="num">${t('f.discount_pct')}</th><th class="num">${t('f.tax_rate')}</th><th class="num">${t('f.total')}</th></tr></thead><tbody>${rows}</tbody></table>
    <div class="totals"><div><span>${t('f.subtotal')}</span><span>${fmtMoney(d.subtotal)}</span></div><div><span>${t('f.tax')}</span><span>${fmtMoney(d.tax)}</span></div>
    <div class="grand"><span>${t('f.total')}</span><span>${fmtMoney(d.total)}</span></div></div>
    ${qr ? `<div style="margin-top:14px" class="qr">${qr}</div>` : ''}${d.notes ? `<p class="muted">${esc(d.notes)}</p>` : ''}`), docName(d));
}

// ====================================================================== form
export async function openDocForm(kind, doc, onSaved) {
  const K = KINDS[kind], isBuy = K.side === 'purchases';
  const [partners, products, warehouses] = await Promise.all([
    loadRef({ resource: K.partner, params: { is_active: 'true' } }), loadRef({ resource: 'products', params: { is_active: 'true' } }),
    loadRef({ resource: 'warehouses', params: { is_active: 'true' } })]);
  const accounts = isBuy && can('accounting.read') ? await loadRef({ resource: 'accounts', params: { type: 'expense', is_active: 'true' } }) : [];
  const blank = () => ({ product_id: '', description: '', qty: 1, unit_price: 0, discount_pct: 0, tax_rate: state.settings.tax, lot_no: '', expiry_date: '', account_id: '' });
  const lines = doc ? doc.lines.map((l) => ({ product_id: l.product_id || '', description: l.description, qty: l.qty, unit_price: l.unit_price, discount_pct: l.discount_pct, tax_rate: l.tax_rate, lot_no: l.lot_no || '', expiry_date: l.expiry_date || '', account_id: l.account_id || '' })) : [blank()];
  const id = 'df' + Date.now();
  const prodOpts = [{ value: '', label: t('doc.free_line') }, ...products.map((p) => ({ value: p.id, label: `${p.sku} — ${p.name}` }))];
  const partnerInfo = (pid) => { const p = partners.find((x) => String(x.id) === String(pid)); return p ? (isBuy ? `${t('f.balance')}: ${fmtMoney(p.balance)}` : `${t('f.balance')}: ${fmtMoney(p.balance)}${p.credit_limit ? ' / ' + t('f.credit_limit') + ': ' + fmtMoney(p.credit_limit) : ''}`) : ''; };

  const linesHtml = () => html`<table class="lines"><thead><tr><th>${t('f.product')}</th><th>${t('f.qty')}</th><th>${t('f.unit_price')}</th><th>${t('f.discount_pct')}</th><th>${t('f.tax_rate')}</th>
    ${isBuy ? html`<th>${t('f.lot_no')}</th><th>${t('f.expiry_date')}</th>${accounts.length ? html`<th>${t('f.account')}</th>` : ''}` : ''}<th>${t('f.total')}</th><th></th></tr></thead><tbody>
    ${lines.map((l, i) => html`<tr>
      <td style="min-width:230px"><select data-change="product" data-i="${i}">${prodOpts.map((o) => html`<option value="${o.value}"${attrs({ selected: String(o.value) === String(l.product_id) })}>${o.label}</option>`)}</select>
        <input style="margin-top:3px" placeholder="${t('f.description')}" value="${l.description}" data-input="line" data-i="${i}" data-f="description"></td>
      <td><input class="w-qty" type="number" step="any" min="0" value="${l.qty}" data-input="line" data-i="${i}" data-f="qty"></td>
      <td><input class="w-price" type="number" step="0.01" min="0" value="${l.unit_price}" data-input="line" data-i="${i}" data-f="unit_price"></td>
      <td><input class="w-pct" type="number" step="any" min="0" max="100" value="${l.discount_pct}" data-input="line" data-i="${i}" data-f="discount_pct"></td>
      <td><input class="w-pct" type="number" step="any" min="0" max="100" value="${l.tax_rate}" data-input="line" data-i="${i}" data-f="tax_rate"></td>
      ${isBuy ? html`<td><input class="w-price" value="${l.lot_no}" data-input="line" data-i="${i}" data-f="lot_no"></td><td><input type="date" value="${l.expiry_date}" data-input="line" data-i="${i}" data-f="expiry_date"></td>
        ${accounts.length ? html`<td><select data-change="account" data-i="${i}">${refOptions(accounts, (a) => `${a.code} ${a.name}`).map((o) => html`<option value="${o.value}"${attrs({ selected: String(o.value) === String(l.account_id) })}>${o.label}</option>`)}</select></td>` : ''}` : ''}
      <td class="w-tot" data-total="${i}">0.00</td>
      <td><button type="button" class="icon-btn" data-action="rm" data-i="${i}">${icon('x')}</button></td></tr>`)}</tbody></table>`;

  const m = openModal({
    title: (doc ? t('common.edit') : t('common.new')) + ' — ' + t(K.title), size: 'xl',
    body: html`<form id="${id}" data-submit="save">
      <div class="grid g4" style="margin-bottom:14px">
        ${field({ name: 'partner_id', label: t('f.partner'), type: 'select', required: true, value: doc?.partner_id ?? '', options: [{ value: '', label: '—' }, ...partners.map((p) => ({ value: p.id, label: p.name }))], hint: partnerInfo(doc?.partner_id) })}
        ${field({ name: 'date', label: t('f.date'), type: 'date', value: doc?.date || today(), required: true })}
        ${field({ name: 'due_date', label: t('f.due_date'), type: 'date', value: doc?.due_date || '' })}
        ${field({ name: 'warehouse_id', label: t('f.warehouse'), type: 'select', value: doc?.warehouse_id ?? '', options: refOptions(warehouses) })}
      </div>
      <div id="lines">${linesHtml()}</div>
      <button type="button" class="btn sm" data-action="add-line" style="margin-top:8px">${icon('plus')} ${t('doc.add_line')}</button>
      <div class="totals" id="totals"></div>
      <div style="margin-top:12px">${field({ name: 'notes', label: t('f.notes'), type: 'textarea', value: doc?.notes || '', rows: 2 })}</div></form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button>
      <button class="btn" type="submit" form="${id}" data-post="0">${t('doc.save_draft')}</button>
      <button class="btn primary" type="submit" form="${id}" data-post="1">${t('doc.save_post')}</button>`,
  });

  const form = m.el.querySelector('form');
  function recalc() {
    let sub = 0, tax = 0;
    lines.forEach((l, i) => {
      const a = lineAmounts(Number(l.qty) || 0, Number(l.unit_price) || 0, Number(l.discount_pct) || 0, Number(l.tax_rate) || 0);
      sub += a.net; tax += a.tax;
      const cell = m.el.querySelector(`[data-total="${i}"]`);
      if (cell) cell.textContent = fmtMoney(a.total);
    });
    mount(m.el.querySelector('#totals'), html`<div><span>${t('f.subtotal')}</span><b>${fmtMoney(sub)}</b></div><div><span>${t('f.tax')}</span><b>${fmtMoney(tax)}</b></div><div class="grand"><span>${t('f.total')}</span><span>${fmtMoney(sub + tax)}</span></div>`);
  }
  const redraw = () => { mount(m.el.querySelector('#lines'), linesHtml()); recalc(); };
  let postFlag = false;
  m.el.querySelectorAll('[data-post]').forEach((b) => b.addEventListener('click', () => { postFlag = b.dataset.post === '1'; }));
  form.addEventListener('change', (e) => {
    if (e.target.name === 'partner_id') e.target.closest('.field').querySelector('.field-hint')?.remove(), e.target.closest('.field').insertAdjacentHTML('beforeend', `<span class="field-hint">${esc(partnerInfo(e.target.value))}</span>`);
  });
  bind(m.el, {
    click: { 'add-line': () => { lines.push(blank()); redraw(); }, rm: (el) => { if (lines.length > 1) { lines.splice(Number(el.dataset.i), 1); redraw(); } } },
    change: {
      product: (el) => {
        const l = lines[Number(el.dataset.i)], p = products.find((x) => String(x.id) === el.value);
        l.product_id = el.value;
        if (p) { l.description = p.name; l.unit_price = isBuy ? p.cost : p.price; l.tax_rate = p.tax_rate ?? state.settings.tax; }
        redraw();
      },
      account: (el) => { lines[Number(el.dataset.i)].account_id = el.value; },
    },
    input: { line: (el) => { lines[Number(el.dataset.i)][el.dataset.f] = el.value; if (['qty', 'unit_price', 'discount_pct', 'tax_rate'].includes(el.dataset.f)) recalc(); } },
    submit: {
      save: (f) => submitForm(f, async () => {
        const v = readForm(f);
        const payload = { ...v, origin_id: doc?.origin_id || undefined, lines: lines.filter((l) => l.product_id || l.description).map((l) => ({
          product_id: l.product_id || null, description: l.description, qty: Number(l.qty), unit_price: Number(l.unit_price), discount_pct: Number(l.discount_pct) || 0,
          tax_rate: Number(l.tax_rate) || 0, lot_no: l.lot_no || null, expiry_date: l.expiry_date || null, account_id: l.account_id || null })) };
        let saved;
        if (doc) { saved = await api.put(`${K.path}/${doc.id}`, payload); if (postFlag) saved = await api.post(`${K.path}/${doc.id}/post`); }
        else saved = await api.post(K.path, { ...payload, post: postFlag });
        clearRefCache();
        m.close();
        toast(t(postFlag ? 'doc.posted' : 'common.saved'), 'ok');
        onSaved?.();
        openDoc(kind, saved.id, onSaved);
      }),
    },
  });
  recalc();
}

// ====================================================================== pages
const partnerCols = [{ key: 'name' }, { key: 'phone' }, { key: 'city' }, { key: 'credit_limit', money: true }, { key: 'balance', money: true }, { key: 'is_active', bool: true, label: 'f.is_active' }];
const partnerFields = [{ name: 'name', required: true, full: true }, { name: 'phone' }, { name: 'email', type: 'email' }, { name: 'address', full: true }, { name: 'city' }, { name: 'country' },
  { name: 'tax_number' }, { name: 'credit_limit', type: 'money', hint: 'f.credit_limit_hint' }, { name: 'payment_terms_days', type: 'number' }, { name: 'notes', type: 'textarea', full: true }, { name: 'is_active', type: 'checkbox' }];
const paymentsView = (direction) => async (body) => {
  const d = await api.get('/payments', { direction, per_page: 50 });
  mount(body, html`<div class="card">${table({ rows: d.items, columns: [
    { label: t('f.number'), key: 'number' }, { label: t('f.date'), key: 'date' }, { label: t('f.partner'), key: 'partner_name' }, { label: t('f.document'), key: 'document_number' },
    { label: t('f.method'), render: (p) => t('method.' + p.method) }, { label: t('f.account'), key: 'account_name' },
    { label: t('f.amount'), align: 'end', render: (p) => fmtMoney(p.amount) }, { label: t('f.status'), render: (p) => statusChip(p.status === 'posted' ? 'posted' : 'cancelled') }] })}${pager(d)}</div>`);
};

export const sales = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.sales')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'sales', [
      { key: 'invoices', label: 'doc.invoices', perm: 'sales.read', render: (b) => docList(b, 'invoice') },
      { key: 'credit-notes', label: 'doc.credit_notes', perm: 'sales.read', render: (b) => docList(b, 'credit_note') },
      { key: 'customers', label: 'nav.customers', perm: 'customers.read', render: resourceView({ name: 'customers', perm: 'customers', singular: 'f.customer', columns: partnerCols, fields: partnerFields, defaults: { is_active: true, payment_terms_days: 0 }, modalSize: 'lg', filters: [{ name: 'is_active', options: [{ value: 'true', i18n: 'status.active' }, { value: 'false', i18n: 'status.inactive' }] }],
        rowActions: [{ key: 'statement', label: 'rep.statement', run: (row) => import('./reports.js').then((m) => m.openStatement(row.id, 'customer')) }] }) },
      { key: 'receipts', label: 'doc.receipts', perm: 'sales.read', render: paymentsView('in') },
    ]);
  },
};
export const purchases = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.purchases')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'purchases', [
      { key: 'bills', label: 'doc.bills', perm: 'purchases.read', render: (b) => docList(b, 'bill') },
      { key: 'debit-notes', label: 'doc.debit_notes', perm: 'purchases.read', render: (b) => docList(b, 'debit_note') },
      { key: 'suppliers', label: 'nav.suppliers', perm: 'suppliers.read', render: resourceView({ name: 'suppliers', perm: 'suppliers', singular: 'f.supplier', columns: partnerCols, fields: partnerFields, defaults: { is_active: true, payment_terms_days: 30 }, modalSize: 'lg', filters: [{ name: 'is_active', options: [{ value: 'true', i18n: 'status.active' }, { value: 'false', i18n: 'status.inactive' }] }],
        rowActions: [{ key: 'statement', label: 'rep.statement', run: (row) => import('./reports.js').then((m) => m.openStatement(row.id, 'supplier')) }] }) },
      { key: 'payments', label: 'doc.payments_out', perm: 'purchases.read', render: paymentsView('out') },
    ]);
  },
};
