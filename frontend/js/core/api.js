// Thin fetch wrapper: JWT header, JSON bodies, uniform ApiError with translatable code/params.
const KEY = 'erp_token';
export const auth = {
  get token() { return localStorage.getItem(KEY); },
  set(token) { localStorage.setItem(KEY, token); },
  clear() { localStorage.removeItem(KEY); },
};

export class ApiError extends Error {
  constructor(status, err) {
    super(err?.message || 'Request failed');
    this.status = status;
    this.code = err?.code || 'error';
    this.params = err?.params || {};
    this.details = err?.details || null;
  }
}

async function request(method, path, { params, body, form, raw } = {}) {
  let url = '/api' + path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    const s = qs.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  const headers = {};
  if (auth.token) headers.Authorization = 'Bearer ' + auth.token;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers['Content-Type'] = 'application/json'; payload = JSON.stringify(body); }
  let res;
  try { res = await fetch(url, { method, headers, body: payload }); }
  catch { throw new ApiError(0, { code: 'network', message: 'Network error' }); }
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    if (res.status === 401 && auth.token && !path.startsWith('/auth/login')) {
      auth.clear();
      window.dispatchEvent(new Event('erp:unauthorized'));
    }
    throw new ApiError(res.status, data?.error);
  }
  return raw ? res : res.json().catch(() => null);
}

export const api = {
  get: (path, params) => request('GET', path, { params }),
  post: (path, body) => request('POST', path, { body: body ?? {} }),
  put: (path, body) => request('PUT', path, { body: body ?? {} }),
  del: (path) => request('DELETE', path),
  upload: (path, form) => request('POST', path, { form }),
  async download(path, params, filename) {
    const res = await request('GET', path, { params, raw: true });
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement('a'), { href: url, download: filename });
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
  },
};
