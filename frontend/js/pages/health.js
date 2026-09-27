// pages/health.js — camera connection & recording health from the REAL health source only.
// In live mode that source is Camview itself (camera.frameSyncStatus / lastFrameSync sent with every alarm);
// a monitoring system can push recording status as well. Nothing here is inferred from alarm names.
//
// Problems first. Filter chips (offline / sync failed / stale / online), search by camera, centre or city,
// grouped by centre or as one list, 50 per page. Updates in place when the live data changes.

import * as api from '../core/api.js';
import { on, pref, setPref } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, empty, errorBox, skeleton, morph, delegate, debounce, $, $$ } from '../core/ui.js';
import { healthChips } from '../components/queue.js';

const BANNERS = {
  none: (s) => `<div class="banner warning">${icon('bang')}<div class="grow"><b>NO CAMERA STATUS YET</b> — ${esc(s.note)}</div></div>`,
  camview: (s) => `<div class="banner info hb-note">${icon('info')}<div class="grow"><b>Camera connection as Camview reports it</b> <span class="muted">— ${esc(s.note)}</span></div></div>`,
  push: () => '',
};
const SIZE = 50;

const kindOf = (h) => {
  const c = h.conditions || [];
  if (h.camera?.state === 'offline') return 'offline';
  if (c.includes('FRAME_SYNC_FAILED')) return 'sync';
  if (c.includes('CAMERA_ONLINE_NO_RECORDING')) return 'norec';
  if (c.includes('HEARTBEAT_STALE')) return 'stale';
  if (h.camera?.state === 'online') return 'online';
  return 'unknown';
};

export default {
  async render(el, ctx) {
    setTitle('Camera & recording health');
    let data = null;
    let hf = ctx.query.hf || 'problems';            // problems | offline | sync | stale | online | all
    let search = ctx.query.hs || '';
    let group = pref('healthGroup', true);
    let page = 1;
    const open = new Set();

    el.innerHTML = `<div id="hb-banner"></div><div class="kpis" id="hb-kpis">${skeleton(1, 70)}</div>
      <section class="card">
        <div class="card-h"><h3>${icon('camera')} Cameras</h3><div class="sub" id="hb-sub"></div></div>
        <div class="mon-toolbar">
          <div class="seg" role="group" aria-label="Show cameras" id="hb-chips"></div>
          <span class="grow"></span>
          <input class="input sm hb-search" id="hb-search" data-page-search placeholder="Camera, centre, city…  ( / )" value="${esc(search)}">
          <div class="seg" role="group" aria-label="Layout"><button type="button" data-group="1" class="${group ? 'on' : ''}">By centre</button><button type="button" data-group="0" class="${group ? '' : 'on'}">All cameras</button></div>
        </div>
        <div class="card-b flush" id="hb-body">${skeleton(8, 30)}</div><div id="hb-pager"></div></section>`;

    const rows = () => {
      const q = search.toLowerCase();
      return data.items.filter((r) => {
        const k = kindOf(r.health);
        const okKind = hf === 'all' || (hf === 'problems' ? !['online', 'unknown'].includes(k) : k === hf);
        return okKind && (!q || [r.label, r.code, r.cameraNumber, r.centre, r.centreName, r.city, r.subLocation, r.cameraId]
          .some((v) => String(v || '').toLowerCase().includes(q)));
      });
    };

    const camRow = (r) => `<tr class="link" tabindex="0" data-key="${esc(r.projectId)}:${esc(r.cameraId)}" data-cam="${esc(r.cameraId)}" data-pid="${esc(r.projectId)}">
        <td><div class="cell-2"><span class="mono" style="font-weight:600">${esc(r.cameraNumber || r.code)}</span><span class="l2">${esc([r.subLocation, `Camview id ${r.cameraId}`].filter(Boolean).join(' · '))}</span></div></td>
        ${group ? '' : `<td><div class="cell-2"><span>${esc(r.centreName || r.centre || '—')}</span><span class="l2">${esc([r.centre, r.city].filter(Boolean).join(' · '))}</span></div></td>`}
        <td>${healthChips(r.health)}</td>
        <td>${r.health.recording.lastRecordingAt ? `<div class="cell-2"><span class="num">${fmt.dt(r.health.recording.lastRecordingAt)}</span><span class="l2">${fmt.rel(r.health.recording.lastRecordingAt)}</span></div>` : '<span class="muted">never</span>'}</td>
        <td>${r.lastAlertAt ? `<span class="muted">${fmt.rel(r.lastAlertAt)}</span>` : '—'}</td>
        <td>${r.health.lastEvent ? `<span class="mono">${esc(r.health.lastEvent.type)}</span> <span class="muted">${fmt.rel(r.health.lastEvent.at)}</span>` : '—'}</td></tr>`;

    const head = (withCentre) => `<thead><tr><th>Camera</th>${withCentre ? '<th>Centre</th>' : ''}<th>Connection</th><th>${data.status.mode === 'camview' ? 'Last frame' : 'Last recording'}</th><th>Last alert</th><th>Last change</th></tr></thead>`;

    const paint = () => {
      const s = data.status, k = data.counts;
      morph($('#hb-banner', el), (BANNERS[s.mode] || BANNERS.none)(s));
      const fromCamview = s.mode === 'camview';
      morph($('#hb-kpis', el), [
        kpi({ label: 'Cameras reporting', value: data.items.length, icon: 'camera', sub: `in your projects · last report ${s.lastReportAt ? fmt.rel(s.lastReportAt) : '—'}` }),
        kpi({ label: 'Offline', value: k.offline, accent: k.offline ? 'critical' : '', icon: 'x', href: '#/monitoring?tab=health&hf=offline', sub: 'no frames now' }),
        fromCamview ? kpi({ label: 'Frame sync failed', value: k.syncFailed, accent: k.syncFailed ? 'warning' : '', icon: 'bang', href: '#/monitoring?tab=health&hf=sync', sub: 'Camview could not sync' }) : '',
        fromCamview ? '' : kpi({ label: 'Online, not recording', value: k.onlineNoRecording, accent: k.onlineNoRecording ? 'warning' : '', icon: 'bang' }),
        kpi({ label: 'Online', value: k.online, accent: 'good', icon: 'check', href: '#/monitoring?tab=health&hf=online', sub: `${data.items.length ? Math.round((k.online / data.items.length) * 100) : 0}% of reporting` }),
        kpi({ label: 'Status stale', value: k.stale, icon: 'clock', href: '#/monitoring?tab=health&hf=stale', sub: `no report > ${Math.round(s.staleSeconds / 60)} min` }),
      ].join(''));
      const all = data.items.map((r) => kindOf(r.health));
      const n = (x) => all.filter((y) => y === x).length;
      const chips = [['problems', 'Problems', all.filter((x) => !['online', 'unknown'].includes(x)).length], ['offline', 'Offline', n('offline')],
        ['sync', 'Sync failed', n('sync')], ['stale', 'Stale', n('stale')], ['online', 'Online', n('online')], ['all', 'All', all.length]];
      morph($('#hb-chips', el), chips.map(([key, l, c]) => `<button type="button" data-hf="${key}" data-key="${key}" class="${key === hf ? 'on' : ''} hf-${key}" aria-pressed="${key === hf}">${l} <span class="n">${fmt.n(c)}</span></button>`).join(''));
      const list = rows();
      $('#hb-sub', el).textContent = `${fmt.n(list.length)} camera${list.length === 1 ? '' : 's'} · problems first${fromCamview ? ' · refreshed with the live feed' : ''}`;
      const body = $('#hb-body', el);
      if (!list.length) {
        morph(body, empty(hf === 'problems' ? 'No camera problems' : 'No cameras here', hf === 'problems' ? 'Every reporting camera is online.' : 'Change the filter or the search.', 'camera'));
        $('#hb-pager', el).innerHTML = '';
        return;
      }
      if (group) {
        const by = new Map();
        list.forEach((r) => {
          const key = `${r.projectId}|${r.centre || r.centreName || '—'}`;
          if (!by.has(key)) by.set(key, { key, name: r.centreName || r.centre || 'Centre not known', code: r.centre, city: r.city, project: r.project, items: [] });
          by.get(key).items.push(r);
        });
        const groups = [...by.values()];
        const pages = Math.max(1, Math.ceil(groups.length / 20));
        page = Math.min(page, pages);
        morph(body, `<div class="hb-groups">${groups.slice((page - 1) * 20, page * 20).map((g) => {
          const kinds = g.items.map((r) => kindOf(r.health));
          const isOpen = open.has(g.key) || groups.length === 1;
          return `<div class="hb-group ${isOpen ? 'open' : ''}" data-key="${esc(g.key)}">
            <button class="hb-ghead" data-toggle="${esc(g.key)}" aria-expanded="${isOpen}">${icon(isOpen ? 'down' : 'right', 's')}
              <div class="grow" style="min-width:0"><b>${esc(g.name)}</b><div class="muted hb-gsub">${esc([g.code, g.city, g.project].filter(Boolean).join(' · '))}</div></div>
              ${kinds.includes('offline') ? `<span class="h-chip bad">${kinds.filter((x) => x === 'offline').length} OFFLINE</span>` : ''}
              ${kinds.includes('sync') ? `<span class="h-chip warn">${kinds.filter((x) => x === 'sync').length} SYNC FAILED</span>` : ''}
              ${kinds.includes('stale') ? `<span class="h-chip warn">${kinds.filter((x) => x === 'stale').length} STALE</span>` : ''}
              ${kinds.includes('online') ? `<span class="h-chip good">${kinds.filter((x) => x === 'online').length} ONLINE</span>` : ''}
              <span class="muted num">${g.items.length} cam</span></button>
            ${isOpen ? `<div class="table-wrap"><table class="t compact">${head(false)}<tbody>${g.items.map(camRow).join('')}</tbody></table></div>` : ''}
          </div>`;
        }).join('')}</div>`);
        pagerHtml(pages, groups.length, 'centres');
      } else {
        const pages = Math.max(1, Math.ceil(list.length / SIZE));
        page = Math.min(page, pages);
        morph(body, `<div class="table-wrap"><table class="t">${head(true)}<tbody>${list.slice((page - 1) * SIZE, page * SIZE).map(camRow).join('')}</tbody></table></div>`);
        pagerHtml(pages, list.length, 'cameras');
      }
    };

    const pagerHtml = (pages, total, what) => {
      $('#hb-pager', el).innerHTML = pages > 1 ? `<div class="pager"><span>${fmt.n(total)} ${what}</span>
        <button class="btn sm" data-pg="${page - 1}" ${page <= 1 ? 'disabled' : ''} aria-label="Previous page">${icon('left', 's')}</button>
        <span class="num">${page} / ${pages}</span>
        <button class="btn sm" data-pg="${page + 1}" ${page >= pages ? 'disabled' : ''} aria-label="Next page">${icon('right', 's')}</button></div>` : '';
    };

    const load = async () => {
      try {
        data = await api.get('/api/camera-health/cameras');
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) { el.innerHTML = errorBox(e); $('[data-retry]', el)?.addEventListener('click', load); }
      }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-hf]', (e, b) => { hf = b.dataset.hf; page = 1; ctx.setQuery({ hf }); paint(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-group]', (e, b) => {
      group = b.dataset.group === '1'; page = 1; setPref('healthGroup', group);
      $$('[data-group]', el).forEach((x) => x.classList.toggle('on', x === b)); paint();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-toggle]', (e, b) => { const k = b.dataset.toggle; if (open.has(k)) open.delete(k); else open.add(k); paint(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-pg]', (e, b) => { page = +b.dataset.pg; paint(); el.scrollIntoView({ block: 'start' }); }));
    const go = (tr) => { location.hash = `#/cameras/${encodeURIComponent(tr.dataset.cam)}?projectId=${encodeURIComponent(tr.dataset.pid)}`; };
    ctx.onCleanup(delegate(el, 'click', 'tr[data-cam]', (e, tr) => go(tr)));
    ctx.onCleanup(delegate(el, 'keydown', 'tr[data-cam]', (e, tr) => { if (e.key === 'Enter') go(tr); }));
    $('#hb-search', el).addEventListener('input', debounce((e) => { search = e.target.value.trim(); page = 1; ctx.setQuery({ hs: search }); if (data) paint(); }, 200));
    ctx.onCleanup(on('data', load));
    await load();
  },
};
