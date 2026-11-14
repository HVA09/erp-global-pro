// Config-driven CRUD screens: one declarative config -> searchable list + add/edit modal + delete.
import { api } from './api.js';
import { can } from './state.js';
import { errMsg, t } from './i18n.js';
import { fmtMoney, fmtNum } from './fmt.js';
import { attrs, bind, confirmDialog, debounce, field, html, icon, mount, notifyError, openModal, pager,
  raw, readForm, statusChip, submitForm, table, toast } from './ui.js';

const refCache = new Map();
export async function loadRef(ref) {
  const key = ref.resource + JSON.stringify(ref.params || {});
  if (!refCache.has(key)) {
    refCache.set(key, api.get('/' + ref.resource, { all: 1, ...ref.params }).then((r) => r.items).catch(() => []));
  }
  return refCache.get(key);
}
export const clearRefCache = () => refCache.clear();
export const refOptions = (items, label = 'name', optional = true) =>
  [...(optional ? [{ value: '', label: '—' }] : []), ...items.map((i) => ({ value: i.id, label: typeof label === 'function' ? label(i) : i[label] }))];

const colLabel = (c) => c.label ? t(c.label) : t('f.' + c.key);

function cell(c, row) {
  const v = row[c.key];
  if (c.render) return c.render(row);
  if (c.money) return fmtMoney(v);
  if (c.qty) return fmtNum(v);
  if (c.chip) return statusChip(v);
  if (c.bool) return v ? raw('✓') : raw('—');
  if (c.pct) return `${fmtNum(v, 1)}%`;
  return v ?? '';
}

/** Opens the add/edit dialog for a resource config. Resolves true when saved. */
export async function openResourceForm(cfg, row, onSaved) {
  const edit = !!row;
  const values = edit ? (cfg.toForm ? cfg.toForm(row) : row) : { ...(cfg.defaults || {}) };
  const specs = (typeof cfg.fields === 'function' ? cfg.fields(row) : cfg.fields).filter((f) => !(edit ? f.createOnly : f.editOnly));
  const parts = [];
  for (const f of specs) {
    let options = f.options;
    if (f.ref) options = refOptions(await loadRef(f.ref), f.ref.label || 'name', f.optional !== false);
    if (typeof options === 'function') options = options();
    const type = f.type || (options ? 'select' : 'text');
    parts.push(field({
      name: f.name, label: t(f.label || 'f.' + f.name), type, value: values[f.name] ?? f.default ?? '', options: options?.map((o) => ({ value: o.value, label: o.i18n ? t(o.i18n) : o.label })),
      required: f.required, hint: f.hint ? t(f.hint) : '', cls: f.full ? 'full' : '', attrs: f.attrs,
    }));
  }
  const id = 'rf' + Math.random().toString(36).slice(2, 7);
  const m = openModal({
    title: t(edit ? 'common.edit' : 'common.add') + ' — ' + t(cfg.singular || cfg.title || cfg.name), size: cfg.modalSize || 'md',
    body: html`<form id="${id}" class="grid g2" data-submit="save">${parts}</form>`,
    footer: html`<button type="button" class="btn" data-modal-close>${t('common.cancel')}</button>
      <button type="submit" form="${id}" class="btn primary">${t('common.save')}</button>`,
  });
  bind(m.el, {
    submit: {
      save: (form) => submitForm(form, async () => {
        let data = readForm(form);
        if (cfg.beforeSave) data = cfg.beforeSave(data, row) || data;
        const saved = edit ? await api.put(`/${cfg.name}/${row.id}`, data) : await api.post(`/${cfg.name}`, data);
        clearRefCache();
        m.close();
        toast(t('common.saved'), 'ok');
        onSaved?.(saved);
      }),
    },
  });
}

export function resourceView(cfg) {
  return async function render(container) {
    const canWrite = cfg.readonly ? false : can(cfg.perm + '.write');
    const S = { page: 1, q: '', filters: {}, data: null };
    const root = document.createElement('div');
    container.replaceChildren(root);

    async function filterBar() {
      const bars = [];
      for (const f of cfg.filters || []) {
        let options = f.options;
        if (f.ref) options = refOptions(await loadRef(f.ref), f.ref.label || 'name');
        else options = [{ value: '', label: t(f.label ? f.label : 'common.all') }, ...options.map((o) => ({ value: o.value, label: o.i18n ? t(o.i18n) : o.label }))];
        bars.push(html`<select data-change="filter" data-name="${f.name}" style="width:auto">${options.map((o) =>
          html`<option value="${o.value}"${attrs({ selected: String(S.filters[f.name] ?? '') === String(o.value) })}>${o.label}</option>`)}</select>`);
      }
      return bars;
    }

    async function load() {
      const params = { page: S.page, per_page: cfg.perPage || 25, q: S.q, sort: cfg.sort, ...S.filters, ...(cfg.params || {}) };
      S.data = await api.get('/' + cfg.name, params);
      draw();
    }

    function actionsCell(row) {
      const extra = (cfg.rowActions || []).filter((a) => (!a.show || a.show(row)) && (!a.perm || can(a.perm)));
      return html`${extra.map((a) => html`<button class="btn sm" data-action="row" data-key="${a.key}" data-id="${row.id}">${t(a.label)}</button>`)}
        ${canWrite && cfg.canEdit !== false && (!cfg.canEditRow || cfg.canEditRow(row)) ? html`<button class="icon-btn" title="${t('common.edit')}" data-action="edit" data-id="${row.id}">${icon('edit')}</button>` : ''}
        ${canWrite && cfg.canDelete !== false && (!cfg.canDeleteRow || cfg.canDeleteRow(row)) ? html`<button class="icon-btn" title="${t('common.delete')}" data-action="del" data-id="${row.id}">${icon('trash')}</button>` : ''}`;
    }

    async function draw() {
      const d = S.data;
      const cols = [...cfg.columns.map((c) => ({ label: colLabel(c), align: c.money || c.qty || c.num ? 'end' : c.align, cls: c.cls, render: (r) => cell(c, r) })),
        { label: '', cls: 'actions', render: actionsCell }];
      const showActions = canWrite || (cfg.rowActions || []).length;
      mount(root, html`
        ${cfg.header ? cfg.header(d) : ''}
        <div class="toolbar">
          ${cfg.search !== false ? html`<input class="search" type="search" placeholder="${t('common.search')}" value="${S.q}" data-input="search">` : ''}
          ${await filterBar()}
          <span class="grow"></span>
          ${cfg.toolbar ? cfg.toolbar() : ''}
          ${canWrite && cfg.canCreate !== false ? html`<button class="btn primary" data-action="add">${icon('plus')} ${t('common.add')}</button>` : ''}
        </div>
        <div class="card">${table({ columns: showActions ? cols : cols.slice(0, -1), rows: d.items, empty: t(cfg.empty || 'common.empty') })}${pager(d)}</div>`);
      const input = root.querySelector('input[type=search]');
      if (input && S.focusSearch) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
    }

    const find = (id) => S.data.items.find((r) => String(r.id) === String(id));
    bind(root, {
      click: {
        page: (el) => { S.page = Number(el.dataset.page); S.focusSearch = false; return load(); },
        add: () => openResourceForm(cfg, null, load),
        edit: (el) => openResourceForm(cfg, find(el.dataset.id), load),
        del: async (el) => {
          const row = find(el.dataset.id);
          if (!(await confirmDialog(t('common.confirm_delete'), { danger: true, okLabel: t('common.delete') }))) return;
          await api.del(`/${cfg.name}/${row.id}`);
          clearRefCache();
          toast(t('common.deleted'), 'ok');
          await load();
        },
        row: async (el) => {
          const action = cfg.rowActions.find((a) => a.key === el.dataset.key);
          await action.run(find(el.dataset.id), load);
        },
      },
      change: { filter: (el) => { S.filters[el.dataset.name] = el.value; S.page = 1; S.focusSearch = false; return load(); } },
      input: { search: debounce((el) => { S.q = el.value.trim(); S.page = 1; S.focusSearch = true; load().catch(notifyError); }, 300) },
    });
    try { await load(); } catch (e) { mount(root, html`<div class="card card-pad form-error">${errMsg(e)}</div>`); }
  };
}

/** Tab container: renders a header of links and delegates the body to the active tab. */
export function tabsPage(root, ctx, base, tabs) {
  const visible = tabs.filter((x) => !x.perm || can(x.perm));
  if (!visible.length) { mount(root, html`<div class="empty">${t('common.no_access')}</div>`); return; }
  const active = visible.find((x) => x.key === ctx.sub) || visible[0];
  mount(root, html`<div class="tabs">${visible.map((x) => html`<a class="tab ${x.key === active.key ? 'active' : ''}" href="#/${base}/${x.key}">${t(x.label)}</a>`)}</div><div id="tab-body"><div class="loading"><div class="spinner"></div></div></div>`);
  const body = root.querySelector('#tab-body');
  Promise.resolve(active.render(body, ctx)).catch((e) => { notifyError(e); mount(body, html`<div class="card card-pad form-error">${errMsg(e)}</div>`); });
}
