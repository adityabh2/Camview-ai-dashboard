// pages/camera.js — CAMERA DETAIL (/cameras/:id)
// Overview · Alarm activity · History · Evidence · Related investigations · Context

import * as api from '../core/api.js';
import { can, currentProject } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { bar } from '../core/charts.js';
import { esc, icon, fmt, kpi, card, table, errorBox, skeleton, empty, bars, legend, contextPath, prov, delegate, toast,
  PRIORITY_COLORS, VERDICT_COLORS, $ } from '../core/ui.js';
import { columns, miniRow, investigateHref } from '../components/alarms.js';
import { lightbox } from '../components/evidence.js';

/** Watch / Unwatch toggle backed by /api/watchlist (server checks scope). */
export async function watchButton(host, { entityType, entityId, label, projectId }) {
  if (!host) return;
  let on = false;
  try {
    const r = await api.get('/api/watchlist');
    on = r.items.some((w) => w.entityType === entityType && String(w.entityId) === String(entityId));
  } catch { /* the button still works; state just starts as "Watch" */ }
  const paint = () => {
    if (!host.isConnected) return;
    host.innerHTML = `<button class="btn ${on ? 'primary' : ''}" aria-pressed="${on}" title="${on ? 'Stop watching' : 'Add to your watchlist — you get a notification when new alarms arrive'}">${icon('eye', 's')} ${on ? 'Watching' : 'Watch'}</button>`;
    host.querySelector('button').addEventListener('click', toggle);
  };
  const toggle = async () => {
    try {
      if (on) await api.post('/api/watchlist/remove', { entityType, entityId });
      else await api.post('/api/watchlist', { entityType, entityId, label, projectId });
      on = !on;
      paint();
      toast(on ? `Watching ${label}` : `Stopped watching ${label}`, 'success');
    } catch (e) { toast(e.message, 'error'); }
  };
  paint();
}

const SECTIONS = [['overview', 'Overview'], ['activity', 'Alarm activity'], ['history', 'History'], ['evidence', 'Evidence'],
  ['investigations', 'Related investigations'], ['context', 'Context']];

export default {
  async render(el, ctx) {
    const camId = ctx.params.id;
    const pid = ctx.query.projectId || currentProject();
    setTitle('Camera', `<a href="#/cameras">Cameras</a> / <span class="mono">${esc(camId)}</span>`);
    el.innerHTML = skeleton(8, 40);
    let d;

    const load = async () => {
      try {
        d = await api.get(`/api/cameras/${encodeURIComponent(camId)}`, { projectId: pid, tzOffset: -new Date().getTimezoneOffset() });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = e.status === 404
          ? `<div class="error-state">${icon('camera')}<div class="e-t">Camera not available</div><div>It may be outside your access scope or not part of this project.</div><a class="btn" href="#/cameras">Back to cameras</a></div>`
          : errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const paint = () => {
      const c = d.camera;
      const m = d.metrics;
      const ctxd = c.context || {};
      setTitle(`Camera ${c.code}`, `<a href="#/cameras">Cameras</a> / <span class="mono">${esc(c.code)}</span>`);
      const nav = `<div class="tabs" role="navigation" aria-label="Camera sections">${SECTIONS.map(([k, l]) => `<button class="tab" data-jump="${k}">${l}</button>`).join('')}</div>`;

      const overview = `<section id="sec-overview">
        <div class="kpis">
          ${kpi({ label: 'Alarms', value: m.total, sub: 'in current window', icon: 'layers' })}
          ${kpi({ label: 'Critical', value: m.critical, sub: `${m.criticalPending} pending`, icon: 'alert', accent: 'critical' })}
          ${kpi({ label: 'Pending', value: m.pending, icon: 'clock', accent: 'warning' })}
          ${kpi({ label: 'Occurrences', value: d.occurrences, sub: 'sum of totalTimesReported', icon: 'zap' })}
          ${kpi({ label: 'Peak period', value: d.peak ? `${String(d.peak.hour).padStart(2, '0')}:00` : '—', sub: d.peak ? `${d.peak.count} alarms in that hour of day` : 'no data', icon: 'clock' })}
          ${kpi({ label: 'With evidence', value: m.withEvidence, icon: 'image' })}
          ${kpi({ label: 'Shared with client', value: m.sharedWithClient, icon: 'share', accent: 'good' })}
        </div>
        <div class="grid g-main">
          ${card({ title: `${icon('clock')} Latest alarm`, flush: true, body: d.latest ? `<div class="list">${miniRow(d.latest)}</div>` : empty('No alarms from this camera', 'Nothing received in the monitored window.') })}
          ${card({ title: `${icon('tree')} Location`, actions: ctxd.mapped ? prov('derived') : prov('unavailable'), body: `${contextPath(ctxd.path || [])}<div class="muted" style="margin-top:8px;font-size:11.5px">${ctxd.mapped ? 'Resolved from nomenclature master data.' : 'This camera is not in the nomenclature master data.'}</div>` })}
        </div></section>`;

      const activity = `<section id="sec-activity" style="margin-top:14px"><div class="grid g-2">
        ${card({ title: `${icon('chart')} Last 24 hours`, sub: 'Alarms raised per hour, by priority',
          body: `${legend(['critical', 'high', 'medium', 'low'].map((k) => ({ label: k[0].toUpperCase() + k.slice(1), color: PRIORITY_COLORS[k] })))}<div class="chart"><canvas id="cam-hourly" aria-label="Hourly activity"></canvas></div>` })}
        ${card({ title: `${icon('calendar')} Last 14 days`, sub: 'Alarms raised per day, by Camview status',
          body: `${legend(['Valid', 'Pending', 'Invalid', 'Exception'].map((k) => ({ label: k, color: VERDICT_COLORS[k] })))}<div class="chart"><canvas id="cam-daily" aria-label="Daily activity"></canvas></div>` })}
      </div>
      <div class="grid g-3" style="margin-top:14px">
        ${card({ title: 'Priority distribution', actions: prov('direct'), body: bars(d.priorityDistribution.map((x) => ({ ...x, color: PRIORITY_COLORS[x.key] || 'var(--st-neutral)', sw: PRIORITY_COLORS[x.key] }))) })}
        ${card({ title: 'Alarm types', actions: prov('direct'), body: bars(d.typeDistribution) })}
        ${card({ title: 'Status (Camview)', actions: prov('direct'), body: bars(d.statusDistribution.map((x) => ({ ...x, color: VERDICT_COLORS[x.label] || 'var(--st-neutral)', sw: VERDICT_COLORS[x.label] }))) })}
      </div></section>`;

      const history = `<section id="sec-history" style="margin-top:14px">${card({ title: `${icon('history')} Alarm history`, sub: `${d.alarms.length} most recent alarms in the monitored window`, flush: true, body: '<div id="cam-table"></div>' })}</section>`;

      const evItems = [];
      d.evidence.forEach((e) => {
        (e.images || []).forEach((url, i) => evItems.push({ kind: 'image', index: i, url, alarmId: e.alarmId, at: e.at }));
        if (e.video) evItems.push({ kind: 'video', index: 0, url: e.video, alarmId: e.alarmId, at: e.at });
      });
      const evidence = `<section id="sec-evidence" style="margin-top:14px">${card({ title: `${icon('image')} Evidence`, sub: can('evidence.view') ? 'Click to open focus mode' : '',
        actions: prov('direct'),
        body: !can('evidence.view') ? empty('No access to evidence', 'Your role cannot view evidence.', 'lock')
          : evItems.length ? `<div class="gallery">${evItems.map((x, i) => `<button class="thumb" data-evi="${i}" aria-label="Open evidence ${i + 1} of ${esc(x.alarmId)}">${x.kind === 'video' ? `<span class="play">${icon('play', 'l')}</span>` : `<img src="${esc(x.url)}" alt="Evidence for ${esc(x.alarmId)}" loading="lazy">`}<span class="tag b outline" style="background:var(--panel)">${esc(x.alarmId)}</span></button>`).join('')}</div>`
            : empty('No evidence', 'No images or video attached to this camera’s alarms.', 'image') })}</section>`;

      const inv = `<section id="sec-investigations" style="margin-top:14px">${card({ title: `${icon('investigate')} Related investigations`, sub: 'Assigned or under investigation', flush: true,
        body: d.investigations.length ? `<div class="list">${d.investigations.map((a) => miniRow(a)).join('')}</div>` : empty('No investigations', 'No alarm from this camera is assigned or under investigation.') })}</section>`;

      const levels = ['project', 'tc', 'centre', 'building', 'floor', 'room', 'camera'].filter((lvl) => ctxd[lvl]);   // only levels that exist
      const context = `<section id="sec-context" style="margin-top:14px">${card({ title: `${icon('tree')} Nomenclature context`, actions: ctxd.mapped ? prov('derived') : prov('unavailable'),
        body: `<dl class="kv">${levels.map((lvl) => {
          const n = ctxd[lvl];
          const link = n.id ? `<a class="mono" href="#/context?node=${encodeURIComponent(n.id)}&projectId=${encodeURIComponent(pid)}">${esc(n.code)}</a>` : `<span class="mono">${esc(n.code)}</span>`;
          return `<dt>${lvl.toUpperCase()}</dt><dd>${link}${n.name && n.name !== n.code ? ` <span class="muted">${esc(n.name)}</span>` : ''}${n.unmapped ? ' <span class="prov unavailable">UNMAPPED</span>' : ''}</dd>`;
        }).join('')}</dl>` })}</section>`;

      el.innerHTML = `
        <div class="page-head"><div class="row" style="gap:12px"><a class="btn icon" href="#/cameras" aria-label="Back to cameras">${icon('left')}</a>
          <div><div class="muted" style="font-size:10.5px;letter-spacing:.14em;font-weight:800">CAMERA</div><h2 class="mono">${esc(c.code)}</h2>
          <div class="dim">${esc(c.name || '')}</div></div></div>
          <div class="row"><span id="cam-watch"></span><a class="btn" href="#/live?camera=${encodeURIComponent(c.cameraId)}">${icon('live', 's')} Open in Live Operations</a></div></div>
        ${nav}${overview}${activity}${history}${evidence}${inv}${context}`;

      watchButton($('#cam-watch', el), { entityType: 'camera', entityId: String(c.cameraId), label: c.code, projectId: pid });

      table($('#cam-table', el), {
        columns: columns(['priority', 'alarm', 'state', 'workflow', 'visibility', 'occurrences', 'shift', 'last', 'evidence']),
        rows: d.alarms,
        onRow: (a) => { location.hash = investigateHref(a); },
        emptyHtml: empty('No alarms', 'No alarms from this camera in the monitored window.'),
      });

      $('.gallery', el)?.querySelectorAll('img').forEach((img) => img.addEventListener('error', () => {
        const b = img.closest('.thumb'); b.classList.add('broken'); b.disabled = true; b.innerHTML = `${icon('alert')}<span>Evidence unavailable</span>`;
      }, { once: true }));

      bar($('#cam-hourly', el), {
        labels: d.hourly.map((h) => new Date(h.start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })),
        series: ['critical', 'high', 'medium', 'low'].map((k) => ({ label: k, data: d.hourly.map((h) => h[k]), color: PRIORITY_COLORS[k] })),
        stacked: true,
      });
      bar($('#cam-daily', el), {
        labels: d.daily.map((x) => new Date(x.date + 'T00:00:00').toLocaleDateString(undefined, { day: '2-digit', month: 'short' })),
        series: [['valid', 'Valid'], ['pending', 'Pending'], ['invalid', 'Invalid'], ['exception', 'Exception']]
          .map(([k, l]) => ({ label: l, data: d.daily.map((x) => x[k]), color: VERDICT_COLORS[l] })),
        stacked: true,
      });

      ctx.onCleanup(delegate(el, 'click', '[data-evi]', (e, b) => {
        const i = +b.dataset.evi;
        const it = evItems[i];
        const sameAlarm = evItems.filter((x) => x.alarmId === it.alarmId);
        lightbox(sameAlarm, sameAlarm.indexOf(it), { alarmId: it.alarmId, canDownload: can('evidence.download'), title: `${it.alarmId} · ${c.code}` });
      }));
    };

    ctx.onCleanup(delegate(el, 'click', '[data-jump]', (e, b) => {
      document.getElementById(`sec-${b.dataset.jump}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
    await load();
  },
};
