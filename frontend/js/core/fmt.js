import { state } from './state.js';

const money = new Intl.NumberFormat('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const plain = (max) => new Intl.NumberFormat('en-US', { maximumFractionDigits: max });

export const fmtMoney = (v) => `${money.format(Number(v) || 0)} ${state.settings.currency || ''}`.trim();
export const fmtNum = (v, max = 3) => plain(max).format(Number(v) || 0);
export function today() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
export const monthStart = () => today().slice(0, 8) + '01';
export function addDays(iso, n) {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + n);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
export const shortDate = (iso) => (iso || '').slice(5);
/** Client-side preview of the server rounding (the server stays authoritative). */
export function lineAmounts(qty, price, discount, taxRate) {
  const net = Math.round((qty * price * (1 - (discount || 0) / 100) + 1e-9) * 100) / 100;
  const tax = Math.round((net * (taxRate || 0) / 100 + 1e-9) * 100) / 100;
  return { net, tax, total: Math.round((net + tax) * 100) / 100 };
}
