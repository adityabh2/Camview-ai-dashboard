// pages/live.js — LIVE OPERATIONS: "What is happening right now?"
// Controlled polling (no fake streaming). Pause keeps the current data and
// shows "New activity available" instead of jumping. All filters are in the URL.
// Views: Table · Cards · Grouped (activity groups — every raw alarm stays one click away).

import * as api from '../core/api.js';
import { can, currentProject, projectInfo, on, pref, setPref } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { refreshMs } from '../core/live.js';
import { esc, icon, fmt, table, pager, errorBox, skeleton, empty, toast, dialog, debounce, delegate, columnChooser,
  priorityBadge, slaBadge, $, $$ } from '../core/ui.js';
import { columns, alarmCard, investigateHref } from '../components/alarms.js';
import { bulkRequest } from '../components/sharing.js';

const QUICK = [['critical', 'Critical'], ['pending', 'Pending'], ['validated', 'Validated'], ['repeated', 'Repeated'],
  ['evidence', 'Evidence'], ['suppressed', 'Suppressed'], ['readyForClient', 'Ready for client'], ['approval', 'Awaiting approval'],
  ['shared', 'Shared'], ['new', 'New'], ['mine', 'Assigned to me']];
const ADV = ['tc', 'centre', 'camera', 'alarmType', 'priority', 'lastActionType', 'workflowState', 'visibility', 'shiftLabel', 'from', 'to'];
const TABLE_KEYS = ['thumb', 'priority', 'alarm', 'camera', 'context', 'shift', 'state', 'workflow', 'visibility', 'occurrences', 'pendingFor', 'last', 'evidence', 'assigned'];
const LOCKED = new Set(['priority', 'alarm']);

// "Pending for 2h 14m" — only for alarms that are still pending; SLA wording only when a target is configured.
const PENDING_COL = {
  key: 'pendingFor', label: 'Pending for', sort: 'ageMinutes',
  render: (a) => (a.flags?.pending && a.ageMinutes != null
    ? `<div class="cell-2"><span class="num">${fmt.dur(a.ageMinutes)}</span>${a.sla ? `<span>${slaBadge(a.sla)}</span>` : ''}</div>`
    : '<span class="muted">—</span>'),
};

function tableColumns() {
  return TABLE_KEYS.map((k) => (k === 'pendingFor' ? PENDING_COL : columns([k])[0]));
}

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Live Operations', `${esc(projectInfo(pid).code)} · what is happening right now`);
    let q = { sort: 'lastInstance', dir: 'desc', page: '1', size: String(pref('liveSize', 25)), ...ctx.query };
    let paused = false, pendingWhilePaused = false, data = null, groups = null;
    let view = ['table', 'cards', 'grouped'].includes(q.view) ? q.view : pref('liveView', 'table');
    let groupBy = q.by || pref('liveGroupBy', 'camera');
    let gap = q.gap || String(pref('liveGroupGap', 10));
    let gpage = 1;
    const expanded = new Set();
    const hidden = new Set(pref('liveHidden', []));
    let density = pref('density', 'comfortable');
    document.body.dataset.density = density;
    const selected = new Set();
    let ctrl = null;

    el.innerHTML = `
      <div class="page-head"><div><h2>Live Operations</h2><p id="lo-sub">Loading…</p></div>
        <div class="row">
          <div class="seg" role="group" aria-label="View">
            <button data-view="table" class="${view === 'table' ? 'on' : ''}">${icon('grid', 's')} Table</button>
            <button data-view="cards" class="${view === 'cards' ? 'on' : ''}">${icon('layers', 's')} Cards</button>
            <button data-view="grouped" class="${view === 'grouped' ? 'on' : ''}" title="Activity groups — related alarms collapsed, nothing hidden">${icon('tree', 's')} Grouped</button>
          </div>
          <button class="btn" id="lo-pause">${icon('pause', 's')} Pause live feed</button>
          ${can('alarm.export') ? `<button class="btn" id="lo-export">${icon('download', 's')} Export</button>` : ''}
          <button class="btn" id="lo-save">${icon('bookmark', 's')} Save view</button>
        </div></div>
      <div id="lo-new" class="banner info hidden" role="status">${icon('zap')}<div class="grow"><b>New activity available.</b> The live feed is paused so the list doesn't move under you.</div><button class="btn sm primary" id="lo-resume">${icon('play', 's')} Resume live feed</button></div>
      <div class="filters" id="lo-quick">${QUICK.map(([k, l]) => `<button class="chip" data-quick="${k}" aria-pressed="false">${l}</button>`).join('')}</div>
      <div class="filters">
        <input class="input" style="min-width:260px" placeholder="Search alarm, camera, ticket, TC/centre…  ( / )" id="lo-search" data-page-search value="${esc(q.search || '')}">
        <button class="btn" id="lo-adv">${icon('filter', 's')} Advanced filters <span class="b outline" id="lo-advn">0</span></button>
        <select class="select" id="lo-views" aria-label="Saved views"><option value="">Saved views…</option></select>
        <span class="grow"></span>
        <span id="lo-tabletools" class="row tight">
          <button class="btn" id="lo-cols" title="Choose columns">${icon('grid', 's')} Columns</button>
          <div class="seg" role="group" aria-label="Row density"><button data-density="comfortable" class="${density === 'comfortable' ? 'on' : ''}">Comfortable</button><button data-density="compact" class="${density === 'compact' ? 'on' : ''}">Compact</button></div>
        </span>
        <span id="lo-grouptools" class="row tight hidden">
          <label class="row tight dim" style="font-size:12px">Group by <select class="select" id="lo-by">${[['camera', 'Camera'], ['room', 'Room'], ['centre', 'Centre']].map(([v, l]) => `<option value="${v}" ${groupBy === v ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
          <label class="row tight dim" style="font-size:12px">Gap <select class="select" id="lo-gap">${[5, 10, 15, 30].map((g) => `<option value="${g}" ${String(gap) === String(g) ? 'selected' : ''}>${g} min</option>`).join('')}</select></label>
        </span>
        <button class="btn ghost" id="lo-clear">Clear filters</button>
        <div id="lo-bulk" class="row hidden"><span class="b outline" id="lo-seln"></span>${can('alarm.validate') || can('alarm.publish') ? `<button class="btn primary" id="lo-bulkshare">${icon('share', 's')} Review for client</button>` : ''}<button class="btn ghost" id="lo-selclear">Clear</button></div>
      </div>
      <div id="lo-groupsum" class="hidden"></div>
      <section class="card"><div id="lo-body" class="card-b flush">${skeleton(8, 28)}</div><div id="lo-pager"></div></section>`;

    const syncChrome = () => {
      const on_ = new Set((q.quick || '').split(',').filter(Boolean));
      $$('[data-quick]', el).forEach((b) => { b.classList.toggle('on', on_.has(b.dataset.quick)); b.setAttribute('aria-pressed', on_.has(b.dataset.quick)); });
      $('#lo-advn', el).textContent = ADV.filter((k) => q[k]).length;
      $('#lo-tabletools', el).classList.toggle('hidden', view !== 'table');
      $('#lo-grouptools', el).classList.toggle('hidden', view !== 'grouped');
      $('#lo-groupsum', el).classList.toggle('hidden', view !== 'grouped');
    };

    const filterParams = () => Object.fromEntries(Object.entries(q).filter(([k]) => !['page', 'size', 'view', 'by', 'gap'].includes(k)));

    const load = async ({ silent = false } = {}) => {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      syncChrome();
      if (!silent && !(view === 'grouped' ? groups : data)) $('#lo-body', el).innerHTML = skeleton(8, 28);
      try {
        if (view === 'grouped') {
          const res = await api.get('/api/groups', { projectId: pid, ...filterParams(), by: groupBy, gap, page: String(gpage), size: '25' }, { signal: ctrl.signal });
          if (ctx.isStale()) return;
          groups = res;
          paintGroups();
        } else {
          const res = await api.get('/api/alarms', { projectId: pid, ...q }, { signal: ctrl.signal });
          if (ctx.isStale()) return;
          data = res;
          paint();
        }
      } catch (e) {
        if (e.name === 'AbortError' || ctx.isStale()) return;
        const have = view === 'grouped' ? groups : data;
        if (!have) { $('#lo-body', el).innerHTML = errorBox(e); $('[data-retry]', el)?.addEventListener('click', () => load()); }
        else toast(`Refresh failed — showing last data. ${e.message}`, 'error');
      }
    };

    const subline = (f, note) => {
      $('#lo-sub', el).innerHTML = `${f.mode === 'demo' ? '<span class="demo-flag">DEMO</span> ' : ''}<b>${paused ? 'PAUSED' : f.state === 'live' ? 'LIVE' : esc(f.state.toUpperCase())}</b> · updated ${f.lastSuccessAt ? `${fmt.rel(f.lastSuccessAt)} (${fmt.time(f.lastSuccessAt)})` : '—'} · refresh every ${refreshMs() / 1000}s · ${esc(note)}`;
    };

    const paint = () => {
      const f = data.freshness;
      subline(f, data.windowNote);
      syncChrome();
      const body = $('#lo-body', el);
      const showSelect = can('alarm.validate') || can('alarm.publish');
      if (view === 'cards') {
        body.innerHTML = data.items.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(290px,1fr));padding:14px">${data.items.map((a) => alarmCard(a)).join('')}</div>` : empty('No alarms found', 'No alarms match the selected filters.', 'search');
      } else {
        table(body, {
          columns: tableColumns(),
          rows: data.items, sort: q.sort, dir: q.dir, hidden, density,
          onSort: (k) => { q.dir = q.sort === k && q.dir === 'desc' ? 'asc' : 'desc'; q.sort = k; q.page = '1'; ctx.setQuery(q); load(); },
          onRow: (a) => { location.hash = investigateHref(a); },
          rowClass: (a) => (a.flags?.new ? 'is-new' : ''),
          selectable: showSelect, selected, onSelect: (id, on_) => { on_ ? selected.add(id) : selected.delete(id); paintBulk(); },
          emptyHtml: empty('No alarms match the selected filters.', (q.quick || q.search || ADV.some((k) => q[k])) ? 'Try clearing some filters.' : 'No alarms have been received for this project yet.', 'search'),
        });
      }
      pager($('#lo-pager', el), { page: data.page, totalPages: data.totalPages, totalElements: data.totalElements, size: data.size,
        note: f.lastError ? `REFRESH FAILED: ${f.lastError.message} — last successful update ${f.lastSuccessAt ? fmt.time(f.lastSuccessAt) : '—'}` : '',
        onPage: (pg) => { q.page = String(pg); ctx.setQuery(q); load(); } });
      paintBulk();
    };

    // ---------------------------------------------------------------- grouped view
    const paintGroups = () => {
      const g = groups;
      subline(g.freshness, `${g.rawAlarms} alarms in the window`);
      syncChrome();
      $('#lo-groupsum', el).innerHTML = `<div class="banner info">${icon('tree')}<div class="grow"><b class="num">${fmt.n(g.rawAlarms)}</b> raw alarms → <b class="num">${fmt.n(g.groupCount)}</b> activity groups
        <span class="prov derived">DERIVED</span><div class="muted" style="font-size:11.5px;margin-top:2px">${esc(g.rule)} ${g.alarmsInGroups === g.rawAlarms ? 'All raw alarms are included.' : ''}</div></div></div>`;
      const body = $('#lo-body', el);
      if (!g.items.length) {
        body.innerHTML = empty('No alarms match the selected filters.', 'There is nothing to group.', 'search');
        $('#lo-pager', el).innerHTML = '';
        return;
      }
      body.innerHTML = `<div class="table-wrap"><table class="t ${density === 'compact' ? 'compact' : ''}"><thead><tr>
        <th scope="col" style="width:28px"><span class="sr-only">Expand</span></th><th scope="col">Group (${esc(g.by)})</th><th scope="col" class="num">Alarms</th><th scope="col" class="num">Reports</th>
        <th scope="col">Top priority</th><th scope="col" class="num">Pending</th><th scope="col">Time window</th><th scope="col" class="num">Duration</th><th scope="col">Types</th></tr></thead>
        <tbody>${g.items.map((x, i) => {
          const open = expanded.has(x.id);
          return `<tr class="group-row link" data-g="${i}" tabindex="0" aria-expanded="${open}">
            <td>${icon(open ? 'down' : 'right', 's')}</td>
            <td class="nowrap"><b class="mono">${esc(x.label)}</b>${x.count > 1 ? '' : ' <span class="muted" style="font-size:11px">· single</span>'}</td>
            <td class="num">${fmt.n(x.count)}</td><td class="num">${fmt.n(x.reports)}</td><td>${priorityBadge(x.topPriority)}</td>
            <td class="num">${fmt.n(x.pending)}</td>
            <td class="num dim nowrap">${fmt.dt(x.start)} → ${fmt.time(x.end)}</td><td class="num nowrap">${fmt.dur(x.durationMinutes)}</td>
            <td class="dim" style="font-size:12px">${Object.entries(x.types).map(([t, n]) => `${esc(t)}${n > 1 ? ` ×${n}` : ''}`).join(', ')}</td></tr>
            ${open ? `<tr><td colspan="9" style="background:var(--panel-2);padding:12px 14px">
              <div class="impact" style="margin-bottom:10px">
                ${[['Projects', x.impact.project], ['TCs', x.impact.tc], ['Centres', x.impact.centre], ['Rooms', x.impact.room], ['Cameras', x.impact.cameras]]
                  .map(([l, v]) => `<div><b>${l} affected</b>${v && v.length ? v.map((c) => `<span class="mono">${esc(c)}</span>`).join(', ') : '<span class="muted">—</span>'}</div>`).join('')}
                <div><b>Alarm count</b><span class="num">${fmt.n(x.count)} alarms · ${fmt.n(x.reports)} reports</span></div>
                <div><b>Time window</b><span class="num">${fmt.dt(x.start)} → ${fmt.dt(x.end)}</span></div>
              </div>
              <div class="muted" style="font-size:11.5px;margin-bottom:6px">Impact view is descriptive (what the group touches) — not a threat score. Every underlying alarm:</div>
              <div class="card" data-gt="${i}"></div></td></tr>` : ''}`;
        }).join('')}</tbody></table></div>`;
      g.items.forEach((x, i) => {
        const host = body.querySelector(`[data-gt="${i}"]`);
        if (host) table(host, { columns: columns(['priority', 'alarm', 'camera', 'state', 'workflow', 'visibility', 'occurrences', 'last', 'evidence']), rows: x.alarms, density,
          onRow: (a) => { location.hash = investigateHref(a); } });
      });
      pager($('#lo-pager', el), { page: g.page, totalPages: g.totalPages, totalElements: g.totalElements, size: g.size,
        note: g.freshness.lastError ? `REFRESH FAILED: ${g.freshness.lastError.message}` : '',
        onPage: (pg) => { gpage = pg; load(); } });
    };
    const toggleGroup = (i) => {
      const x = groups.items[i];
      expanded.has(x.id) ? expanded.delete(x.id) : expanded.add(x.id);
      paintGroups();
      el.querySelector(`[data-g="${i}"]`)?.focus();
    };
    ctx.onCleanup(delegate(el, 'click', 'tr[data-g]', (e, tr) => toggleGroup(+tr.dataset.g)));
    ctx.onCleanup(delegate(el, 'keydown', 'tr[data-g]', (e, tr) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleGroup(+tr.dataset.g); } }));
    $('#lo-by', el).addEventListener('change', (e) => { groupBy = e.target.value; setPref('liveGroupBy', groupBy); gpage = 1; expanded.clear(); ctx.setQuery({ ...q, by: groupBy }); load(); });
    $('#lo-gap', el).addEventListener('change', (e) => { gap = e.target.value; setPref('liveGroupGap', +gap); gpage = 1; expanded.clear(); ctx.setQuery({ ...q, gap }); load(); });

    const paintBulk = () => {
      $('#lo-bulk', el).classList.toggle('hidden', !selected.size);
      $('#lo-seln', el).textContent = `${selected.size} selected`;
    };

    const setQ = (patch) => {
      q = { ...q, ...patch, page: '1' };
      Object.keys(q).forEach((k) => (q[k] === '' || q[k] == null) && delete q[k]);
      gpage = 1; expanded.clear();
      ctx.setQuery(q); load();
    };

    // quick filters, view, density, columns
    ctx.onCleanup(delegate(el, 'click', '[data-quick]', (e, b) => {
      const cur = new Set((q.quick || '').split(',').filter(Boolean));
      cur.has(b.dataset.quick) ? cur.delete(b.dataset.quick) : cur.add(b.dataset.quick);
      setQ({ quick: [...cur].join(',') });
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-view]', (e, b) => {
      view = b.dataset.view; setPref('liveView', view);
      $$('[data-view]', el).forEach((x) => x.classList.toggle('on', x === b));
      ctx.setQuery({ ...q, view });
      load();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-density]', (e, b) => {
      density = b.dataset.density; setPref('density', density); document.body.dataset.density = density;
      $$('[data-density]', el).forEach((x) => x.classList.toggle('on', x === b));
      if (view === 'grouped' ? groups : data) (view === 'grouped' ? paintGroups : paint)();
    }));
    $('#lo-cols', el).addEventListener('click', (e) => columnChooser(e.currentTarget, {
      keys: tableColumns().map((c) => ({ key: c.key, label: c.label, locked: LOCKED.has(c.key) })),
      hidden,
      onChange: (h) => { LOCKED.forEach((k) => h.delete(k)); setPref('liveHidden', [...h]); if (data) paint(); },
    }));
    $('#lo-search', el).addEventListener('input', debounce((e) => setQ({ search: e.target.value.trim() }), 300));
    $('#lo-clear', el).addEventListener('click', () => { q = { sort: q.sort, dir: q.dir, size: q.size, page: '1' }; $('#lo-search', el).value = ''; gpage = 1; ctx.setQuery(q); load(); });
    $('#lo-selclear', el).addEventListener('click', () => { selected.clear(); paint(); });
    $('#lo-bulkshare', el)?.addEventListener('click', async () => {
      const clients = await api.get('/api/sharing', { projectId: pid, tab: 'candidates', size: 1 }).then((r) => r.clients).catch(() => []);
      if (await bulkRequest([...selected], pid, clients)) { selected.clear(); load(); }
    });

    // pause / resume
    const pauseBtn = $('#lo-pause', el);
    const setPaused = (v) => {
      paused = v;
      pauseBtn.innerHTML = paused ? `${icon('play', 's')} Resume live feed` : `${icon('pause', 's')} Pause live feed`;
      pauseBtn.classList.toggle('primary', paused);
      if (!paused) { $('#lo-new', el).classList.add('hidden'); if (pendingWhilePaused) { pendingWhilePaused = false; load({ silent: true }); } }
      if (view === 'grouped' ? groups : data) (view === 'grouped' ? paintGroups : paint)();
    };
    pauseBtn.addEventListener('click', () => setPaused(!paused));
    $('#lo-resume', el).addEventListener('click', () => setPaused(false));
    ctx.onCleanup(on('data', (s) => {
      if (paused) { if (s.newAlarms) { pendingWhilePaused = true; $('#lo-new', el).classList.remove('hidden'); } return; }
      load({ silent: true });
    }));

    // export (server enforces alarm.export and audits it)
    $('#lo-export', el)?.addEventListener('click', async () => {
      try { await api.download('/api/export/alarms.csv', { projectId: pid, ...filterParams() }); toast('Export downloaded (recorded in audit trail)', 'success'); }
      catch (e) { toast(e.message, 'error'); }
    });

    // advanced filters drawer
    $('#lo-adv', el).addEventListener('click', () => {
      const fc = data?.facets || { alarmTypes: [], shifts: [], tecs: [], tcs: [], centres: [] };
      const opt = (arr, cur) => `<option value="">Any</option>${arr.map((v) => { const [val, lab] = Array.isArray(v) ? v : [v, v]; return `<option value="${esc(val)}" ${String(cur) === String(val) ? 'selected' : ''}>${esc(lab)}</option>`; }).join('')}`;
      dialog({
        title: `${icon('filter')} Advanced filters`, side: true,
        body: `
          <div class="field"><label>TC</label><select class="select" data-f="tc">${opt(fc.tcs, q.tc)}</select></div>
          <div class="field"><label>Centre</label><select class="select" data-f="centre">${opt(fc.centres, q.centre)}</select></div>
          <div class="field"><label>Camera (id or code)</label><input class="input" data-f="camera" value="${esc(q.camera || '')}"></div>
          <div class="field"><label>Alarm type</label><select class="select" data-f="alarmType">${opt(fc.alarmTypes, q.alarmType)}</select></div>
          <div class="field"><label>Priority</label><select class="select" data-f="priority">${opt([['critical', 'Critical'], ['high', 'High'], ['medium', 'Medium'], ['low', 'Low']], q.priority)}</select></div>
          <div class="field"><label>Camview status (lastActionType)</label><select class="select" data-f="lastActionType">${opt([['0', 'Pending'], ['1', 'Valid'], ['2', 'Invalid'], ['3', 'Exception']], q.lastActionType)}</select></div>
          <div class="field"><label>Workflow</label><select class="select" data-f="workflowState">${opt([['NEW', 'New'], ['UNDER_REVIEW', 'Under review'], ['INVESTIGATING', 'Investigating'], ['READY_FOR_CLIENT', 'Ready for client'], ['READY_FOR_APPROVAL', 'Awaiting approval'], ['APPROVED', 'Approved'], ['SHARED', 'Shared'], ['CLIENT_ACKNOWLEDGED', 'Client acknowledged'], ['WITHDRAWN', 'Withdrawn'], ['CLOSED', 'Closed']], q.workflowState)}</select></div>
          <div class="field"><label>Client visibility</label><select class="select" data-f="visibility">${opt([['internal', 'Internal'], ['ready_for_review', 'Ready for review'], ['approved', 'Approved'], ['shared', 'Shared'], ['withdrawn', 'Withdrawn']], q.visibility)}</select></div>
          <div class="field"><label>Shift</label><select class="select" data-f="shiftLabel">${opt(fc.shifts, q.shiftLabel)}</select></div>
          <div class="grid g-2"><div class="field"><label>From</label><input class="input" type="datetime-local" data-f="from" value="${esc(toLocalInput(q.from))}"></div>
          <div class="field"><label>To</label><input class="input" type="datetime-local" data-f="to" value="${esc(toLocalInput(q.to))}"></div></div>`,
        actions: [
          { label: 'Reset', onClick: ({ close }) => { ADV.forEach((k) => delete q[k]); setQ({}); close(); } },
          { label: 'Apply', kind: 'primary', onClick: ({ close, el: d }) => {
            const patch = {};
            $$('[data-f]', d).forEach((i) => {
              let v = i.value.trim();
              if ((i.dataset.f === 'from' || i.dataset.f === 'to') && v) v = new Date(v).toISOString();
              patch[i.dataset.f] = v;
            });
            setQ(patch); close();
          } },
        ],
      });
    });

    // saved views (per user; replayed through the same permission-checked API)
    const views = async () => {
      try {
        const r = await api.get('/api/views');
        const sel = $('#lo-views', el);
        const mine = r.items.filter((v) => v.route === '/live');
        sel.innerHTML = `<option value="">Saved views…</option>${[
          ['Critical today', `quick=critical&from=${encodeURIComponent(startOfToday())}`], ['Pending review', 'quick=pending'],
          ['Ready for client', 'quick=readyForClient'], ['Shared today', `quick=shared&from=${encodeURIComponent(startOfToday())}`]]
          .map(([n, qs]) => `<option value="${esc(qs)}">★ ${esc(n)}</option>`).join('')}${mine.map((v) => `<option value="${esc(v.query)}" data-id="${v.id}">${esc(v.name)}</option>`).join('')}`;
      } catch { /* optional */ }
    };
    $('#lo-views', el).addEventListener('change', (e) => {
      if (!e.target.value) return;
      q = { sort: q.sort, dir: q.dir, size: q.size, ...Object.fromEntries(new URLSearchParams(e.target.value)) };
      gpage = 1; expanded.clear();
      ctx.setQuery(q); $('#lo-search', el).value = q.search || ''; load();
    });
    $('#lo-save', el).addEventListener('click', () => {
      dialog({ title: 'Save current view', body: '<div class="field"><label>Name</label><input class="input" id="sv-name" placeholder="e.g. TC-0701 morning critical" autofocus></div><p class="muted">Saves the current filters. Views always respect your permissions.</p>',
        actions: [{ label: 'Cancel', onClick: ({ close }) => close() }, { label: 'Save', kind: 'primary', onClick: async ({ close, el: d }) => {
          const name = $('#sv-name', d).value.trim(); if (!name) return;
          const query = new URLSearchParams(Object.fromEntries(Object.entries(q).filter(([k]) => !['page'].includes(k)))).toString();
          await api.post('/api/views', { name, route: '/live', query }); toast('View saved', 'success'); close(); views();
        } }] });
    });

    views();
    await load();
  },
};

function toLocalInput(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return '';
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function startOfToday() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d.toISOString();
}
