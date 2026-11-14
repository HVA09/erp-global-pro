// UI toolkit: auto-escaping html`` templates (XSS-safe by default), event delegation, modals, forms, tables, charts.
import { errMsg, lang, t } from './i18n.js';
import { fmtMoney } from './fmt.js';

// ---------------------------------------------------------------- safe templates
export const raw = (s) => ({ __raw: String(s) });
const isRaw = (v) => v !== null && typeof v === 'object' && '__raw' in v;
export const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const render = (v) => (isRaw(v) ? v.__raw : Array.isArray(v) ? v.map(render).join('') : v === null || v === undefined || v === false ? '' : esc(v));
export function html(strings, ...vals) {
  let out = strings[0];
  for (let i = 0; i < vals.length; i++) out += render(vals[i]) + strings[i + 1];
  return raw(out);
}
export const mount = (el, tpl) => { el.innerHTML = isRaw(tpl) ? tpl.__raw : esc(tpl); return el; };
export const attrs = (o) => raw(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== false)
  .map(([k, v]) => ` ${k}="${esc(v)}"`).join(''));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
export const debounce = (fn, ms = 300) => { let h; return (...a) => { clearTimeout(h); h = setTimeout(() => fn(...a), ms); }; };

// ---------------------------------------------------------------- icons (Feather-style)
const ICONS = {
  home: '<path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/>',
  cart: '<circle cx="9" cy="21" r="1"/><circle cx="20" cy="21" r="1"/><path d="M1 1h4l2.7 13.4a2 2 0 0 0 2 1.6h9.7a2 2 0 0 0 2-1.6L23 6H6"/>',
  receipt: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>',
  truck: '<rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/>',
  box: '<path d="M21 16V8a2 2 0 0 0-1-1.7l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.7l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/><polyline points="3.3 7 12 12 20.7 7"/><line x1="12" y1="22" x2="12" y2="12"/>',
  tag: '<path d="M20.6 13.4l-7.2 7.2a2 2 0 0 1-2.8 0L2 12V2h10l8.6 8.6a2 2 0 0 1 0 2.8z"/><line x1="7" y1="7" x2="7.01" y2="7"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"/>',
  target: '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="6"/><circle cx="12" cy="12" r="2"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/>',
  briefcase: '<rect x="2" y="7" width="20" height="14" rx="2"/><path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>',
  layers: '<polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/>',
  tool: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9z"/>',
  folder: '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
  chart: '<line x1="12" y1="20" x2="12" y2="10"/><line x1="18" y1="20" x2="18" y2="4"/><line x1="6" y1="20" x2="6" y2="16"/>',
  gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  transfer: '<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
  plus: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trash: '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
  x: '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
  search: '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.7" y2="16.7"/>',
  printer: '<polyline points="6 9 6 2 18 2 18 9"/><path d="M6 18H4a2 2 0 0 1-2-2v-5a2 2 0 0 1 2-2h16a2 2 0 0 1 2 2v5a2 2 0 0 1-2 2h-2"/><rect x="6" y="14" width="12" height="8"/>',
  check: '<polyline points="20 6 9 17 4 12"/>',
  menu: '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
  upload: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
  globe: '<circle cx="12" cy="12" r="10"/><line x1="2" y1="12" x2="22" y2="12"/><path d="M12 2a15 15 0 0 1 0 20 15 15 0 0 1 0-20z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/>',
  user: '<path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  card: '<rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
};
export const icon = (name, cls = 'icon') => raw(`<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name] || ''}</svg>`);

// ---------------------------------------------------------------- events
export function toast(message, type = '') {
  let box = document.getElementById('toasts');
  if (!box) { box = Object.assign(document.createElement('div'), { id: 'toasts' }); box.setAttribute('role', 'status'); document.body.appendChild(box); }
  const el = Object.assign(document.createElement('div'), { className: 'toast ' + type, textContent: message });
  box.appendChild(el);
  setTimeout(() => el.remove(), type === 'error' ? 6500 : 3200);
}
export const notifyError = (e) => { console.error(e); toast(errMsg(e), 'error'); };

/** Event delegation. Elements opt in with data-action / data-change / data-input / data-keydown / data-submit. */
export function bind(root, map = {}) {
  const wrap = (fn) => (el, ev) => {
    try { const r = fn(el, ev); if (r && typeof r.catch === 'function') r.catch(notifyError); } catch (e) { notifyError(e); }
  };
  const on = (type, attr, handlers) => root.addEventListener(type, (ev) => {
    const el = ev.target.closest?.(`[${attr}]`);
    if (!el || !root.contains(el)) return;
    const fn = handlers[el.getAttribute(attr)];
    if (fn) wrap(fn)(el, ev);
  });
  if (map.click) on('click', 'data-action', map.click);
  if (map.change) on('change', 'data-change', map.change);
  if (map.input) on('input', 'data-input', map.input);
  if (map.keydown) on('keydown', 'data-keydown', map.keydown);
  if (map.submit) {
    root.addEventListener('submit', (ev) => {
      const form = ev.target.closest('[data-submit]');
      if (!form) return;
      ev.preventDefault();
      const fn = map.submit[form.dataset.submit];
      if (fn) wrap(fn)(form, ev);
    });
  }
}

// ---------------------------------------------------------------- modal / confirm
const stack = [];
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && stack.length) stack[stack.length - 1].close(); });

export function openModal({ title = '', body = raw(''), footer = raw(''), size = 'md', onClose } = {}) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `<div class="modal modal-${size}" role="dialog" aria-modal="true"><div class="modal-head"><h3></h3>` +
    `<button type="button" class="icon-btn" data-modal-close aria-label="Close">${icon('x').__raw}</button></div>` +
    '<div class="modal-body"></div><div class="modal-foot"></div></div>';
  const q = (s) => overlay.querySelector(s);
  q('h3').textContent = title;
  mount(q('.modal-body'), body);
  mount(q('.modal-foot'), footer);
  const m = {
    el: overlay, body: q('.modal-body'),
    setTitle: (x) => { q('h3').textContent = x; },
    setBody: (x) => mount(q('.modal-body'), x),
    setFooter: (x) => mount(q('.modal-foot'), x),
    close() {
      if (!overlay.isConnected) return;
      overlay.remove();
      const i = stack.indexOf(m); if (i >= 0) stack.splice(i, 1);
      onClose?.();
    },
  };
  overlay.addEventListener('mousedown', (e) => { if (e.target === overlay) m.close(); });
  overlay.addEventListener('click', (e) => { if (e.target.closest('[data-modal-close]')) m.close(); });
  document.body.appendChild(overlay);
  stack.push(m);
  q('.modal-body input:not([type=hidden]):not([disabled]),.modal-body select,.modal-body textarea')?.focus();
  return m;
}

export function confirmDialog(message, { danger = false, okLabel } = {}) {
  return new Promise((resolve) => {
    let answered = false;
    const m = openModal({
      title: t('common.confirm'), size: 'sm', body: html`<p>${message}</p>`,
      footer: html`<button class="btn" data-action="no">${t('common.cancel')}</button>
        <button class="btn ${danger ? 'danger' : 'primary'}" data-action="yes">${okLabel || t('common.ok')}</button>`,
      onClose: () => { if (!answered) resolve(false); },
    });
    bind(m.el, { click: { yes: () => { answered = true; m.close(); resolve(true); }, no: () => m.close() } });
  });
}

// ---------------------------------------------------------------- forms
export function field({ name, label, type = 'text', value = '', options = [], required = false, placeholder = '', hint = '', cls = '', attrs: a = {}, rows }) {
  const extra = Object.entries(a).map(([k, v]) => ` ${k}="${esc(v)}"`).join('');
  const req = required ? ' required' : '';
  if (type === 'checkbox') {
    return raw(`<label class="check ${esc(cls)}"><input type="checkbox" name="${esc(name)}"${value ? ' checked' : ''}${extra}> ${esc(label)}</label>`);
  }
  let control;
  if (type === 'select') {
    control = `<select name="${esc(name)}"${req}${extra}>${options.map((o) =>
      `<option value="${esc(o.value)}"${String(o.value) === String(value ?? '') ? ' selected' : ''}>${esc(o.label)}</option>`).join('')}</select>`;
  } else if (type === 'textarea') {
    control = `<textarea name="${esc(name)}" rows="${rows || 3}" placeholder="${esc(placeholder)}"${extra}>${esc(value)}</textarea>`;
  } else {
    const kind = type === 'money' ? 'number' : type;
    const step = type === 'money' ? ' step="0.01" min="0"' : type === 'number' ? ' step="any"' : '';
    control = `<input type="${esc(kind)}" name="${esc(name)}" value="${esc(value ?? '')}" placeholder="${esc(placeholder)}"${step}${req}${extra}>`;
  }
  return raw(`<label class="field ${esc(cls)}"><span class="field-label">${esc(label)}${required ? ' <span class="req">*</span>' : ''}</span>${control}` +
    `${hint ? `<span class="field-hint">${esc(hint)}</span>` : ''}</label>`);
}

export function readForm(form) {
  const out = {};
  for (const el of form.querySelectorAll('[name]')) {
    if (el.disabled) continue;
    if (el.type === 'checkbox') out[el.name] = el.checked;
    else if (el.type === 'number') out[el.name] = el.value === '' ? '' : Number(el.value);
    else out[el.name] = el.value;
  }
  return out;
}

export function clearErrors(form) {
  form.querySelectorAll('.field-error,.form-error').forEach((e) => e.remove());
  form.querySelectorAll('.invalid').forEach((e) => e.classList.remove('invalid'));
}
export function showFieldErrors(form, details) {
  if (!details || typeof details !== 'object' || Array.isArray(details)) return;
  for (const [name, code] of Object.entries(details)) {
    const el = form.querySelector(`[name="${CSS.escape(name)}"]`);
    if (!el) continue;
    el.classList.add('invalid');
    el.closest('.field')?.appendChild(Object.assign(document.createElement('div'), { className: 'field-error', textContent: t('v.' + code) }));
  }
}
export function formError(form, message) {
  form.querySelector('.form-error')?.remove();
  form.prepend(Object.assign(document.createElement('div'), { className: 'form-error', textContent: message, style: 'grid-column:1/-1' }));
}
/** Run an async submit handler with busy buttons and field-level server errors. */
export async function submitForm(form, fn) {
  clearErrors(form);
  const btns = [...document.querySelectorAll(`button[form="${form.id}"],#${form.id} button[type=submit]`)];
  btns.forEach((b) => { b.disabled = true; });
  try { return await fn(); }
  catch (e) { showFieldErrors(form, e.details); formError(form, errMsg(e)); return undefined; }
  finally { btns.forEach((b) => { b.disabled = false; }); }
}

// ---------------------------------------------------------------- display helpers
export const chip = (text, tone = '') => html`<span class="chip ${tone ? 'chip-' + tone : ''}">${text}</span>`;
const TONES = { draft: 'info', posted: 'primary', cancelled: 'bad', paid: 'ok', partial: 'warn', unpaid: 'bad', active: 'ok', inactive: '',
  pending: 'warn', approved: 'ok', rejected: 'bad', open: 'warn', in_progress: 'info', resolved: 'ok', closed: '', new: 'info',
  contacted: 'primary', qualified: 'ok', lost: 'bad', converted: 'ok', todo: '', doing: 'info', done: 'ok', planned: 'info',
  low: '', medium: 'info', high: 'warn', critical: 'bad', present: 'ok', late: 'warn', absent: 'bad', leave: 'info',
  expired: 'bad', expiring: 'warn', ok: 'ok', reversed: 'bad', fully_depreciated: '' };
export const statusChip = (v) => (v ? chip(t('status.' + v), TONES[v] ?? '') : raw(''));

export function table({ columns, rows, empty, rowAttr, footer }) {
  if (!rows.length) return html`<div class="empty">${empty ?? t('common.empty')}</div>`;
  return html`<div class="table-wrap"><table class="table"><thead><tr>${columns.map((c) =>
    html`<th class="${c.align === 'end' ? 'num' : ''}">${c.label}</th>`)}</tr></thead><tbody>${rows.map((r) =>
    html`<tr${raw(rowAttr ? rowAttr(r).__raw ?? rowAttr(r) : '')}>${columns.map((c) =>
      html`<td class="${c.align === 'end' ? 'num' : ''} ${c.cls || ''}">${c.render ? c.render(r) : r[c.key]}</td>`)}</tr>`)}
    ${footer || ''}</tbody></table></div>`;
}

export function pager({ page, per_page: per, total }) {
  const pages = Math.max(1, Math.ceil(total / per));
  if (pages <= 1) return raw('');
  return html`<div class="pager"><button class="btn sm" data-action="page" data-page="${page - 1}"${attrs({ disabled: page <= 1 })}>${lang === 'ar' ? '›' : '‹'}</button>
    <span>${page} / ${pages}</span><button class="btn sm" data-action="page" data-page="${page + 1}"${attrs({ disabled: page >= pages })}>${lang === 'ar' ? '‹' : '›'}</button>
    <span class="muted">${total} ${t('common.records')}</span></div>`;
}

export const kpi = ({ label, value, sub = '', tone = '' }) =>
  html`<div class="card kpi ${tone}"><span class="label">${label}</span><span class="value">${value}</span>${sub ? html`<span class="sub">${sub}</span>` : ''}</div>`;

export function barChart(items, { height = 210, fmt = (v) => v } = {}) {
  const W = 640, H = height, pad = { t: 12, b: 26, l: 8, r: 8 };
  const max = Math.max(1, ...items.map((i) => i.value));
  const bw = (W - pad.l - pad.r) / Math.max(1, items.length);
  const inner = H - pad.t - pad.b;
  const lines = [0, 0.5, 1].map((f) => `<line class="grid-line" x1="${pad.l}" x2="${W - pad.r}" y1="${H - pad.b - inner * f}" y2="${H - pad.b - inner * f}"/>`).join('');
  const bars = items.map((it, i) => {
    const h = inner * (Math.max(0, it.value) / max);
    const x = pad.l + i * bw + bw * 0.15;
    return `<rect class="bar" x="${x}" y="${H - pad.b - h}" width="${bw * 0.7}" height="${Math.max(h, it.value > 0 ? 2 : 0)}" rx="3"><title>${esc(it.label)}: ${esc(fmt(it.value))}</title></rect>` +
      `<text x="${x + bw * 0.35}" y="${H - 8}" text-anchor="middle">${esc(it.short ?? it.label)}</text>`;
  }).join('');
  return raw(`<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${lines}${bars}</svg>`);
}

export function hbars(items) {
  const max = Math.max(1, ...items.map((i) => i.value));
  if (!items.length) return html`<div class="empty">${t('common.empty')}</div>`;
  return html`${items.map((i) => html`<div class="hbar"><span>${i.label}</span><span class="bar"><i style="width:${Math.max(2, (i.value / max) * 100)}%"></i></span><b class="num">${i.text}</b></div>`)}`;
}

export function money(v) { return fmtMoney(v); }

// ---------------------------------------------------------------- printing (works with Arabic, uses the browser's PDF printer)
const PRINT_CSS = `*{box-sizing:border-box}body{font-family:'Segoe UI',Tahoma,'Noto Sans Arabic',Arial,sans-serif;color:#111;margin:0;padding:24px;font-size:13px}
h1{font-size:22px;margin:0}h2{font-size:15px;margin:14px 0 6px}table{width:100%;border-collapse:collapse;margin-top:10px}th,td{border-bottom:1px solid #ddd;padding:6px 8px;text-align:start}
th{background:#f3f4f6;font-size:12px}.num{text-align:end}.head{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;border-bottom:2px solid #111;padding-bottom:12px}
.muted{color:#555}.totals{margin-inline-start:auto;width:300px;margin-top:12px}.totals div{display:flex;justify-content:space-between;padding:3px 0}.totals .grand{font-weight:700;font-size:15px;border-top:2px solid #111}
.meta{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;margin:14px 0}.qr{width:120px;height:120px}.receipt{width:78mm;padding:4mm;font-size:12px}.receipt table{margin-top:6px}.center{text-align:center}
@page{margin:10mm}`;

export function printHtml(content, title = 'Document') {
  const frame = document.createElement('iframe');
  frame.style.cssText = 'position:fixed;width:0;height:0;border:0;inset-inline-end:0;bottom:0';
  document.body.appendChild(frame);
  const d = frame.contentDocument;
  d.open();
  d.write(`<!doctype html><html lang="${lang}" dir="${lang === 'ar' ? 'rtl' : 'ltr'}"><head><meta charset="utf-8"><title>${esc(title)}</title><style>${PRINT_CSS}</style></head><body>${content.__raw ?? content}</body></html>`);
  d.close();
  setTimeout(() => { frame.contentWindow.focus(); frame.contentWindow.print(); }, 300);
  setTimeout(() => frame.remove(), 120000);
}

export function setBusy(btn, busy) { if (btn) btn.disabled = busy; }
