// Settings: company, users, roles & permissions, audit log, system (backup, demo data, password).
import { api } from '../core/api.js';
import { can, state } from '../core/state.js';
import { errMsg, t } from '../core/i18n.js';
import { fmtNum } from '../core/fmt.js';
import { attrs, bind, confirmDialog, debounce, field, html, icon, mount, notifyError, openModal, pager, readForm, statusChip, submitForm, table, toast } from '../core/ui.js';
import { clearRefCache, loadRef, refOptions, tabsPage } from '../core/resource.js';

export function openChangePassword() {
  const id = 'pw' + Date.now();
  const m = openModal({ title: t('set.change_password'), size: 'sm', body: html`<form id="${id}" class="grid" data-submit="save">
    ${field({ name: 'current_password', label: t('set.current_password'), type: 'password', required: true })}${field({ name: 'new_password', label: t('set.new_password'), type: 'password', required: true, hint: t('set.password_hint') })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { await api.post('/auth/change-password', readForm(form)); m.close(); toast(t('common.saved'), 'ok'); }) } });
}

// ---------------------------------------------------------------- company
async function companyTab(container) {
  const s = await api.get('/settings');
  const whs = await loadRef({ resource: 'warehouses', params: { is_active: 'true' } }).catch(() => []);
  const write = can('settings.manage');
  mount(container, html`<div class="card card-pad"><form id="cf" class="grid g3" data-submit="save">
    ${field({ name: 'company', label: t('set.company'), value: s.company, required: true })}${field({ name: 'currency', label: t('set.currency'), value: s.currency, required: true, attrs: { maxlength: 8 } })}
    ${field({ name: 'tax', label: t('set.tax'), type: 'number', value: s.tax, attrs: { min: 0, max: 100 }, hint: t('set.tax_hint') })}
    ${field({ name: 'tax_number', label: t('set.tax_number'), value: s.tax_number })}${field({ name: 'cr_number', label: t('set.cr_number'), value: s.cr_number })}${field({ name: 'country', label: t('f.country'), value: s.country })}
    ${field({ name: 'address', label: t('f.address'), value: s.address })}${field({ name: 'phone', label: t('f.phone'), value: s.phone })}${field({ name: 'email', label: t('f.email'), value: s.email })}
    ${field({ name: 'invoice_prefix', label: t('set.invoice_prefix'), value: s.invoice_prefix })}${field({ name: 'lock_date', label: t('set.lock_date'), type: 'date', value: s.lock_date, hint: t('set.lock_hint') })}
    ${field({ name: 'default_warehouse_id', label: t('set.default_warehouse'), type: 'select', value: s.default_warehouse_id ?? '', options: refOptions(whs) })}
    ${field({ name: 'allow_negative_stock', label: t('set.negative_stock'), type: 'checkbox', value: s.allow_negative_stock })}
    ${write ? html`<div style="grid-column:1/-1"><button class="btn primary" type="submit">${t('common.save')}</button></div>` : ''}</form></div>`);
  bind(container, { submit: { save: (form) => submitForm(form, async () => {
    const v = readForm(form);
    v.default_warehouse_id = v.default_warehouse_id || null;
    state.settings = await api.put('/settings', v);
    window.dispatchEvent(new Event('erp:settings')); toast(t('common.saved'), 'ok');
  }) } });
}

// ---------------------------------------------------------------- users
async function usersTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const [u, r] = await Promise.all([api.get('/users'), api.get('/roles')]);
    root._users = u.items; root._roles = r.items;
    mount(root, html`<div class="toolbar"><span class="grow"></span><button class="btn primary" data-action="add">${icon('plus')} ${t('common.add')}</button></div>
      <div class="card">${table({ rows: u.items, columns: [{ label: t('f.name'), key: 'name' }, { label: t('f.email'), key: 'email' }, { label: t('set.role'), render: (x) => (r.items.find((y) => y.name === x.role)?.label || x.role) },
        { label: t('f.status'), render: (x) => statusChip(x.is_active ? 'active' : 'inactive') }, { label: t('set.last_login'), render: (x) => x.last_login || '—' },
        { label: '', cls: 'actions', render: (x) => html`<button class="icon-btn" data-action="edit" data-id="${x.id}">${icon('edit')}</button>` }] })}</div>`);
  }
  function form(user) {
    const id = 'uf' + Date.now();
    const m = openModal({ title: t(user ? 'common.edit' : 'common.add') + ' — ' + t('set.user'), size: 'md', body: html`<form id="${id}" class="grid g2" data-submit="save">
      ${field({ name: 'name', label: t('f.name'), required: true, value: user?.name })}${user ? '' : field({ name: 'email', label: t('f.email'), type: 'email', required: true })}
      ${field({ name: 'role', label: t('set.role'), type: 'select', value: user?.role || 'viewer', options: root._roles.map((x) => ({ value: x.name, label: x.label })) })}
      ${field({ name: 'password', label: t('set.password'), type: 'password', required: !user, hint: user ? t('set.keep_password') : t('set.password_hint') })}
      ${user ? field({ name: 'is_active', label: t('f.is_active'), type: 'checkbox', value: user.is_active }) : ''}</form>`,
      footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
    bind(m.el, { submit: { save: (f) => submitForm(f, async () => {
      const v = readForm(f); if (user && !v.password) delete v.password;
      if (user) await api.put(`/users/${user.id}`, v); else await api.post('/users', v);
      m.close(); toast(t('common.saved'), 'ok'); load();
    }) } });
  }
  bind(root, { click: { add: () => form(null), edit: (el) => form(root._users.find((x) => String(x.id) === el.dataset.id)) } });
  await load();
}

// ---------------------------------------------------------------- roles
const MODULES = [['dashboard', ['read']], ['pos', ['use']], ['sales', ['read', 'write', 'void']], ['purchases', ['read', 'write', 'void']], ['payments', ['read', 'write']],
  ['customers', ['read', 'write']], ['suppliers', ['read', 'write']], ['products', ['read', 'write']], ['inventory', ['read', 'write']], ['accounting', ['read', 'write']], ['reports', ['read']],
  ['crm', ['read', 'write']], ['hr', ['read', 'write']], ['projects', ['read', 'write']], ['manufacturing', ['read', 'write']], ['maintenance', ['read', 'write']], ['documents', ['read', 'write']], ['data', ['export', 'import']]];
const COL = { read: 0, use: 0, export: 0, write: 1, import: 1, void: 2 };
const holds = (perms, m, a) => perms.includes('*') || perms.includes(`${m}.${a}`) || perms.includes(`${m}.*`) || perms.includes(`*.${a}`);

function roleEditor(role, done) {
  const perms = role?.permissions || [];
  const id = 'rl' + Date.now();
  const grid = MODULES.map(([m, actions]) => {
    const cells = [null, null, null];
    actions.forEach((a) => { cells[COL[a]] = html`<label title="${a}"><input type="checkbox" name="${m}.${a}"${attrs({ checked: holds(perms, m, a) })}> <span class="muted">${t('perm.' + a)}</span></label>`; });
    return html`<b>${t('perm.m.' + m)}</b>${cells.map((c) => c || html`<span></span>`)}`;
  });
  const m = openModal({ title: role ? `${t('set.roles')} — ${role.label}` : t('set.new_role'), size: 'lg', body: html`<form id="${id}" data-submit="save">
    ${role ? '' : html`<div class="grid g2" style="margin-bottom:12px">${field({ name: 'name', label: t('f.code'), required: true })}${field({ name: 'label', label: t('f.name'), required: true })}</div>`}
    <div class="perm-grid"><span class="h">${t('set.module')}</span><span class="h">${t('perm.read')}</span><span class="h">${t('perm.write')}</span><span class="h">${t('perm.void')}</span>${grid}</div></form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => {
    const v = readForm(form);
    const list = Object.entries(v).filter(([k, on]) => on === true && k.includes('.')).map(([k]) => k);
    if (role) await api.put(`/roles/${role.name}`, { permissions: list });
    else await api.post('/roles', { name: v.name, label: v.label, permissions: list });
    m.close(); toast(t('common.saved'), 'ok'); done();
  }) } });
}

async function rolesTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = await api.get('/roles'); root._roles = d.items;
    mount(root, html`<div class="toolbar"><span class="grow"></span><button class="btn primary" data-action="add">${icon('plus')} ${t('set.new_role')}</button></div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.name'), render: (r) => html`<b>${r.label}</b> <span class="muted">${r.name}</span>` }, { label: t('set.users'), align: 'end', key: 'users' },
        { label: t('set.permissions'), render: (r) => (r.permissions.includes('*') ? html`<span class="chip chip-primary">${t('set.full_access')}</span>` : html`<span class="muted">${r.permissions.length}</span>`) },
        { label: '', cls: 'actions', render: (r) => (r.name === 'admin' ? '' : html`<button class="icon-btn" data-action="edit" data-id="${r.name}">${icon('edit')}</button>${r.is_system ? '' : html`<button class="icon-btn" data-action="del" data-id="${r.name}">${icon('trash')}</button>`}`) }] })}</div>`);
  }
  bind(root, { click: { add: () => roleEditor(null, load), edit: (el) => roleEditor(root._roles.find((r) => r.name === el.dataset.id), load),
    del: async (el) => { if (await confirmDialog(t('common.confirm_delete'), { danger: true })) { await api.del(`/roles/${el.dataset.id}`); toast(t('common.deleted'), 'ok'); await load(); } } } });
  await load();
}

// ---------------------------------------------------------------- audit
async function auditTab(container) {
  const S = { q: '', date_from: '', date_to: '', page: 1 };
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = await api.get('/audit', { ...S, per_page: 30 });
    mount(root, html`<div class="toolbar"><input class="search" type="search" placeholder="${t('common.search')}" value="${S.q}" data-input="search"><input type="date" value="${S.date_from}" data-change="f" data-name="date_from" style="width:auto"><input type="date" value="${S.date_to}" data-change="f" data-name="date_to" style="width:auto"></div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('set.time'), key: 'ts', cls: 'nowrap' }, { label: t('set.user'), render: (r) => r.user_email || '—' }, { label: t('set.action'), key: 'action' }, { label: t('set.entity'), render: (r) => `${r.entity || ''} ${r.entity_id ? '#' + r.entity_id : ''}` },
        { label: t('set.details'), render: (r) => html`<span class="muted" style="font-size:12px">${(r.details || '').slice(0, 140)}</span>` }] })}${pager(d)}</div>`);
    const s = root.querySelector('input[type=search]'); if (S.focus) { s.focus(); s.setSelectionRange(s.value.length, s.value.length); }
  }
  bind(root, { click: { page: (el) => { S.page = Number(el.dataset.page); S.focus = false; return load(); } },
    change: { f: (el) => { S[el.dataset.name] = el.value; S.page = 1; S.focus = false; return load(); } },
    input: { search: debounce((el) => { S.q = el.value.trim(); S.page = 1; S.focus = true; load().catch(notifyError); }, 300) } });
  await load();
}

// ---------------------------------------------------------------- system
async function systemTab(container) {
  const info = await api.get('/system/info');
  mount(container, html`<div class="grid g2"><div class="card card-pad"><h3>${t('set.system')}</h3><div class="kv" style="margin-top:12px"><div><small>${t('set.version')}</small>${info.version}</div><div><small>${t('set.db_size')}</small>${fmtNum(info.db_size / 1024 / 1024, 2)} MB</div></div>
      ${Object.entries(info.counts).map(([k, v]) => html`<div class="check-row"><span class="grow">${t('set.count.' + k)}</span><b>${fmtNum(v)}</b></div>`)}</div>
    <div class="card card-pad"><h3>${t('set.backup')}</h3><p class="muted">${t('set.backup_hint')}</p>${can('system.backup') ? html`<button class="btn" data-action="backup">${icon('download')} ${t('set.download_backup')}</button>` : ''}
      ${info.demo_allowed ? html`<hr style="margin:16px 0;border:0;border-top:1px solid var(--border)"><h3>${t('set.demo')}</h3><p class="muted">${t('set.demo_hint')}</p><button class="btn" data-action="demo">${t('set.load_demo')}</button>` : ''}
      <hr style="margin:16px 0;border:0;border-top:1px solid var(--border)"><button class="btn" data-action="password">${icon('shield')} ${t('set.change_password')}</button></div></div>`);
  bind(container, { click: {
    backup: () => api.download('/system/backup', {}, `erp-backup-${new Date().toISOString().slice(0, 10)}.sqlite3`),
    password: openChangePassword,
    demo: async () => { if (await confirmDialog(t('set.confirm_demo'))) { await api.post('/system/demo-data'); clearRefCache(); toast(t('common.saved'), 'ok'); location.hash = '#/dashboard'; location.reload(); } },
  } });
}

export default {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.settings')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'settings', [
      { key: 'company', label: 'set.company_tab', perm: 'settings.manage', render: companyTab },
      { key: 'users', label: 'set.users', perm: 'users.manage', render: usersTab },
      { key: 'roles', label: 'set.roles', perm: 'users.manage', render: rolesTab },
      { key: 'audit', label: 'set.audit', perm: 'audit.read', render: auditTab },
      { key: 'system', label: 'set.system', perm: 'settings.manage', render: systemTab },
    ]);
  },
};
