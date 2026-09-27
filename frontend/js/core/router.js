// router.js — hash router with per-route permission/audience checks.
// URL state: filters live in the hash query (#/live?quick=critical&sort=...),
// updated with replaceState so links are shareable and Back returns to the
// same filtered view.

import { session, can, isClient } from './state.js';
import { destroyAll } from './charts.js';
import { errorBox, esc, icon } from './ui.js';

const routes = [];
let current = null;
let cleanups = [];
let renderToken = 0;
let hooks = { beforeRender: () => {}, contentEl: () => document.getElementById('content') };

/**
 * route('/investigations/:id', () => import('../pages/investigation.js'),
 *       { perms: ['alarm.view'], any: false, audience: 'internal', title, full })
 */
export function route(pattern, load, opts = {}) {
  const keys = [];
  const rx = new RegExp('^' + pattern.replace(/\//g, '\\/').replace(/:(\w+)/g, (_, k) => { keys.push(k); return '([^/]+)'; }) + '$');
  routes.push({ pattern, rx, keys, load, ...opts });
}

export function configure(h) { hooks = { ...hooks, ...h }; }

export function parseHash() {
  const raw = location.hash.replace(/^#/, '') || '/';
  const [path, qs = ''] = raw.split('?');
  return { path: path || '/', query: Object.fromEntries(new URLSearchParams(qs)) };
}

export function navigate(to) {
  if (location.hash === to || location.hash === '#' + to.replace(/^#/, '')) render();
  else location.hash = to.startsWith('#') ? to : '#' + to;
}

export function setQuery(patch, { replace = true } = {}) {
  const { path, query } = parseHash();
  const next = { ...query, ...patch };
  Object.keys(next).forEach((k) => (next[k] === '' || next[k] == null || next[k] === false) && delete next[k]);
  const qs = new URLSearchParams(next).toString();
  const url = `#${path}${qs ? '?' + qs : ''}`;
  if (replace) history.replaceState(null, '', url);
  else history.pushState(null, '', url);
  return next;
}

export function allowed(r) {
  if (r.audience === 'internal' && isClient()) return false;
  if (r.audience === 'client' && !isClient()) return false;
  if (!r.perms || !r.perms.length) return true;
  return r.any ? r.perms.some(can) : r.perms.every(can);
}

export function match(path) {
  for (const r of routes) {
    const m = path.match(r.rx);
    if (m) return { r, params: Object.fromEntries(r.keys.map((k, i) => [k, decodeURIComponent(m[i + 1])])) };
  }
  return null;
}

export function currentRoute() { return current; }

export async function render() {
  const token = ++renderToken;
  cleanups.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
  cleanups = [];
  destroyAll();
  const { path, query } = parseHash();
  if (path === '/' || path === '') {
    location.replace(isClient() ? '#/client' : '#/dashboard');
    return;
  }
  const m = match(path);
  current = m ? { ...m.r, params: m.params, path, query } : null;
  hooks.beforeRender(current);
  let el = hooks.contentEl(current);
  if (!el) return;
  // A fresh container per page: no event listener can outlive the page that added it
  // (a stale click handler must never act on a previously viewed alert).
  const fresh = el.cloneNode(false);
  el.replaceWith(fresh);
  el = fresh;
  if (!m) {
    el.innerHTML = errorBox({ status: 404, message: 'This page does not exist.' }, { retry: false });
    return;
  }
  if (!allowed(m.r)) {
    // same message whether the page is hidden by role or by audience
    el.innerHTML = `<div class="error-state">${icon('lock')}<div class="e-t">Access denied</div><div>You don't have access to this page. If you think you should, ask an administrator.</div><a class="btn sm" href="${isClient() ? '#/client' : '#/dashboard'}">Go to start page</a></div>`;
    return;
  }
  el.innerHTML = '';
  try {
    const mod = await m.r.load();
    if (token !== renderToken) return;
    const ctx = {
      params: m.params, query, path,
      setQuery, navigate,
      onCleanup: (fn) => cleanups.push(fn),
      isStale: () => token !== renderToken,
    };
    await mod.default.render(el, ctx);
  } catch (e) {
    console.error(e);
    if (token === renderToken) el.innerHTML = errorBox(e) + `<p class="muted" style="text-align:center">${esc(path)}</p>`;
  }
}

export function start() {
  window.addEventListener('hashchange', render);
  window.addEventListener('popstate', render);
  render();
}
