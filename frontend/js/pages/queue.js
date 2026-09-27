// pages/queue.js — ALERTS: one queue across every exam, client and project you can see.
// Sorted automatically (priority → most recent → most repeated). Refreshes by itself;
// new alerts are marked NEW in place and nothing you are looking at jumps away.

import * as api from '../core/api.js';
import { on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, empty, errorBox, skeleton, toast, pager, delegate, morph, $, $$ } from '../core/ui.js';
import { alertRow, rememberQueue } from '../components/queue.js';

const TABS = [['all', 'All'], ['pending', 'Pending'], ['valid', 'Valid'], ['invalid', 'Invalid'], ['exception', 'Exception']];
// Two kinds of records come from Camview: alerts to decide (image + video) and camera online/offline events (no media).
const KINDS = [['alert', 'Alerts', 'alert'], ['camera_status', 'Camera status events', 'camera']];
const FILTERS = ['search', 'projectId', 'client', 'exam', 'priority', 'centre', 'camera', 'city', 'by', 'from', 'to', 'kind', 'type', 'range'];
const todayStr = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`; };
const SIZE = 50;
const RULES = {
  alert: null,                                      // the server's sort rule
  camera_status: 'Camera online / offline events as Camview reports them (most recent first). They carry no image or video; each row shows the camera\'s current connection.',
};

export default {
  async render(el, ctx) {
    setTitle('Alerts', 'Every exam and client in one queue');
    const q = { status: 'pending', page: 1, ...Object.fromEntries(FILTERS.map((k) => [k, ''])), kind: 'alert', ...ctx.query };
    q.page = +q.page || 1;
    if (q.kind === 'camera') q.kind = 'camera_status';
    if (!KINDS.some(([k]) => k === q.kind)) q.kind = 'alert';
    // Today by default: earlier days only when a date is chosen (or "All dates")
    if (!q.from && !q.to && q.range !== 'all') { q.from = todayStr(); q.range = 'today'; }
    let data = null, ctrl = null;
    const seen = new Set();
    let firstLoad = true;

    el.innerHTML = `
      <div class="page-head"><div><h2>Alerts</h2><p id="q-rule">Sorted automatically: priority first, then most recent, then most repeated.</p></div>
        <div class="row"><span class="muted" id="q-fresh"></span><button class="btn sm" id="q-refresh">${icon('refresh', 's')} Refresh</button></div></div>
      <div class="tabs kind-tabs ${q.kind === 'camera_status' ? '' : 'hidden'}" role="tablist" id="q-kinds" aria-label="Kind of record">${KINDS.map(([k, l, ic]) => `<button class="tab ${k === q.kind ? 'on' : ''}" role="tab" aria-selected="${k === q.kind}" data-kind="${k}">${icon(ic, 's')} ${l}<span class="n" data-kn="${k}">·</span></button>`).join('')}</div>
      <div class="tabs" role="tablist" id="q-tabs">${TABS.map(([k, l]) => `<button class="tab ${k === q.status ? 'on' : ''}" role="tab" aria-selected="${k === q.status}" data-tab="${k}">${l}<span class="n" data-n="${k}">·</span></button>`).join('')}</div>
      <div class="filters">
        <input class="input" style="min-width:220px" id="f-search" data-page-search placeholder="Search alert, camera, room, exam…  ( / )" value="${esc(q.search)}">
        <select class="select" id="f-projectId" aria-label="Project"><option value="">All projects</option></select>
        <select class="select" id="f-client" aria-label="Client"><option value="">All clients</option></select>
        <select class="select" id="f-exam" aria-label="Exam"><option value="">All exams</option></select>
        <select class="select" id="f-type" aria-label="Alert type"><option value="">All types</option></select>
        <select class="select" id="f-by" aria-label="Decided by" title="Who decided the alert"><option value="">Decided by anyone</option><option value="team">Decided by our team</option><option value="camview">Status from Camview only</option></select>
        <select class="select" id="f-priority" aria-label="Priority"><option value="">All priorities</option>${['critical', 'high', 'medium', 'low'].map((p) => `<option value="${p}">${p[0].toUpperCase() + p.slice(1)}</option>`).join('')}</select>
        <select class="select" id="f-centre" aria-label="Centre"><option value="">All centres</option></select>
        <select class="select" id="f-camera" aria-label="Camera"><option value="">All cameras</option></select>
        <div class="seg" role="group" aria-label="Period"><button class="btn sm ${q.range !== 'all' && (q.from || '') === todayStr() && !q.to ? 'primary' : ''}" id="f-today">Today</button><button class="btn sm ${q.range === 'all' ? 'primary' : ''}" id="f-all">All dates</button></div>
        <label class="row tight muted" style="font-size:12px">From <input class="input" type="date" id="f-from" value="${esc((q.from || '').slice(0, 10))}"></label>
        <label class="row tight muted" style="font-size:12px">To <input class="input" type="date" id="f-to" value="${esc((q.to || '').slice(0, 10))}"></label>
        <button class="btn sm ghost" id="f-clear">Clear</button>
      </div>
      <section class="card"><div class="qhead"><span>Priority</span><span>Alert · where</span><span>Exam · client</span><span>Status</span><span>When</span></div>
        <div id="q-rows" class="qrows">${skeleton(8, 40)}</div><div class="card-b" id="q-pager"></div></section>`;
    // a frame whose signed link expired shows a neutral tile instead of a broken image (CSP forbids inline handlers)
    $('#q-rows', el).addEventListener('error', (e) => { if (e.target?.tagName === 'IMG') e.target.closest('.q-thumb')?.classList.add('broken'); }, true);

    // filter options are rebuilt only when the list itself changed (an open menu is never yanked away)
    const opts = (sel, list, cur) => {
      const s = $(sel, el);
      const html = list.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
      if (s.dataset.sig !== html) { s.innerHTML = s.options[0].outerHTML + html; s.dataset.sig = html; }
      if (s.value !== (cur || '') && document.activeElement !== s) s.value = cur || '';
    };

    const params = () => {
      const p = { status: q.status, page: q.page, size: SIZE };
      FILTERS.forEach((k) => { if (q[k] && k !== 'range') p[k] = q[k]; });
      if (p.from) p.from = `${p.from.slice(0, 10)}T00:00:00`;
      if (p.to) p.to = `${p.to.slice(0, 10)}T23:59:59`;
      return p;
    };

    let loadedAt = null;
    const load = async ({ quiet = false } = {}) => {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      const btn = $('#q-refresh', el);
      btn?.classList.add('loading');
      try {
        const r = await api.get('/api/queue', params(), { signal: ctrl.signal });
        if (ctx.isStale()) return;
        data = r;
        loadedAt = new Date();
        paint();
      } catch (e) {
        if (e.name === 'AbortError' || ctx.isStale()) return;
        if (!data) { $('#q-rows', el).innerHTML = errorBox(e); $('[data-retry]', el)?.addEventListener('click', () => load()); }
        else if (!quiet) toast(`Refresh failed — showing the last data. ${e.message}`, 'error');
      } finally {
        if (!ctx.isStale()) btn?.classList.remove('loading');
      }
    };

    const paint = () => {
      const f = data.facets;
      opts('#f-projectId', f.projects || [], q.projectId);
      opts('#f-client', f.clients, q.client);
      opts('#f-exam', f.exams, q.exam);
      opts('#f-centre', f.centres.map((c) => [c, c]), q.centre);
      opts('#f-camera', f.cameras.map((c) => [c, c]), q.camera);
      opts('#f-type', (f.types || []).map(([v, l]) => [v, l || `Alert type ${v}`]), q.type);
      $('#f-priority', el).value = q.priority || '';
      $('#f-by', el).value = q.by || '';
      TABS.forEach(([k]) => { const n = $(`[data-n="${k}"]`, el); if (n) n.textContent = data.counts[k] ?? 0; });
      KINDS.forEach(([k]) => { const n = $(`[data-kn="${k}"]`, el); if (n) n.textContent = data.kinds?.[k] ?? 0; });
      $('#q-rule', el).textContent = RULES[q.kind] || data.sortRule;
      const fr = Object.values(data.freshness || {});
      const failed = fr.filter((x) => x.lastError);
      const arrived = data.items.filter((a) => !firstLoad && !seen.has(a.alarmId)).length;
      $('#q-fresh', el).innerHTML = (failed.length ? `<span class="sla-attention">${icon('alert', 's')} CAMVIEW DATA TEMPORARILY UNAVAILABLE (${failed.length} project${failed.length === 1 ? '' : 's'}) — showing last data</span> · ` : '')
        + `<span title="When this list was last re-read">Updated ${fmt.time(loadedAt?.toISOString())}</span>`;
      rememberQueue(data.items, { ...Object.fromEntries(FILTERS.map((k) => [k, q[k]])), status: q.status });
      const rows = $('#q-rows', el);
      // Patched in place: rows that stayed keep their thumbnails, focus and scroll position; new rows slide in marked NEW.
      if (!data.items.length) {
        morph(rows, q.kind === 'camera_status'
          ? empty('No camera status events', 'Camera online / offline events reported by Camview appear here.', 'camera')
          : empty(q.status === 'pending' ? 'Nothing waiting for a decision' : 'No alerts here',
            q.status === 'pending' ? 'Every alert has been decided. New alerts appear here automatically.' : 'No alerts match these filters.', 'check'));
      } else {
        morph(rows, data.items.map((a) => alertRow(a, { isNew: !firstLoad && !seen.has(a.alarmId) })).join(''));
      }
      if (arrived && q.status === 'pending' && q.page === 1 && document.scrollingElement.scrollTop > 400) {
        toast(`${arrived} new alert${arrived === 1 ? '' : 's'} at the top of the list`);
      }
      data.items.forEach((a) => seen.add(a.alarmId));
      firstLoad = false;
      pager($('#q-pager', el), { page: data.page, totalPages: data.totalPages, totalElements: data.totalElements, size: SIZE,
        onPage: (p) => { q.page = p; ctx.setQuery({ page: p }); load(); } });
    };

    const set = (patch) => {
      Object.assign(q, patch, { page: 1 });
      ctx.setQuery({ ...patch, page: 1 });
      load();
    };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => {
      $$('[data-tab]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
      set({ status: b.dataset.tab });
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-kind]', (e, b) => {
      $$('[data-kind]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
      const status = b.dataset.kind === 'camera_status' ? 'all' : 'pending';       // status events are all "pending" in Camview
      $$('[data-tab]', el).forEach((x) => { x.classList.toggle('on', x.dataset.tab === status); x.setAttribute('aria-selected', x.dataset.tab === status); });
      set({ kind: b.dataset.kind, status });
    }));
    ['projectId', 'client', 'exam', 'priority', 'centre', 'camera', 'type', 'by'].forEach((k) => $(`#f-${k}`, el).addEventListener('change', (e) => set({ [k]: e.target.value })));
    ['from', 'to'].forEach((k) => $(`#f-${k}`, el).addEventListener('change', (e) => set({ [k]: e.target.value, range: '' })));
    $('#f-today', el).addEventListener('click', () => { $('#f-from', el).value = todayStr(); $('#f-to', el).value = ''; set({ from: todayStr(), to: '', range: 'today' }); });
    $('#f-all', el).addEventListener('click', () => { $('#f-from', el).value = ''; $('#f-to', el).value = ''; set({ from: '', to: '', range: 'all' }); });
    let t;
    $('#f-search', el).addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => set({ search: e.target.value.trim() }), 300); });
    $('#f-clear', el).addEventListener('click', () => {
      FILTERS.forEach((k) => { const i = $(`#f-${k}`, el); if (i) i.value = ''; });
      set({ ...Object.fromEntries(FILTERS.map((k) => [k, ''])), kind: q.kind, from: todayStr(), range: 'today' });
    });
    $('#q-refresh', el).addEventListener('click', () => load());
    ctx.onCleanup(on('data', () => load({ quiet: true })));
    await load();
  },
};
