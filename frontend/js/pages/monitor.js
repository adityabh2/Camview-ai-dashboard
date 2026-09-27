// pages/monitor.js — MONITOR: the simple home screen.
//   Top:    centre alarm-status board (OK / WARNING / ALARM) — click a centre to filter.
//   Bottom: alarm inbox — pick an alarm, see where + evidence, mark Valid / Invalid / Exception.
// Keyboard: ↑/↓ (or J/K) move · V valid · I invalid · E exception.

import * as api from '../core/api.js';
import { can, currentProject, projectInfo, on, session, pref, setPref } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, empty, errorBox, skeleton, toast, priorityBadge, stateBadge, contextPath, delegate, morph, spark, $, $$ } from '../core/ui.js';
import { lightbox } from '../components/evidence.js';
import { thumb, healthChips } from '../components/queue.js';
import { kpi } from '../core/ui.js';

const TABS = [
  ['toReview', 'To review'],
  ['valid', 'Valid'],
  ['invalid', 'Invalid'],
  ['exception', 'Exception'],
  ['all', 'All'],
];
const STATE_LABEL = { alarm: ['ALARM', 'alert'], warning: ['WARNING', 'bang'], ok: ['OK', 'check'] };
const STATE_FILTERS = [['all', 'All'], ['alarm', 'Alarm'], ['warning', 'Warning'], ['ok', 'OK'], ['offline', 'Cameras offline']];
const SORTS = [['attention', 'Needs attention'], ['today', 'Most alerts today'], ['review', 'Most to review'], ['name', 'Name']];
const COLLAPSED = 12;                  // tiles shown before "Show all"
const tzOffset = () => -new Date().getTimezoneOffset();

export default {
  async render(el, ctx) {
    const pid = currentProject();
    const p = projectInfo(pid);
    setTitle('Monitor', `${esc(p.code)} · alarms as they happen`);
    if (!pid) { el.innerHTML = empty('No project available', 'Ask an administrator to give you access to a project.', 'tree'); return; }

    let tab = TABS.some(([k]) => k === ctx.query.list) ? ctx.query.list : 'toReview';
    let centre = ctx.query.centre || '';
    let search = ctx.query.search || '';
    let selectedId = ctx.query.alarm || null;
    let board = null, list = null, detail = null;
    let sf = ctx.query.state || 'all';                        // board filter
    let city = ctx.query.city || '';
    let sort = pref('monSort', 'attention');
    let view = pref('monView', 'tiles');
    let expanded = pref('monExpanded', false);
    let listCtrl = null, busy = false;
    const seen = new Set();
    let firstLoad = true;

    el.innerHTML = `
      <div class="mon-head">
        <div><h2>Centres</h2><div class="mon-sub"><span class="mono" title="Project">${esc(p.code)}</span> <span class="muted" id="m-fresh"></span></div></div>
        <span class="mon-count" id="m-count">…</span>
        <span class="grow"></span>
        <input class="input mon-search" id="m-search" data-page-search placeholder="Search centre, city, alarm, camera…  ( / )" value="${esc(search)}">
      </div>
      <div class="kpis mon-kpis" id="m-kpis"></div>
      <section class="card mon-board">
        <div class="card-h"><h3>${icon('building')} Centre status</h3>
          <div class="sub" id="m-rule"></div>
          <div class="actions"><button class="btn sm ghost hidden" id="m-allc">${icon('x', 's')} Clear centre</button></div></div>
        <div class="mon-toolbar">
          <div class="seg mon-sf" role="group" aria-label="Show centres">${STATE_FILTERS.map(([k, l]) => `<button type="button" data-sf="${k}" class="${k === sf ? 'on' : ''} sf-${k}" aria-pressed="${k === sf}">${l} <span class="n" data-sfn="${k}"></span></button>`).join('')}</div>
          <span class="grow"></span>
          <select class="select sm" id="m-city" aria-label="City"><option value="">All cities</option></select>
          <select class="select sm" id="m-sort" aria-label="Sort centres">${SORTS.map(([k, l]) => `<option value="${k}" ${k === sort ? 'selected' : ''}>${l}</option>`).join('')}</select>
          <div class="seg" role="group" aria-label="Layout"><button type="button" data-view="tiles" class="${view === 'tiles' ? 'on' : ''}" title="Tiles" aria-label="Tiles">${icon('grid', 's')}</button><button type="button" data-view="list" class="${view === 'list' ? 'on' : ''}" title="List" aria-label="List">${icon('menu', 's')}</button></div>
        </div>
        <div class="card-b" id="m-board">${skeleton(2, 70)}</div>
      </section>
      <div class="inbox">
        <section class="card mon-list">
          <div class="tabs in-card" role="tablist" id="m-tabs">${TABS.map(([k, l]) => `<button class="tab ${k === tab ? 'on' : ''}" role="tab" data-tab="${k}">${l}<span class="n" data-n="${k}">·</span></button>`).join('')}</div>
          <div id="m-filter" class="muted" style="padding:6px 14px;font-size:11.5px"></div>
          <div class="rows" id="m-rows" role="listbox" aria-label="Alarms">${skeleton(6, 34)}</div>
        </section>
        <section class="card mon-detail" id="m-detail" aria-live="polite">${empty('Select an alarm', 'Pick an alarm on the left to see where it happened and its evidence.', 'investigate')}</section>
      </div>`;

    // ------------------------------------------------------------------ board
    const loadBoard = async () => {
      try {
        board = await api.get('/api/health', { projectId: pid, tzOffset: tzOffset() });
        if (ctx.isStale()) return;
        paintBoard();
      } catch (e) {
        if (!board) $('#m-board', el).innerHTML = errorBox(e);
      }
    };

    // Filter / sort the centre tiles on the client: the board is small (tens of centres) and this keeps every
    // control instant. Live updates re-read the board and are patched in place (morph), never rebuilt.
    const filtered = () => {
      const q = search.toLowerCase();
      const order = { alarm: 0, warning: 1, ok: 2 };
      const by = {
        attention: (a, b) => order[a.state] - order[b.state] || b.urgent - a.urgent || b.toReview - a.toReview || String(a.code).localeCompare(String(b.code)),
        today: (a, b) => b.today - a.today || order[a.state] - order[b.state],
        review: (a, b) => b.toReview - a.toReview || b.critical - a.critical,
        name: (a, b) => String(a.name || a.code).localeCompare(String(b.name || b.code)),
      }[sort] || (() => 0);
      return board.tiles.filter((t) => (sf === 'all' ? true : sf === 'offline' ? t.camerasOffline > 0 : t.state === sf)
        && (!city || t.city === city)
        && (!q || [t.code, t.name, t.city].some((v) => String(v || '').toLowerCase().includes(q)))).sort(by);
    };

    const tileHtml = (t) => {
      const [lab, ic] = STATE_LABEL[t.state];
      const place = [t.city, t.state_name].filter(Boolean).join(', ');
      return `<button class="htile ${t.state} ${centre === t.key ? 'sel' : ''}" data-key="${esc(t.key)}" data-centre="${esc(t.key)}" aria-pressed="${centre === t.key}" title="${esc(board.rule)}">
          ${t.state === 'alarm' ? '<span class="pulse" aria-hidden="true"></span>' : ''}
          <div class="ht-top"><span class="st">${icon(ic, 's')} ${lab}</span><span class="ht-code mono">${esc(t.code)}</span></div>
          <div class="ht-name">${esc(t.name && t.name !== t.code ? t.name : t.code)}</div>
          ${place ? `<div class="ht-place">${icon('map', 's')} ${esc(place)}</div>` : ''}
          <div class="ht-stats">
            <span title="Alerts today"><b>${fmt.n(t.today)}</b> today</span>
            <span title="Waiting for a decision"${t.toReview ? ' class="warn"' : ''}><b>${fmt.n(t.toReview)}</b> to review</span>
            ${t.critical ? `<span class="bad" title="Critical alerts waiting"><b>${fmt.n(t.critical)}</b> critical</span>` : ''}
          </div>
          <div class="ht-trend">${spark(t.hourly, { w: 150, h: 22 })}<span class="ht-cams" title="Cameras seen · offline · frame sync failed">${icon('camera', 's')} ${fmt.n(t.cameras)}${t.camerasOffline ? ` · <b class="bad">${fmt.n(t.camerasOffline)} off</b>` : ''}${t.syncFailed ? ` · <b class="warn">${fmt.n(t.syncFailed)} sync</b>` : ''}</span></div>
          <div class="ht-foot">${(t.topTypes || []).length ? esc(t.topTypes.join(' · ')) : '<span class="muted">no alerts in the window</span>'}${t.lastAlarmAt ? `<span class="muted"> · last ${fmt.rel(t.lastAlarmAt)}</span>` : ''}</div>
        </button>`;
    };

    const listHtml = (tiles) => `<div class="table-wrap"><table class="t mon-table"><thead><tr><th>State</th><th>Centre</th><th>City</th><th class="num">Today</th><th>Today by hour</th><th class="num">To review</th><th class="num">Critical</th><th class="num">Cameras</th><th class="num">Offline</th><th>Last alert</th></tr></thead><tbody>
      ${tiles.map((t) => {
    const [lab, ic] = STATE_LABEL[t.state];
    return `<tr class="link ${centre === t.key ? 'sel' : ''}" data-key="${esc(t.key)}" data-centre="${esc(t.key)}" tabindex="0">
        <td><span class="st-pill ${t.state}">${icon(ic, 's')} ${lab}</span></td>
        <td><div class="cell-2"><b>${esc(t.name && t.name !== t.code ? t.name : t.code)}</b><span class="l2 mono">${esc(t.code)}</span></div></td>
        <td>${esc(t.city || '—')}</td><td class="num"><b>${fmt.n(t.today)}</b></td><td>${spark(t.hourly, { w: 110, h: 18 })}</td>
        <td class="num ${t.toReview ? 'sla-approaching' : ''}">${fmt.n(t.toReview)}</td><td class="num ${t.critical ? 'sla-attention' : ''}">${fmt.n(t.critical)}</td>
        <td class="num">${fmt.n(t.cameras)}</td><td class="num ${t.camerasOffline ? 'sla-attention' : ''}">${fmt.n(t.camerasOffline)}</td>
        <td>${t.lastAlarmAt ? `<span class="muted">${fmt.rel(t.lastAlarmAt)}</span>` : '—'}</td></tr>`;
  }).join('')}</tbody></table></div>`;

    const paintBoard = () => {
      const f = board.freshness;
      const n = board.toReview;
      const c = $('#m-count', el);
      c.textContent = n ? `${fmt.n(n)} to review` : 'All reviewed';
      c.classList.toggle('zero', !n);
      $('#m-fresh', el).innerHTML = `· <span class="dot ${f.state === 'live' ? 'live' : esc(f.state)}" style="display:inline-block;vertical-align:middle"></span> ${session.mode === 'demo' ? 'DEMO' : 'LIVE'} · Camview read ${f.lastSuccessAt ? fmt.time(f.lastSuccessAt) : '—'}${f.lastError ? ` · <span class="sla-attention">refresh failed: ${esc(f.lastError.message)}</span>` : ''}`;
      $('#m-rule', el).textContent = `${board.counts.alarm} alarm · ${board.counts.warning} warning · ${board.counts.ok} OK`;
      $('#m-rule', el).title = board.rule;
      $('#m-allc', el).classList.toggle('hidden', !centre);
      const all = board.tiles.filter((t) => !t.unmapped);
      const sum = (k) => all.reduce((s, t) => s + (t[k] || 0), 0);
      morph($('#m-kpis', el), [
        kpi({ label: 'Centres in alarm', value: board.counts.alarm, accent: board.counts.alarm ? 'critical' : '', icon: 'alert', title: board.rule, sub: 'critical / high waiting' }),
        kpi({ label: 'Centres warning', value: board.counts.warning, accent: board.counts.warning ? 'warning' : '', icon: 'bang', sub: 'other alerts waiting' }),
        kpi({ label: 'Centres OK', value: board.counts.ok, accent: 'good', icon: 'check', sub: 'nothing waiting' }),
        kpi({ label: 'To review', value: n, accent: n ? 'warning' : '', icon: 'clock', sub: `${fmt.n(sum('critical'))} critical` }),
        kpi({ label: 'Alerts today', value: sum('today'), icon: 'zap', sub: `across ${all.length} centres` }),
        kpi({ label: 'Cameras offline', value: sum('camerasOffline'), accent: sum('camerasOffline') ? 'critical' : '', icon: 'camera', href: '#/monitoring?tab=health&hf=offline', sub: `${fmt.n(sum('camerasOnline'))} online · ${fmt.n(sum('syncFailed'))} sync failed` }),
      ].join(''));
      const cnt = { all: board.tiles.length, alarm: board.counts.alarm, warning: board.counts.warning, ok: board.counts.ok, offline: board.tiles.filter((t) => t.camerasOffline > 0).length };
      STATE_FILTERS.forEach(([k]) => { const x = $(`[data-sfn="${k}"]`, el); if (x) x.textContent = cnt[k]; });
      const cities = [...new Set(board.tiles.map((t) => t.city).filter(Boolean))].sort();
      const cs = $('#m-city', el);
      const sig = cities.join('|');
      if (cs.dataset.sig !== sig) {
        cs.innerHTML = '<option value="">All cities</option>' + cities.map((x) => `<option value="${esc(x)}">${esc(x)} (${board.tiles.filter((t) => t.city === x).length})</option>`).join('');
        cs.dataset.sig = sig;
      }
      if (document.activeElement !== cs) cs.value = city;
      cs.classList.toggle('hidden', !cities.length);
      const tiles = filtered();
      const shown = expanded || view === 'list' ? tiles : tiles.slice(0, COLLAPSED);
      const more = tiles.length - shown.length;
      const pager = more > 0 ? `<div class="mon-more"><button class="btn sm" data-expand>${icon('down', 's')} Show all ${tiles.length} centres</button></div>`
        : expanded && view === 'tiles' && tiles.length > COLLAPSED ? '<div class="mon-more"><button class="btn sm ghost" data-expand>Show fewer</button></div>' : '';
      morph($('#m-board', el), !board.tiles.length
        ? empty('No centres', 'Centres appear here from the live Camview data or the imported nomenclature.', 'tree')
        : !tiles.length ? empty('No centres match', 'Change the filter, city or search.', 'filter')
          : `${view === 'list' ? listHtml(shown) : `<div class="health-grid">${shown.map(tileHtml).join('')}</div>`}${pager}
             <div class="muted mon-rule">${icon('info', 's')} ${esc(board.rule)}</div>`);
    };

    // ------------------------------------------------------------------ list
    const params = () => {
      const q = { projectId: pid, size: 100, sort: tab === 'toReview' ? 'priority' : 'lastInstance', dir: tab === 'toReview' ? 'asc' : 'desc' };
      const quick = [];
      if (tab !== 'all') quick.push(tab);
      if (centre === '__unmapped__') quick.push('unmapped');
      else if (centre) q.centre = centre;
      if (quick.length) q.quick = quick.join(',');
      if (search) q.search = search;
      return q;
    };

    const loadCounts = async () => {
      await Promise.all(TABS.map(async ([k]) => {
        const q = { ...params(), size: 1 };
        q.quick = [k !== 'all' ? k : null, centre === '__unmapped__' ? 'unmapped' : null].filter(Boolean).join(',') || undefined;
        try {
          const r = await api.get('/api/alarms', q);
          const n = $(`[data-n="${k}"]`, el);
          if (n) n.textContent = r.totalElements;
        } catch { /* counts are best effort */ }
      }));
    };

    const loadList = async ({ keepSelection = true } = {}) => {
      if (listCtrl) listCtrl.abort();
      listCtrl = new AbortController();
      try {
        list = await api.get('/api/alarms', params(), { signal: listCtrl.signal });
        if (ctx.isStale()) return;
        paintList();
        if (!keepSelection || !list.items.some((a) => a.alarmId === selectedId)) {
          if (!selectedId || !keepSelection) select(list.items[0]?.alarmId || null);
        }
        loadCounts();
      } catch (e) {
        if (e.name === 'AbortError' || ctx.isStale()) return;
        if (!list) $('#m-rows', el).innerHTML = errorBox(e);
        else toast(`Refresh failed — showing last data. ${e.message}`, 'error');
      }
    };

    const paintList = () => {
      const tileName = centre ? (board?.tiles.find((t) => t.key === centre)?.code || centre) : '';
      $('#m-filter', el).innerHTML = `${list.totalElements} alarm${list.totalElements === 1 ? '' : 's'}${tileName ? ` in <b>${esc(tileName)}</b> <button class="btn sm ghost" id="m-clearc">clear</button>` : ''}${search ? ` matching “${esc(search)}”` : ''}${list.totalElements > list.items.length ? ` · showing newest ${list.items.length}` : ''}`;
      $('#m-clearc', el)?.addEventListener('click', () => setCentre(''));
      const rows = $('#m-rows', el);
      if (!list.items.length) {
        morph(rows, empty(tab === 'toReview' ? 'Nothing to review' : 'No alarms here',
          tab === 'toReview' ? 'Every alarm has been reviewed. New alarms appear here automatically.' : 'No alarms match this view.', 'check'));
        return;
      }
      morph(rows, list.items.map((a) => {
        const isNew = !firstLoad && !seen.has(a.alarmId);
        const path = (a.context?.path || []);
        const where = ['room', 'centre'].map((lvl) => path.find((n) => n.level === lvl)).filter(Boolean)
          .map((n) => (n.level === 'room' ? 'Room ' + n.code : n.code)).join(' · ') || 'location not mapped';
        return `<div class="mrow ${a.alarmId === selectedId ? 'sel' : ''} ${isNew ? 'new' : ''}" role="option" tabindex="0" aria-selected="${a.alarmId === selectedId}" data-id="${esc(a.alarmId)}" data-key="${esc(a.alarmId)}">
          ${thumb(a)}
          <div style="min-width:0;flex:1"><div class="t1">${esc(a.alarmTypeName)}${a.flags?.new || isNew ? '<span class="b new">NEW</span>' : ''}</div>
            <div class="t2"><span class="mono">${esc(a.locationLabel || a.cameraCode)}</span>${a.totalTimesReported > 1 ? ` · <b>${a.totalTimesReported}×</b>` : ''}</div>
            <div class="t3">${priorityBadge(a.priority)}${healthChips(a.health, 'camera')}${a.evidence?.video ? `<span class="ev-av yes">${icon('video', 's')} video</span>` : ''}</div></div>
          <div class="when">${fmt.time(a.lastInstance)}<br>${fmt.rel(a.lastInstance)}</div></div>`;
      }).join(''));
      list.items.forEach((a) => seen.add(a.alarmId));
      firstLoad = false;
    };

    // ------------------------------------------------------------------ detail
    const select = async (id) => {
      selectedId = id;
      ctx.setQuery({ alarm: id || '' });
      $$('.mrow', el).forEach((r) => { r.classList.toggle('sel', r.dataset.id === id); r.setAttribute('aria-selected', r.dataset.id === id); });
      // keep the selected row visible INSIDE the list only — never scroll the page (the board would be cut off)
      const row = $(`.mrow[data-id="${CSS.escape(id || '')}"]`, el), box_ = $('#m-rows', el);
      if (row && box_) {
        const top = row.offsetTop, bottom = top + row.offsetHeight;          // .rows is the offset parent
        if (top < box_.scrollTop) box_.scrollTop = top;
        else if (bottom > box_.scrollTop + box_.clientHeight) box_.scrollTop = bottom - box_.clientHeight;
      }
      const box = $('#m-detail', el);
      if (!id) { box.innerHTML = empty('Select an alarm', 'Pick an alarm on the left.', 'investigate'); return; }
      box.innerHTML = `<div class="card-b">${skeleton(5, 30)}</div>`;
      try {
        detail = await api.get(`/api/alarms/${encodeURIComponent(id)}`, { projectId: pid });
        if (ctx.isStale() || selectedId !== id) return;
        paintDetail();
      } catch (e) {
        box.innerHTML = errorBox(e, { retry: false });
      }
    };

    const canDecide = () => can('alarm.validate') || can('alarm.invalidate') || can('alarm.exception');

    const paintDetail = () => {
      const a = detail.alarm;
      const ev = detail.evidenceItems || [];
      const first = ev.find((x) => x.kind === 'image');
      const clip = ev.find((x) => x.kind === 'video');
      const isEvent = a.eventKind === 'camera_status';
      const decided = { marked_valid: 'Valid', marked_invalid: 'Invalid', marked_exception: 'Exception' }[a.review?.status];
      $('#m-detail', el).innerHTML = `
        <div class="card-h"><div><h3>${esc(a.alarmTypeName)}</h3><div class="sub"><span class="mono">${esc(a.alarmId)}</span> · ${fmt.dt(a.lastInstance)} · ${fmt.rel(a.lastInstance)}</div></div>
          <div class="actions">${priorityBadge(a.priority)}${a.ticket && a.ticket.status === 'open' && a.ticket.deliveryStatus === 'delivered' ? `<span class="b vis-shared" title="Sent to the client">${icon('share')}delivered</span>` : ''}</div></div>
        <div class="card-b">
          ${clip ? `<div class="shot video"><video src="${esc(clip.url)}" controls playsinline preload="metadata" ${first ? `poster="${esc(first.url)}"` : ''}></video></div>` : ''}
          ${clip ? `<div class="mon-evbar"><span class="muted">${icon('video', 's')} Video clip${first ? ' · frame shown before play' : ''}</span><button class="btn sm" id="m-shot" type="button">${icon('image', 's')} ${ev.length > 1 ? `All evidence (${ev.length})` : 'Full screen'}</button></div>` : ''}
          <button class="shot ${clip ? 'hidden' : ''}" id="${clip ? 'm-shot-img' : 'm-shot'}" ${ev.length ? '' : 'disabled'} aria-label="Open evidence">
            ${first ? `<img src="${esc(first.url)}" alt="Evidence for ${esc(a.alarmId)}">` : ev.length ? `${icon('video', 'l')}<span>Video evidence — click to play</span>` : isEvent ? `${icon('camera', 'l')}<span>Camera status event — ${esc(a.alarmEvent?.reason || a.alarmTypeName)}${a.alarmEvent?.status ? ` (${esc(a.alarmEvent.status)})` : ''}. No image or video is attached to these.</span>` : `${icon('image', 'l')}<span>No image or video attached</span>`}
            ${ev.length > 1 && !clip ? `<span class="count b outline" style="background:var(--panel)">${ev.length} items</span>` : first && clip ? '<span class="count b outline" style="background:var(--panel)">frame · enlarge</span>' : ''}
          </button>
          <div class="mon-facts">
            <span class="mono" title="Location">${icon('building', 's')} ${esc(a.locationLabel || a.cameraCode)}</span>
            ${a.centreName ? `<span title="Centre">${esc(a.centreName)}</span>` : ''}
            ${healthChips(a.health, 'camera')}
            ${a.exam ? `<span title="Exam">${icon('report', 's')} ${esc(a.exam.name)}</span>` : ''}
          </div>
          <div style="margin:10px 0 6px">${contextPath(a.context?.path || [])}</div>
          <dl class="kv" style="grid-template-columns:120px 1fr">
            <dt>When</dt><dd class="num">${fmt.dt(a.firstInstance)}${a.lastInstance && a.lastInstance !== a.firstInstance ? ` → ${fmt.time(a.lastInstance)}` : ''} <span class="muted">(${fmt.rel(a.lastInstance)})</span></dd>
            <dt>Camera</dt><dd>${esc(a.cameraCode)}${a.cameraName && a.cameraName !== a.cameraCode ? ` <span class="muted">${esc(a.cameraName)}</span>` : ''}</dd>
            <dt>Reported</dt><dd>${a.totalTimesReported} time${a.totalTimesReported === 1 ? '' : 's'}</dd>
            <dt>Camview status</dt><dd>${stateBadge(a.lastActionType, a.lastActionLabel)}</dd>
            ${a.ticketId ? `<dt>Ticket</dt><dd>#${esc(a.ticketId)}</dd>` : ''}
            ${decided ? `<dt>Your team marked</dt><dd><b>${decided}</b> <span class="muted">by ${esc(a.review.by || '')} · ${fmt.rel(a.review.at)}</span></dd>` : ''}
          </dl>
          ${isEvent ? `<div class="banner info" style="margin-top:12px">${icon('camera')}<div>Camera status event — counted in the Cameras KPI and camera health. It is not decided and never becomes a ticket.</div></div>` : canDecide() ? `
            <input class="input" id="m-note" placeholder="Optional note (internal)" style="width:100%;margin-top:12px">
            <div class="decide">
              ${can('alarm.validate') ? `<button class="btn good" data-mark="mark_valid" ${a.review?.status === 'marked_valid' ? 'disabled' : ''}>${icon('check')} Valid <span class="kbd" style="margin-left:4px">V</span></button>` : ''}
              ${can('alarm.invalidate') ? `<button class="btn invalid" data-mark="mark_invalid" ${a.review?.status === 'marked_invalid' ? 'disabled' : ''}>${icon('x')} Invalid <span class="kbd" style="margin-left:4px">I</span></button>` : ''}
              ${can('alarm.exception') ? `<button class="btn exception" data-mark="mark_exception" ${a.review?.status === 'marked_exception' ? 'disabled' : ''}>${icon('bang')} Exception <span class="kbd" style="margin-left:4px">E</span></button>` : ''}
            </div>
            ${decided && can('alarm.validate') ? '<button class="btn ghost sm" data-mark="reopen" style="margin-top:8px">Undo — move back to review</button>' : ''}`
          : '<div class="banner info" style="margin-top:12px">' + icon('lock') + '<div>You can view alarms but not mark them.</div></div>'}
          <div class="row" style="margin-top:14px"><a class="btn sm" href="#/investigations/${encodeURIComponent(a.alarmId)}?projectId=${esc(a.projectId)}">${icon('investigate', 's')} Full details</a>
            <span class="muted" style="font-size:11px">Marks are saved in Command Center; Camview’s own record is not changed.</span></div>
        </div>`;
      $('#m-shot', el)?.addEventListener('click', () => lightbox(ev, 0, { alarmId: a.alarmId, canDownload: detail.canDownloadEvidence, title: `${a.alarmId} · ${a.cameraCode}` }));
      $('#m-shot img', el)?.addEventListener('error', (e) => { e.target.replaceWith(Object.assign(document.createElement('span'), { textContent: 'Image could not be loaded' })); }, { once: true });
    };

    const mark = async (action) => {
      if (!detail || busy) return;
      const btn = $(`[data-mark="${action}"]`, el);
      if (!btn || btn.disabled) return;
      busy = true;
      const id = detail.alarm.alarmId;
      const idx = list.items.findIndex((x) => x.alarmId === id);
      try {
        const note = $('#m-note', el)?.value.trim() || null;
        const result = { mark_valid: 'valid', mark_invalid: 'invalid', mark_exception: 'exception' }[action];
        if (result) {
          // the same decision as on the Alerts page: VALID creates the ticket and delivers it to the exam's client
          const r = await api.post(`/api/queue/${encodeURIComponent(id)}/decide`, { result, note, projectId: pid });
          if (result === 'valid' && r.delivery?.status === 'needs_client') toast('This exam has several clients — choose one on the alert page', 'warning');
        } else {
          await api.post(`/api/alarms/${encodeURIComponent(id)}/review`, { action, note, projectId: pid });
        }
        toast({ mark_valid: 'Marked valid', mark_invalid: 'Marked invalid', mark_exception: 'Marked exception', reopen: 'Moved back to review' }[action], 'success');
        // move on to the next alarm in the list (the reviewed one leaves "To review")
        const next = list.items[idx + 1]?.alarmId || list.items[idx - 1]?.alarmId || null;
        selectedId = tab === 'toReview' && action !== 'reopen' ? next : id;
        await Promise.all([loadList({ keepSelection: true }), loadBoard()]);
        select(selectedId && list.items.some((x) => x.alarmId === selectedId) ? selectedId : list.items[0]?.alarmId || null);
      } catch (e) {
        toast(e.message, 'error');
      } finally { busy = false; }
    };

    // ------------------------------------------------------------------ events
    const setCentre = (c) => { centre = c; ctx.setQuery({ centre }); paintBoard(); loadList({ keepSelection: false }); };
    ctx.onCleanup(delegate(el, 'click', '[data-centre]', (e, b) => setCentre(centre === b.dataset.centre ? '' : b.dataset.centre)));
    ctx.onCleanup(delegate(el, 'keydown', 'tr[data-centre]', (e, b) => { if (e.key === 'Enter') setCentre(centre === b.dataset.centre ? '' : b.dataset.centre); }));
    ctx.onCleanup(delegate(el, 'click', '[data-sf]', (e, b) => {
      sf = b.dataset.sf; ctx.setQuery({ state: sf === 'all' ? '' : sf });
      $$('[data-sf]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
      if (board) paintBoard();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-view]', (e, b) => {
      view = b.dataset.view; setPref('monView', view);
      $$('[data-view]', el).forEach((x) => x.classList.toggle('on', x === b));
      if (board) paintBoard();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-expand]', () => { expanded = !expanded; setPref('monExpanded', expanded); if (board) paintBoard(); }));
    $('#m-city', el).addEventListener('change', (e) => { city = e.target.value; ctx.setQuery({ city }); if (board) paintBoard(); });
    $('#m-sort', el).addEventListener('change', (e) => { sort = e.target.value; setPref('monSort', sort); if (board) paintBoard(); });
    $('#m-allc', el).addEventListener('click', () => setCentre(''));
    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => {
      tab = b.dataset.tab; ctx.setQuery({ list: tab });
      $$('[data-tab]', el).forEach((x) => x.classList.toggle('on', x === b));
      loadList({ keepSelection: false });
    }));
    ctx.onCleanup(delegate(el, 'click', '.mrow', (e, r) => select(r.dataset.id)));
    ctx.onCleanup(delegate(el, 'click', '[data-mark]', (e, b) => mark(b.dataset.mark)));
    let t;
    $('#m-search', el).addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => { search = e.target.value.trim(); ctx.setQuery({ search }); if (board) paintBoard(); loadList({ keepSelection: false }); }, 300); });
    const keys = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName) || e.ctrlKey || e.metaKey || e.altKey) return;
      if (document.querySelector('.overlay, .lightbox, .palette')) return;
      const i = list?.items.findIndex((x) => x.alarmId === selectedId) ?? -1;
      const k = e.key.toLowerCase();
      if ((k === 'arrowdown' || k === 'j') && list?.items[i + 1]) { e.preventDefault(); select(list.items[i + 1].alarmId); }
      else if ((k === 'arrowup' || k === 'k') && i > 0) { e.preventDefault(); select(list.items[i - 1].alarmId); }
      else if (k === 'v') mark('mark_valid');
      else if (k === 'i') mark('mark_invalid');
      else if (k === 'e') mark('mark_exception');
    };
    document.addEventListener('keydown', keys);
    ctx.onCleanup(() => document.removeEventListener('keydown', keys));
    ctx.onCleanup(on('data', () => { if (!busy) { loadBoard(); loadList({ keepSelection: true }); } }));

    await Promise.all([loadBoard(), loadList({ keepSelection: !!selectedId })]);
    if (selectedId) select(selectedId);
  },
};
