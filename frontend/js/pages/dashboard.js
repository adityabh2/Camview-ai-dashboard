// pages/dashboard.js — DASHBOARD: "what needs my decision right now?"
// Six numbers, the highest-priority pending alerts, and every exam at a glance.

import * as api from '../core/api.js';
import { on, can, session } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, empty, errorBox, skeleton, priorityBadge, morph, $ } from '../core/ui.js';
import { where, cameraName, reviewHref, rememberQueue } from '../components/queue.js';
import { bar, setSeriesVisible } from '../core/charts.js';

const VERDICTS = [['pending', 'Pending', 'var(--v-pending)'], ['valid', 'Valid', 'var(--v-valid)'], ['invalid', 'Invalid', 'var(--v-invalid)'], ['exception', 'Exception', 'var(--v-exception)']];
// Legend = series toggles (real buttons: focusable, Enter/Space, aria-pressed). Swatch mirrors the mark; text stays in text ink.
const legend = (items, hidden) => `<div class="ch-legend" role="group" aria-label="Show or hide a verdict">${items.map(([, l, c], i) => `<button type="button" class="ch-key" data-series="${i}" aria-pressed="${hidden.has(i) ? 'false' : 'true'}" title="${hidden.has(i) ? 'Show' : 'Hide'} ${l}"><i style="background:${c}"></i>${l}</button>`).join('')}</div>`;
const hh = (h) => `${String(h).padStart(2, '0')}:00`;

export default {
  async render(el, ctx) {
    setTitle('Dashboard', 'Alerts that need a decision');
    el.innerHTML = skeleton(6, 70);
    let data = null;

    const card = (a) => `<a class="pcard p-${esc(a.priority || 'unknown')}" href="${reviewHref(a)}" data-key="${esc(a.alarmId)}">
      ${a.imageUrl ? `<div class="pc-frame"><img src="${esc(a.imageUrl)}" alt="" loading="lazy">${a.hasVideo ? `<span class="q-play">${icon('play', 's')}</span>` : ''}</div>` : ''}
      <div class="row" style="gap:6px">${priorityBadge(a.priority)}<span class="grow"></span><span class="muted num" title="${esc(fmt.dt(a.lastInstance))}">${fmt.rel(a.lastInstance)}</span></div>
      <div class="t1">${esc(a.alarmTypeName)}${a.totalTimesReported > 1 ? ` <span class="b outline">${a.totalTimesReported}×</span>` : ''}</div>
      <div class="t2 mono">${icon('camera', 's')} ${esc(where(a))}</div>${cameraName(a) ? `<div class="t2" style="padding-left:18px">${esc(cameraName(a))}</div>` : ''}
      <div class="t2">${icon('report', 's')} ${esc(a.exam?.name || 'No exam mapped')}${a.client ? ` · ${esc(a.client.name)}` : ''}</div>
      <div class="pc-foot"><span>${a.evidence?.count ? `${icon(a.evidence.video ? 'video' : 'image', 's')} ${a.evidence.count} evidence` : '<span class="muted">No evidence</span>'}</span><span class="btn sm primary">Review ${icon('right', 's')}</span></div>
    </a>`;

    // Camera connection (from the real health source) and Camview's camera status events — never mixed with alerts.
    const cameraKpis = () => {
      const c = data.cameras || {};
      return kpi({ label: 'Cameras offline', value: c.reporting ? c.offline : '—', accent: c.offline ? 'critical' : '', icon: 'camera', href: '#/monitoring?tab=health',
        sub: c.reporting ? `${fmt.n(c.online)} online · ${fmt.n(c.reporting)} reporting${c.syncFailed ? ` · ${fmt.n(c.syncFailed)} sync failed` : ''}` : 'no camera status yet',
        title: c.source === 'camview' ? 'Camera connection as Camview reports it (frameSyncStatus)' : 'Camera connection from the connected health source' })
        + kpi({ label: 'Camera status events', value: c.events, icon: 'zap', href: '#/alerts?kind=camera_status&status=all',
          sub: `${fmt.n(c.eventsPending)} pending · latest ${c.latestEventAt ? fmt.rel(c.latestEventAt) : '—'}`, title: 'Camera online / offline events reported by Camview (no image or video)' });
    };

    // Charts: alerts per hour per verdict, and the busiest alert types (tables below carry the same numbers).
    // Form: 24 discrete hourly counts split by verdict = part-to-whole per bucket → stacked columns (an area would
    // imply continuity between hours and hide small verdicts). One-series ranking → horizontal bars, value at tip.
    const hiddenSeries = new Set();   // verdicts switched off in the legend; kept across live refreshes
    const chartHead = (ic, title, sub, value, unit) => `<div class="card-h ch-h"><div class="ch-titles"><h3>${icon(ic)} ${title}</h3><div class="sub">${sub}</div></div>
      <div class="ch-stat"><b>${fmt.n(value)}</b><span>${unit}</span></div></div>`;
    const busiest = () => {
      const types = (data.byType || []).filter((t) => t.type !== 'Unknown').slice(0, 8);
      const useToday = types.some((t) => t.today);
      return { types: useToday ? types.filter((t) => t.today) : types.filter((t) => t.total), useToday };
    };
    const chartsHtml = () => {
      const day = data.hourlyDay;
      const isToday = day === new Date().toLocaleDateString('en-CA');
      const hourly = data.hourly || [];
      const total = hourly.reduce((s, h) => s + h.pending + h.valid + h.invalid + h.exception, 0);
      const { types, useToday } = busiest();
      const typeTotal = types.reduce((s, t) => s + (useToday ? t.today : t.total), 0);
      const srTable = total ? `<table class="sr-only"><caption>Alerts per hour by verdict</caption><thead><tr><th>Hour</th>${VERDICTS.map(([, l]) => `<th>${l}</th>`).join('')}<th>Total</th></tr></thead><tbody>
        ${hourly.map((h) => `<tr><th>${hh(h.hour)}</th>${VERDICTS.map(([k]) => `<td>${h[k] || 0}</td>`).join('')}<td>${h.pending + h.valid + h.invalid + h.exception}</td></tr>`).join('')}</tbody></table>` : '';
      return `<div class="grid g-main charts ch-grid" style="margin-bottom:14px" data-key="charts">
        <section class="card ch-card" data-key="ch-hourly-card">${chartHead('chart', 'Alerts per hour', `${isToday ? 'Today' : `Latest day with alerts · ${esc(day || '')}`} · stacked by verdict`, total, total === 1 ? 'alert' : 'alerts')}
          <div class="card-b">${total ? legend(VERDICTS, hiddenSeries) : ''}
            <div class="ch-box">${total ? '<canvas id="ch-hourly" role="img" aria-label="Stacked columns: alerts per hour by verdict. The same numbers are in the table that follows."></canvas>'
              : empty('No alerts on this day', 'The chart fills as alerts arrive.', 'chart')}</div>${srTable}</div></section>
        <section class="card ch-card" data-key="ch-types-card">${chartHead('layers', 'Busiest alert types', `${useToday ? 'Today' : 'In the window'} · top ${types.length || 0} · click a bar to open`, typeTotal, typeTotal === 1 ? 'alert' : 'alerts')}
          <div class="card-b"><div class="ch-box">${types.length ? '<canvas id="ch-types" role="img" aria-label="Horizontal bars: alerts by type. The same numbers are in the Alerts by type table."></canvas>'
            : empty('No alert types yet', 'Types appear here as alerts arrive.', 'layers')}</div></div></section>
      </div>`;
    };
    const mountCharts = () => {
      const hourly = data.hourly || [];
      const isToday = data.hourlyDay === new Date().toLocaleDateString('en-CA');
      const nowAt = isToday ? hourly.findIndex((h) => Number(h.hour) === new Date().getHours()) : -1;
      if ($('#ch-hourly', el)) {
        bar($('#ch-hourly', el), { labels: hourly.map((h) => hh(h.hour)), titles: hourly.map((h) => `${hh(h.hour)} – ${hh((Number(h.hour) + 1) % 24)}`),
          stacked: true, tickEvery: 3, nowIndex: nowAt >= 0 ? nowAt : null, hidden: [...hiddenSeries],
          series: VERDICTS.map(([k, l, c]) => ({ label: l, data: hourly.map((h) => h[k] || 0), color: c })) });
      }
      const { types, useToday } = busiest();
      if ($('#ch-types', el)) {
        bar($('#ch-types', el), { labels: types.map((t) => t.type), horizontal: true, valueLabels: true,
          series: [{ label: useToday ? 'Today' : 'In the window', data: types.map((t) => (useToday ? t.today : t.total)), color: 'var(--chart-1)' }],
          onClick: (i) => { const t = types[i]; if (t) location.hash = `#/alerts?status=all&type=${encodeURIComponent(t.alarmType ?? '')}${useToday ? '' : '&range=all'}`; } });
      }
    };
    el.addEventListener('click', (e) => {
      const b = e.target.closest('.ch-key[data-series]');
      if (!b) return;
      const i = Number(b.dataset.series);
      if (hiddenSeries.has(i)) hiddenSeries.delete(i); else hiddenSeries.add(i);
      const on = !hiddenSeries.has(i);
      b.setAttribute('aria-pressed', String(on));
      b.title = `${on ? 'Hide' : 'Show'} ${VERDICTS[i][1]}`;
      setSeriesVisible($('#ch-hourly', el), i, on);
    });

    // How many alerts of each type today (and in the whole window)
    const typeStrip = () => {
      const rows = (data.byType || []).filter((t) => t.type !== 'Unknown').slice(0, 10);
      if (!rows.length) return '';
      const max = Math.max(1, ...rows.map((t) => t.today || t.total));
      return `<section class="card" style="margin-bottom:14px" data-key="types"><div class="card-h"><h3>${icon('layers')} Alerts by type</h3><div class="sub">Today · every verdict counted separately</div></div>
        <div class="card-b flush"><div class="table-wrap"><table class="t"><thead><tr><th>Type</th><th style="min-width:120px"></th><th class="num">Today</th><th class="num">Pending</th><th class="num">Valid</th><th class="num">Invalid</th><th class="num">Exception</th><th class="num">Window</th></tr></thead><tbody>
        ${rows.map((t) => `<tr class="link" tabindex="0" data-key="${esc(t.type)}" data-href="#/alerts?status=all&type=${encodeURIComponent(t.alarmType ?? '')}"><td><b>${esc(t.type)}</b></td>
          <td><span class="bar-track" style="display:block"><span class="bar-fill" style="display:block;width:${Math.max(1, ((t.today || t.total) / max) * 100)}%"></span></span></td>
          <td class="num"><b>${fmt.n(t.today)}</b></td><td class="num">${fmt.n(t.pending)}</td><td class="num sla-within">${fmt.n(t.valid)}</td><td class="num">${fmt.n(t.invalid)}</td><td class="num">${fmt.n(t.exception)}</td><td class="num muted">${fmt.n(t.total)}</td></tr>`).join('')}
        </tbody></table></div></div></section>`;
    };

    // Automatic flow: live totals straight from the Camview feed (status decided by Camview).
    const autoKpis = () => {
      const t = data.totals || {};
      const k = data.kpis;
      return `<div class="kpis" data-key="kpis">
          ${kpi({ label: 'Total alerts', value: t.all, sub: `${fmt.n(t.today)} today · latest ${t.latestAlertAt ? fmt.rel(t.latestAlertAt) : '—'}`, icon: 'alert', href: '#/alerts?status=all' })}
          ${kpi({ label: 'Valid', value: t.valid, accent: 'good', sub: `${fmt.n(k.clientAlerts)} sent to clients${data.autoShareValid ? ' automatically' : ''}`, icon: 'check', href: '#/alerts?status=valid' })}
          ${kpi({ label: 'Pending', value: t.pending, accent: t.pending ? 'warning' : '', sub: data.deliveryTrigger === 'arrival' ? 'delivered on arrival' : 'waiting for Camview', icon: 'clock', href: '#/alerts?status=pending' })}
          ${kpi({ label: 'Invalid', value: t.invalid, sub: 'never sent', icon: 'x', href: '#/alerts?status=invalid' })}
          ${kpi({ label: 'Exceptions', value: t.exception, sub: 'never sent', icon: 'bang', href: '#/alerts?status=exception' })}
          ${kpi({ label: 'Delivered to clients', value: k.clientAlerts, icon: 'share', href: '#/tickets?delivery=delivered' })}
          ${cameraKpis()}
        </div>
        <div class="proj-strip" data-key="projects">${(data.projects || []).map((p) => `<a class="pchip" data-key="${esc(p.externalId)}" href="#/alerts?status=all&projectId=${encodeURIComponent(p.externalId)}" title="${esc(p.freshness?.lastError?.message || 'Refreshing normally')}">
          <span class="dot ${p.freshness?.lastError ? 'delayed' : 'live'}" style="animation:none"></span><b>${esc(p.code)}</b>
          <span class="muted">${fmt.n(p.total)} alerts · ${fmt.n(p.valid)} valid · updated ${p.freshness?.lastSuccessAt ? fmt.rel(p.freshness.lastSuccessAt) : '—'}</span></a>`).join('')}</div>`;
    };

    // Camview answers (LIVE) but has not produced an alert for this project for a long time: say so, instead of
    // letting months-old alerts pass as "live". Threshold: one day without a single new alert.
    const quietBanner = () => {
      if (session.mode === 'demo') return '';
      const latest = data.totals?.latestAlertAt;
      const days = latest ? Math.floor((Date.now() - new Date(latest).getTime()) / 86400000) : null;
      if (days == null || days < 1) return '';
      return `<div class="banner warning" style="margin-bottom:10px">${icon('clock')}<div class="grow"><b>NO NEW ALERTS FROM CAMVIEW FOR ${days} DAY${days === 1 ? '' : 'S'}</b> — the newest alert in ${(data.projects || []).length === 1 ? 'this project' : 'your projects'} was raised ${fmt.dt(latest)}. The connection works; Camview is simply not sending anything newer for the project id${(data.projects || []).length === 1 ? '' : 's'} being monitored (${(data.projects || []).map((p) => p.code).join(', ') || '—'}). If an exam is running now, it is probably a different project id.</div>${can('settings.manage') ? '<a class="btn sm primary" href="#/settings?tab=projects">Find the running project</a>' : ''}</div>`;
    };

    const paint = () => {
      const k = data.kpis;
      const fr = Object.values(data.freshness || {});
      const failed = fr.filter((x) => x.lastError);
      const latest = fr.map((x) => x.lastSuccessAt).filter(Boolean).sort().pop();
      // Patched in place (never rebuilt): numbers that changed flash, charts glide, nothing flickers or jumps.
      morph(el, `
        <div class="page-head" data-key="head"><div><h2>Dashboard</h2><p>${session.mode === 'demo' ? 'DEMO data · ' : ''}Camview read ${latest ? fmt.time(latest) : '—'} · updates the moment data changes${failed.length ? ` · <span class="sla-attention">${failed.length} feed(s) failed — showing last data</span>` : ''}</p></div>
          <a class="btn primary" href="#/alerts?status=${data.manualReview ? 'pending' : 'all'}">${icon(data.manualReview ? 'check' : 'alert', 's')} ${data.manualReview ? `Start reviewing${k.pending ? ` (${k.pending})` : ''}` : 'Open alerts'}</a></div>
        ${data.manualReview ? '' : autoKpis()}
        <div class="kpis ${data.manualReview ? '' : 'hidden'}" data-key="kpis-manual">
          ${kpi({ label: 'Alerts today', value: data.totals?.today, sub: `${fmt.n(data.totals?.all)} in the window · latest ${data.totals?.latestAlertAt ? fmt.rel(data.totals.latestAlertAt) : '—'}`, icon: 'alert', href: '#/alerts?status=all' })}
          ${kpi({ label: 'Pending', value: data.totals?.pending, accent: data.totals?.pending ? 'warning' : '', sub: `${fmt.n(data.totals?.todayByDecision?.pending)} today`, icon: 'clock', href: '#/alerts?status=pending' })}
          ${kpi({ label: 'Valid', value: data.totals?.valid, accent: 'good', sub: `${fmt.n(data.totals?.todayByDecision?.valid)} today`, icon: 'check', href: '#/alerts?status=valid' })}
          ${kpi({ label: 'Invalid', value: data.totals?.invalid, sub: `${fmt.n(data.totals?.todayByDecision?.invalid)} today`, icon: 'x', href: '#/alerts?status=invalid' })}
          ${kpi({ label: 'Exception', value: data.totals?.exception, sub: `${fmt.n(data.totals?.todayByDecision?.exception)} today`, icon: 'bang', href: '#/alerts?status=exception' })}
          ${kpi({ label: 'Delivered to clients', value: k.clientAlerts, icon: 'share', href: '#/tickets?delivery=delivered', title: 'Tickets delivered to clients' })}
          ${cameraKpis()}
        </div>
        ${chartsHtml()}
        ${typeStrip()}
        ${quietBanner()}
        ${failed.length ? `<div class="banner critical" style="margin-bottom:10px">${icon('alert')}<div class="grow"><b>CAMVIEW DATA TEMPORARILY UNAVAILABLE</b> for ${failed.length} project${failed.length === 1 ? '' : 's'} — showing the last data received${latest ? ` (${fmt.time(latest)})` : ''}. ${esc(failed[0].lastError.message || '')}</div></div>` : ''}
        ${(data.unroutedValid || []).map((u) => `<div class="banner warning" style="margin-bottom:10px">${icon('bang')}<div class="grow"><b>${u.count}</b> VALID alert${u.count === 1 ? '' : 's'} in <b>${esc(u.project.code)}</b> cannot be sent: no client is mapped to this project.</div>${can('client.manage') ? '<a class="btn sm primary" href="#/clients">Add client</a>' : ''}</div>`).join('')}
        ${k.readyToSend && can('alarm.publish') ? `<div class="banner warning" style="margin-bottom:14px">${icon('share')}<div class="grow"><b>${k.readyToSend}</b> valid ticket${k.readyToSend === 1 ? '' : 's'} waiting to be sent to clients (controlled delivery).</div><a class="btn sm primary" href="#/tickets?delivery=ready">Send</a></div>` : ''}
        <div class="row" style="margin:22px 0 10px" data-key="prio-head"><div class="section-title grow" style="margin:0">${icon('alert', 's')} ${data.manualReview ? 'Priority alerts' : 'Priority alerts pending in Camview'}</div><a class="btn sm ghost" href="#/alerts?status=pending">All pending ${icon('right', 's')}</a></div>
        ${data.priorityAlerts.length ? `<div class="pcards" data-key="pcards">${data.priorityAlerts.map(card).join('')}</div>`
          : `<div class="card" data-key="pcards-empty">${empty('Nothing waiting for a decision', 'New alerts appear here automatically, highest priority first.', 'check')}</div>`}
        <div class="section-title" data-key="exams-head">${icon('report', 's')} Exams</div>
        ${data.exams.length ? `<div class="exam-tiles" data-key="exams">${data.exams.map((e) => `<a class="etile" data-key="${esc(e.id)}" href="#/alerts?exam=${encodeURIComponent(e.id)}&status=pending">
            <div class="t1">${esc(e.name)}</div><div class="t2">${esc((e.clients || []).map((c) => c.name).join(', ') || 'No client mapped')}</div>
            <div class="row" style="margin-top:8px"><span class="${e.pending ? 'sla-approaching' : 'muted'}"><b class="num">${e.pending}</b> pending</span><span class="muted">· ${e.total} total</span></div></a>`).join('')}</div>`
          : `<div class="card">${empty('No exams mapped', can('client.manage') ? 'Map exams to clients and projects in <a href="#/exams">Exams</a>.' : 'An administrator maps exams to clients and projects.', 'report')}</div>`}
        <p class="muted" style="font-size:11.5px;margin-top:14px" data-key="foot">${icon('info', 's')} ${esc(data.sortRule)} · Delivery: <b>${data.deliveryTrigger === 'arrival' ? 'every alert goes to the client the moment Camview sends it' : data.deliveryMode === 'automatic' ? 'automatic on VALID' : 'controlled (one-click Send)'}</b></p>`);
      rememberQueue(data.priorityAlerts, { status: 'pending' });
      mountCharts();
    };

    const load = async (quiet = false) => {
      try {
        data = await api.get('/api/queue/summary', { tzOffset: -new Date().getTimezoneOffset() });
        if (ctx.isStale()) return;
        paint();
      } catch (e) {
        if (ctx.isStale() || (quiet && data)) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', () => load());
      }
    };
    el.addEventListener('click', (e) => { const r = e.target.closest('tr.link[data-href]'); if (r) location.hash = r.dataset.href; });
    ctx.onCleanup(on('data', () => load(true)));
    await load();
  },
};
