// pages/history.js — ALARM HISTORY
// Live mode uses Camview's documented history query (useHistory + startTime/endTime,
// page/size, lastKey cursor). Filters and range live in the URL.

import * as api from '../core/api.js';
import { can, currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, pager, errorBox, skeleton, empty, toast, debounce, $ } from '../core/ui.js';
import { columns, investigateHref } from '../components/alarms.js';

const localIso = (iso) => { const d = new Date(iso); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16); };

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Alarm History', `${esc(projectInfo(pid).code)} · historical alarms`);
    const now = new Date();
    let q = {
      from: new Date(now.getTime() - 86400000).toISOString(), to: now.toISOString(), page: '1', size: '50', sort: 'lastInstance', dir: 'desc',
      ...ctx.query,
    };
    const cursors = { 1: undefined };   // page -> lastKey used to fetch it
    let data = null;

    el.innerHTML = `
      <div class="page-head"><div><h2>Alarm history</h2><p id="hi-sub">Historical alarms for the selected range.</p></div>
        <div class="row">${[['24h', 1], ['7d', 7], ['30d', 30]].map(([l, days]) => `<button class="btn sm" data-preset="${days}">Last ${l}</button>`).join('')}</div></div>
      <div class="filters">
        <label class="row" style="gap:6px"><span class="muted">From</span><input class="input" type="datetime-local" id="hi-from" value="${esc(localIso(q.from))}"></label>
        <label class="row" style="gap:6px"><span class="muted">To</span><input class="input" type="datetime-local" id="hi-to" value="${esc(localIso(q.to))}"></label>
        <select class="select" id="hi-state" aria-label="Camview status"><option value="">Any status</option>${[['0', 'Pending'], ['1', 'Valid'], ['2', 'Invalid'], ['3', 'Exception']].map(([v, l]) => `<option value="${v}" ${q.lastActionType === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <select class="select" id="hi-prio" aria-label="Priority"><option value="">Any priority</option>${['critical', 'high', 'medium', 'low'].map((p) => `<option value="${p}" ${q.priority === p ? 'selected' : ''}>${p}</option>`).join('')}</select>
        <input class="input" id="hi-type" placeholder="Alarm type id" style="width:120px" value="${esc(q.alarmType || '')}" inputmode="numeric" aria-label="Alarm type id">
        <input class="input" id="hi-shift" placeholder="Shift label" style="width:150px" value="${esc(q.shiftLabel || '')}" aria-label="Shift label">
        <input class="input" id="hi-search" data-page-search placeholder="Search ID, camera, ticket…  ( / )" style="min-width:220px" value="${esc(q.search || '')}">
        <button class="btn primary" id="hi-apply">${icon('search', 's')} Apply</button>
        <button class="btn ghost" id="hi-clear">Reset</button>
        <span class="grow"></span>
        ${can('alarm.export') ? `<button class="btn" id="hi-export">${icon('download', 's')} Export</button>` : ''}
      </div>
      <section class="card"><div id="hi-body" class="card-b flush">${skeleton(10, 26)}</div><div id="hi-pager"></div></section>`;

    const load = async () => {
      $('#hi-body', el).innerHTML = skeleton(10, 26);
      const params = { projectId: pid, ...q };
      if (cursors[q.page]) params.lastKey = cursors[q.page]; else delete params.lastKey;
      try {
        data = await api.get('/api/history', params);
        if (ctx.isStale()) return;
        if (data.lastKey) cursors[+q.page + 1] = data.lastKey;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#hi-body', el).innerHTML = errorBox(e);
        $('#hi-pager', el).innerHTML = '';
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const paint = () => {
      const src = data.source === 'demo' ? '<span class="demo-flag">DEMO</span> demo alarm set' : 'Camview history query (useHistory)';
      $('#hi-sub', el).innerHTML = `${fmt.dt(q.from)} → ${fmt.dt(q.to)} · source: ${src}`;
      table($('#hi-body', el), {
        columns: columns(['priority', 'alarm', 'camera', 'context', 'state', 'workflow', 'visibility', 'occurrences', 'shift', 'first', 'last', 'evidence']),
        rows: data.items,
        sort: q.sort, dir: q.dir,
        onSort: data.source === 'demo' ? (k) => { q.dir = q.sort === k && q.dir === 'desc' ? 'asc' : 'desc'; q.sort = k; q.page = '1'; ctx.setQuery(q); load(); } : undefined,
        onRow: (a) => { location.hash = investigateHref(a); },
        emptyHtml: empty('No alarms found', 'No alarms in this range match the filters.', 'history'),
      });
      pager($('#hi-pager', el), {
        page: data.page, totalPages: data.totalPages || (data.hasNext ? data.page + 1 : data.page), totalElements: data.totalElements ?? data.items.length,
        note: data.source !== 'demo' ? 'Priority/search filters apply to each returned page' : '',
        onPage: (pg) => { q.page = String(pg); ctx.setQuery(q); load(); },
      });
    };

    const readFilters = () => {
      const f = $('#hi-from', el).value, t = $('#hi-to', el).value;
      if (!f || !t || new Date(f) >= new Date(t)) { toast('Choose a valid date range', 'warning'); return false; }
      q = { ...q, from: new Date(f).toISOString(), to: new Date(t).toISOString(), lastActionType: $('#hi-state', el).value, priority: $('#hi-prio', el).value,
        alarmType: $('#hi-type', el).value.trim(), shiftLabel: $('#hi-shift', el).value.trim(), search: $('#hi-search', el).value.trim(), page: '1' };
      Object.keys(q).forEach((k) => (q[k] === '' || q[k] == null) && delete q[k]);
      Object.keys(cursors).forEach((k) => k !== '1' && delete cursors[k]);
      ctx.setQuery(q);
      return true;
    };
    $('#hi-apply', el).addEventListener('click', () => { if (readFilters()) load(); });
    $('#hi-search', el).addEventListener('keydown', (e) => { if (e.key === 'Enter' && readFilters()) load(); });
    $('#hi-clear', el).addEventListener('click', () => { ctx.setQuery({ from: '', to: '', page: '', lastActionType: '', priority: '', alarmType: '', shiftLabel: '', search: '' }); location.reload(); });
    el.querySelectorAll('[data-preset]').forEach((b) => b.addEventListener('click', () => {
      const end = new Date();
      $('#hi-from', el).value = localIso(new Date(end.getTime() - +b.dataset.preset * 86400000).toISOString());
      $('#hi-to', el).value = localIso(end.toISOString());
      if (readFilters()) load();
    }));
    $('#hi-export', el)?.addEventListener('click', async () => {
      try { await api.download('/api/export/alarms.csv', { projectId: pid, from: q.from, to: q.to, lastActionType: q.lastActionType, priority: q.priority, alarmType: q.alarmType, shiftLabel: q.shiftLabel, search: q.search }); toast('Export downloaded (recorded in audit trail)', 'success'); }
      catch (e) { toast(e.message, 'error'); }
    });
    void debounce;
    await load();
  },
};
