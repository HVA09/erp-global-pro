// Shared application state + permission helper (mirrors the server rule: '*', 'module.*', '*.action', exact).
export const state = { user: null, settings: { currency: 'USD', tax: 15, company: '' }, companies: [], activeCompany: null };

export function can(perm) {
  const p = state.user?.permissions || [];
  if (p.includes('*') || p.includes(perm)) return true;
  const [module, action] = perm.split('.');
  return p.includes(`${module}.*`) || p.includes(`*.${action}`);
}
export const canAny = (...perms) => perms.some(can);
