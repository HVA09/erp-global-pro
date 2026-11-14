// Manufacturing (bills of materials, production orders), maintenance and the documents register.
import { api } from '../core/api.js';
import { can } from '../core/state.js';
import { t } from '../core/i18n.js';
import { fmtMoney, fmtNum, today } from '../core/fmt.js';
import { attrs, bind, confirmDialog, esc, field, html, icon, mount, openModal, raw, readForm, statusChip, submitForm, table, toast } from '../core/ui.js';
import { clearRefCache, loadRef, refOptions, resourceView, tabsPage } from '../core/resource.js';

// ====================================================================== manufacturing
async function bomForm(bom, done) {
  const prods = (await loadRef({ resource: 'products', params: { is_active: 'true' } })).filter((p) => p.kind !== 'service');
  const comps = bom ? bom.components.map((c) => ({ product_id: c.product_id, qty: c.qty })) : [{ product_id: '', qty: 1 }];
  const id = 'bm' + Date.now();
  const popts = refOptions(prods, (p) => `${p.sku} — ${p.name}`);
  const rows = () => html`${comps.map((c, i) => html`<div class="row" style="margin-bottom:6px"><select class="grow" data-change="comp" data-i="${i}">${popts.map((o) => html`<option value="${o.value}"${attrs({ selected: String(o.value) === String(c.product_id) })}>${o.label}</option>`)}</select>
    <input type="number" step="any" min="0" style="width:110px" value="${c.qty}" data-input="qty" data-i="${i}"><button type="button" class="icon-btn" data-action="rm" data-i="${i}">${icon('x')}</button></div>`)}`;
  const m = openModal({ title: t(bom ? 'common.edit' : 'common.add') + ' — ' + t('mfg.bom'), size: 'lg', body: html`<form id="${id}" data-submit="save">
    <div class="grid g2">${field({ name: 'name', label: t('f.name'), required: true, value: bom?.name || '' })}
      ${field({ name: 'product_id', label: t('mfg.finished'), type: 'select', required: true, value: bom?.product_id ?? '', options: refOptions(prods.filter((p) => p.kind === 'storable'), (p) => `${p.sku} — ${p.name}`) })}
      ${field({ name: 'labor_cost', label: t('mfg.labor'), type: 'money', value: bom?.labor_cost ?? 0 })}${field({ name: 'overhead_cost', label: t('mfg.overhead'), type: 'money', value: bom?.overhead_cost ?? 0 })}</div>
    <h4 style="margin:14px 0 8px">${t('mfg.components')} <span class="muted">(${t('mfg.per_unit')})</span></h4><div id="comps">${rows()}</div>
    <button type="button" class="btn sm" data-action="add">${icon('plus')} ${t('doc.add_line')}</button>
    <div style="margin-top:12px">${field({ name: 'is_active', label: t('f.is_active'), type: 'checkbox', value: bom ? bom.is_active : true })}</div></form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('common.save')}</button>` });
  const redraw = () => mount(m.el.querySelector('#comps'), rows());
  bind(m.el, {
    click: { add: () => { comps.push({ product_id: '', qty: 1 }); redraw(); }, rm: (el) => { if (comps.length > 1) { comps.splice(Number(el.dataset.i), 1); redraw(); } } },
    change: { comp: (el) => { comps[Number(el.dataset.i)].product_id = el.value; } }, input: { qty: (el) => { comps[Number(el.dataset.i)].qty = el.value; } },
    submit: { save: (form) => submitForm(form, async () => {
      const v = readForm(form);
      const body = { ...v, components: comps.filter((c) => c.product_id).map((c) => ({ product_id: Number(c.product_id), qty: Number(c.qty) })) };
      if (bom) await api.put(`/manufacturing/boms/${bom.id}`, body); else await api.post('/manufacturing/boms', body);
      m.close(); toast(t('common.saved'), 'ok'); done();
    }) },
  });
}

async function produceForm(bom, done) {
  const whs = await loadRef({ resource: 'warehouses', params: { is_active: 'true' } });
  const id = 'pr' + Date.now();
  const m = openModal({ title: `${t('mfg.produce')} — ${bom.product_name}`, size: 'sm', body: html`<form id="${id}" class="grid" data-submit="save">
    <p class="muted">${t('mfg.unit_cost')}: <b>${fmtMoney(bom.unit_cost)}</b></p>${field({ name: 'qty', label: t('f.qty'), type: 'number', required: true, value: 1, attrs: { min: '0.001' } })}
    ${field({ name: 'warehouse_id', label: t('f.warehouse'), type: 'select', required: true, options: refOptions(whs, 'name', false) })}${field({ name: 'date', label: t('f.date'), type: 'date', value: today() })}</form>`,
    footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('mfg.produce')}</button>` });
  bind(m.el, { submit: { save: (form) => submitForm(form, async () => { await api.post('/manufacturing/orders', { bom_id: bom.id, ...readForm(form) }); clearRefCache(); m.close(); toast(t('mfg.produced'), 'ok'); done(); }) } });
}

async function bomsTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  const write = can('manufacturing.write');
  async function load() {
    const d = await api.get('/manufacturing/boms');
    root._items = d.items;
    mount(root, html`<div class="toolbar"><span class="grow"></span>${write ? html`<button class="btn primary" data-action="add">${icon('plus')} ${t('common.add')}</button>` : ''}</div>
      <div class="card">${table({ rows: d.items, columns: [{ label: t('f.name'), key: 'name' }, { label: t('mfg.finished'), key: 'product_name' },
        { label: t('mfg.components'), render: (b) => b.components.map((c) => `${c.product_name} × ${fmtNum(c.qty)}`).join(', ') }, { label: t('mfg.material_cost'), align: 'end', render: (b) => fmtMoney(b.material_cost) },
        { label: t('mfg.unit_cost'), align: 'end', render: (b) => html`<b>${fmtMoney(b.unit_cost)}</b>` }, { label: t('f.is_active'), render: (b) => (b.is_active ? raw('✓') : raw('—')) },
        { label: '', cls: 'actions', render: (b) => (write ? html`<button class="btn sm ok" data-action="produce" data-id="${b.id}">${t('mfg.produce')}</button>
          <button class="icon-btn" data-action="edit" data-id="${b.id}">${icon('edit')}</button><button class="icon-btn" data-action="del" data-id="${b.id}">${icon('trash')}</button>` : '') }] })}</div>`);
  }
  const find = (id) => root._items.find((b) => String(b.id) === String(id));
  bind(root, { click: {
    add: () => bomForm(null, load), edit: (el) => bomForm(find(el.dataset.id), load), produce: (el) => produceForm(find(el.dataset.id), load),
    del: async (el) => { if (await confirmDialog(t('common.confirm_delete'), { danger: true })) { await api.del(`/manufacturing/boms/${el.dataset.id}`); toast(t('common.deleted'), 'ok'); await load(); } },
  } });
  await load();
}

async function ordersTab(container) {
  const root = document.createElement('div'); container.replaceChildren(root);
  async function load() {
    const d = await api.get('/manufacturing/orders', { per_page: 50 });
    mount(root, html`<div class="card">${table({ rows: d.items, columns: [{ label: t('f.number'), key: 'number' }, { label: t('f.date'), key: 'date' }, { label: t('f.product'), key: 'product_name' },
      { label: t('f.qty'), align: 'end', render: (o) => fmtNum(o.qty) }, { label: t('f.warehouse'), key: 'warehouse_name' }, { label: t('mfg.material_cost'), align: 'end', render: (o) => fmtMoney(o.material_cost) },
      { label: t('mfg.labor'), align: 'end', render: (o) => fmtMoney(o.labor_cost + o.overhead_cost) }, { label: t('f.total'), align: 'end', render: (o) => html`<b>${fmtMoney(o.total_cost)}</b>` },
      { label: t('f.status'), render: (o) => statusChip(o.status === 'done' ? 'done' : 'cancelled') },
      { label: '', cls: 'actions', render: (o) => (o.status === 'done' && can('manufacturing.write') ? html`<button class="btn sm danger" data-action="cancel" data-id="${o.id}">${t('common.cancel_it')}</button>` : '') }] })}</div>`);
  }
  bind(root, { click: { cancel: async (el) => { if (await confirmDialog(t('doc.confirm_cancel'), { danger: true })) { await api.post(`/manufacturing/orders/${el.dataset.id}/cancel`); clearRefCache(); toast(t('doc.cancelled'), 'ok'); await load(); } } } });
  await load();
}

export const manufacturing = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.manufacturing')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'manufacturing', [
      { key: 'boms', label: 'mfg.boms', perm: 'manufacturing.read', render: bomsTab }, { key: 'orders', label: 'mfg.orders', perm: 'manufacturing.read', render: ordersTab }]);
  },
};

// ====================================================================== maintenance
const PRIORITIES = ['low', 'medium', 'high', 'critical'];
const statusOpts = (list) => list.map((v) => ({ value: v, i18n: 'status.' + v }));
export const maintenance = {
  render(root, ctx) {
    mount(root, html`<div class="page-head"><h1>${t('nav.maintenance')}</h1></div><div id="inner"></div>`);
    tabsPage(root.querySelector('#inner'), ctx, 'maintenance', [
      { key: 'requests', label: 'mnt.requests', perm: 'maintenance.read', render: resourceView({ name: 'maintenance_requests', perm: 'maintenance', singular: 'mnt.request', defaults: { priority: 'medium', status: 'open', cost: 0, reported_date: today() },
        columns: [{ key: 'asset_name' }, { key: 'issue' }, { key: 'priority', chip: true }, { key: 'status', chip: true }, { key: 'reported_date' }, { key: 'cost', money: true }],
        fields: [{ name: 'asset_name', required: true }, { name: 'issue', required: true }, { name: 'priority', options: statusOpts(PRIORITIES) }, { name: 'status', options: statusOpts(['open', 'in_progress', 'resolved', 'closed']) },
          { name: 'reported_date', type: 'date' }, { name: 'resolved_date', type: 'date' }, { name: 'cost', type: 'money' }, { name: 'notes', type: 'textarea', full: true }],
        filters: [{ name: 'status', options: statusOpts(['open', 'in_progress', 'resolved', 'closed']) }, { name: 'priority', options: statusOpts(PRIORITIES) }] }) },
      { key: 'schedules', label: 'mnt.schedules', perm: 'maintenance.read', render: resourceView({ name: 'maintenance_schedules', perm: 'maintenance', singular: 'mnt.schedule', defaults: { frequency_days: 30 },
        columns: [{ key: 'asset_name' }, { key: 'task' }, { key: 'frequency_days', label: 'mnt.every_days' }, { key: 'last_done' }, { key: 'next_due' }, { key: 'overdue', label: 'f.status', render: (r) => (r.overdue ? statusChip('expired') : statusChip('ok')) }],
        fields: [{ name: 'asset_name', required: true }, { name: 'task', required: true }, { name: 'frequency_days', label: 'mnt.every_days', type: 'number', required: true }, { name: 'last_done', type: 'date' }],
        rowActions: [{ key: 'done', label: 'mnt.mark_done', perm: 'maintenance.write', run: async (r, reload) => { await api.post(`/maintenance/schedules/${r.id}/done`, {}); toast(t('common.saved'), 'ok'); reload(); } }] }) },
    ]);
  },
};

// ====================================================================== documents register + private attachments
const safeLink = (u) => (/^https?:\/\//i.test(u || '') ? html`<a href="${u}" target="_blank" rel="noopener noreferrer">${t('common.open')}</a>` : '');
const fileBytes = (value) => {
  const n = Number(value) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
};
const scanChip = (status) => {
  const tones = { clean: 'ok', pending: 'warn', infected: 'bad', error: 'bad' };
  return html`<span class="chip ${'chip-' + (tones[status] || '')}">${t('file.scan_' + (status || 'pending'))}</span>`;
};

export const files = {
  render(root) {
    mount(root, html`<div class="page-head"><h1>${t('nav.files')}</h1></div>
      <section id="private-files"></section><section style="margin-top:18px"><div class="page-head"><h2>${t('file.legacy_title')}</h2></div><div id="legacy-files"></div></section>`);
    const privateRoot = root.querySelector('#private-files');
    const legacyRoot = root.querySelector('#legacy-files');
    const canWrite = can('documents.write');
    const S = { offset: 0, limit: 100, items: [], total: 0, usage: null, loading: false };

    async function loadPrivate({ append = false } = {}) {
      if (S.loading) return;
      S.loading = true;
      try {
        const d = await api.get('/file-objects', { limit: S.limit, offset: append ? S.offset : 0 });
        S.total = Number(d.total || 0);
        S.usage = d.usage || null;
        S.items = append ? [...S.items, ...(d.items || [])] : (d.items || []);
        S.offset = S.items.length;
        drawPrivate();
      } finally { S.loading = false; }
    }

    function drawPrivate() {
      const usage = S.usage || { used_bytes: 0, quota_bytes: 0, file_count: 0 };
      const pct = usage.quota_bytes ? Math.min(100, Math.round(usage.used_bytes / usage.quota_bytes * 100)) : 0;
      mount(privateRoot, html`<div class="card">
        <div class="card-head"><h3>${t('file.private_title')}</h3><span class="muted">${fileBytes(usage.used_bytes)} / ${fileBytes(usage.quota_bytes)}</span>
          ${canWrite ? html`<button class="btn primary sm" data-action="upload-private">${icon('plus')} ${t('file.upload')}</button>` : ''}</div>
        <div class="card-pad">
          <div class="file-quota"><div class="file-quota-track"><span style="width:${pct}%"></span></div><small>${usage.file_count} ${t('file.count')} · ${pct}% ${t('file.used')}</small></div>
          <p class="field-hint">${t('file.scan_notice')}</p>
          ${S.items.length ? table({ rows: S.items, columns: [
            { label: t('file.title'), render: (r) => html`<b>${r.title}</b><div class="muted" style="font-size:12px">${r.original_name}</div>` },
            { label: t('file.category'), key: 'category' },
            { label: t('file.size'), align: 'end', render: (r) => fileBytes(r.size_bytes) },
            { label: t('file.security'), render: (r) => scanChip(r.scan_status) },
            { label: t('f.date'), key: 'created_at' },
            { label: '', cls: 'actions', render: (r) => html`${r.scan_status === 'clean' ? html`<button class="btn sm" data-action="download-private" data-id="${r.id}">${icon('download')} ${t('common.download')}</button>` : html`<button class="btn sm" disabled title="${t('file.scan_notice')}">${icon('lock')} ${t('file.unavailable')}</button>`}
              ${canWrite ? html`<button class="icon-btn" title="${t('common.delete')}" data-action="delete-private" data-id="${r.id}">${icon('trash')}</button>` : ''}` },
          ] }) : html`<div class="empty">${t('file.empty')}</div>`}
          ${S.items.length < S.total ? html`<div class="toolbar" style="justify-content:center;margin-top:12px"><button class="btn" data-action="load-more-private">${t('file.load_more')}</button></div>` : ''}
        </div></div>`);
    }

    function uploadModal() {
      const id = 'private-upload-' + Date.now();
      const m = openModal({ title: t('file.upload'), size: 'md', body: html`<form id="${id}" class="grid" data-submit="save-private-upload" enctype="multipart/form-data">
        ${field({ name: 'file', label: t('file.select'), type: 'file', required: true, attrs: { accept: '.pdf,.png,.jpg,.jpeg,.webp,.txt,.csv' } })}
        ${field({ name: 'title', label: t('file.title'), required: true, attrs: { maxlength: 200 } })}
        <div class="grid g2">${field({ name: 'category', label: t('file.category'), attrs: { maxlength: 100 } })}${field({ name: 'related_to', label: t('file.related'), attrs: { maxlength: 150 } })}</div>
        ${field({ name: 'note', label: t('file.note'), type: 'textarea', attrs: { maxlength: 1000 } })}
        <p class="field-hint">${t('file.upload_limits')}</p>
      </form>`, footer: html`<button class="btn" data-modal-close>${t('common.cancel')}</button><button class="btn primary" type="submit" form="${id}">${t('file.upload')}</button>` });
      bind(m.el, { submit: { 'save-private-upload': (form) => submitForm(form, async () => {
        const selected = form.elements.namedItem('file')?.files?.[0];
        if (!selected) throw new Error(t('file.choose_file'));
        if (selected.size > 10 * 1024 * 1024) throw new Error(t('file.upload_limits'));
        const body = new FormData();
        body.append('file', selected);
        for (const key of ['title', 'category', 'related_to', 'note']) body.append(key, form.elements.namedItem(key)?.value || '');
        await api.upload('/file-objects/upload', body);
        m.close();
        toast(t('file.uploaded_pending'), 'ok');
        await loadPrivate();
      }) } });
      const fileInput = m.el.querySelector('input[type=file]');
      const titleInput = m.el.querySelector('input[name=title]');
      fileInput?.addEventListener('change', () => { if (fileInput.files?.[0] && !titleInput.value) titleInput.value = fileInput.files[0].name; });
    }

    bind(privateRoot, { click: {
      'upload-private': uploadModal,
      'load-more-private': () => loadPrivate({ append: true }),
      'download-private': async (el) => {
        const item = S.items.find((r) => String(r.id) === String(el.dataset.id));
        if (!item || item.scan_status !== 'clean') return;
        await api.download(`/file-objects/${item.id}/download`, null, item.original_name);
      },
      'delete-private': async (el) => {
        if (!await confirmDialog(t('common.confirm_delete'), { danger: true })) return;
        await api.del(`/file-objects/${el.dataset.id}`);
        toast(t('common.deleted'), 'ok');
        await loadPrivate();
      },
    } });

    loadPrivate().catch((error) => mount(privateRoot, html`<div class="card card-pad"><div class="form-error">${error.message || t('app.server_error')}</div></div>`));
    return resourceView({ name: 'files', perm: 'documents', singular: 'nav.files', modalSize: 'lg', defaults: { date: today() },
      columns: [{ key: 'title' }, { key: 'category' }, { key: 'related_to' }, { key: 'date' }, { key: 'link', render: (r) => safeLink(r.link) }],
      fields: [{ name: 'title', required: true, full: true }, { name: 'category' }, { name: 'related_to' }, { name: 'link', full: true, hint: 'f.link_hint' }, { name: 'date', type: 'date' }, { name: 'note', type: 'textarea', full: true }] })(legacyRoot);
  },
};
