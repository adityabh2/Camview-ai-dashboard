// layout.js — application shell: role-aware navigation, top bar (project,
// freshness, search, notifications, theme, user), DEMO DATA flag.

import * as api from './api.js';
import { session, can, canAny, isClient, currentProject, setProject, projectInfo, projectsWithoutCode, on, pref, setPref } from './state.js';
import { status, restart, pollNow } from './live.js';
import { net } from './api.js';
import { icon, esc, fmt, dialog, toast, $, $$ } from './ui.js';
import { openPalette } from './palette.js';
import { allowed, match, parseHash } from './router.js';

export const NAV_INTERNAL = [
  { href: '#/dashboard', label: 'Dashboard', icon: 'grid', route: '/dashboard', main: true },
  { href: '#/alerts', label: 'Alerts', icon: 'alert', route: '/alerts', main: true },
  { href: '#/monitoring', label: 'Monitoring', icon: 'camera', route: '/monitoring', main: true },
  { href: '#/tickets', label: 'Tickets', icon: 'report', route: '/tickets', main: true },
  { href: '#/incidents', label: 'Incidents', icon: 'layers', route: '/incidents', main: true, flag: 'ENABLE_INCIDENTS' },
  { href: '#/map', label: 'Map', icon: 'map', route: '/map', main: true, flag: 'ENABLE_MAP' },
  { href: '#/assistant', label: 'AI Assistant', icon: 'cpu', route: '/assistant', main: true, flag: 'ENABLE_AI' },
  { href: '#/management', label: 'Management', icon: 'tree', route: '/management', main: true },
  { href: '#/reports', label: 'Reports', icon: 'chart', route: '/reports', main: true },
  { href: '#/admin', label: 'Administration', icon: 'settings', route: '/admin', main: true },
  // Everything below is reachable from Administration › Advanced tools (not in the sidebar).
  { group: 'Monitoring' },
  { href: '#/monitor', label: 'Centre Health Board', icon: 'live', route: '/monitor' },
  { href: '#/clients', label: 'Clients', icon: 'building', route: '/clients' },
  { href: '#/exams', label: 'Exams', icon: 'calendar', route: '/exams' },
  { group: 'Operations' },
  { href: '#/command', label: 'Command Center', icon: 'command', route: '/command' },
  { href: '#/live', label: 'Live Operations', icon: 'live', route: '/live' },
  { href: '#/work', label: 'My Work', icon: 'work', route: '/work', badge: 'work' },
  { href: '#/review', label: 'Review Queue', icon: 'check', route: '/review' },
  { href: '#/insights', label: 'Intelligent Alerts', icon: 'zap', route: '/insights' },
  { href: '#/investigations', label: 'Investigations', icon: 'investigate', route: '/investigations' },
  { href: '#/watchlist', label: 'Watchlist', icon: 'eye', route: '/watchlist' },
  { href: '#/shift', label: 'Shift Control', icon: 'shift', route: '/shift' },
  { href: '#/brief', label: 'Daily Brief', icon: 'calendar', route: '/brief' },
  { group: 'Client collaboration' },
  { href: '#/sharing', label: 'Client Sharing', icon: 'share', route: '/sharing' },
  { group: 'Intelligence' },
  { href: '#/cameras', label: 'Cameras', icon: 'camera', route: '/cameras' },
  { href: '#/context', label: 'Nomenclature', icon: 'tree', route: '/context' },
  { href: '#/data-quality', label: 'Data Quality', icon: 'shield', route: '/data-quality' },
  { href: '#/evidence', label: 'Evidence', icon: 'image', route: '/evidence' },
  { href: '#/analytics', label: 'Analytics', icon: 'chart', route: '/analytics' },
  { href: '#/history', label: 'Alarm History', icon: 'history', route: '/history' },
  { href: '#/replay', label: 'Activity Replay', icon: 'play', route: '/replay' },
  { group: 'Workspace' },
  { href: '#/notifications', label: 'Alert Inbox', icon: 'bell', route: '/notifications', badge: 'unread' },
  { href: '#/presentation', label: 'Presentation Mode', icon: 'present', route: '/presentation' },
  { href: '#/preferences', label: 'Preferences', icon: 'user', route: '/preferences' },
  { group: 'Administration' },
  { href: '#/rules', label: 'Alert Rules', icon: 'rules', route: '/rules' },
  { href: '#/users', label: 'Users', icon: 'users', route: '/users' },
  { href: '#/roles', label: 'Roles & Permissions', icon: 'key', route: '/roles' },
  { href: '#/audit', label: 'Audit Trail', icon: 'audit', route: '/audit' },
  { href: '#/settings', label: 'Settings', icon: 'settings', route: '/settings' },
  { group: 'V2 features' },
  { href: '#/v2/features', label: 'V2 features', icon: 'zap', route: '/v2/features' },
  { href: '#/incidents', label: 'Incidents', icon: 'layers', route: '/incidents', flag: 'ENABLE_INCIDENTS' },
  { href: '#/map', label: 'Operations Map', icon: 'map', route: '/map', flag: 'ENABLE_MAP' },
  { href: '#/assistant', label: 'AI Assistant', icon: 'cpu', route: '/assistant', flag: 'ENABLE_AI' },
  { href: '#/search', label: 'Smart Search', icon: 'search', route: '/search', flag: 'ENABLE_SMART_SEARCH' },
];

export const NAV_CLIENT = [
  { group: 'Client portal' },
  { href: '#/client', label: 'Client Command Center', icon: 'command', route: '/client' },
  { href: '#/client/alerts', label: 'Shared Alerts', icon: 'share', route: '/client/alerts' },
  { href: '#/client/evidence', label: 'Evidence', icon: 'image', route: '/client/evidence' },
  { href: '#/client/analytics', label: 'Analytics', icon: 'chart', route: '/client/analytics' },
  { href: '#/client/reports', label: 'Reports', icon: 'report', route: '/client/reports' },
  { href: '#/notifications', label: 'Alert Inbox', icon: 'bell', route: '/notifications', badge: 'unread' },
  { href: '#/presentation', label: 'Presentation Mode', icon: 'present', route: '/presentation' },
  { href: '#/client/profile', label: 'My Access', icon: 'user', route: '/client/profile' },
  { href: '#/preferences', label: 'Preferences', icon: 'settings', route: '/preferences' },
];

const link = (it) => `<a href="${it.href}" data-route="${it.route}" class="${it.v2 ? 'v2' : ''}">${icon(it.icon)}<span>${esc(it.label)}</span>${it.v2 ? '<span class="count info">V2</span>' : ''}${it.badge ? `<span class="count hidden" data-badge="${it.badge}"></span>` : ''}</a>`;

function navHtml() {
  if (isClient()) return groupsHtml(NAV_CLIENT);
  // Seven destinations. Every other screen is a tab of one of them or an Administration › Advanced tool.
  return NAV_INTERNAL.filter((it) => it.main && allowedItem(it)).map(link).join('');
}

function allowedItem(it) {
  if (it.flag && !session.features?.[it.flag]) return false;
  const m = match(it.route);
  return m && allowed(m.r);
}

function groupsHtml(items) {
  const out = [];
  let pendingGroup = null;
  for (const it of items) {
    if (it.group) { pendingGroup = it.group; continue; }
    if (!allowedItem(it)) continue;
    if (pendingGroup) { out.push(`<div class="nav-group">${esc(pendingGroup)}</div>`); pendingGroup = null; }
    out.push(link(it));
  }
  return out.join('');
}

// ------------------------------------------------------------------ organisation branding
// Name, subtitle and logo come from GET /api/branding (public, so the sign-in page can use it too).
// Defaults reproduce the stock CAMVIEW / Command Center look exactly.
const BRAND_DEFAULT = { name: 'CAMVIEW', subtitle: 'Command Center', hasLogo: false, logoVersion: null };
export const brand = { ...BRAND_DEFAULT };
let brandLoad = null;
let lastTitle = '';

// `b.src` (a data: URL) is only used by the Settings › Branding preview before the logo is saved.
export const brandLogoUrl = (b = brand) => b.src || (b.hasLogo && b.logoVersion ? `/api/branding/logo?v=${encodeURIComponent(b.logoVersion)}` : '');
const isStockBrand = () => brand.name === BRAND_DEFAULT.name && brand.subtitle === BRAND_DEFAULT.subtitle;
/** "CAMVIEW Command Center" when nothing is customised, otherwise the organisation name. */
export const brandTitle = () => (isStockBrand() ? 'CAMVIEW Command Center' : brand.name);

/** The logo <img> (or the default shield mark). `cls` sizes it: '' = sidebar, 'l' = large. */
export function brandMarkHtml(b = brand, cls = '') {
  const url = brandLogoUrl(b);
  return url ? `<img class="brand-logo ${cls}" src="${esc(url)}" alt="">`
    : `<div class="brand-mark ${cls ? 'brand-mark-' + cls : ''}">${icon('shield', cls === 'l' ? 'l' : '')}</div>`;
}

/** Inner HTML of a sidebar brand block — also used by the Settings › Branding live preview. */
export function brandInnerHtml(b = brand, { client = isClient() } = {}) {
  return `${brandMarkHtml(b)}<div class="brand-text"><div class="brand-name">${esc(b.name)}</div><div class="brand-sub">${esc(client ? 'Client Portal' : b.subtitle)}</div></div>`;
}

function setFavicon() {
  let l = document.querySelector('link[rel="icon"]');
  if (!l) { l = document.createElement('link'); l.rel = 'icon'; document.head.appendChild(l); }
  if (!l.dataset.default) l.dataset.default = l.getAttribute('href') || '';
  const url = brandLogoUrl();
  l.setAttribute('href', url || l.dataset.default);
  if (url) l.removeAttribute('type'); else if (l.dataset.default.startsWith('data:image/svg')) l.type = 'image/svg+xml';
}

/** Adopt new branding everywhere without a reload (sidebar, title, favicon, [data-brand-*] hooks). */
export function applyBranding(b) {
  Object.assign(brand, BRAND_DEFAULT, b || {});
  const box = document.getElementById('shell-brand');
  if (box) box.innerHTML = brandInnerHtml();
  document.querySelectorAll('[data-brand-mark]').forEach((n) => { n.innerHTML = brandMarkHtml(brand, n.dataset.brandMark || ''); });
  document.querySelectorAll('[data-brand-name]').forEach((n) => { n.textContent = brand.name; });
  document.querySelectorAll('[data-brand-sub]').forEach((n) => { n.textContent = brand.subtitle; });
  if (lastTitle) document.title = `${lastTitle} · ${brandTitle()}`;
  setFavicon();
}

/** Fetches /api/branding once per page load (force = refetch). Never throws: defaults stay on failure. */
export function loadBranding(force = false) {
  if (!brandLoad || force) {
    brandLoad = api.get('/api/branding').then((b) => { applyBranding(b); return brand; })
      .catch(() => { brandLoad = null; return brand; });
  }
  return brandLoad;
}

export function mountShell() {
  const u = session.user;
  const app = document.getElementById('app');
  app.innerHTML = `
  <div class="shell" id="shell">
    <aside class="sidebar" aria-label="Main navigation">
      <div class="brand" id="shell-brand">${brandInnerHtml()}</div>
      <nav class="nav" id="nav">${navHtml()}</nav>
      <div class="sidebar-foot">${isClient() ? esc(u.client?.name || '') : 'API key stays on the server'}<br><span class="muted">${esc(session.product.tagline || '')}</span></div>
    </aside>
    <div class="main">
      <header class="topbar">
        <button class="btn icon ghost menu-btn" id="menu-btn" aria-label="Open navigation">${icon('menu')}</button>
        <div class="title"><h1 id="page-title">CAMVIEW</h1><div class="crumbs" id="crumbs"></div></div>
        ${session.mode === 'demo' ? '<span class="demo-flag" title="All data is generated demo data — not real alarms">DEMO DATA</span>' : ''}
        <span class="spacer"></span>
        <span id="project-slot">${projectSelect()}</span>
        <button class="fresh" id="fresh" type="button" aria-label="Data freshness — click to check for new data now"><span class="dot" id="fresh-dot"></span><span class="txt" id="fresh-txt" role="status" aria-live="polite">Connecting…</span><span class="bolt hidden" id="fresh-push" title="Push updates connected">${icon('zap', 's')}</span></button>
        <div class="netbar" id="netbar" aria-hidden="true"></div>
        ${!isClient() ? `<button class="btn ghost hide-sm" id="search-btn" aria-label="Search (Ctrl+K)">${icon('search')}<span class="kbd">Ctrl K</span></button>` : ''}
        <div class="rel"><button class="btn icon ghost" id="bell" aria-label="Notifications">${icon('bell')}<span class="notif-dot hidden" id="bell-n"></span></button></div>
        <button class="btn icon ghost" id="theme" aria-label="Toggle theme">${icon(document.documentElement.dataset.theme === 'light' ? 'moon' : 'sun')}</button>
        <div class="rel"><button class="user-chip" id="user-btn" aria-haspopup="menu" aria-label="User menu"><span class="avatar">${esc(initials(u.name))}</span><span class="hide-sm" style="font-size:12px;font-weight:600">${esc(u.name)}</span></button></div>
      </header>
      <div id="shell-banner"></div>
      <main class="content" id="content" tabindex="-1"></main>
    </div>
  </div>`;
  loadBranding();                                   // organisation name / logo (repaints #shell-brand when it arrives)

  wireProjectSelect();
  $('#menu-btn').addEventListener('click', () => $('#shell').classList.toggle('nav-open'));
  $('#nav').addEventListener('click', (e) => {
    const t = e.target.closest('#adv-toggle');
    if (t) {
      const open = !pref('advancedNav', false);
      setPref('advancedNav', open);
      $('#adv-nav').classList.toggle('hidden', !open);
      t.setAttribute('aria-expanded', open);
      t.innerHTML = `${icon(open ? 'down' : 'right', 's')} Advanced`;
      return;
    }
    $('#shell').classList.remove('nav-open');
  });
  $('#search-btn')?.addEventListener('click', openPalette);
  $('#theme').addEventListener('click', toggleTheme);
  $('#bell').addEventListener('click', toggleNotifications);
  $('#user-btn').addEventListener('click', userMenu);
  $('#fresh').addEventListener('click', () => { pollNow(); toast('Checking for new data…'); });

  // thin progress line under the top bar while any request is in flight (shown only if it takes a moment)
  let netTimer = null;
  on('net', (n) => {
    const bar = $('#netbar');
    if (!bar) return;
    clearTimeout(netTimer);
    if (n > 0) netTimer = setTimeout(() => { if (net.active > 0) bar.classList.add('on'); }, 180);
    else bar.classList.remove('on');
  });
  // the queue's sticky column header sits right under the top bar, whatever its height
  const header = $('.topbar');
  const setTop = () => document.documentElement.style.setProperty('--topbar-h', `${header.offsetHeight}px`);
  setTop();
  if ('ResizeObserver' in window) new ResizeObserver(setTop).observe(header);

  on('status', paintStatus);
  setInterval(paintStatus, 1000);
  restart();
  paintCodeBanner();
}

// ---- project codes: Camview sends only the numeric project id. Any monitored project without its exam code
// gets one bar (administrators only) asking for it once; saved codes are shown on every screen from then on.
function paintCodeBanner() {
  const box = $('#shell-banner');
  if (!box) return;
  const missing = isClient() || !canAny('settings.manage', 'nomenclature.manage') ? [] : projectsWithoutCode();
  let dismissed = [];
  try { dismissed = JSON.parse(sessionStorage.getItem('camview:codebar') || '[]'); } catch { /* ignore */ }
  const show = missing.filter((p) => !dismissed.includes(p.externalId));
  if (!show.length) { box.innerHTML = ''; return; }
  let expanded = false;
  try { expanded = sessionStorage.getItem('camview:codebar-open') === '1'; } catch { /* ignore */ }
  box.innerHTML = `<div class="codebar ${expanded ? 'open' : ''}" role="region" aria-label="Project codes needed">${icon('tree')}
    <div class="grow"><div class="codebar-line"><b>${show.length === 1 ? `Project ${esc(show[0].externalId)} has` : `${show.length} projects have`} no code yet</b>
      <span class="muted">— Camview sends only the number; enter the exam's code once and it is shown everywhere.</span>
      <button class="btn sm" data-code-toggle>${expanded ? 'Hide' : 'Enter codes'}</button></div>
      <div class="codebar-rows">${show.map((p) => `<label class="codebar-row"><span class="mono">Project ${esc(p.externalId)}</span>
        <input class="input" data-code-for="${esc(p.externalId)}" placeholder="Project code" maxlength="160" autocomplete="off">
        <button class="btn sm primary" data-code-save="${esc(p.externalId)}">Save</button></label>`).join('')}</div></div>
    <button class="btn icon ghost" data-code-later aria-label="Remind me later">${icon('x')}</button></div>`;
  box.onclick = async (e) => {
    if (e.target.closest('[data-code-toggle]')) {
      const open = !box.querySelector('.codebar').classList.contains('open');
      try { sessionStorage.setItem('camview:codebar-open', open ? '1' : '0'); } catch { /* ignore */ }
      box.querySelector('.codebar').classList.toggle('open', open);
      e.target.closest('[data-code-toggle]').textContent = open ? 'Hide' : 'Enter codes';
      if (open) box.querySelector('[data-code-for]')?.focus();
      return;
    }
    const later = e.target.closest('[data-code-later]');
    if (later) {
      try { sessionStorage.setItem('camview:codebar', JSON.stringify([...dismissed, ...show.map((p) => p.externalId)])); } catch { /* ignore */ }
      box.innerHTML = '';
      return;
    }
    const b = e.target.closest('[data-code-save]');
    if (!b) return;
    const pid = b.dataset.codeSave;
    const code = box.querySelector(`[data-code-for="${CSS.escape(pid)}"]`).value.trim();
    if (!code) { toast('Enter the project code first', 'warning'); return; }
    b.disabled = true;
    try {
      await api.put(`/api/nomenclature/projects/${encodeURIComponent(pid)}`, { code });
      const s = await api.get('/api/auth/session');
      session.projects = s.projects || session.projects;
      toast(`Project ${pid} is now shown as ${code}`, 'success');
      paintCodeBanner();
      window.dispatchEvent(new HashChangeEvent('hashchange'));     // re-render the page with the code
    } catch (err) { b.disabled = false; toast(err.message, 'error'); }
  };
  box.onkeydown = (e) => { if (e.key === 'Enter' && e.target.matches('[data-code-for]')) box.querySelector(`[data-code-save="${CSS.escape(e.target.dataset.codeFor)}"]`)?.click(); };
}
on('session', () => paintCodeBanner());

function projectSelect() {
  return !isClient() && session.projects.length > 1 ? `<select class="select hide-sm" id="project-sel" aria-label="Project">${session.projects.map((p) => `<option value="${esc(p.externalId)}">${esc(p.code)}${p.name && p.name !== p.code ? ' · ' + esc(p.name) : ''}</option>`).join('')}</select>` : '';
}
function wireProjectSelect() {
  const sel = $('#project-sel');
  if (!sel) return;
  sel.value = currentProject() || '';
  sel.addEventListener('change', () => { setProject(sel.value); window.dispatchEvent(new HashChangeEvent('hashchange')); });
}

// Projects found running in Camview are monitored by the server on its own: pick them up without a new sign-in.
let projectsCheckedAt = Date.now();
on('data', async () => {
  if (!session.authenticated || isClient() || Date.now() - projectsCheckedAt < 60000) return;
  projectsCheckedAt = Date.now();
  try {
    const s = await api.get('/api/auth/session');
    const sig = (list) => (list || []).map((p) => `${p.externalId}=${p.code}`).join('|');
    if (!s.authenticated || sig(s.projects) === sig(session.projects)) return;
    const added = (s.projects || []).filter((p) => !session.projects.some((q) => q.externalId === p.externalId));
    session.projects = s.projects || [];
    const slot = $('#project-slot');
    if (slot) { slot.innerHTML = projectSelect(); wireProjectSelect(); }
    paintCodeBanner();
    if (added.length) toast(`Now monitoring ${added.map((p) => p.code).join(', ')} — found running in Camview`, 'success');
  } catch { /* next change tries again */ }
});

function initials(name) { return (name || '?').split(/\s+/).map((p) => p[0]).slice(0, 2).join('').toUpperCase(); }

export function setTitle(title, crumbs = '') {
  const t = $('#page-title');
  if (t) t.textContent = title;
  const c = $('#crumbs');
  if (c) c.innerHTML = crumbs;
  lastTitle = title;
  document.title = `${title} · ${brandTitle()}`;
}

// Screens of the review flow merge every project; the per-project selector only applies to the advanced tools.
const ALL_PROJECT_ROUTES = ['/dashboard', '/alerts', '/alerts/:id', '/tickets', '/exams', '/admin'];

export function setActive(route) {
  $('#project-sel')?.classList.toggle('hidden', !!route && ALL_PROJECT_ROUTES.includes(route.pattern));
  $$('#nav a').forEach((a) => {
    const r = a.dataset.route;
    const on_ = route && (route.path === r || (r !== '/client' && route.path.startsWith(r + '/')) || route.pattern === r);
    a.classList.toggle('active', !!on_);
    if (on_) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

function paintStatus() {
  const dot = $('#fresh-dot'), txt = $('#fresh-txt');
  if (!dot) return;
  let state = status.state;
  let text;
  if (isClient()) {
    text = state === 'disconnected' ? 'Disconnected' : 'Connected';
  } else {
    const since = status.lastSuccessAt ? Math.round((Date.now() - new Date(status.lastSuccessAt).getTime()) / 1000) : null;
    const age = since == null ? '' : since < 60 ? `${since}s ago` : fmt.rel(status.lastSuccessAt);
    if (state === 'live') {
      if (since != null && since > Math.max(90, (status.cacheSeconds || 0) * 2 + 30)) state = 'delayed';
      text = `${session.mode === 'demo' ? 'DEMO' : 'LIVE'} · Updated ${age}`;
      // the connection is live; whether Camview is still producing alerts is a separate fact, shown as such
      if (session.mode !== 'demo' && status.quietHours != null && status.quietHours >= 24) text += ` · NO NEW ALERTS for ${Math.floor(status.quietHours / 24)} days`;
    }
    if (state === 'delayed') text = `⚠ DATA DELAYED · Last successful update: ${age || '—'}`;
    if (state === 'disconnected') text = `✕ LIVE DATA DISCONNECTED${status.lastSuccessAt ? ' · last update ' + fmt.time(status.lastSuccessAt) : ''}`;
    if (state === 'connecting') text = 'Connecting…';
  }
  dot.className = 'dot ' + (state === 'live' ? 'live' : state === 'delayed' ? 'delayed' : state === 'disconnected' ? 'disconnected' : '');
  txt.textContent = text;
  const pill = $('#fresh');
  pill.classList.toggle('busy', !!status.polling);
  pill.classList.toggle('push', !!status.push);
  $('#fresh-push')?.classList.toggle('hidden', !status.push);
  pill.title = (status.lastError ? `REFRESH FAILED: ${status.lastError.message}. Showing last successful data. Click to retry.` : 'Click to check for new data now')
    + (status.push ? '\nPush updates connected: screens update the moment data changes.' : `\nChecking every ${Math.round(pref('refreshMs', 15000) / 1000)} s (push updates not connected).`)
    + (status.lastSuccessAt ? `\nLast successful update: ${fmt.time(status.lastSuccessAt)}` : '')
    + (status.lastDataAt ? `\nScreen data last re-read: ${fmt.time(new Date(status.lastDataAt).toISOString())}` : '')
    + (status.latestAlertAt ? `\nNewest alert Camview has for your projects: ${fmt.dt(status.latestAlertAt)}` : '')
    + (status.lastAttemptAt ? `\nLast attempted update: ${fmt.time(status.lastAttemptAt)}` : '');
  const n = $('#bell-n');
  if (n) { n.textContent = status.unread > 99 ? '99+' : status.unread; n.classList.toggle('hidden', !status.unread); }
  $$('[data-badge="unread"]').forEach((b) => { b.textContent = status.unread; b.classList.toggle('hidden', !status.unread); });
}

export function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem('camview:theme', next); } catch { /* ignore */ }
  const b = $('#theme');
  if (b) b.innerHTML = icon(next === 'light' ? 'moon' : 'sun');
  window.dispatchEvent(new CustomEvent('themechange'));
}

let popover = null;
async function toggleNotifications(e) {
  e.stopPropagation();
  if (popover) { popover.remove(); popover = null; return; }
  popover = document.createElement('div');
  popover.className = 'popover';
  popover.setAttribute('role', 'dialog');
  popover.innerHTML = '<div class="card-h"><h3>Notifications</h3><div class="actions"><button class="btn sm ghost" data-all>Mark all read</button><a class="btn sm" href="#/notifications">Open</a></div></div><div class="list" id="np-list"><div class="card-b">Loading…</div></div>';
  $('#bell').parentElement.appendChild(popover);
  const close = (ev) => { if (popover && !popover.contains(ev.target)) { popover.remove(); popover = null; document.removeEventListener('click', close); } };
  setTimeout(() => document.addEventListener('click', close), 0);
  popover.querySelector('[data-all]').addEventListener('click', async () => {
    const r = await api.post('/api/notifications/mark', { action: 'read', ids: 'all' });
    status.unread = r.unread; paintStatus(); popover?.remove(); popover = null;
  });
  try {
    const d = await api.get('/api/notifications');
    const list = popover?.querySelector('#np-list');
    if (!list) return;
    list.innerHTML = d.items.length ? d.items.slice(0, 12).map((n) => `<a class="li" href="${esc(n.link || '#/notifications')}" data-nid="${n.id}">
      <span class="b outline">${esc(n.category)}</span><div class="grow"><div class="t1">${n.read ? '' : '<span class="dot live" style="animation:none"></span>'}${esc(n.title)}</div><div class="t2">${esc(n.body || '')}</div><div class="t2">${fmt.rel(n.createdAt)}</div></div></a>`).join('')
      : '<div class="empty"><div class="e-t">No notifications</div><div>Notifications are created only from real events (shares, approvals, assignments, critical alarms).</div></div>';
    $$('[data-nid]', list).forEach((a) => a.addEventListener('click', () => { api.post('/api/notifications/mark', { action: 'read', ids: [a.dataset.nid] }); popover?.remove(); popover = null; }));
  } catch (err) {
    const list = popover?.querySelector('#np-list');
    if (list) list.innerHTML = `<div class="card-b">${esc(err.message)}</div>`;
  }
}

function userMenu() {
  const u = session.user;
  dialog({
    title: `${icon('user')} ${esc(u.name)}`,
    side: true,
    body: `<dl class="kv"><dt>Email</dt><dd>${esc(u.email)}</dd><dt>Role</dt><dd>${esc(u.roleName)}</dd><dt>Audience</dt><dd>${esc(u.audience)}</dd>
      ${u.client ? `<dt>Client</dt><dd>${esc(u.client.name)}</dd>` : ''}
      <dt>Scope</dt><dd>${u.audience === 'client' ? 'Projects: ' + esc(u.clientProjects.join(', ') || '—') : Object.entries(u.scopes).map(([k, v]) => `${esc(k)}: ${esc(v.join(', '))}`).join('<br>') || 'Global'}</dd>
      <dt>Data mode</dt><dd>${session.mode === 'demo' ? '<span class="demo-flag">DEMO DATA</span>' : 'Live Camview data'}</dd></dl>
      <div class="section-title">Preferences</div>
      <div class="field"><label>Refresh interval</label><select class="select" id="pref-refresh">
        ${[5000, 10000, 15000, 30000, 60000, 120000, 180000].map((ms) => `<option value="${ms}" ${pref('refreshMs', 15000) === ms ? 'selected' : ''}>Every ${ms < 60000 ? ms / 1000 + ' seconds' : ms / 60000 + ' min'}</option>`).join('')}</select>
        <div class="hint">${status.push ? 'Push updates are connected: screens refresh the moment data changes. This interval is only the fallback check.' : 'How often the screen checks for changes when push updates are not available.'} Live data itself is read from Camview every ${Math.round((status.cacheSeconds || 30))} s.</div></div>
      <div class="section-title">Change password</div>
      <div class="field"><label>Current password</label><input class="input" type="password" id="pw-cur" autocomplete="current-password"></div>
      <div class="field"><label>New password (min. ${session.passwordMin || 5} characters)</label><input class="input" type="password" id="pw-new" autocomplete="new-password"></div>
      <div class="row"><button class="btn" id="pw-save">Change password</button><span class="grow"></span><button class="btn danger" id="logout">${icon('logout')} Sign out</button></div>
      <div class="section-title">Keyboard</div><div class="muted">Ctrl+K search · ? shortcuts · Esc closes dialogs · Enter opens a focused row</div>`,
  });
  $('#pref-refresh').addEventListener('change', (e) => { setPref('refreshMs', +e.target.value); restart(); toast(`Refresh every ${+e.target.value / 1000}s`); });
  $('#pw-save').addEventListener('click', async () => {
    try { await api.post('/api/auth/password', { current: $('#pw-cur').value, new: $('#pw-new').value }); toast('Password changed', 'success'); $('#pw-cur').value = $('#pw-new').value = ''; }
    catch (e) { toast(e.message, 'error'); }
  });
  $('#logout').addEventListener('click', async () => { await api.post('/api/auth/logout'); location.hash = ''; location.reload(); });
}
