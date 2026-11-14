// Application bootstrap: first-run setup / login, shell layout (sidebar, header) and hash router.
import { api, auth } from './core/api.js';
import { can, canAny, state } from './core/state.js';
import { applyLang, errMsg, lang, setLang, t } from './core/i18n.js';
import { bind, field, html, icon, mount, notifyError, readForm, submitForm } from './core/ui.js';

const app = document.getElementById('app');
applyLang();
const savedTheme = localStorage.getItem('erp_theme');
document.documentElement.dataset.theme = savedTheme || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');

const NAV = [
  { group: null, items: [{ key: 'dashboard', icon: 'home', perms: ['dashboard.read'] }, { key: 'pos', icon: 'cart', perms: ['pos.use'] }] },
  { group: 'nav.g_trade', items: [{ key: 'sales', icon: 'receipt', perms: ['sales.read', 'customers.read'] }, { key: 'purchases', icon: 'truck', perms: ['purchases.read', 'suppliers.read'] }] },
  { group: 'nav.g_stock', items: [{ key: 'products', icon: 'tag', perms: ['products.read'] }, { key: 'inventory', icon: 'box', perms: ['inventory.read'] }, { key: 'manufacturing', icon: 'layers', perms: ['manufacturing.read'] }] },
  { group: 'nav.g_finance', items: [{ key: 'accounting', icon: 'book', perms: ['accounting.read'] }, { key: 'reports', icon: 'chart', perms: ['reports.read'] }] },
  { group: 'nav.g_people', items: [{ key: 'crm', icon: 'target', perms: ['crm.read'] }, { key: 'hr', icon: 'users', perms: ['hr.read'] }, { key: 'projects', icon: 'briefcase', perms: ['projects.read'] },
    { key: 'maintenance', icon: 'tool', perms: ['maintenance.read'] }, { key: 'files', icon: 'folder', perms: ['documents.read'] }] },
  { group: 'nav.g_system', items: [{ key: 'data', icon: 'upload', perms: ['data.export', 'data.import'] }, { key: 'settings', icon: 'gear', perms: ['settings.manage', 'users.manage', 'audit.read'] }] },
];
const PAGES = {
  dashboard: () => import('./pages/dashboard.js').then((m) => m.default), pos: () => import('./pages/pos.js').then((m) => m.default),
  sales: () => import('./pages/documents.js').then((m) => m.sales), purchases: () => import('./pages/documents.js').then((m) => m.purchases),
  products: () => import('./pages/inventory.js').then((m) => m.products), inventory: () => import('./pages/inventory.js').then((m) => m.inventory),
  manufacturing: () => import('./pages/ops.js').then((m) => m.manufacturing), maintenance: () => import('./pages/ops.js').then((m) => m.maintenance), files: () => import('./pages/ops.js').then((m) => m.files),
  accounting: () => import('./pages/accounting.js').then((m) => m.default), reports: () => import('./pages/reports.js').then((m) => m.default),
  crm: () => import('./pages/people.js').then((m) => m.crm), hr: () => import('./pages/people.js').then((m) => m.hr), projects: () => import('./pages/people.js').then((m) => m.projectsPage),
  data: () => import('./pages/data.js').then((m) => m.default), settings: () => import('./pages/settings.js').then((m) => m.default),
};
const allowed = () => NAV.flatMap((g) => g.items).filter((i) => canAny(...i.perms)).map((i) => i.key);

// ---------------------------------------------------------------- auth screens
const langButton = () => html`<button class="btn sm" data-action="lang">${icon('globe')} ${t('app.switch_lang')}</button>`;
function authScreen(inner, after) {
  mount(app, html`<div class="auth"><div class="card"><div class="row" style="justify-content:space-between"><div class="brand" style="padding:0"><img src="/icons/icon.svg" alt="" width="32" height="32"> ERP Global Pro</div>${langButton()}</div>${inner}</div></div>`);
  bind(app, { click: { lang: () => { setLang(lang === 'ar' ? 'en' : 'ar'); after(); } } });
}

function showLogin(message) {
  authScreen(html`<h1 style="margin-top:18px">${t('auth.welcome')}</h1>${message ? html`<div class="form-error" style="margin-top:10px">${message}</div>` : ''}
    <form id="login" data-submit="login">${field({ name: 'email', label: t('auth.email'), type: 'email', required: true, attrs: { autocomplete: 'username' } })}
    ${field({ name: 'password', label: t('auth.password'), type: 'password', required: true, attrs: { autocomplete: 'current-password' } })}
    <button class="btn primary block" type="submit">${t('auth.login')}</button></form>`, () => showLogin(message));
  bind(app, { submit: { login: (form) => submitForm(form, async () => {
    const res = await api.post('/auth/login', readForm(form));
    auth.set(res.token); await startSession();
  }) } });
}

function showSetup() {
  authScreen(html`<h1 style="margin-top:18px">${t('auth.setup_title')}</h1><p class="muted">${t('auth.setup_sub')}</p>
    <form id="setup" class="grid" data-submit="setup">${field({ name: 'company', label: t('auth.company'), required: true })}
    <div class="grid g2">${field({ name: 'currency', label: t('set.currency'), value: 'USD', required: true, attrs: { maxlength: 8 } })}${field({ name: 'tax', label: t('set.tax'), type: 'number', value: 15, attrs: { min: 0, max: 100 } })}</div>
    ${field({ name: 'name', label: t('auth.admin_name'), required: true })}${field({ name: 'email', label: t('auth.email'), type: 'email', required: true })}
    ${field({ name: 'password', label: t('auth.password'), type: 'password', required: true, hint: t('set.password_hint') })}
    ${field({ name: 'demo', label: t('auth.load_demo'), type: 'checkbox', value: true })}<button class="btn primary block" type="submit">${t('auth.create')}</button></form>`, showSetup);
  bind(app, { submit: { setup: (form) => submitForm(form, async () => {
    const res = await api.post('/auth/setup', readForm(form));
    auth.set(res.token); await startSession();
  }) } });
}

// ---------------------------------------------------------------- shell
let routeSeq = 0;
async function startSession() {
  const me = await api.get('/auth/me');
  const companyState = await api.get('/auth/companies');
  state.user = me.user;
  state.companies = companyState.items || [];
  state.activeCompany = companyState.active_company || null;
  if (!state.activeCompany || !companyState.can_use_operations) {
    showCompanyGate(Boolean(state.activeCompany) && !companyState.can_use_operations);
    return;
  }
  state.settings = await api.get('/settings');
  // Company identity is taken from the verified membership, not a browser-supplied header.
  state.settings.company = state.activeCompany.name;
  renderShell();
  await route();
}

function showCompanyGate(isolationPending = false) {
  const hasCompanies = state.companies.length > 0;
  const options = state.companies.map((c) => ({ value: c.company_id, label: c.company_name }));
  const selected = state.activeCompany?.id ?? options[0]?.value ?? '';
  const title = isolationPending ? t('tenant.isolation_title') : t('tenant.choose_title');
  const message = isolationPending ? t('tenant.isolation_message') :
    hasCompanies ? t('tenant.choose_message') : t('tenant.no_memberships');
  const picker = hasCompanies ? html`<form id="company-select" class="grid" data-submit="select-company">
    ${field({ name: 'company_id', label: t('tenant.select_company'), type: 'select', value: selected, options, required: true })}
    <button class="btn primary block" type="submit">${isolationPending ? t('tenant.switch_company') : t('tenant.continue')}</button>
  </form>` : '';
  authScreen(html`<h1 style="margin-top:18px">${title}</h1><p class="muted">${message}</p>
    ${picker}<button class="btn block" data-action="gate-logout">${icon('logout')} ${t('auth.logout')}</button>`,
    () => showCompanyGate(isolationPending));
  bind(app, {
    submit: { 'select-company': (form) => submitForm(form, async () => {
      const result = await api.post('/auth/switch-company', readForm(form));
      auth.set(result.token);
      window.location.reload();
    }) },
    click: { 'gate-logout': () => { auth.clear(); state.user = null; state.activeCompany = null; state.companies = []; showLogin(); } },
  });
}

function renderShell() {
  const groups = NAV.map((g) => ({ ...g, items: g.items.filter((i) => canAny(...i.perms)) })).filter((g) => g.items.length);
  const companyPicker = state.companies.length > 1 ? html`<select class="company-switch" data-change="switch-company" aria-label="${t('tenant.select_company')}">
    ${state.companies.map((c) => c.company_id === state.activeCompany?.id ? html`<option value="${c.company_id}" selected>${c.company_name}</option>` : html`<option value="${c.company_id}">${c.company_name}</option>`)}</select>` : '';
  mount(app, html`<div class="shell"><aside class="sidebar" id="side"><div class="brand"><img src="/icons/icon.svg" alt="" width="30" height="30"><span id="company">${state.settings.company || 'ERP Global Pro'}</span></div>
    <nav class="nav">${groups.map((g) => html`${g.group ? html`<div class="nav-group">${t(g.group)}</div>` : ''}${g.items.map((i) => html`<a href="#/${i.key}" data-nav="${i.key}">${icon(i.icon)}<span>${t('nav.' + i.key)}</span></a>`)}`)}</nav></aside>
    <div class="backdrop" id="backdrop" data-action="close-menu"></div>
    <div class="main"><header class="topbar"><button class="icon-btn menu-btn" data-action="menu" aria-label="Menu">${icon('menu')}</button><h2 id="title"></h2><span class="grow"></span>${companyPicker}
      <button class="btn sm" data-action="lang">${icon('globe')} ${t('app.switch_lang')}</button><button class="icon-btn" data-action="theme" title="${t('app.theme')}">${icon('moon')}</button>
      <div class="rel"><button class="btn sm" data-action="user-menu">${icon('user')} ${state.user.name}</button><div class="menu hidden" id="user-menu">
        <div class="muted" style="padding:6px 10px;font-size:12px">${state.user.email}</div><hr><button data-action="password">${icon('shield')} ${t('set.change_password')}</button>
        <button data-action="logout">${icon('logout')} ${t('auth.logout')}</button></div></div></header>
      <main class="content" id="outlet"></main></div></div>`);
  const menu = () => document.getElementById('user-menu');
  bind(app, { change: {
    'switch-company': async (el) => {
      const companyId = Number(el.value);
      if (!Number.isInteger(companyId) || companyId === state.activeCompany?.id) return;
      el.disabled = true;
      try {
        const result = await api.post('/auth/switch-company', { company_id: companyId });
        auth.set(result.token);
        window.location.reload();
      } catch (error) {
        el.disabled = false;
        el.value = String(state.activeCompany.id);
        throw error;
      }
    },
  }, click: {
    lang: () => { setLang(lang === 'ar' ? 'en' : 'ar'); renderShell(); route(); },
    theme: () => { const next = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = next; localStorage.setItem('erp_theme', next); },
    menu: () => { document.getElementById('side').classList.toggle('open'); document.getElementById('backdrop').classList.toggle('open'); },
    'close-menu': () => { document.getElementById('side').classList.remove('open'); document.getElementById('backdrop').classList.remove('open'); },
    'user-menu': (el, ev) => { ev.stopPropagation(); menu().classList.toggle('hidden'); },
    password: async () => { menu().classList.add('hidden'); (await import('./pages/settings.js')).openChangePassword(); },
    logout: () => { auth.clear(); state.user = null; showLogin(); },
  } });
}
document.addEventListener('click', () => document.getElementById('user-menu')?.classList.add('hidden'));
window.addEventListener('erp:settings', () => { const c = document.getElementById('company'); if (c) c.textContent = state.settings.company || 'ERP Global Pro'; });
window.addEventListener('erp:unauthorized', () => { state.user = null; showLogin(t('auth.expired')); });

async function route() {
  if (!state.user) return;
  const outlet = document.getElementById('outlet');
  if (!outlet) return;
  const seq = ++routeSeq;
  const ok = allowed();
  let [key, sub] = location.hash.replace(/^#\/?/, '').split('/');
  if (!key || !PAGES[key]) key = ok.includes('dashboard') ? 'dashboard' : ok[0];
  if (!key) { mount(outlet, html`<div class="empty">${t('common.no_access')}</div>`); return; }
  document.querySelectorAll('[data-nav]').forEach((a) => a.classList.toggle('active', a.dataset.nav === key));
  document.getElementById('title').textContent = t('nav.' + key);
  document.title = `${t('nav.' + key)} — ${state.settings.company || 'ERP Global Pro'}`;
  document.getElementById('side')?.classList.remove('open'); document.getElementById('backdrop')?.classList.remove('open');
  if (!ok.includes(key)) { mount(outlet, html`<div class="empty">${t('common.no_access')}</div>`); return; }
  mount(outlet, html`<div class="loading"><div class="spinner"></div></div>`);
  try {
    const page = await PAGES[key]();
    if (seq !== routeSeq) return;
    outlet.replaceChildren();
    await page.render(outlet, { sub });
  } catch (e) {
    if (seq !== routeSeq) return;
    console.error(e);
    mount(outlet, html`<div class="card card-pad"><div class="form-error">${errMsg(e)}</div></div>`);
  }
}
window.addEventListener('hashchange', route);

async function boot() {
  try {
    const { needs_setup: needsSetup } = await api.get('/auth/setup-status');
    if (needsSetup) return showSetup();
  } catch (e) {
    mount(app, html`<div class="boot"><div class="card card-pad" style="text-align:center"><p>${t('app.server_error')}</p><button class="btn primary" data-action="retry">${t('app.retry')}</button></div></div>`);
    bind(app, { click: { retry: () => location.reload() } });
    return undefined;
  }
  if (auth.token) {
    try { await startSession(); return undefined; } catch { auth.clear(); }
  }
  showLogin();
  return undefined;
}
boot();

if ('serviceWorker' in navigator && (location.protocol === 'https:' || ['localhost', '127.0.0.1'].includes(location.hostname))) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}
