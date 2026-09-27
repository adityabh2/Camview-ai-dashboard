// main.js — boot: theme, session, routes, shell.

import * as api from './core/api.js';
import { setSession, session, on, isClient } from './core/state.js';
import { route, start, configure, render } from './core/router.js';
import { mountShell, setActive } from './core/layout.js';
import { stop } from './core/live.js';

try {
  const t = localStorage.getItem('camview:theme');
  document.documentElement.dataset.theme = t || (matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark');
} catch { /* ignore */ }

const I = 'internal';
const C = 'client';
const p = (name) => () => import(`./pages/${name}.js`);

// ---------------------------------------------------------------- internal
// Main flow: Dashboard → Alerts → review (VALID / INVALID / EXCEPTION) → Tickets.
route('/dashboard', p('dashboard'), { audience: I, perms: ['live.view', 'alarm.view'], any: true });
route('/alerts', p('queue'), { audience: I, perms: ['live.view', 'alarm.view'], any: true });
route('/alerts/:id', p('alert'), { audience: I, perms: ['alarm.view'] });
route('/tickets', p('tickets'), { audience: I, perms: ['alarm.view'] });
route('/exams', p('exams'), { audience: I, perms: ['alarm.view', 'client.view'], any: true });
route('/admin', p('admin'), { audience: I, perms: [] });
route('/monitoring', p('monitoring'), { audience: I, perms: ['live.view', 'alarm.view', 'camera.view'], any: true });
route('/management', p('management'), { audience: I, perms: ['client.view', 'nomenclature.view', 'alarm.view'], any: true });
route('/health', p('health'), { audience: I, perms: ['alarm.view'] });
route('/monitor', p('monitor'), { audience: I, perms: ['live.view', 'alarm.view'], any: true });
route('/command', p('command'), { audience: I, perms: ['dashboard.view'] });
route('/live', p('live'), { audience: I, perms: ['live.view'] });
route('/work', p('work'), { audience: I, perms: ['work.view'] });
route('/insights', p('alerts'), { audience: I, perms: ['alert.view'] });
route('/investigations', p('investigations'), { audience: I, perms: ['alarm.investigate'] });
route('/investigations/:id', p('investigation'), { audience: I, perms: ['alarm.view'] });
route('/compare', p('compare'), { audience: I, perms: ['alarm.view'] });
route('/shift', p('shift'), { audience: I, perms: ['shift.view'] });
route('/sharing', p('sharing'), { audience: I, perms: ['alarm.approve', 'alarm.publish', 'alarm.validate', 'client.view'], any: true });
route('/clients', p('clients'), { audience: I, perms: ['client.view'] });
route('/cameras', p('cameras'), { audience: I, perms: ['camera.view'] });
route('/cameras/:id', p('camera'), { audience: I, perms: ['camera.view'] });
route('/context', p('context'), { audience: I, perms: ['nomenclature.view'] });
route('/evidence', p('evidence'), { audience: I, perms: ['evidence.view'] });
route('/analytics', p('analytics'), { audience: I, perms: ['analytics.view'] });
route('/history', p('history'), { audience: I, perms: ['history.view'] });
route('/reports', p('reports'), { audience: I, perms: ['report.view'] });
route('/reports/:id', p('report'), { audience: I, perms: ['report.view'] });
route('/rules', p('rules'), { audience: I, perms: ['alert.view'] });
route('/users', p('users'), { audience: I, perms: ['user.view'] });
route('/roles', p('roles'), { audience: I, perms: ['role.view'] });
route('/audit', p('audit'), { audience: I, perms: ['audit.view'] });
route('/settings', p('settings'), { audience: I, perms: ['settings.view'] });
route('/review', p('review'), { audience: I, perms: ['alarm.view'] });
route('/watchlist', p('watchlist'), { audience: I, perms: ['alarm.view'] });
route('/replay', p('replay'), { audience: I, perms: ['history.view'] });
route('/brief', p('brief'), { audience: I, perms: ['dashboard.view'] });
route('/data-quality', p('data-quality'), { audience: I, perms: ['nomenclature.view'] });
route('/v2/:feature', p('v2'), { audience: I, perms: [] });
// V2 features (each also has a feature flag, see session.features)
route('/incidents', p('incidents'), { audience: I, perms: ['alarm.view'] });
route('/incidents/:id', p('incident'), { audience: I, perms: ['alarm.view'] });
route('/map', p('map'), { audience: I, perms: ['alarm.view', 'camera.view'], any: true });
route('/assistant', p('assistant'), { audience: I, perms: ['alarm.view'] });
route('/search', p('search'), { audience: I, perms: ['alarm.view'] });
route('/preferences', p('preferences'), { perms: [] });

// ---------------------------------------------------------------- shared
route('/notifications', p('notifications'), { perms: ['notification.view'] });
route('/presentation', p('presentation'), { perms: ['presentation.view'], full: true });

// ---------------------------------------------------------------- client portal
route('/client', p('client/dashboard'), { audience: C, perms: ['client.portal'] });
route('/client/alerts', p('client/alerts'), { audience: C, perms: ['client.portal'] });
route('/client/alerts/:id', p('client/alert'), { audience: C, perms: ['client.portal'] });
route('/client/evidence', p('client/evidence'), { audience: C, perms: ['client.evidence'] });
route('/client/analytics', p('client/analytics'), { audience: C, perms: ['client.analytics'] });
route('/client/reports', p('client/reports'), { audience: C, perms: ['client.report.view'] });
route('/client/reports/:id', p('client/report'), { audience: C, perms: ['client.report.view'] });
route('/client/profile', p('client/profile'), { audience: C, perms: ['client.portal'] });

let shellMounted = false;

configure({
  beforeRender(r) {
    if (r?.full) {
      shellMounted = false;
      document.getElementById('app').innerHTML = '<div id="content"></div>';
      return;
    }
    if (!shellMounted) { mountShell(); shellMounted = true; }
    setActive(r);
    document.getElementById('shell')?.classList.remove('nav-open');
  },
  contentEl() { return document.getElementById('content'); },
});

async function showLogin() {
  stop();
  shellMounted = false;
  const mod = await import('./pages/login.js');
  mod.default.render(document.getElementById('app'), { onSuccess: boot });
}

async function boot() {
  let s;
  try {
    s = await api.get('/api/auth/session');
  } catch (e) {
    const app = document.getElementById('app');
    app.innerHTML = `<div class="error-state" style="min-height:100vh"><div class="e-t">Cannot reach the Command Center backend</div><div></div><button class="btn" id="boot-retry">Retry</button></div>`;
    app.querySelector('.error-state div:nth-child(2)').textContent = e.message;
    app.querySelector('#boot-retry').addEventListener('click', () => location.reload());
    return;
  }
  setSession(s);
  if (!s.authenticated) return showLogin();
  try { document.body.dataset.tz = JSON.parse(localStorage.getItem(`camview:${s.user.id}:tz`) || '"local"'); } catch { /* ignore */ }
  try { document.body.dataset.density = JSON.parse(localStorage.getItem(`camview:${s.user.id}:density`) || '"comfortable"'); } catch { /* ignore */ }
  const home = isClient() ? '#/client' : '#/dashboard';
  if (!location.hash || location.hash === '#' || location.hash === '#/') history.replaceState(null, '', home);
  if (!started) { started = true; start(); } else { shellMounted = false; render(); }
}
let started = false;

on('auth:expired', () => {
  if (session.authenticated) { session.authenticated = false; showLogin(); }
});

document.addEventListener('keydown', (e) => {
  if (e.key === '?' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
    import('./core/ui.js').then(({ dialog }) => dialog({
      title: 'Keyboard shortcuts',
      body: `<dl class="kv"><dt><span class="kbd">Ctrl K</span></dt><dd>Search & commands</dd><dt><span class="kbd">/</span></dt><dd>Focus the page filter</dd>
        <dt><span class="kbd">Enter</span></dt><dd>Open the focused row</dd><dt><span class="kbd">Esc</span></dt><dd>Close dialogs / viewers</dd>
        <dt><span class="kbd">← →</span></dt><dd>Previous / next evidence in the viewer</dd><dt><span class="kbd">+ − 0</span></dt><dd>Zoom in / out / reset in the viewer</dd>
        <dt><span class="kbd">F</span></dt><dd>Fullscreen evidence</dd></dl>`,
    }));
  }
  if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) {
    const f = document.querySelector('[data-page-search]');
    if (f) { e.preventDefault(); f.focus(); }
  }
});

boot();
