// Import / export (CSV & Excel) with all-or-nothing validated imports.
import { api } from '../core/api.js';
import { can } from '../core/state.js';
import { errMsg, t } from '../core/i18n.js';
import { bind, html, icon, mount, notifyError, table, toast } from '../core/ui.js';
import { clearRefCache } from '../core/resource.js';

const EXPORTS = [['products', 'products.read'], ['customers', 'customers.read'], ['suppliers', 'suppliers.read'], ['invoices', 'sales.read'], ['bills', 'purchases.read'], ['journal', 'accounting.read'], ['employees', 'hr.read'], ['stock', 'inventory.read']];
const IMPORTS = [['products', 'products.write', 'sku, name, barcode, category, kind, unit, price, cost, tax_rate, min_stock, opening_qty, opening_cost'],
  ['customers', 'customers.write', 'name, phone, email, address, city, country, tax_number, credit_limit, payment_terms_days'],
  ['suppliers', 'suppliers.write', 'name, phone, email, address, city, country, tax_number, credit_limit, payment_terms_days']];

export default {
  render(root) {
    mount(root, html`<div class="page-head"><h1>${t('nav.data')}</h1></div>
      <div class="card"><div class="card-head"><h3>${icon('download')} ${t('data.export')}</h3></div><div class="card-pad"><p class="muted">${t('data.export_hint')}</p>
        <div class="grid g4">${EXPORTS.filter(([, p]) => can(p)).map(([n]) => html`<div class="card card-pad"><b>${t('data.ds.' + n)}</b><div class="row" style="margin-top:8px">
          <button class="btn sm" data-action="export" data-name="${n}" data-fmt="csv">CSV</button><button class="btn sm" data-action="export" data-name="${n}" data-fmt="xlsx">Excel</button></div></div>`)}</div></div></div>
      <div class="card" style="margin-top:14px"><div class="card-head"><h3>${icon('upload')} ${t('data.import')}</h3></div><div class="card-pad"><p class="muted">${t('data.import_hint')}</p>
        ${IMPORTS.filter(([, p]) => can(p)).map(([n, , cols]) => html`<div class="card card-pad" style="margin-bottom:10px" data-box="${n}"><b>${t('data.ds.' + n)}</b>
          <div class="muted" style="font-size:12px;margin:4px 0 8px">${t('data.columns')}: ${cols}</div>
          <div class="row"><input type="file" accept=".csv,.xlsx" data-file="${n}" style="max-width:320px"><button class="btn" data-action="import" data-name="${n}" data-dry="1">${t('data.validate')}</button>
            <button class="btn primary" data-action="import" data-name="${n}" data-dry="0">${t('data.do_import')}</button></div><div data-result="${n}" style="margin-top:10px"></div></div>`)}</div></div>`);
    bind(root, { click: {
      export: (el) => api.download(`/data/export/${el.dataset.name}`, { format: el.dataset.fmt }, `${el.dataset.name}.${el.dataset.fmt}`),
      import: async (el) => {
        const name = el.dataset.name, dry = el.dataset.dry === '1';
        const file = root.querySelector(`[data-file="${name}"]`).files[0];
        const out = root.querySelector(`[data-result="${name}"]`);
        if (!file) return toast(t('data.choose_file'), 'error');
        const form = new FormData(); form.append('file', file); form.append('dry_run', dry ? '1' : '0');
        try {
          const r = await api.upload(`/data/import/${name}`, form);
          clearRefCache();
          mount(out, html`<div class="chip chip-ok">${dry ? t('data.valid') : t('data.imported')}: ${t('data.result', { created: r.created, updated: r.updated })}</div>`);
          if (!dry) toast(t('data.imported'), 'ok');
        } catch (e) {
          const rows = Array.isArray(e.details) ? e.details : [];
          mount(out, html`<div class="form-error">${errMsg(e)}</div>${rows.length ? table({ rows, columns: [{ label: t('data.row'), key: 'row' }, { label: t('data.errors'), render: (r) => Object.entries(r.errors).map(([k, v]) => `${k}: ${t('v.' + v)}`).join(' · ') }] }) : ''}`);
        }
      },
    } });
  },
};
