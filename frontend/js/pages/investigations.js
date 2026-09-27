// pages/investigations.js — MY INVESTIGATIONS: assigned, investigating, bookmarked alarms,
// watched cameras & contexts (spec §107) + compare launcher.

import * as api from '../core/api.js';
import { currentProject, projectInfo, projectCode } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, card, table, empty, errorBox, skeleton, priorityBadge, toast, delegate, $ } from '../core/ui.js';
import { columns, investigateHref } from '../components/alarms.js';

const TABS = [['mine', 'Assigned to me'], ['investigating', 'Investigating'], ['bookmarked', 'Bookmarked'], ['watched', 'Watched cameras & contexts']];
const CONTEXT_TYPES = ['camera', 'project', 'tc', 'centre', 'room'];

function watchHref(w) {
  const pid = encodeURIComponent(w.projectId || '');
  if (w.entityType === 'camera') return `#/cameras/${encodeURIComponent(w.entityId)}?projectId=${pid}`;
  if (String(w.entityId).includes(':')) return `#/context?node=${encodeURIComponent(w.entityId)}&projectId=${pid}`;
  if (['tc', 'centre'].includes(w.entityType)) return `#/live?${w.entityType}=${encodeURIComponent(w.entityId)}&projectId=${pid}`;
  return `#/context?projectId=${pid}`;
}

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Investigations', `${esc(projectInfo(pid).code)} · open investigations`);
    let tab = TABS.some((t) => t[0] === ctx.query.tab) ? ctx.query.tab : 'mine';

    el.innerHTML = `<div class="page-head"><div><h2>Investigations</h2><p>Alarms assigned to you, alarms under investigation, your bookmarks and the cameras / locations you watch.</p></div><a class="btn" href="#/watchlist">${icon('eye', 's')} Open Watchlist</a></div>
      <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab ${k === tab ? 'on' : ''}" role="tab" data-tab="${k}">${l}<span class="n" data-count="${k}">…</span></button>`).join('')}</div>
      <div class="grid g-side">
        <section class="card"><div id="inv-body" class="card-b flush">${skeleton(6, 28)}</div></section>
        ${card({ title: `${icon('compare')} Compare two alarms`, body: `
          <div class="field"><label for="cmp-a">Alarm A</label><input class="input" id="cmp-a" placeholder="ALM-…"></div>
          <div class="field"><label for="cmp-b">Alarm B</label><input class="input" id="cmp-b" placeholder="ALM-…"></div>
          <button class="btn primary" id="cmp-go">${icon('compare', 's')} Compare</button>
          <div class="muted" style="font-size:11.5px;margin-top:8px">Tip: IDs from any list can be pasted here. Only alarms inside your scope can be opened.</div>` })}
      </div>`;

    const counts = async () => {
      try {
        const [m, i, b, w] = await Promise.all([
          api.get('/api/alarms', { projectId: pid, quick: 'mine', size: 1 }),
          api.get('/api/alarms', { projectId: pid, workflowState: 'INVESTIGATING', size: 1 }),
          api.get('/api/bookmarks'),
          api.get('/api/watchlist').catch(() => ({ items: [] })),
        ]);
        if (ctx.isStale()) return;
        const set = (k, v) => { const n = el.querySelector(`[data-count="${k}"]`); if (n) n.textContent = v; };
        set('mine', m.totalElements); set('investigating', i.totalElements); set('bookmarked', b.items.length);
        set('watched', w.items.filter((x) => CONTEXT_TYPES.includes(x.entityType)).length);
      } catch { /* counts are best effort */ }
    };

    const load = async () => {
      const body = $('#inv-body', el);
      body.innerHTML = skeleton(6, 28);
      try {
        if (tab === 'watched') {
          const r = await api.get('/api/watchlist');
          if (ctx.isStale()) return;
          const rows = r.items.filter((x) => CONTEXT_TYPES.includes(x.entityType));
          table(body, {
            columns: [
              { label: 'Type', render: (x) => `<span class="b outline">${esc(x.entityType.toUpperCase())}</span>` },
              { label: 'Watched', render: (x) => `<div class="cell-2"><a class="mono" href="${watchHref(x)}">${esc(x.label || x.entityId)}</a><span class="l2 mono">${esc(projectCode(x.projectId))}</span></div>` },
              { label: 'Alarms', num: true, render: (x) => `<span class="num">${esc(x.alarms)}</span>` },
              { label: 'Pending', num: true, render: (x) => `<span class="num">${esc(x.pending)}</span>` },
              { label: 'Critical', num: true, render: (x) => `<span class="num">${esc(x.critical)}</span>` },
              { label: 'New since watched', num: true, render: (x) => `<span class="num">${esc(x.newSinceWatched)}</span>` },
              { label: 'Latest alarm', render: (x) => (x.latest ? `<div class="cell-2">${priorityBadge(x.latest.priority)}<span class="l2">${esc(x.latest.alarmTypeName)} · ${fmt.rel(x.latest.lastInstance)}</span></div>` : '<span class="muted">—</span>') },
              { label: 'Watching since', render: (x) => `<span class="dim">${fmt.rel(x.createdAt)}</span>` },
            ],
            rows, rowKey: (x) => `${x.entityType}:${x.entityId}`,
            onRow: (x) => { location.hash = watchHref(x); },
            emptyHtml: empty('Nothing watched yet', 'Use Watch on a camera or a context node (TC, centre, room) to follow it here and get notified of new alarms.', 'eye'),
          });
        } else if (tab === 'bookmarked') {
          const r = await api.get('/api/bookmarks');
          if (ctx.isStale()) return;
          table(body, {
            columns: [
              { label: 'Priority', render: (x) => priorityBadge(x.priority) },
              { label: 'Alarm', render: (x) => `<div class="cell-2"><a class="mono" href="#/investigations/${encodeURIComponent(x.alarmId)}?projectId=${encodeURIComponent(x.projectId)}">${esc(x.alarmId)}</a><span class="l2">${esc(x.alarmTypeName || '')}</span></div>` },
              { label: 'Camera', render: (x) => `<span class="mono">${esc(x.camera || '—')}</span>` },
              { label: 'Project', render: (x) => esc(x.projectId) },
              { label: 'Last seen', render: (x) => `<span class="num dim">${fmt.dt(x.lastInstance)}</span>` },
              { label: 'Bookmarked', render: (x) => `<span class="dim">${fmt.rel(x.createdAt)}</span>` },
            ],
            rows: r.items, rowKey: (x) => x.alarmId,
            onRow: (x) => { location.hash = `#/investigations/${encodeURIComponent(x.alarmId)}?projectId=${encodeURIComponent(x.projectId)}`; },
            emptyHtml: empty('No bookmarks', 'Use the Bookmark button in an investigation to keep it here.', 'star'),
          });
        } else {
          const params = tab === 'mine' ? { quick: 'mine' } : { workflowState: 'INVESTIGATING' };
          const r = await api.get('/api/alarms', { projectId: pid, size: 100, sort: 'priority', ...params });
          if (ctx.isStale()) return;
          table(body, {
            columns: columns(['priority', 'alarm', 'camera', 'context', 'state', 'workflow', 'visibility', 'assigned', 'last', 'evidence']),
            rows: r.items, onRow: (a) => { location.hash = investigateHref(a); },
            emptyHtml: empty(tab === 'mine' ? 'Nothing assigned to you' : 'No alarms under investigation', tab === 'mine' ? 'Supervisors can assign alarms from the investigation workspace.' : 'Alarms marked exception or assigned to someone appear here.', 'check'),
          });
        }
      } catch (e) {
        if (ctx.isStale()) return;
        body.innerHTML = errorBox(e);
        $('[data-retry]', body)?.addEventListener('click', load);
      }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => {
      tab = b.dataset.tab; ctx.setQuery({ tab });
      el.querySelectorAll('[data-tab]').forEach((x) => x.classList.toggle('on', x === b));
      load();
    }));
    $('#cmp-go', el).addEventListener('click', () => {
      const a = $('#cmp-a', el).value.trim(), b = $('#cmp-b', el).value.trim();
      if (!a || !b) return toast('Enter two alarm IDs', 'warning');
      location.hash = `#/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&projectId=${encodeURIComponent(pid || '')}`;
    });
    counts();
    await load();
  },
};
