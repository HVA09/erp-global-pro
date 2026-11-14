// Translations (EN/AR). Dictionary lives in dict.js as [key, english, arabic] triples.
import { TRIPLES } from './dict.js';

export const dict = { en: {}, ar: {} };
for (const [key, en, ar] of TRIPLES) { dict.en[key] = en; dict.ar[key] = ar; }

const stored = localStorage.getItem('erp_lang');
export let lang = stored || ((navigator.language || 'en').toLowerCase().startsWith('ar') ? 'ar' : 'en');

export function applyLang() {
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === 'ar' ? 'rtl' : 'ltr';
}
export function setLang(l) { lang = l; localStorage.setItem('erp_lang', l); applyLang(); }
export const isRTL = () => lang === 'ar';

export function t(key, params) {
  let s = dict[lang][key] ?? dict.en[key] ?? key;
  if (params) s = s.replace(/\{(\w+)\}/g, (_, k) => (params[k] ?? ''));
  return s;
}

/** Human message for an ApiError: translated by code (with params) when we have a translation. */
export function errMsg(e) {
  if (!e) return t('err.unknown');
  const key = 'err.' + e.code;
  if (dict.en[key]) {
    const p = { ...(e.params || {}) };
    for (const k of Object.keys(p)) if (typeof p[k] === 'number') p[k] = Number.isInteger(p[k]) ? p[k] : p[k].toFixed(2);
    return t(key, p);
  }
  return e.message || t('err.unknown');
}
