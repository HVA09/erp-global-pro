// HR (employees, attendance, leave, advances, payroll), CRM (leads, pipeline, activities, campaigns), projects.
import { api } from '../core/api.js';
import { can } from '../core/state.js';
import { t } from '../core/i18n.js';
import { fmtMoney, fmtNum, today } from '../core/fmt.js';
import { attrs, bind, confirmDialog, field, html, icon, mount, openModal, readForm, statusChip, submitForm, table, toast } from '../core/ui.js';
import { clearRefCache, loadRef, openResourceForm, refOptions, resourceView, tabsPage } from '../core/resource.js';

const opts = (prefix, list) => list.map((v) => ({ value: v, i18n: `${prefix}.${v}` }));
const activeFilter = [{ name: 'status', options: opts('status', ['active', 'inactive']) }];

// ====================================================================== HR
const employees = resourceView({ name: 'employees', perm: 'hr', singular: 'f.employee', modalSize: 'lg', defaults: { status: 'active', salary: 0 },
  columns: [{ key: 'name' }, { key: 'department_name', label: 'f.department' }, { key: 'position' }, { key: 'salary', money: true }, { key: 'advance_balance', money: true }, { key: 'status', chip: true }],
  fields: [{ name: 'name', required: true }, { name: 'department_id', label: 'f.department', ref: { resource: 'departments' } }, { name: 'position' }, { name: 'salary', type: 'money' },
    { name: 'phone' }, { name: 'email', type: 'email' }, { name: 'hire_date', type: 'date' }, { name: 'status', options: opts('status', ['active', 'inactive']) }],
  filters: [{ name: 'department_id', ref: { resource: 'departments' } }, ...activeFilter] });

const leaves = resourceView({ name: 'leave_requests', perm: 'hr', singular: 'hr.leave_request', defaults: { type: 'annual', status: 'pending', start_date: today(), end_date: today() },
  columns: [{ key: 'employee_name', label: 'f.employee' }, { key: 'type', label: 'f.type', render: (r) => t('leave.' + r.type) }, { key: 'start_date' }, { key: 'end_date' }, { key: 'status', chip: true }, { key: 'note' }],
  fields: [{ name: 'employee_id', label: 'f.employee', required: true, ref: { resource: 'employees' }, optional: false }, { name: 'type', options: opts('leave', ['annual', 'sick', 'unpaid', 'other']) },
    { name: 'start_date', type: 'date', required: true }, { name: 'end_date', type: 'date', required: true }, { name: 'status', options: opts('status', ['pending', 'approved', 'rejected']) }, { name: 'note', type: 'textarea', full: true }],
  filters: [{ name: 'status', options: opts('status', ['pending', 'approved', 'rejected']) }],
  rowActions: [
    { key: 'approve', label: 'hr.approve', perm: 'hr.write', show: (r) => r.status === 'pending', run: async (r, reload) => { await api.put(`/leave_requests/${r.id}`, { status: 'approved' }); toast(t('common.saved'), 'ok'); reload(); } },
    { key: 'reject', label: 'hr.reject', perm: 'hr.write', show: (r) => r.status === 'pending', run: async (r, reload) => { await api.put(`/leave_requests/${r.id}`, { status: 'rejected' }); toast(t('common.saved'), 'ok'); reload(); } }] });

async function attendanceTab(container) {
  let date = today();
  const write = can('hr.write');
  async function load() {
    const d = await api.get('/hr/attendance-sheet', { date });
    mount(container, html`<div class="toolbar"><label class="row">${t('f.date')} <input type="date" value="${date}" data-change="date" style="width:auto"></label><span class="grow"></span>
      ${write ? html`<button class="btn" data-action="all-present">${t('hr.all_present')}</button><button class="btn primary" data-action="save">${t('common.save')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.employee'), key: 'name' }, { label: t('f.position'), render: (r) => r.position || '' },
        { label: t('f.status'), render: (r) => html`<select data-emp="${r.employee_id}" style="width:auto"${attrs({ disabled: !write })}><option value="">—</option>${['present', 'late', 'absent', 'leave'].map((s) => html`<option value="${s}"${attrs({ selected: r.status === s })}>${t('status.' + s)}</option>`)}</select>`) }] })}</div>`);
  }
  bind(container, {
    change: { date: (el) => { date = el.value || today(); return load(); } },
    click: {
      'all-present': () => container.querySelectorAll('[data-emp]').forEach((s) => { s.value = 'present'; }),
      save: async () => {
        const records = [...container.querySelectorAll('[data-emp]')].filter((s) => s.value).map((s) => ({ employee_id: Number(s.dataset.emp), status: s.value }));
        await api.put('/hr/attendance', { date, records }); toast(t('common.saved'), 'ok');
      },
    },
  });
  await load();
}

async function advancesTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = await api.get('/hr/advances', { per_page: 50 });
    mount(root, html`<div class="toolbar"><span class="grow"></span>${can('hr.write') ? html`<button class="btn primary" data-action="add">${icon('plus')} ${t('common.add')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.date'), key: 'date' }, { label: t('f.employee'), key: 'employee_name' }, { label: t('f.amount'), align: 'end', render: (r) => fmtMoney(r.amount) }, { label: t('f.note'), render: (r) => r.note || '' }] })}</div>`);
  }
  bind(root, { click: { add: async () => {
    const [emps, accts] = [await loadRef({ resource: 'employees', params: { status: 'active' } }), (await api.get('/payment-accounts')).items];
    const id = 'adv' + Date.now();
    const m = openModal({ title: t('hr.advance'), size: 'sm', body: html`<form id="${id}" class="grid" data-submit="save">
      ${field({ name: 'employee_id', label: t('f.employee'), type: 'select', required: true, options: refOptions(emps, 'name', false) })}${field({ name: 'amount', label: t('f.amount'), type: 'money', required: true })}
      ${field({ name: 'date', label: t('f.date'), type: 'date', value: today() })}${field({ name: 'account_id', label: t('f.account'), type: 'select', options: accts.map((a) => ({ value: a.id, label: a.name })) })}${field({ name: 'note', label: t('f.note') })}</form>`,
      footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
    bind(m.el, { submit: { save: (form) => submitForm(form, async () => { await api.post('/hr/advances', readForm(form)); clearRefCache(); m.close(); toast(t('common.saved'), 'ok'); load(); }) } });
  } } });
  await load();
}

async function payrollTab(container) {
  const S = { month: today().slice(0, 7), items: {}, account: '' };
  const accts = (await api.get('/payment-accounts')).items;
  S.account = (accts.find((a) => a.subtype === 'bank') || accts[0] || {}).id || '';
  const write = can('hr.write');
  const num = (v) => Number(v) || 0;
  const net = (e) => { const i = S.items[e.id] || {}; return e.salary + num(i.bonus) - num(i.deduction) - num(i.advance_deduction); };
  async function load() {
    const [prev, runs] = await Promise.all([api.get('/hr/payroll/preview', { month: S.month }), api.get('/hr/payroll')]);
    const emps = prev.employees.map((e) => ({ ...e, id: e.id, salary: e.salary }));
    mount(container, html`<div class="card"><div class="card-head"><h3>${t('hr.run_payroll')}</h3></div><div class="card-pad">
      <div class="toolbar"><label class="row">${t('f.period')} <input type="month" value="${S.month}" data-change="month" style="width:auto"></label>
        <label class="row">${t('f.account')} <select data-change="account" style="width:auto">${accts.map((a) => html`<option value="${a.id}"${attrs({ selected: String(a.id) === String(S.account) })}>${a.name}</option>`)}</select></label>
        ${prev.exists ? html`<span class="chip chip-warn">${t('hr.payroll_exists')}</span>` : ''}<span class="grow"></span>
        ${write ? html`<button class="btn primary" data-action="run"${attrs({ disabled: prev.exists || !emps.length })}>${icon('check')} ${t('hr.run_payroll')}</button>` : ''}</div>
      ${table({ rows: emps, columns: [{ label: t('f.employee'), key: 'name' }, { label: t('f.salary'), align: 'end', render: (e) => fmtMoney(e.salary) },
        { label: t('hr.bonus'), render: (e) => html`<input type="number" min="0" step="0.01" style="width:100px" data-input="item" data-id="${e.id}" data-f="bonus" value="${S.items[e.id]?.bonus ?? ''}">` },
        { label: t('hr.deduction'), render: (e) => html`<input type="number" min="0" step="0.01" style="width:100px" data-input="item" data-id="${e.id}" data-f="deduction" value="${S.items[e.id]?.deduction ?? ''}">` },
        { label: t('f.advance_balance'), align: 'end', render: (e) => fmtMoney(e.advance_balance) },
        { label: t('hr.advance_deduction'), render: (e) => html`<input type="number" min="0" step="0.01" max="${e.advance_balance}" style="width:100px" data-input="item" data-id="${e.id}" data-f="advance_deduction" value="${S.items[e.id]?.advance_deduction ?? ''}">` },
        { label: t('hr.net'), align: 'end', render: (e) => html`<b data-net="${e.id}">${fmtMoney(net(e))}</b>` }] })}</div></div>
      <div class="card" style="margin-top:14px"><div class="card-head"><h3>${t('hr.payroll_history')}</h3></div>${table({ rows: runs.items, columns: [{ label: t('f.number'), key: 'number' }, { label: t('f.period'), key: 'month' }, { label: t('f.date'), key: 'date' },
        { label: t('hr.employees'), align: 'end', key: 'employees' }, { label: t('hr.net'), align: 'end', render: (r) => fmtMoney(r.total_net) }, { label: t('f.status'), render: (r) => statusChip(r.status === 'posted' ? 'posted' : 'cancelled') },
        { label: '', cls: 'actions', render: (r) => html`<button class="btn sm" data-action="view" data-id="${r.id}">${t('common.view')}</button>${write && r.status === 'posted' ? html` <button class="btn sm danger" data-action="cancel" data-id="${r.id}">${t('common.cancel_it')}</button>` : ''}` }] })}</div>`);
    container._emps = emps;
  }
  bind(container, {
    change: { month: (el) => { S.month = el.value || S.month; S.items = {}; return load(); }, account: (el) => { S.account = el.value; } },
    input: { item: (el) => {
      const id = el.dataset.id; (S.items[id] ||= {})[el.dataset.f] = el.value;
      const e = container._emps.find((x) => String(x.id) === id); container.querySelector(`[data-net="${id}"]`).textContent = fmtMoney(net(e));
    } },
    click: {
      run: async () => {
        if (!(await confirmDialog(t('hr.confirm_payroll', { month: S.month })))) return;
        const items = container._emps.map((e) => ({ employee_id: e.id, bonus: num(S.items[e.id]?.bonus), deduction: num(S.items[e.id]?.deduction), advance_deduction: num(S.items[e.id]?.advance_deduction) }));
        await api.post('/hr/payroll', { month: S.month, account_id: S.account, items }); S.items = {}; clearRefCache(); toast(t('hr.payroll_done'), 'ok'); await load();
      },
      view: async (el) => {
        const r = await api.get(`/hr/payroll/${el.dataset.id}`);
        openModal({ title: `${t('hr.payroll')} ${r.number} — ${r.month}`, size: 'lg', body: table({ rows: r.items, columns: [{ label: t('f.employee'), key: 'employee_name' }, { label: t('f.salary'), align: 'end', render: (i) => fmtMoney(i.salary) },
          { label: t('hr.bonus'), align: 'end', render: (i) => fmtMoney(i.bonus) }, { label: t('hr.deduction'), align: 'end', render: (i) => fmtMoney(i.deduction) }, { label: t('hr.advance_deduction'), align: 'end', render: (i) => fmtMoney(i.advance_deduction) }, { label: t('hr.net'), align: 'end', render: (i) => html`<b>${fmtMoney(i.net)}</b>` }] }) });
      },
      cancel: async (el) => { if (await confirmDialog(t('doc.confirm_cancel'), { danger: true })) { await api.post(`/hr/payroll/${el.dataset.id}/cancel`); toast(t('doc.cancelled'), 'ok'); await load(); } },
    },
  });
  await load();
}

export const hr = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.hr')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'hr', [
      { key: 'employees', label: 'hr.employees', perm: 'hr.read', render: employees },
      { key: 'departments', label: 'hr.departments', perm: 'hr.read', render: resourceView({ name: 'departments', perm: 'hr', singular: 'f.department', columns: [{ key: 'name' }], fields: [{ name: 'name', required: true }] }) },
      { key: 'attendance', label: 'hr.attendance', perm: 'hr.read', render: attendanceTab },
      { key: 'leave', label: 'hr.leave', perm: 'hr.read', render: leaves },
      { key: 'advances', label: 'hr.advances', perm: 'hr.read', render: advancesTab },
      { key: 'payroll', label: 'hr.payroll', perm: 'hr.read', render: payrollTab },
    ]);
  },
};

// ====================================================================== CRM
const STAGES = ['prospecting', 'proposal', 'negotiation', 'won', 'lost'];
const oppCfg = { name: 'opportunities', perm: 'crm', singular: 'crm.opportunity', modalSize: 'lg', defaults: { stage: 'prospecting', probability: 10, value: 0 }, columns: [],
  fields: [{ name: 'title', required: true, full: true }, { name: 'partner_id', label: 'f.customer', ref: { resource: 'customers' } }, { name: 'contact_name' }, { name: 'value', type: 'money' },
    { name: 'expected_close', type: 'date' }, { name: 'stage', options: opts('stage', STAGES) }, { name: 'probability', type: 'number', attrs: { min: 0, max: 100 } }] };

async function pipelineTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = (await api.get('/opportunities', { all: 1 })).items;
    const by = (s) => d.filter((x) => x.stage === s);
    mount(root, html`<div class="toolbar"><span class="grow"></span>${can('crm.write') ? html`<button class="btn primary" data-action="add">${icon('plus')} ${t('common.add')}</button>` : ''}</div>
      <div class="board">${STAGES.map((s) => html`<div class="col"><h4><span>${t('stage.' + s)}</span><span class="muted">${by(s).length} · ${fmtMoney(by(s).reduce((a, x) => a + x.value, 0))}</span></h4>
        ${by(s).map((o) => html`<div class="deal" data-action="edit" data-id="${o.id}"><b>${o.title}</b><div class="muted">${o.partner_name || o.contact_name || ''}</div><div class="row" style="justify-content:space-between"><span>${fmtMoney(o.value)}</span><span class="chip">${o.probability}%</span></div></div>`)}</div>`)}</div>`);
    root._d = d;
  }
  bind(root, { click: { add: () => openResourceForm(oppCfg, null, load), edit: (el) => openResourceForm(oppCfg, root._d.find((x) => String(x.id) === el.dataset.id), load) } });
  await load();
}

const leads = resourceView({ name: 'leads', perm: 'crm', singular: 'crm.lead', defaults: { status: 'new' }, canEditRow: (r) => r.status !== 'converted',
  columns: [{ key: 'name' }, { key: 'phone' }, { key: 'email' }, { key: 'source' }, { key: 'status', chip: true }],
  fields: [{ name: 'name', required: true }, { name: 'phone' }, { name: 'email', type: 'email' }, { name: 'source' }, { name: 'status', options: opts('status', ['new', 'contacted', 'qualified', 'lost']) }, { name: 'notes', type: 'textarea', full: true }],
  filters: [{ name: 'status', options: opts('status', ['new', 'contacted', 'qualified', 'lost', 'converted']) }],
  rowActions: [{ key: 'convert', label: 'crm.convert', perm: 'crm.write', show: (r) => !['converted', 'lost'].includes(r.status),
    run: async (r, reload) => { if (await confirmDialog(t('crm.confirm_convert', { name: r.name }))) { await api.post(`/crm/leads/${r.id}/convert`); clearRefCache(); toast(t('crm.converted'), 'ok'); reload(); } } }] });

export const crm = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.crm')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'crm', [
      { key: 'leads', label: 'crm.leads', perm: 'crm.read', render: leads },
      { key: 'pipeline', label: 'crm.pipeline', perm: 'crm.read', render: pipelineTab },
      { key: 'activities', label: 'crm.activities', perm: 'crm.read', render: resourceView({ name: 'activities', perm: 'crm', singular: 'crm.activity', defaults: { type: 'call', date: today(), done: false },
        columns: [{ key: 'date' }, { key: 'type', label: 'f.type', render: (r) => t('act.' + r.type) }, { key: 'contact_name' }, { key: 'notes' }, { key: 'done', bool: true }],
        fields: [{ name: 'type', options: opts('act', ['call', 'meeting', 'email', 'visit', 'task']) }, { name: 'contact_name' }, { name: 'date', type: 'date', required: true }, { name: 'notes', type: 'textarea', full: true }, { name: 'done', type: 'checkbox' }],
        filters: [{ name: 'done', options: [{ value: 'false', i18n: 'crm.open' }, { value: 'true', i18n: 'crm.done' }] }] }) },
      { key: 'campaigns', label: 'crm.campaigns', perm: 'crm.read', render: resourceView({ name: 'campaigns', perm: 'crm', singular: 'crm.campaign', defaults: { budget: 0 },
        columns: [{ key: 'name' }, { key: 'channel' }, { key: 'start_date' }, { key: 'end_date' }, { key: 'budget', money: true }],
        fields: [{ name: 'name', required: true }, { name: 'channel' }, { name: 'start_date', type: 'date' }, { name: 'end_date', type: 'date' }, { name: 'budget', type: 'money' }, { name: 'notes', type: 'textarea', full: true }] }) },
    ]);
  },
};

// ====================================================================== projects
const PROJECT_STATUS = ['planned', 'active', 'done', 'cancelled'];
const projects = resourceView({ name: 'projects', perm: 'projects', singular: 'f.project', modalSize: 'lg', defaults: { status: 'planned', budget: 0 },
  columns: [{ key: 'name' }, { key: 'partner_name', label: 'f.customer' }, { key: 'status', chip: true }, { key: 'budget', money: true }, { key: 'actual_cost', money: true, label: 'proj.actual_cost' }, { key: 'hours', qty: true },
    { key: 'progress', label: 'proj.progress', render: (r) => html`<div class="progress" title="${r.tasks_done}/${r.tasks_total}"><i style="width:${r.tasks_total ? (r.tasks_done / r.tasks_total) * 100 : 0}%"></i></div>` }, { key: 'end_date' }],
  fields: [{ name: 'name', required: true, full: true }, { name: 'partner_id', label: 'f.customer', ref: { resource: 'customers' } }, { name: 'status', options: opts('status', PROJECT_STATUS) },
    { name: 'start_date', type: 'date' }, { name: 'end_date', type: 'date' }, { name: 'budget', type: 'money' }, { name: 'notes', type: 'textarea', full: true }],
  filters: [{ name: 'status', options: opts('status', PROJECT_STATUS) }],
  rowActions: [{ key: 'summary', label: 'proj.summary', run: async (r) => {
    const s = await api.get(`/projects/${r.id}/summary`);
    openModal({ title: `${t('proj.summary')} — ${s.project.name}`, size: 'md', body: html`<div class="kv"><div><small>${t('f.budget')}</small>${fmtMoney(s.project.budget)}</div><div><small>${t('proj.actual_cost')}</small>${fmtMoney(s.actual_cost)}</div>
      <div><small>${t('proj.remaining')}</small><b class="${s.budget_remaining < 0 ? 'bad-text' : 'ok-text'}">${fmtMoney(s.budget_remaining)}</b></div><div><small>${t('f.hours')}</small>${fmtNum(s.hours)}</div></div>
      ${table({ rows: s.by_employee, columns: [{ label: t('f.employee'), key: 'name' }, { label: t('f.hours'), align: 'end', render: (x) => fmtNum(x.hours) }, { label: t('f.cost'), align: 'end', render: (x) => fmtMoney(x.cost) }] })}` });
  } }] });

export const projectsPage = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.projects')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'projects', [
      { key: 'projects', label: 'nav.projects', perm: 'projects.read', render: projects },
      { key: 'tasks', label: 'proj.tasks', perm: 'projects.read', render: resourceView({ name: 'tasks', perm: 'projects', singular: 'proj.task', defaults: { status: 'todo', priority: 'medium' },
        columns: [{ key: 'title' }, { key: 'project_name', label: 'f.project' }, { key: 'assignee_name', label: 'proj.assignee' }, { key: 'due_date' }, { key: 'priority', chip: true }, { key: 'status', chip: true }],
        fields: [{ name: 'project_id', label: 'f.project', required: true, ref: { resource: 'projects' }, optional: false }, { name: 'title', required: true }, { name: 'assignee_id', label: 'proj.assignee', ref: { resource: 'employees' } },
          { name: 'due_date', type: 'date' }, { name: 'priority', options: opts('status', ['low', 'medium', 'high']) }, { name: 'status', options: opts('status', ['todo', 'doing', 'done']) }],
        filters: [{ name: 'project_id', ref: { resource: 'projects' } }, { name: 'status', options: opts('status', ['todo', 'doing', 'done']) }] }) },
      { key: 'timelogs', label: 'proj.timelogs', perm: 'projects.read', render: resourceView({ name: 'time_logs', perm: 'projects', singular: 'proj.timelog', defaults: { date: today(), hours: 1 },
        columns: [{ key: 'date' }, { key: 'employee_name', label: 'f.employee' }, { key: 'project_name', label: 'f.project' }, { key: 'task_title', label: 'proj.task' }, { key: 'hours', qty: true }],
        fields: [{ name: 'employee_id', label: 'f.employee', required: true, ref: { resource: 'employees' }, optional: false }, { name: 'project_id', label: 'f.project', required: true, ref: { resource: 'projects' }, optional: false },
          { name: 'task_id', label: 'proj.task', ref: { resource: 'tasks' } }, { name: 'hours', type: 'number', required: true }, { name: 'date', type: 'date', required: true }, { name: 'note' }] }) },
    ]);
  },
};
