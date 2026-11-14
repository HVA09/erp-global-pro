// Point of sale: barcode scanner friendly, one-click checkout that posts invoice + payment atomically.
import { api } from '../core/api.js';
import { state } from '../core/state.js';
import { errMsg, t } from '../core/i18n.js';
import { fmtMoney, fmtNum, lineAmounts } from '../core/fmt.js';
import { attrs, bind, debounce, esc, html, icon, mount, notifyError, openModal, printHtml, raw, toast } from '../core/ui.js';

export default {
  async render(root) {
    const [prods, custs, accts] = await Promise.all([
      api.get('/products', { all: 1, is_active: 'true' }),
      api.get('/customers', { all: 1, is_active: 'true' }).catch(() => ({ items: [] })),
      api.get('/payment-accounts')]);
    const S = { products: prods.items, q: '', cart: [], customer: '', method: 'cash', account: '', received: '', discount: 0, later: false, busy: false };
    const accounts = accts.items;
    const defaultAccount = () => (accounts.find((a) => a.subtype === (S.method === 'cash' ? 'cash' : 'bank')) || accounts[0] || {}).id || '';
    S.account = defaultAccount();
    const sellable = (p) => p.kind === 'service' || p.stock_qty > 0 || state.settings.allow_negative_stock;

    mount(root, html`
      <div class="page-head"><h1>${t('nav.pos')}</h1></div>
      <div class="pos"><div>
        <div class="toolbar"><input class="search" type="search" placeholder="${t('pos.search')}" data-input="search" autofocus>
          <input type="text" placeholder="${t('pos.barcode')}" data-keydown="barcode" style="max-width:220px"></div>
        <div class="pos-products" id="grid"></div></div>
        <div class="card cart" id="cart"></div></div>`);

    const grid = root.querySelector('#grid'), cart = root.querySelector('#cart');
    const totals = () => {
      let sub = 0, tax = 0;
      for (const l of S.cart) { const a = lineAmounts(l.qty, l.price, S.discount, l.tax_rate); sub += a.net; tax += a.tax; }
      return { sub, tax, total: Math.round((sub + tax) * 100) / 100 };
    };
    function drawGrid() {
      const q = S.q.toLowerCase();
      const list = S.products.filter((p) => !q || p.name.toLowerCase().includes(q) || p.sku.toLowerCase().includes(q) || (p.barcode || '').includes(q)).slice(0, 120);
      mount(grid, list.length ? html`${list.map((p) => html`<button class="pos-item" data-action="add" data-id="${p.id}"${attrs({ disabled: !sellable(p) })}>
        <b>${p.name}</b><span class="muted">${p.sku}</span><span class="price">${fmtMoney(p.price)}</span>
        <small class="muted">${p.kind === 'service' ? t('kind.service') : t('f.stock') + ': ' + fmtNum(p.stock_qty)}</small></button>`)}` : html`<div class="empty">${t('common.empty')}</div>`);
    }
    function updateTotals() {
      const x = totals();
      const set = (id, v) => { const el = cart.querySelector(id); if (el) el.textContent = v; };
      set('#c-sub', fmtMoney(x.sub)); set('#c-tax', fmtMoney(x.tax)); set('#c-total', fmtMoney(x.total));
      const rec = Number(S.received);
      set('#c-change', S.method === 'cash' && rec > 0 ? fmtMoney(Math.max(0, rec - x.total)) : '—');
      const btn = cart.querySelector('[data-action=checkout]'); if (btn) btn.disabled = !S.cart.length || S.busy;
    }
    function drawCart() {
      const selected = S.customer;
      mount(cart, html`
        <div class="card-head"><h3>${icon('cart')} ${t('pos.cart')}</h3><button class="btn sm" data-action="clear">${t('pos.clear')}</button></div>
        <div class="cart-lines">${S.cart.length ? S.cart.map((l, i) => html`<div class="cart-line"><div><b>${l.name}</b><div class="muted">${fmtMoney(l.price)}</div></div>
          <div class="qty"><button data-action="dec" data-i="${i}">−</button><b>${fmtNum(l.qty)}</b><button data-action="inc" data-i="${i}">+</button></div>
          <button class="icon-btn" data-action="rm" data-i="${i}">${icon('x')}</button></div>`) : html`<div class="empty">${t('pos.empty_cart')}</div>`}</div>
        <div class="card-pad grid">
          <label class="field"><span class="field-label">${t('f.customer')}</span><select data-change="customer"><option value="">${t('pos.walk_in')}</option>${custs.items.filter((c) => !c.is_system).map((c) => html`<option value="${c.id}"${attrs({ selected: String(c.id) === String(selected) })}>${c.name}</option>`)}</select></label>
          <div class="grid g2">
            <label class="field"><span class="field-label">${t('pos.discount')}</span><input type="number" min="0" max="100" step="any" value="${S.discount}" data-input="discount"></label>
            <label class="field"><span class="field-label">${t('f.method')}</span><select data-change="method">${['cash', 'card', 'transfer'].map((m) => html`<option value="${m}"${attrs({ selected: S.method === m })}>${t('method.' + m)}</option>`)}</select></label></div>
          <div class="grid g2"><label class="field"><span class="field-label">${t('f.account')}</span><select data-change="account">${accounts.map((a) => html`<option value="${a.id}"${attrs({ selected: String(a.id) === String(S.account) })}>${a.name}</option>`)}</select></label>
            <label class="field"><span class="field-label">${t('pos.received')}</span><input type="number" min="0" step="0.01" value="${S.received}" data-input="received"></label></div>
          ${S.customer ? html`<label class="check" style="padding-top:0"><input type="checkbox" data-change="later"${attrs({ checked: S.later })}> ${t('pos.pay_later')}</label>` : ''}
          <div class="totals" style="width:100%;margin:0"><div><span>${t('f.subtotal')}</span><b id="c-sub"></b></div><div><span>${t('f.tax')}</span><b id="c-tax"></b></div>
            <div><span>${t('pos.change')}</span><b id="c-change"></b></div><div class="grand"><span>${t('f.total')}</span><span class="big-total" id="c-total"></span></div></div>
          <button class="btn primary block" data-action="checkout" style="padding:12px;font-size:16px">${icon('check')} ${t('pos.checkout')}</button></div>`);
      updateTotals();
    }
    const addProduct = (p) => {
      if (!p || !sellable(p)) return;
      const line = S.cart.find((l) => l.id === p.id);
      const cap = p.kind === 'service' || state.settings.allow_negative_stock ? Infinity : p.stock_qty;
      if (line) { if (line.qty + 1 > cap) return toast(t('pos.no_more_stock'), 'error'); line.qty += 1; }
      else S.cart.push({ id: p.id, name: p.name, price: p.price, tax_rate: p.tax_rate ?? state.settings.tax, qty: 1, cap });
      drawCart();
    };

    async function checkout() {
      if (!S.cart.length || S.busy) return;
      S.busy = true; updateTotals();
      try {
        const doc = await api.post('/pos/checkout', {
          lines: S.cart.map((l) => ({ product_id: l.id, qty: l.qty, discount_pct: Number(S.discount) || 0 })),
          partner_id: S.customer || undefined, method: S.method, account_id: S.account || undefined, paid: S.later ? 0 : undefined,
        });
        const change = S.method === 'cash' && Number(S.received) > doc.total ? Number(S.received) - doc.total : 0;
        S.cart = []; S.received = ''; S.later = false;
        const fresh = await api.get('/products', { all: 1, is_active: 'true' });
        S.products = fresh.items; drawGrid(); drawCart();
        const m = openModal({ title: t('pos.sale_done'), size: 'sm', body: html`<div class="center" style="text-align:center"><div class="big-total">${fmtMoney(doc.total)}</div><p><b>${doc.number}</b></p>${change ? html`<p>${t('pos.change')}: <b>${fmtMoney(change)}</b></p>` : ''}</div>`,
          footer: html`<button class="btn" data-action="print">${icon('printer')} ${t('pos.print_receipt')}</button><button class="btn primary" data-modal-close>${t('pos.new_sale')}</button>` });
        bind(m.el, { click: { print: () => printReceipt(doc) } });
      } finally { S.busy = false; updateTotals(); }
    }

    bind(root, {
      click: {
        add: (el) => addProduct(S.products.find((p) => String(p.id) === el.dataset.id)),
        inc: (el) => { const l = S.cart[Number(el.dataset.i)]; if (l.qty + 1 > l.cap) return toast(t('pos.no_more_stock'), 'error'); l.qty += 1; drawCart(); },
        dec: (el) => { const i = Number(el.dataset.i); S.cart[i].qty -= 1; if (S.cart[i].qty <= 0) S.cart.splice(i, 1); drawCart(); },
        rm: (el) => { S.cart.splice(Number(el.dataset.i), 1); drawCart(); },
        clear: () => { S.cart = []; drawCart(); },
        checkout,
      },
      input: {
        search: debounce((el) => { S.q = el.value; drawGrid(); }, 120),
        discount: (el) => { S.discount = Math.min(100, Math.max(0, Number(el.value) || 0)); updateTotals(); },
        received: (el) => { S.received = el.value; updateTotals(); },
      },
      change: {
        customer: (el) => { S.customer = el.value; if (!el.value) S.later = false; drawCart(); },
        method: (el) => { S.method = el.value; S.account = defaultAccount(); drawCart(); },
        account: (el) => { S.account = el.value; },
        later: (el) => { S.later = el.checked; },
      },
      keydown: {
        barcode: (el, ev) => {
          if (ev.key !== 'Enter') return;
          const code = el.value.trim(); el.value = '';
          if (!code) return;
          const p = S.products.find((x) => x.barcode === code || x.sku.toLowerCase() === code.toLowerCase());
          p ? addProduct(p) : toast(t('pos.not_found', { code }), 'error');
        },
      },
    });
    drawGrid(); drawCart();
  },
};

async function printReceipt(doc) {
  const s = state.settings;
  let qr = '';
  try { qr = (await api.get(`/sales/invoices/${doc.id}/qr`)).svg; } catch { /* optional */ }
  const rows = doc.lines.map((l) => `<tr><td>${esc(l.product_name || l.description)}<br><small>${fmtNum(l.qty)} × ${fmtMoney(l.unit_price)}</small></td><td class="num">${fmtMoney(l.total)}</td></tr>`).join('');
  printHtml(raw(`<div class="receipt"><div class="center"><b>${esc(s.company)}</b><br>${esc(s.address)} ${esc(s.phone)}<br>${s.tax_number ? t('set.tax_number') + ': ' + esc(s.tax_number) : ''}</div><hr>
    <div>${esc(doc.number)} — ${esc(doc.date)}</div><table>${rows}</table><hr>
    <div class="totals" style="width:100%"><div><span>${t('f.subtotal')}</span><span>${fmtMoney(doc.subtotal)}</span></div><div><span>${t('f.tax')}</span><span>${fmtMoney(doc.tax)}</span></div>
    <div class="grand"><span>${t('f.total')}</span><span>${fmtMoney(doc.total)}</span></div></div>
    ${qr ? `<div class="center" style="margin-top:8px">${qr.replace('width="180"', 'width="110"').replace('height="180"', 'height="110"')}</div>` : ''}
    <div class="center" style="margin-top:8px">${t('pos.thanks')}</div></div>`), doc.number);
}
