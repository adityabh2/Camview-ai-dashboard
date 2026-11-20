// pages/dashboard.js — DASHBOARD: "what needs my decision right now?"
// Six numbers, the highest-priority pending alerts, and every exam at a glance.

import * as api from '../core/api.js';
import { on, can, session, pref, setPref } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, empty, errorBox, skeleton, priorityBadge, typeBadge, morph, dialog, toast, $ } from '../core/ui.js';
import { where, cameraName, reviewHref, rememberQueue } from '../components/queue.js';
import { bar, setSeriesVisible } from '../core/charts.js';

const VERDICTS = [['pending', 'Pending', 'var(--v-pending)'], ['valid', 'Valid', 'var(--v-valid)'], ['invalid', 'Invalid', 'var(--v-invalid)'], ['exception', 'Exception', 'var(--v-exception)']];
// Legend = series toggles (real buttons: focusable, Enter/Space, aria-pressed). Swatch mirrors the mark; text stays in text ink.
const legend = (items, hidden) => `<div class="ch-legend" role="group" aria-label="Show or hide a verdict">${items.map(([, l, c], i) => `<button type="button" class="ch-key" data-series="${i}" aria-pressed="${hidden.has(i) ? 'false' : 'true'}" title="${hidden.has(i) ? 'Show' : 'Hide'} ${l}"><i style="background:${c}"></i>${l}</button>`).join('')}</div>`;
const hh = (h) => `${String(h).padStart(2, '0')}:00`;
const PHASE = { pre_exam: 'PRE-EXAM', live_exam: 'LIVE EXAM', post_exam: 'POST-EXAM' };   // from the exam's date window

export default {
  async render(el, ctx) {
    setTitle('Dashboard', 'Alerts that need a decision');
    el.innerHTML = skeleton(6, 70);
    let data = null;
    let codes = [];                                    // registered project codes (code first)
    let codesCanManage = can('settings.manage');
    let selCode = pref('dashCode', '') || '';          // '' = all projects
    const loadCodes = async () => {
      try {
        const r = await api.get('/api/project-codes');
        codes = r.items || []; codesCanManage = !!r.canManage;
      } catch { codes = []; }
      if (selCode && !codes.some((c) => c.code === selCode)) { selCode = ''; setPref('dashCode', ''); }
    };

    const card = (a) => `<a class="pcard p-${esc(a.priority || 'unknown')}" href="${reviewHref(a)}" data-key="${esc(a.alarmId)}">
      ${a.imageUrl ? `<div class="pc-frame"><img src="${esc(a.imageUrl)}" alt="" loading="lazy">${a.hasVideo ? `<span class="q-play">${icon('play', 's')}</span>` : ''}</div>` : ''}
      <div class="row" style="gap:6px">${typeBadge(a)}<span class="grow"></span><span class="muted num" title="${esc(fmt.dt(a.lastInstance))}">${fmt.rel(a.lastInstance)}</span></div>
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
        // recording is a separate fact from a real source; the KPI exists only when a source reports it
        + (c.recordingReporting ? kpi({ label: 'Recording issues', value: c.notRecording, accent: c.notRecording ? 'critical' : '', icon: 'video', href: '#/alerts?view=recording_issues&status=all',
          sub: `${fmt.n(c.recordingReporting)} camera${c.recordingReporting === 1 ? '' : 's'} report recording`, title: 'Cameras whose health source reports NOT RECORDING' }) : '')
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
          ${kpi({ label: 'Pending', value: t.pending, accent: t.pending ? 'warning' : '', sub: ['arrival', 'all'].includes(data.deliveryTrigger) ? 'already sent to clients' : 'waiting for Camview', icon: 'clock', href: '#/alerts?status=pending' })}
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
      return `<div class="banner warning" style="margin-bottom:12px" data-key="quiet">${icon('clock')}<div class="grow"><b>No new alerts for ${days} day${days === 1 ? '' : 's'}.</b> The newest one was raised ${fmt.dt(latest)}. Camview is connected but sends nothing newer for this project. If an exam is running now, add its project code above.</div></div>`;
    };

    // ---- Code first: the dashboard is organised by the exam's project code (Camview's clientProjectId), e.g.
    // RAILTEL-J&K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL. A code stands for every Camview project that carries it;
    // registering a code is all it takes — the numeric ids are found by Camview's Project Lookup API.
    const codeParts = (code) => String(code || '').split('/').map((s) => s.trim()).filter(Boolean);
    const codeHead = (code) => codeParts(code).map((s) => `<span>${esc(s)}</span>`).join('<i aria-hidden="true">/</i>');
    const selected = () => codes.find((c) => c.code === selCode) || null;

    const masthead = () => {
      const fr = Object.values(data.freshness || {});
      const failed = fr.some((x) => x.lastError);
      const latest = fr.map((x) => x.lastSuccessAt).filter(Boolean).sort().pop();
      const k = data.kpis;
      const sel = selected();
      const ps = data.projects || [];
      const manage = codesCanManage;
      const tabs = `<nav class="dm-tabs" aria-label="Project codes">
          <button type="button" class="dm-tab ${sel ? '' : 'on'}" data-code="" aria-pressed="${sel ? 'false' : 'true'}">All projects</button>
          ${codes.map((c) => `<button type="button" class="dm-tab ${sel?.code === c.code ? 'on' : ''}" data-code="${esc(c.code)}" aria-pressed="${sel?.code === c.code}" title="${esc(c.code)}">${esc(codeParts(c.code)[0] || c.code)}<span class="muted">${esc(codeParts(c.code).slice(1, 3).join('/'))}</span></button>`).join('')}
          ${manage ? `<button type="button" class="dm-tab dm-add" data-codes>${icon('plus', 's')} Add project code</button>` : ''}
        </nav>`;
      const title = sel ? codeHead(sel.code) : (codes.length ? 'All project codes' : 'No project code yet');
      const meta = sel
        ? `${sel.projects.map((p) => `<span>${p.name && p.name !== sel.code ? esc(p.name) : 'Camview project'} <b class="num muted">#${esc(p.externalId)}</b></span>`).join('')}
           ${sel.source === 'configured' ? '<span class="muted">Mapped in configuration</span>' : `<span class="muted">Looked up ${sel.resolvedAt ? fmt.rel(sel.resolvedAt) : '—'}</span>`}
           ${sel.error ? `<span class="sla-attention">${esc(sel.error)}</span>` : ''}`
        : codes.length ? `<span class="muted">${fmt.n(codes.length)} code${codes.length === 1 ? '' : 's'} · ${fmt.n(ps.length)} Camview project${ps.length === 1 ? '' : 's'} monitored</span>`
          : `<span class="muted">Add the exam's project code to start. Camview finds its projects for you.</span>`;
      return `<header class="dash-mast" data-key="mast">
        <div class="dm-top">
          <div class="dm-main">
            <div class="dm-kicker">${session.mode === 'demo' ? '<span class="demo-flag">DEMO DATA</span>' : `<span class="dm-live ${failed ? 'is-delayed' : ''}"><span class="dot ${failed ? 'delayed' : 'live'}"></span>${failed ? 'Camview delayed' : 'Live from Camview'}</span>`}
              <span class="muted">Read ${latest ? fmt.time(latest) : '—'}</span></div>
            <h2 class="dm-code" title="${esc(sel?.code || '')}">${title}</h2>
            <div class="dm-meta">${meta}</div>
          </div>
          <div class="dm-actions">
            ${manage ? `<button type="button" class="btn" data-codes>${icon('edit', 's')} Manage codes</button>` : ''}
            <a class="btn primary" href="#/alerts?status=${data.manualReview ? 'pending' : 'all'}">${icon(data.manualReview ? 'check' : 'alert', 's')} ${data.manualReview ? `Start reviewing${k.pending ? ` (${fmt.n(k.pending)})` : ''}` : 'Open alerts'}</a>
          </div>
        </div>
        ${tabs}
      </header>`;
    };

    // One ribbon for the verdicts: the proportions read at a glance, the numbers sit underneath.
    const ribbon = () => {
      const t = data.totals || {};
      const parts = VERDICTS.map(([key, label, color]) => ({ key, label, color, n: Number(t[key] || 0) }));
      const sum = parts.reduce((s, x) => s + x.n, 0);
      return `<section class="dash-ribbon" data-key="ribbon" aria-label="Alerts by verdict">
        <div class="dr-bar" role="img" aria-label="${parts.map((x) => `${x.label} ${x.n}`).join(', ')}">${sum ? parts.filter((x) => x.n).map((x) => `<span style="flex-grow:${x.n};background:${x.color}" title="${x.label}: ${fmt.n(x.n)}"></span>`).join('') : '<span class="dr-empty"></span>'}</div>
        <div class="dr-keys">${parts.map((x) => `<a class="dr-key" href="#/alerts?status=${x.key}"><i style="background:${x.color}"></i><span>${x.label}</span><b class="num">${fmt.n(x.n)}</b><span class="muted num">${sum ? Math.round((x.n / sum) * 100) : 0}%</span></a>`).join('')}
          <span class="dr-total muted">${fmt.n(sum)} alerts in the window · ${fmt.n(t.today)} today</span></div>
      </section>`;
    };

    // Register / remove project codes. Adding one looks it up in Camview and monitors every project behind it.
    const openCodesDialog = () => {
      const list = () => codes.length ? `<div class="pc-results">${codes.map((c) => `<div class="pc-row"><div class="grow" style="min-width:0">
            <b class="mono" style="overflow-wrap:anywhere">${esc(c.code)}</b>
            <div class="muted">${c.projects.map((p) => `#${esc(p.externalId)}${p.name && p.name !== c.code ? ` ${esc(p.name)}` : ''}`).join(', ') || 'No Camview project'}${c.source === 'configured' ? ' · mapped in configuration' : ''}</div></div>
            <button type="button" class="btn sm ghost" data-rm="${esc(c.code)}">${icon('x', 's')} Remove</button></div>`).join('')}</div>`
        : '<div class="muted">No project code registered yet.</div>';
      dialog({
        title: `${icon('tree')} Project codes`,
        body: `<div class="field"><label for="pc-new">Add a project code</label>
            <div class="row" style="gap:8px"><input class="input mono grow" id="pc-new" placeholder="e.g. RAILTEL-J&amp;K/JKSSB-AAE-FOC/151126/CCTV-AI/IIL" autocomplete="off" spellcheck="false"><button type="button" class="btn primary" id="pc-add">${icon('plus', 's')} Add</button></div>
            <div class="hint">Type it exactly as set in Camview. Camview's Project Lookup finds every project with this code; they are monitored and shown under the code. Projects added later under the same code are picked up by themselves.</div></div>
          <div id="pc-msg"></div>
          <div class="section-title" style="margin-top:14px">Registered codes</div>
          <div id="pc-list">${list()}</div>`,
        onClose: () => load(true),
      });
      const box = document.querySelector('.dialog:last-of-type') || document;
      const repaint = () => { $('#pc-list', box).innerHTML = list(); };
      const add = async () => {
        const input = $('#pc-new', box), code = input.value.trim(), btn = $('#pc-add', box);
        if (!code) { input.focus(); return; }
        btn.disabled = true; $('#pc-msg', box).innerHTML = '<div class="muted">Looking the code up in Camview…</div>';
        try {
          const r = await api.post('/api/project-codes', { code });
          await loadCodes();
          selCode = r.code; setPref('dashCode', selCode);
          input.value = '';
          $('#pc-msg', box).innerHTML = `<div class="banner info">${icon('check')}<div>Added <b class="mono">${esc(r.code)}</b>: ${r.projects.length} Camview project${r.projects.length === 1 ? '' : 's'} (${r.projects.map((p) => '#' + esc(p.externalId)).join(', ')}).</div></div>`;
          repaint(); toast(`Project code added: ${r.code}`, 'success');
        } catch (err) { $('#pc-msg', box).innerHTML = `<div class="banner warning">${icon('alert')}<div>${esc(err.message)}</div></div>`; }
        btn.disabled = false;
      };
      $('#pc-add', box).addEventListener('click', add);
      $('#pc-new', box).addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
      box.addEventListener('click', async (e) => {
        const b = e.target.closest('[data-rm]');
        if (!b) return;
        b.disabled = true;
        try {
          const r = await api.post('/api/project-codes/remove', { code: b.dataset.rm });
          if (selCode === r.code) { selCode = ''; setPref('dashCode', ''); }
          await loadCodes(); repaint();
          toast(`Removed ${r.code}${r.stoppedMonitoring.length ? ` · stopped monitoring ${r.stoppedMonitoring.map((x) => '#' + x).join(', ')}` : ''}`, 'success');
        } catch (err) { toast(err.message, 'error'); b.disabled = false; }
      });
      $('#pc-new', box).focus();
    };
    el.addEventListener('click', (e) => {
      if (e.target.closest('[data-codes]')) { openCodesDialog(); return; }
      const t = e.target.closest('.dm-tab[data-code]');
      if (t) { selCode = t.dataset.code; setPref('dashCode', selCode); load(); }
    });

    const paint = () => {
      const k = data.kpis;
      const fr = Object.values(data.freshness || {});
      const failed = fr.filter((x) => x.lastError);
      const latest = fr.map((x) => x.lastSuccessAt).filter(Boolean).sort().pop();
      // Patched in place (never rebuilt): numbers that changed flash, charts glide, nothing flickers or jumps.
      morph(el, `
        ${masthead()}
        ${quietBanner()}
        ${ribbon()}
        ${data.manualReview ? '' : autoKpis()}
        <div class="kpis ${data.manualReview ? '' : 'hidden'}" data-key="kpis-manual">
          ${kpi({ label: 'Alerts today', value: data.totals?.today, sub: `${fmt.n(data.totals?.all)} in the window · latest ${data.totals?.latestAlertAt ? fmt.rel(data.totals.latestAlertAt) : '—'}`, icon: 'alert', href: '#/alerts?status=all' })}
          ${kpi({ label: 'Pending', value: data.totals?.pending, accent: data.totals?.pending ? 'warning' : '', sub: `${fmt.n(data.totals?.todayByDecision?.pending)} today`, icon: 'clock', href: '#/alerts?status=pending' })}
          ${kpi({ label: 'Not acknowledged', value: k.unacknowledged, accent: k.unacknowledged ? 'critical' : '', sub: 'pending alerts nobody has picked up', icon: 'bell', href: '#/alerts?view=unacknowledged&status=pending', title: 'Pending alerts no operator has acknowledged yet' })}
          ${kpi({ label: 'Valid', value: data.totals?.valid, accent: 'good', sub: `${fmt.n(data.totals?.todayByDecision?.valid)} today`, icon: 'check', href: '#/alerts?status=valid' })}
          ${kpi({ label: 'Invalid', value: data.totals?.invalid, sub: `${fmt.n(data.totals?.todayByDecision?.invalid)} today`, icon: 'x', href: '#/alerts?status=invalid' })}
          ${kpi({ label: 'Exception', value: data.totals?.exception, sub: `${fmt.n(data.totals?.todayByDecision?.exception)} today`, icon: 'bang', href: '#/alerts?status=exception' })}
          ${kpi({ label: 'Delivered to clients', value: k.clientAlerts, icon: 'share', href: '#/tickets?delivery=delivered', title: 'Tickets delivered to clients' })}
          ${cameraKpis()}
        </div>
        ${chartsHtml()}
        ${typeStrip()}
        ${failed.length ? `<div class="banner critical" style="margin-bottom:10px">${icon('alert')}<div class="grow"><b>Camview data is not arriving</b> for ${failed.length} project${failed.length === 1 ? '' : 's'}. Showing the last data received${latest ? ` (${fmt.time(latest)})` : ''}. ${esc(failed[0].lastError.message || '')}</div></div>` : ''}
        ${(data.unroutedValid || []).map((u) => `<div class="banner warning" style="margin-bottom:10px">${icon('bang')}<div class="grow"><b>${u.count}</b> VALID alert${u.count === 1 ? '' : 's'} in <b>${esc(u.project.code)}</b> cannot be sent: no client is mapped to this project.</div>${can('client.manage') ? '<a class="btn sm primary" href="#/clients">Add client</a>' : ''}</div>`).join('')}
        ${k.readyToSend && can('alarm.publish') ? `<div class="banner warning" style="margin-bottom:14px">${icon('share')}<div class="grow"><b>${k.readyToSend}</b> valid ticket${k.readyToSend === 1 ? '' : 's'} waiting to be sent to clients (controlled delivery).</div><a class="btn sm primary" href="#/tickets?delivery=ready">Send</a></div>` : ''}
        <div class="row" style="margin:22px 0 10px" data-key="prio-head"><div class="section-title grow" style="margin:0">${icon('alert', 's')} ${data.manualReview ? 'Priority alerts' : 'Priority alerts pending in Camview'}</div><a class="btn sm ghost" href="#/alerts?status=pending">All pending ${icon('right', 's')}</a></div>
        ${data.priorityAlerts.length ? `<div class="pcards" data-key="pcards">${data.priorityAlerts.map(card).join('')}</div>`
          : `<div class="card" data-key="pcards-empty">${empty('Nothing waiting for a decision', 'New alerts appear here automatically, highest priority first.', 'check')}</div>`}
        <div class="section-title" data-key="exams-head">${icon('report', 's')} Exams</div>
        ${data.exams.length ? `<div class="exam-tiles" data-key="exams">${data.exams.map((e) => `<a class="etile" data-key="${esc(e.id)}" href="#/alerts?exam=${encodeURIComponent(e.id)}&status=pending">
            <div class="t1">${esc(e.name)}${PHASE[e.phase] ? ` <span class="b outline phase phase-${esc(e.phase)}">${PHASE[e.phase]}</span>` : ''}</div><div class="t2">${esc((e.clients || []).map((c) => c.name).join(', ') || 'No client mapped')}</div>
            <div class="row" style="margin-top:8px"><span class="${e.pending ? 'sla-approaching' : 'muted'}"><b class="num">${e.pending}</b> pending</span><span class="muted">· ${e.total} total</span></div></a>`).join('')}</div>`
          : `<div class="card">${empty('No exams mapped', can('client.manage') ? 'Map exams to clients and projects in <a href="#/exams">Exams</a>.' : 'An administrator maps exams to clients and projects.', 'report')}</div>`}
        <p class="muted" style="font-size:11.5px;margin-top:14px" data-key="foot">${icon('info', 's')} ${esc(data.sortRule)} · Delivery: <b>${data.deliveryTrigger === 'all' ? 'every alert goes to the client; an administrator can revoke it' : data.deliveryTrigger === 'arrival' ? 'every alert goes to the client the moment Camview sends it' : data.deliveryMode === 'automatic' ? 'automatic on VALID' : 'controlled (one-click Send)'}</b></p>`);
      rememberQueue(data.priorityAlerts, { status: 'pending' });
      mountCharts();
    };

    const load = async (quiet = false) => {
      try {
        if (!quiet || !codes.length) await loadCodes();
        const q = { tzOffset: -new Date().getTimezoneOffset() };
        if (selCode) q.code = selCode;
        data = await api.get('/api/queue/summary', q);
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
