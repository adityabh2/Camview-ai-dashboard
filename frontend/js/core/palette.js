// palette.js — Ctrl+K command palette: navigation actions + global search.
// Search results come from /api/search, which only returns what the user's
// scope allows. "Smart search": when the text reads as filters (e.g. "critical
// pending mobile phone today"), an item opens the Alerts queue with them.

import * as api from './api.js';
import { currentProject, isClient, session } from './state.js';
import * as nl from './nlsearch.js';
import { esc, icon, debounce } from './ui.js';
import { allowed, match } from './router.js';

const ACTIONS = [
  ['Go to Dashboard', '#/dashboard', 'grid'], ['Review pending alerts', '#/alerts?status=pending', 'alert'],
  ['Open Tickets', '#/tickets', 'report'], ['Tickets ready to send', '#/tickets?delivery=ready', 'share'],
  ['Open Exams', '#/exams', 'calendar'], ['Open Administration', '#/admin', 'settings'],
  ['Go to Centre Health Board', '#/monitor', 'live'],
  ['Go to Command Center', '#/command', 'command'], ['Go to Live Operations', '#/live', 'live'],
  ['Open My Work', '#/work', 'work'], ['Open Intelligent Alerts', '#/insights', 'zap'],
  ['Open Investigations', '#/investigations', 'investigate'], ['Open Client Sharing', '#/sharing', 'share'],
  ['Critical alarms', '#/live?quick=critical', 'alert'], ['Pending review', '#/live?quick=pending', 'clock'],
  ['Ready for client', '#/sharing?tab=candidates', 'share'], ['Open Cameras', '#/cameras', 'camera'],
  ['Open Nomenclature', '#/context', 'tree'], ['Open Analytics', '#/analytics', 'chart'],
  ['Open Alarm History', '#/history', 'history'], ['Open Reports', '#/reports', 'report'],
  ['Open Evidence', '#/evidence', 'image'], ['Open Shift Control', '#/shift', 'shift'],
  ['Presentation Mode', '#/presentation', 'present'], ['Open Settings', '#/settings', 'settings'],
  ['Open Audit Trail', '#/audit', 'audit'], ['Manage Users', '#/users', 'users'], ['Roles & Permissions', '#/roles', 'key'],
];
const GROUP_LABELS = { alarms: 'Alarms', tickets: 'Tickets', projects: 'Projects', tc: 'TC', centres: 'Centres',
  cameras: 'Cameras', investigations: 'Investigations' };

let open = false;

export function openPalette() {
  if (open || isClient()) return;
  open = true;
  const el = document.createElement('div');
  el.className = 'palette';
  el.innerHTML = `<div class="box" role="dialog" aria-modal="true" aria-label="Search and commands">
    <input placeholder="Search alarm ID, camera, TC, centre, ticket… or type a command" aria-label="Search" autocomplete="off">
    <div class="res" role="listbox"></div></div>`;
  document.body.appendChild(el);
  const input = el.querySelector('input');
  const res = el.querySelector('.res');
  let items = [];
  let idx = 0;

  const actions = ACTIONS.filter(([, href]) => {
    const m = match(href.split('?')[0].slice(1));
    return m && allowed(m.r);
  });

  function paint(groups) {
    items = [];
    let html = '';
    for (const [g, list] of groups) {
      if (!list.length) continue;
      html += `<div class="grp">${esc(g)}</div>`;
      for (const it of list) {
        items.push(it);
        html += `<a class="it" role="option" href="${esc(it.link)}" data-i="${items.length - 1}">${icon(it.icon || 'arrow', 's')}<span>${esc(it.label)}</span><span class="s">${esc(it.sub || '')}</span></a>`;
      }
    }
    res.innerHTML = html || '<div class="empty"><div class="e-t">No matches</div></div>';
    idx = 0;
    hl();
  }
  function hl() {
    res.querySelectorAll('.it').forEach((a, i) => a.classList.toggle('on', i === idx));
    res.querySelector('.it.on')?.scrollIntoView({ block: 'nearest' });
  }
  const local = (q) => actions.filter(([l]) => !q || l.toLowerCase().includes(q.toLowerCase()))
    .slice(0, q ? 6 : 10).map(([label, link, ic]) => ({ label, link, icon: ic }));

  const alertsRoute = match('/alerts');
  const smartOn = session.features?.ENABLE_SMART_SEARCH !== false && alertsRoute && allowed(alertsRoute.r);
  const smart = async (q) => {
    if (!smartOn || q.trim().length < 3) return [];
    try {
      const r = nl.parse(q, await nl.loadVocabulary());
      if (!r.recognised) return [];
      const out = [{ label: `Search alerts: ${nl.summary(r.chips)}`, sub: 'Open in Alerts', link: nl.toAlertsHref(r.filters), icon: 'search' }];
      const sr = match('/search');
      if (sr && allowed(sr.r)) out.push({ label: 'Refine in Smart search', sub: 'See the filters and a preview', link: `#/search?q=${encodeURIComponent(q.trim())}`, icon: 'filter' });
      return out;
    } catch { return []; }            // smart search is best-effort
  };

  const search = debounce(async (q) => {
    const groups = [['Commands', local(q)]];
    const sm = await smart(q);
    if (sm.length) groups.splice(groups[0][1].length ? 1 : 0, 0, ['Smart search', sm]);   // a matching command keeps Enter
    if (q.trim().length >= 2) {
      try {
        const r = await api.get('/api/search', { q: q.trim(), projectId: currentProject() });
        for (const [k, list] of Object.entries(r.groups || {})) {
          groups.push([GROUP_LABELS[k] || k, list.map((x) => ({ label: x.label, sub: x.sub, link: x.link, icon: k === 'cameras' ? 'camera' : k === 'alarms' || k === 'tickets' ? 'investigate' : 'tree' }))]);
        }
      } catch { /* search is best-effort */ }
    }
    if (open) paint(groups);
  }, 180);

  const close = () => { open = false; el.remove(); document.removeEventListener('keydown', key, true); };
  const key = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); idx = Math.min(items.length - 1, idx + 1); hl(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); idx = Math.max(0, idx - 1); hl(); }
    else if (e.key === 'Enter' && items[idx]) { e.preventDefault(); location.hash = items[idx].link.replace(/^#/, ''); close(); }
  };
  document.addEventListener('keydown', key, true);
  el.addEventListener('mousedown', (e) => { if (e.target === el) close(); });
  res.addEventListener('click', (e) => { if (e.target.closest('.it')) close(); });
  input.addEventListener('input', () => search(input.value));
  paint([['Commands', local('')]]);
  input.focus();
}

document.addEventListener('keydown', (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); openPalette(); }
});
