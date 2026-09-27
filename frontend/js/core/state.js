// state.js — session, current project, preferences and a tiny event bus.
// Permissions here only drive what the UI shows; the server enforces them.

const listeners = new Map();

export function on(event, fn) {
  if (!listeners.has(event)) listeners.set(event, new Set());
  listeners.get(event).add(fn);
  return () => listeners.get(event)?.delete(fn);
}

export function emit(event, payload) {
  (listeners.get(event) || []).forEach((fn) => {
    try { fn(payload); } catch (e) { console.error(e); }
  });
}

export const session = {
  authenticated: false,
  user: null,
  mode: 'demo',
  features: {},
  projects: [],
  product: {},
  apiConfigured: false,
};

export function setSession(data) {
  Object.assign(session, {
    authenticated: !!data.authenticated,
    user: data.user || null,
    mode: data.mode,
    features: data.features || {},
    projects: data.projects || [],
    product: data.product || {},
    apiConfigured: !!data.apiConfigured,
    demoUsers: data.demoUsers || [],
    demoPassword: data.demoPassword,
  });
  emit('session', session);
}

export const can = (perm) => !!session.user && session.user.permissions.includes(perm);
export const canAny = (...perms) => perms.some(can);
export const isClient = () => session.user?.audience === 'client';

// ---- preferences (per-viewer conveniences only; never security state) ----
function key(k) { return `camview:${session.user?.id || 'anon'}:${k}`; }
export function pref(k, fallback) {
  try { const v = localStorage.getItem(key(k)); return v === null ? fallback : JSON.parse(v); } catch { return fallback; }
}
export function setPref(k, v) {
  try { localStorage.setItem(key(k), JSON.stringify(v)); } catch { /* private mode */ }
}

export function currentProject() {
  const saved = pref('project', null);
  const ids = session.projects.map((p) => p.externalId);
  if (saved && ids.includes(String(saved))) return String(saved);
  return ids[0] || null;
}
export function setProject(id) {
  setPref('project', String(id));
  emit('project', String(id));
}
export function projectInfo(id = currentProject()) {
  return session.projects.find((p) => p.externalId === String(id)) || { code: id ? String(id) : '—', name: '' };
}

/** The exam's own project code (e.g. MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL) for a Camview project id.
 *  Camview sends only the number; the code is set once (config or Administration) and shown everywhere. */
export function projectCode(id) {
  if (id == null || id === '') return '—';
  const p = session.projects.find((x) => x.externalId === String(id));
  return p && p.code ? p.code : String(id);
}

/** Monitored projects that still show a bare number because no code was entered yet. */
export const projectsWithoutCode = () => session.projects.filter((p) => !p.code || p.code === p.externalId);
