// api.js — the ONLY way the UI talks to the backend.
// * same-origin, cookie session, JSON only (the backend rejects non-JSON writes)
// * GET requests are de-duplicated while in flight and can be cancelled
// * errors become ApiError with a human-readable message (never a stack trace)
// * every request is counted: the shell shows a thin progress bar while any is in flight

import { emit } from './state.js';

export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

const inflight = new Map();
export const net = { active: 0 };

const FRIENDLY = {
  0: 'Cannot reach the Command Center backend. Check that it is running.',
  401: 'Your session has ended. Please sign in again.',
  403: "You don't have permission to do that.",
  404: 'Not available.',
  429: 'Too many requests — please wait a moment.',
  500: 'Something went wrong on the server.',
  502: 'The upstream Camview service returned an error.',
  503: 'Service not configured.',
  504: 'The upstream Camview service did not respond in time.',
};

function busy(delta) {
  net.active = Math.max(0, net.active + delta);
  emit('net', net.active);
}

async function request(method, path, { body, signal, raw } = {}) {
  let res;
  busy(1);
  try {
    try {
      res = await fetch(path, {
        method,
        credentials: 'same-origin',
        headers: method === 'GET' ? { Accept: 'application/json' } : { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: method === 'GET' ? undefined : JSON.stringify(body || {}),
        signal,
      });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      throw new ApiError(0, 'network', navigator.onLine === false ? 'You are offline.' : FRIENDLY[0]);
    }
    if (raw && res.ok) return res;
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) {
      if (res.status === 401 && !path.startsWith('/api/auth/')) emit('auth:expired');
      const msg = (data && data.message) || FRIENDLY[res.status] || `Request failed (HTTP ${res.status}).`;
      throw new ApiError(res.status, (data && data.error) || 'http_' + res.status, msg);
    }
    return data;
  } finally {
    busy(-1);
  }
}

const qs = (params) => {
  if (!params) return '';
  const s = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();
  return s ? '?' + s : '';
};

export function get(path, params, { signal } = {}) {
  const url = path + qs(params);
  if (!signal && inflight.has(url)) return inflight.get(url);
  const p = request('GET', url, { signal }).finally(() => { if (inflight.get(url) === p) inflight.delete(url); });
  if (!signal) inflight.set(url, p);
  return p;
}

export const post = (path, body) => request('POST', path, { body });
export const put = (path, body) => request('PUT', path, { body });
export const del = (path, body) => request('DELETE', path, { body });

export async function download(path, params, filename) {
  const res = await request('GET', path + qs(params), { raw: true });
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename || (res.headers.get('Content-Disposition') || '').match(/filename="([^"]+)"/)?.[1] || 'export.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
