// pages/presentation.js — PRESENTATION MODE (full screen, no shell).
// Internal users see internal metrics for their project; client users see ONLY
// client-visible metrics (served by /api/client/presentation from the client dataset).
// Two views: GRID (everything at once) and CYCLE (auto-advancing slides with
// Play / Pause / Next / Previous). Data freshness is always visible.

import * as api from '../core/api.js';
import { session, isClient, currentProject, can, pref, setPref } from '../core/state.js';
import { bar, destroyAll } from '../core/charts.js';
import { brand, brandMarkHtml, brandTitle, loadBranding } from '../core/layout.js';
import { esc, icon, fmt, errorBox, bars, legend, PRIORITY_COLORS, VERDICT_COLORS, priorityBadge, $, $$ } from '../core/ui.js';

const INTERNAL_SLIDES = [['overview', 'Overview'], ['activity', 'Activity'], ['priority', 'Priority & status'], ['shift', 'Shift'], ['cameras', 'Camera activity'], ['critical', 'Critical alerts']];
const CLIENT_SLIDES = [['overview', 'Overview'], ['priority', 'Priority'], ['recent', 'Recently shared']];
const PRIOS = ['critical', 'high', 'medium', 'low'];

export default {
  async render(el, ctx) {
    document.title = `Presentation · ${brandTitle()}`;
    loadBranding().then(() => { if (!ctx.isStale()) document.title = `Presentation · ${brandTitle()}`; });
    const client = isClient();
    const home = client ? '#/client' : '#/command';
    const SLIDES = client ? CLIENT_SLIDES : INTERNAL_SLIDES;
    el.innerHTML = `<div class="present"><div class="empty" style="min-height:60vh"><div class="e-t">Loading presentation…</div></div></div>`;
    let d = null;
    let ov = null;                                   // overview (top cameras) — optional, internal only
    let mode = ctx.query.view === 'cycle' ? 'cycle' : 'grid';
    let slide = Math.max(0, SLIDES.findIndex((s) => s[0] === ctx.query.slide));
    let playing = mode === 'cycle';
    let secs = [10, 15, 30, 60].includes(+pref('presentationCycle', 15)) ? +pref('presentationCycle', 15) : 15;
    let timer = null;

    const load = async (refresh = false) => {
      try {
        if (client) d = await api.get('/api/client/presentation');
        else {
          const [p, o] = await Promise.all([
            api.get('/api/presentation', { projectId: currentProject(), tzOffset: -new Date().getTimezoneOffset() }),
            can('dashboard.view') ? api.get('/api/overview', { projectId: currentProject(), tzOffset: -new Date().getTimezoneOffset() }).catch(() => null) : Promise.resolve(null),
          ]);
          d = p; ov = o;
        }
        if (!ctx.isStale()) paint(!refresh);
      } catch (e) {
        if (ctx.isStale()) return;
        if (!d) {
          el.innerHTML = `<div class="present">${errorBox(e)}<div style="text-align:center"><a class="btn" href="${home}">Exit</a></div></div>`;
          $('[data-retry]', el)?.addEventListener('click', load);
        }
      }
    };

    const tile = (label, value, color, ic, sub = '') => `<div class="p-kpi" style="box-shadow:inset 4px 0 0 ${color}"><div class="l">${icon(ic)}${esc(label)}</div><div class="v">${esc(fmt.n(value))}</div>${sub ? `<div class="muted" style="margin-top:6px">${sub}</div>` : ''}</div>`;

    const freshLine = () => {
      if (client) return `Last update ${esc(fmt.time(d.generatedAt))}`;
      const f = d.freshness;
      return `<span class="dot ${f.state === 'live' ? 'live' : f.state}" style="display:inline-block;vertical-align:middle;margin-right:6px"></span>${f.mode === 'demo' ? 'DEMO' : esc(f.state.toUpperCase())} · last successful update ${f.lastSuccessAt ? esc(fmt.time(f.lastSuccessAt)) : '—'}${f.lastError ? ` · <span class="sla-attention">refresh failed</span>` : ''}`;
    };

    const head = () => {
      const title = client ? (d.client?.name || 'Shared alerts') : (d.project?.code || 'Command Center');
      const sub = client ? 'Alerts shared with you by the operations team' : esc(d.project?.name || '');
      return `
      <div class="p-head">
        <span class="p-brand-mark" data-brand-mark="l">${brandMarkHtml(brand, 'l')}</span>
        <div class="grow"><div class="p-brand"><span data-brand-name>${esc(brand.name)}</span> · ${client ? 'CLIENT PORTAL' : `<span data-brand-sub>${esc(brand.subtitle)}</span>`}</div><h1>${esc(title)}</h1><div class="dim" style="font-size:15px;margin-top:4px">${sub}</div></div>
        <div style="text-align:right">
          ${session.mode === 'demo' ? '<div class="demo-flag" style="display:inline-block;margin-bottom:8px">DEMO DATA</div>' : ''}
          <div class="num" id="pr-clock" style="font-size:26px;font-weight:700"></div>
          <div class="muted" id="pr-fresh">${freshLine()}</div>
        </div></div>
      <div class="row no-print" style="margin:-10px 0 18px;gap:10px" role="toolbar" aria-label="Presentation controls">
        <div class="seg" role="group" aria-label="View"><button data-mode="grid" class="${mode === 'grid' ? 'on' : ''}">${icon('grid', 's')} Grid</button><button data-mode="cycle" class="${mode === 'cycle' ? 'on' : ''}">${icon('play', 's')} Auto-cycle</button></div>
        ${mode === 'cycle' ? `
          <button class="btn sm" data-nav="-1" aria-label="Previous slide">${icon('left', 's')} Prev</button>
          <button class="btn sm ${playing ? '' : 'primary'}" data-play aria-label="${playing ? 'Pause' : 'Play'}">${playing ? `${icon('pause', 's')} Pause` : `${icon('play', 's')} Play`}</button>
          <button class="btn sm" data-nav="1" aria-label="Next slide">Next ${icon('right', 's')}</button>
          <span class="row tight" role="tablist" aria-label="Slides">${SLIDES.map(([k, l], i) => `<button class="btn sm ${i === slide ? 'primary' : 'ghost'}" data-go="${i}" role="tab" aria-selected="${i === slide}" title="${esc(l)}" style="min-width:28px;padding:0 8px">${i + 1}</button>`).join('')}</span>
          <label class="row tight muted" style="font-size:12px">Every <select class="select" data-secs aria-label="Slide interval">${[10, 15, 30, 60].map((s) => `<option value="${s}" ${s === secs ? 'selected' : ''}>${s}s</option>`).join('')}</select></label>` : ''}
        <span class="grow"></span>
        <button class="btn sm" id="pr-fs">${icon('external', 's')} Fullscreen</button><a class="btn sm" href="${home}">${icon('x', 's')} Exit</a>
      </div>`;
    };

    // ------------------------------------------------------------- content blocks
    const kpiTiles = () => {
      if (client) {
        return `<div class="p-kpis">
          ${tile('Shared alerts', d.total, 'var(--vis-shared)', 'share')}
          ${tile('Critical shared', d.critical, 'var(--st-critical)', 'alert')}
          ${tile('Acknowledged', d.acknowledged, 'var(--v-valid)', 'check', d.total ? `${fmt.pct(d.acknowledged / d.total)} of shared` : '')}
          ${tile('Awaiting acknowledgement', d.total - d.acknowledged, 'var(--st-warning)', 'clock')}</div>`;
      }
      const m = d.metrics;
      return `<div class="p-kpis">
        ${tile('Total alarms', m.total, 'var(--accent)', 'layers', 'in monitored window')}
        ${tile('Critical', m.critical, 'var(--st-critical)', 'alert', `${m.criticalPending} pending`)}
        ${tile('Pending', m.pending, 'var(--v-pending)', 'clock')}
        ${tile('Valid', m.valid, 'var(--v-valid)', 'check', `false-alarm rate ${fmt.pct(m.falseAlarmRate)}`)}
        ${tile('Client shared', m.sharedWithClient, 'var(--vis-shared)', 'share', `${m.approvedForClient} approved · ${m.awaitingApproval} awaiting`)}</div>`;
    };
    const prioBars = () => bars(d.priorityDistribution.map((x) => ({ ...x, color: PRIORITY_COLORS[x.key] || 'var(--st-neutral)', sw: PRIORITY_COLORS[x.key] })));
    const statusBars = () => bars(d.statusDistribution.map((x) => ({ ...x, color: VERDICT_COLORS[x.label] || 'var(--st-neutral)', sw: VERDICT_COLORS[x.label] })));
    const activityChart = (h = 330) => `<div class="card-h"><h3>Activity — last 24 hours</h3><div class="actions">${legend(PRIOS.map((k) => ({ label: k, color: PRIORITY_COLORS[k] })))}</div></div><div class="card-b"><div class="chart lg" style="height:${h}px"><canvas id="pr-hourly" aria-label="Hourly activity"></canvas></div></div>`;
    const criticalList = (big) => (d.critical.length ? d.critical.map((a) => `<div class="li" style="${big ? 'font-size:16px;padding:14px 18px' : ''}">${priorityBadge(a.priority)}<div class="grow"><div class="t1">${esc(a.alarmTypeName)} <span class="mono muted" style="font-weight:400">${esc(a.alarmId)}</span></div><div class="t2">${esc((a.context?.path || []).map((n) => n.code).join(' / ') || a.cameraCode)} · ${fmt.rel(a.lastInstance)}</div></div></div>`).join('')
      : '<div class="empty"><div class="e-t">No critical alarms pending</div></div>');
    const tecTable = () => (d.byTc.length ? `<table class="t"><thead><tr><th>TC</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Pending</th></tr></thead><tbody>${d.byTc.map((r) => `<tr><td><span class="mono">${esc(r.code)}</span> <span class="muted">${esc(r.name && r.name !== r.code ? r.name : '')}</span></td><td class="num">${r.total}</td><td class="num">${r.critical}</td><td class="num">${r.pending}</td></tr>`).join('')}</tbody></table>` : '<div class="empty"><div class="e-t">No context mapped</div></div>');
    const cameraTable = () => {
      const cams = ov?.topCameras || [];
      if (!cams.length) return '<div class="empty"><div class="e-t">Camera activity unavailable</div></div>';
      const max = Math.max(1, ...cams.map((c) => c.count));
      return `<table class="t" style="font-size:15px"><thead><tr><th>Camera</th><th>Location</th><th style="width:30%">Activity</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Pending</th></tr></thead><tbody>
        ${cams.map((c) => `<tr><td class="mono">${esc(c.code)}</td><td class="dim">${esc(c.location || 'not mapped')}</td><td><span class="bar-track" style="display:block;height:10px"><span class="bar-fill" style="display:block;width:${Math.max(1, (c.count / max) * 100)}%"></span></span></td><td class="num">${c.count}</td><td class="num">${c.critical}</td><td class="num">${c.pending}</td></tr>`).join('')}</tbody></table>`;
    };
    const recentShared = (big) => (d.recent.length ? d.recent.map((a) => `<div class="li" style="font-size:${big ? 16 : 15}px">${priorityBadge(a.priority)}<div class="grow"><div class="t1">${esc(a.alarmTypeName)} <span class="mono muted">${esc(a.alarmId)}</span></div><div class="t2">${esc((a.context || []).map((c) => c.code).join(' / '))} · shared ${fmt.rel(a.sharedAt)}</div></div>${a.acknowledgedAt ? `<span class="b vis-shared">${icon('check')}acknowledged</span>` : '<span class="b vis-ready_for_review">awaiting</span>'}</div>`).join('')
      : '<div class="empty"><div class="e-t">No alerts shared yet</div></div>');

    const grid = () => {
      if (client) {
        return `${kpiTiles()}
          <div class="grid g-main">
            <section class="card"><div class="card-h"><h3>Recently shared</h3></div><div class="list">${recentShared(false)}</div></section>
            <section class="card"><div class="card-h"><h3>Priority</h3></div><div class="card-b">${prioBars()}</div></section>
          </div>
          <div class="muted" style="margin-top:18px;font-size:12px">Only information approved and shared with ${esc(d.client?.name || 'you')} is shown.</div>`;
      }
      return `${kpiTiles()}
        <div class="grid g-main">
          <section class="card">${activityChart()}</section>
          <div class="stack">
            <section class="card"><div class="card-h"><h3>Priority</h3></div><div class="card-b">${prioBars()}</div></section>
            <section class="card"><div class="card-h"><h3>Status</h3></div><div class="card-b">${statusBars()}</div></section>
          </div>
        </div>
        <div class="grid g-3" style="margin-top:14px">
          <section class="card"><div class="card-h"><h3>Shift</h3></div><div class="card-b">${bars(d.shiftDistribution)}</div></section>
          <section class="card"><div class="card-h"><h3>By TC</h3></div><div class="card-b flush">${tecTable()}</div></section>
          <section class="card"><div class="card-h"><h3>Critical pending</h3></div><div class="list">${criticalList(false)}</div></section>
        </div>`;
    };

    const slideHtml = () => {
      const [key, label] = SLIDES[slide];
      const frame = (body) => `<div class="p-slide" aria-live="polite" aria-label="Slide ${slide + 1} of ${SLIDES.length}: ${esc(label)}">
        <div class="row" style="margin-bottom:14px"><span class="p-brand" style="font-size:13px">${slide + 1} / ${SLIDES.length}</span><h2 style="margin:0;font-size:26px">${esc(label)}</h2>
        <span class="grow"></span><div class="bar-track" style="width:180px;height:4px" aria-hidden="true"><div id="pr-progress" class="bar-fill" style="width:0%;height:100%;transition:none"></div></div></div>${body}</div>`;
      if (client) {
        if (key === 'overview') return frame(`${kpiTiles()}<div class="muted" style="font-size:13px">Only information approved and shared with ${esc(d.client?.name || 'you')} is shown.</div>`);
        if (key === 'priority') return frame(`<section class="card"><div class="card-b" style="font-size:16px;padding:22px">${prioBars()}</div></section>`);
        return frame(`<section class="card"><div class="list">${recentShared(true)}</div></section>`);
      }
      if (key === 'overview') return frame(kpiTiles());
      if (key === 'activity') return frame(`<section class="card">${activityChart(440)}</section>`);
      if (key === 'priority') return frame(`<div class="grid g-2"><section class="card"><div class="card-h"><h3>Priority</h3></div><div class="card-b" style="font-size:16px;padding:22px">${prioBars()}</div></section><section class="card"><div class="card-h"><h3>Status (Camview)</h3></div><div class="card-b" style="font-size:16px;padding:22px">${statusBars()}</div></section></div>`);
      if (key === 'shift') return frame(`<div class="grid g-2"><section class="card"><div class="card-h"><h3>Shift</h3><div class="actions muted">labels as reported by Camview</div></div><div class="card-b" style="font-size:16px;padding:22px">${bars(d.shiftDistribution)}</div></section><section class="card"><div class="card-h"><h3>By TC</h3></div><div class="card-b flush" style="font-size:15px">${tecTable()}</div></section></div>`);
      if (key === 'cameras') return frame(`<section class="card"><div class="card-h"><h3>Most active cameras</h3><div class="actions muted">neutral activity measure</div></div><div class="card-b flush">${cameraTable()}</div></section>`);
      return frame(`<section class="card"><div class="list">${criticalList(true)}</div></section>`);
    };

    const drawCharts = () => {
      const c = $('#pr-hourly', el);
      if (!c || client) return;
      bar(c, {
        labels: d.hourly.map((h) => new Date(h.start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })),
        series: PRIOS.map((k) => ({ label: k, data: d.hourly.map((h) => h[k]), color: PRIORITY_COLORS[k] })),
        stacked: true,
      });
    };

    const renderStage = (reset = true) => {
      destroyAll();
      $('#pr-stage', el).innerHTML = mode === 'grid' ? grid() : slideHtml();
      drawCharts();
      if (reset) startProgress();
    };

    // progress bar + auto-advance
    let progStart = 0;
    const startProgress = () => {
      clearTimeout(timer);
      progStart = Date.now();
      const p = $('#pr-progress', el);
      if (p) p.style.width = '0%';
      if (mode === 'cycle' && playing) timer = setTimeout(() => go(1), secs * 1000);
    };
    const progTick = setInterval(() => {
      const p = $('#pr-progress', el);
      if (p && mode === 'cycle' && playing) p.style.width = `${Math.min(100, ((Date.now() - progStart) / (secs * 1000)) * 100)}%`;
    }, 250);

    const go = (delta, absolute) => {
      slide = absolute != null ? absolute : (slide + delta + SLIDES.length) % SLIDES.length;
      ctx.setQuery({ view: 'cycle', slide: SLIDES[slide][0] });
      paint();
    };

    const paint = (reset = true) => {
      el.innerHTML = `<div class="present">${head()}<div id="pr-stage"></div></div>`;
      renderStage(reset);
      tick();
    };

    const setMode = (m) => {
      mode = m;
      playing = m === 'cycle';
      ctx.setQuery({ view: m === 'cycle' ? 'cycle' : '', slide: m === 'cycle' ? SLIDES[slide][0] : '' });
      paint();
    };

    el.addEventListener('click', (e) => {
      const t = e.target.closest('button, a, select');
      if (!t) return;
      if (t.dataset.mode) setMode(t.dataset.mode);
      else if (t.dataset.nav) go(+t.dataset.nav);
      else if (t.dataset.go != null && t.dataset.go !== '') go(0, +t.dataset.go);
      else if (t.hasAttribute('data-play')) { playing = !playing; paint(); }
      else if (t.id === 'pr-fs') { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen?.(); }
    });
    el.addEventListener('change', (e) => {
      if (e.target.matches('[data-secs]')) { secs = +e.target.value; setPref('presentationCycle', secs); startProgress(); }
    });

    const tick = () => {
      const c = $('#pr-clock', el);
      if (c) c.textContent = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      const f = $('#pr-fresh', el);
      if (f && d) f.innerHTML = freshLine();
    };
    const clock = setInterval(tick, 1000);
    // background refresh (30 s) re-renders data but never resets the slide timer
    const refresh = setInterval(() => { if (!document.hidden) load(true); }, 30000);
    const onKey = (e) => {
      if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement?.tagName)) return;
      if (e.key === 'Escape' && !document.fullscreenElement) location.hash = home;
      else if (e.key === 'ArrowRight') { if (mode !== 'cycle') { mode = 'cycle'; playing = false; } go(1); }
      else if (e.key === 'ArrowLeft') { if (mode !== 'cycle') { mode = 'cycle'; playing = false; } go(-1); }
      else if (e.key === ' ' && mode === 'cycle') { e.preventDefault(); playing = !playing; paint(); }
    };
    document.addEventListener('keydown', onKey);
    ctx.onCleanup(() => { clearInterval(clock); clearInterval(refresh); clearInterval(progTick); clearTimeout(timer); document.removeEventListener('keydown', onKey); });
    await load();
  },
};
