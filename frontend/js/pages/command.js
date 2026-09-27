// pages/command.js — COMMAND CENTER.
// "What is happening? Where? How much? What needs attention? What has been
// validated / shared? What still requires review?"  Role-aware layout.
// Every chart / distribution drills down to the underlying records.

import * as api from '../core/api.js';
import { session, can, currentProject, projectInfo, on, pref } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { pollNow } from '../core/live.js';
import { bar } from '../core/charts.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, alertItem, why, legend, prov, delegate, PRIORITY_COLORS, VERDICT_COLORS, $ } from '../core/ui.js';
import { miniRow } from '../components/alarms.js';

const QUEUE_TABS = [
  ['pendingValidation', 'Pending validation', '#/live?quick=pending'],
  ['needsInvestigation', 'Needs investigation', '#/investigations'],
  ['readyForApproval', 'Ready for approval', '#/sharing?tab=ready_for_review'],
  ['readyForClient', 'Ready for client', '#/sharing?tab=candidates'],
  ['recentlyShared', 'Recently shared', '#/sharing?tab=shared'],
];
const ACT_RANGES = [['today', 'Today'], ['24h', '24 hours'], ['7d', '7 days'], ['30d', '30 days'], ['custom', 'Custom']];
const PRIOS = ['critical', 'high', 'medium', 'low'];
const VERDICTS = [['valid', 'Valid'], ['pending', 'Pending'], ['invalid', 'Invalid'], ['exception', 'Exception']];

const localIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
const q = (o) => new URLSearchParams(Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== '')).toString();

function persona() {
  if (can('alarm.approve') || can('alarm.publish')) return can('alarm.validate') ? 'supervisor' : 'manager';
  if (can('alarm.validate')) return 'operator';
  return 'viewer';
}

/** Distribution rows with count AND percentage; every row is a link to the filtered records. */
function distRows(items, colorFn, hrefFn) {
  if (!items || !items.length) return empty('No data');
  const total = items.reduce((s, x) => s + x.count, 0) || 1;
  const max = Math.max(1, ...items.map((x) => x.count));
  return `<div class="bars">${items.map((x) => {
    const c = colorFn(x) || 'var(--chart-1)';
    const pct = fmt.pct(x.count / total);
    const href = hrefFn(x);
    const tag = href ? 'a' : 'div';
    return `<${tag} class="bar-row" ${href ? `href="${esc(href)}"` : ''} style="grid-template-columns:minmax(80px,130px) 1fr 92px;color:inherit;text-decoration:none"
        title="${esc(x.label)}: ${x.count} (${pct})${href ? ' — click to open these alarms' : ''}">
      <span class="lab"><span class="sw" style="background:${c}"></span>${esc(x.label)}</span>
      <span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(1, (x.count / max) * 100)}%;background:${c}"></span></span>
      <span class="num" style="text-align:right">${fmt.n(x.count)} <span class="muted" style="font-size:11px">${pct}</span></span></${tag}>`;
  }).join('')}</div>`;
}

// Provenance tag in the tile's sub-line (keeps long labels readable).
const kp = (o) => kpi({ ...o, prov: undefined, sub: `${o.prov ? prov(o.prov) + ' ' : ''}${o.sub || ''}` });

export default {
  async render(el, ctx) {
    const pid = currentProject();
    const p = projectInfo(pid);
    setTitle('Command Center', `${esc(p.code)} · Operational overview`);
    if (!pid) { el.innerHTML = empty('No project available', 'An administrator must import nomenclature master data or configure a default project, and grant you access.', 'tree'); return; }
    el.innerHTML = skeleton(6, 60);
    let queueTab = ctx.query.queue || 'pendingValidation';
    let data;
    // activity range (24h uses overview data; others use /api/analytics)
    let actRange = ACT_RANGES.some((r) => r[0] === ctx.query.act) ? ctx.query.act : '24h';
    if (actRange !== '24h' && !can('analytics.view')) actRange = '24h';
    let actFrom = ctx.query.actFrom || localIso(new Date(Date.now() - 3 * 86400000));
    let actTo = ctx.query.actTo || localIso(new Date());
    let actData = null, actLoading = false, actError = null;
    // independent widgets (partial failure never breaks the page)
    let slv = null, slvFailed = false, quality = null, tree = null, extrasAt = 0;

    const load = async () => {
      try {
        data = await api.get('/api/overview', { projectId: pid, tzOffset: -new Date().getTimezoneOffset() });
        if (ctx.isStale()) return;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) { el.innerHTML = errorBox(e); el.querySelector('[data-retry]')?.addEventListener('click', load); }
      }
    };

    const loadExtras = async () => {
      extrasAt = Date.now();
      const jobs = [api.get('/api/since-last-visit', { projectId: pid }).then((r) => { slv = r; slvFailed = false; }).catch(() => { slvFailed = true; })];
      if (can('nomenclature.view')) {
        jobs.push(api.get('/api/context/quality', { projectId: pid }).then((r) => { quality = r; }).catch(() => { quality = { error: true }; }));
        jobs.push(api.get('/api/context/tree', { projectId: pid }).then((r) => { tree = r; }).catch(() => { tree = { error: true }; }));
      }
      await Promise.all(jobs);
      if (!ctx.isStale() && data) paint();
    };

    const loadActivity = async () => {
      if (actRange === '24h') { actData = null; paint(); return; }
      actLoading = true; actError = null; paint();
      const params = { projectId: pid, range: actRange, tzOffset: -new Date().getTimezoneOffset() };
      if (actRange === 'custom') { params.from = new Date(actFrom).toISOString(); params.to = new Date(actTo).toISOString(); }
      try { actData = await api.get('/api/analytics', params); }
      catch (e) { actError = e; actData = null; }
      actLoading = false;
      if (!ctx.isStale()) paint();
    };

    // buckets for the activity chart: [{label, start, end, values:{…}}]
    const activityBuckets = () => {
      if (actRange === '24h') {
        return { kind: 'hour', items: data.hourly.map((h) => ({ start: new Date(h.start), end: new Date(new Date(h.start).getTime() + 3599000), v: h })) };
      }
      if (!actData) return null;
      const r = actData.range;
      if (r.bucket === 'hour') {
        const s = new Date(r.start).getTime() - 3600000;
        return { kind: 'hour', items: actData.hourly.filter((h) => new Date(h.start).getTime() >= s).map((h) => ({ start: new Date(h.start), end: new Date(new Date(h.start).getTime() + 3599000), v: h })) };
      }
      const s = new Date(r.start); s.setHours(0, 0, 0, 0);
      return { kind: 'day', items: actData.daily.filter((x) => new Date(x.date + 'T00:00:00') >= s).map((x) => { const st = new Date(x.date + 'T00:00:00'); return { start: st, end: new Date(st.getTime() + 86399000), v: x }; }) };
    };

    const activityCard = () => {
      const allowed = can('analytics.view') ? ACT_RANGES : ACT_RANGES.filter(([k]) => k === '24h');
      const b = actRange === '24h' || actData ? activityBuckets() : null;
      const sub = actRange === '24h' ? 'Last 24 hours · alarms raised per hour, by priority'
        : actData ? `${esc(actData.range.label)} · ${b?.kind === 'hour' ? 'per hour, by priority' : 'per day, by Camview status'}${actData.truncated ? ' · based on newest alarms fetched' : ''}` : 'Loading…';
      const lg = b?.kind === 'day'
        ? legend(VERDICTS.map(([, l]) => ({ label: l, color: VERDICT_COLORS[l] })))
        : legend(PRIOS.map((k) => ({ label: k[0].toUpperCase() + k.slice(1), color: PRIORITY_COLORS[k] })));
      const controls = `<div class="seg" role="group" aria-label="Activity range">${allowed.map(([k, l]) => `<button data-act="${k}" class="${k === actRange ? 'on' : ''}">${l}</button>`).join('')}</div>`;
      const custom = actRange === 'custom' ? `<div class="row" style="margin-bottom:8px"><input class="input" type="datetime-local" id="act-from" value="${esc(actFrom)}" aria-label="From"><span class="muted">→</span><input class="input" type="datetime-local" id="act-to" value="${esc(actTo)}" aria-label="To"><button class="btn sm" id="act-apply">Apply</button></div>` : '';
      let bodyHtml;
      if (actError) bodyHtml = `<div class="banner critical">${icon('alert')}<div class="grow">Activity chart unavailable: ${esc(actError.message)}</div><button class="btn sm" data-act="${actRange}">Retry</button></div>`;
      else if (actLoading || !b) bodyHtml = `<div class="skel" style="height:220px"></div>`;
      else if (!b.items.length || b.items.every((x) => !(x.v.total))) bodyHtml = empty('No alarms in this period', 'Nothing was raised in the selected range.', 'chart');
      else bodyHtml = `${lg}<div class="chart"><canvas id="c-act" aria-label="Alarm activity chart — click a bar to open those alarms in history"></canvas></div><div class="muted" style="font-size:11px;margin-top:4px">Hover for time and count · click a bar to open those alarms in History</div>`;
      return card({ title: `${icon('chart')} Alarm activity`, sub, actions: controls, body: custom + bodyHtml });
    };

    const drawActivity = () => {
      const canvas = $('#c-act', el);
      if (!canvas) return;
      const b = activityBuckets();
      if (!b) return;
      const fmtLabel = (d) => (b.kind === 'hour'
        ? d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) + (actRange === '24h' || actRange === 'today' ? '' : ` ${d.toLocaleDateString(undefined, { day: '2-digit', month: 'short' })}`)
        : d.toLocaleDateString(undefined, { weekday: 'short', day: '2-digit', month: 'short' }));
      bar(canvas, {
        labels: b.items.map((x) => fmtLabel(x.start)),
        series: b.kind === 'hour'
          ? PRIOS.map((k) => ({ label: k, data: b.items.map((x) => x.v[k] || 0), color: PRIORITY_COLORS[k] }))
          : VERDICTS.map(([k, l]) => ({ label: l, data: b.items.map((x) => x.v[k] || 0), color: VERDICT_COLORS[l] })),
        stacked: true,
        onClick: (i) => { const x = b.items[i]; if (x) location.hash = `#/history?${q({ from: x.start.toISOString(), to: x.end.toISOString() })}`; },
      });
    };

    const sinceCard = () => {
      if (slvFailed) return `<div class="muted" style="font-size:11.5px;margin:-4px 0 12px">${icon('info', 's')} "Since your last visit" is unavailable right now.</div>`;
      if (!slv) return '';
      if (!slv.available) return `<div class="banner info" style="margin-bottom:14px">${icon('clock')}<div class="grow"><b>Since your last visit</b> — ${esc(slv.reason)}</div></div>`;
      const since = slv.since;
      const tiles = [
        ['New alarms', slv.newAlarms, `#/live?${q({ from: since })}`, 'layers', 'info'],
        ['New critical', slv.newCritical, `#/live?${q({ from: since, quick: 'critical' })}`, 'alert', 'critical'],
        ['Validated by Ops', slv.newValidated, '#/review?tab=ready_for_client', 'check', 'good'],
        ['Invalidated by Ops', slv.newInvalidated, null, 'x', 'violet'],
        ['Approval requests', slv.newApprovalRequests, '#/sharing?tab=ready_for_review', 'clock', 'warning'],
        ['Shared with clients', slv.newShares, '#/sharing?tab=shared', 'share', 'good'],
        ['Client acknowledgements', slv.newAcknowledgements, '#/sharing?tab=shared', 'user', ''],
        ['Pending now', slv.pendingNow, '#/live?quick=pending', 'clock', 'warning'],
      ];
      return card({
        title: `${icon('clock')} Since your last visit`,
        sub: `Changes since ${esc(fmt.dt(since))} (${esc(fmt.rel(since))})`,
        actions: prov('derived'),
        body: `${slv.reliable ? '' : `<div class="banner warning" style="margin-bottom:10px">${icon('info')}<div class="grow">${esc(slv.note || 'Counts cover only the monitored window.')}</div></div>`}
          <div class="kpis nopad" style="grid-template-columns:repeat(auto-fit,minmax(122px,1fr));margin-bottom:${slv.sample.length ? '10px' : '0'}">
            ${tiles.map(([l, v, h, ic, ac]) => kpi({ label: l, value: v, href: h || undefined, icon: ic, accent: ac || undefined })).join('')}</div>
          ${slv.sample.length ? `<details><summary class="dim" style="cursor:pointer;font-size:12px">Newest ${slv.sample.length} of ${fmt.n(slv.newAlarms)} new alarms</summary><div class="list" style="margin-top:6px;border:1px solid var(--border);border-radius:8px">${slv.sample.map((a) => miniRow(a)).join('')}</div></details>` : ''}`,
      }) + '<div style="height:14px"></div>';
    };

    const qualityCard = () => {
      if (!can('nomenclature.view')) return '';
      if (!quality) return card({ title: `${icon('shield')} Data quality`, body: skeleton(3, 14) });
      if (quality.error) return card({ title: `${icon('shield')} Data quality`, body: `<div class="muted">Data quality check unavailable right now.</div>` });
      const seen = quality.camerasSeen || 0;
      const mapped = quality.mappedCameras || 0;
      const cov = seen ? mapped / seen : null;
      const row = (label, n, href, warn = true) => `<dt>${esc(label)}</dt><dd>${n ? `<a href="${href}" class="${warn ? 'sla-approaching' : ''}"><b class="num">${fmt.n(n)}</b></a>` : '<span class="num muted">0</span>'}</dd>`;
      return card({
        title: `${icon('shield')} Data quality`, sub: 'Problems are surfaced, not hidden',
        actions: `${prov('derived')}<a class="btn sm" href="#/data-quality">Open</a>`,
        body: `<div class="dim" style="font-size:12px;margin-bottom:6px">Camera mapping coverage: <b class="num">${fmt.n(mapped)}</b> of <b class="num">${fmt.n(seen)}</b> cameras seen ${cov == null ? '' : `(${fmt.pct(cov)})`}</div>
          <div class="split" style="margin-bottom:12px" role="img" aria-label="Mapped cameras ${mapped} of ${seen}"><div style="flex:${mapped || 0};background:var(--v-valid)" title="Mapped: ${mapped}"></div><div style="flex:${Math.max(0, seen - mapped)};background:var(--st-warning)" title="Unmapped: ${seen - mapped}"></div></div>
          <dl class="kv" style="grid-template-columns:1fr auto">
            ${row('Unmapped cameras', (quality.unmappedCameras || []).length, '#/data-quality')}
            ${row('Unknown alarm types', (quality.unknownAlarmTypes || []).length, '#/data-quality')}
            ${row('Unknown projects', (quality.unknownProjects || []).length, '#/data-quality')}
            ${row('Cameras missing TC / centre', (quality.missingTc || 0) + (quality.missingCentre || 0), '#/data-quality')}
            ${row('Cameras missing room', quality.missingRoom || 0, '#/data-quality')}
            ${row('Alarms without evidence', quality.alarmsMissingEvidence || 0, '#/data-quality', false)}
          </dl>`,
      });
    };

    const contextCard = () => {
      if (!can('nomenclature.view')) return '';
      if (!tree) return card({ title: `${icon('tree')} Project context`, body: skeleton(3, 14) });
      const proj = tree.tree && tree.tree[0];
      if (tree.error || !proj) {
        return card({ title: `${icon('tree')} Project context`, sub: 'TC breakdown',
          body: empty('No nomenclature for this project', 'Import master data in Nomenclature to see TC / centre context.', 'tree') });
      }
      const tecs = (proj.children || []).filter((n) => n.level === 'tc');
      const max = Math.max(1, ...tecs.map((t) => t.count));
      return card({
        title: `${icon('tree')} Project context`, sub: `${esc(proj.code)} · ${fmt.n(tecs.length)} TC${tecs.length === 1 ? '' : 's'} · alarms in the monitored window`,
        actions: `${prov('derived')}<a class="btn sm" href="#/context?${q({ node: proj.id, projectId: pid })}">Explore</a>`, flush: true,
        body: tecs.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>TC</th><th>Activity</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Pending</th><th></th></tr></thead><tbody>
          ${tecs.map((t) => `<tr class="link" tabindex="0" data-href="#/context?${esc(q({ node: t.id, projectId: pid }))}">
            <td><span class="mono">${esc(t.code)}</span>${t.name && t.name !== t.code ? `<div class="muted" style="font-size:11px">${esc(t.name)}</div>` : ''}</td>
            <td style="min-width:90px"><span class="bar-track" style="display:block"><span class="bar-fill" style="display:block;width:${Math.max(1, (t.count / max) * 100)}%"></span></span></td>
            <td class="num">${fmt.n(t.count)}</td><td class="num">${fmt.n(t.critical)}</td><td class="num">${fmt.n(t.pending)}</td>
            <td><a class="btn sm ghost" href="#/live?${esc(q({ tc: t.code }))}" title="Open ${esc(t.code)} alarms in Live Operations">Alarms</a></td></tr>`).join('')}</tbody></table></div>
          ${tree.unmappedAlarms ? `<div class="muted" style="font-size:11.5px;padding:8px 14px">${fmt.n(tree.unmappedAlarms)} alarm(s) come from cameras without a mapping — see <a href="#/data-quality">Data quality</a>.</div>` : ''}`
          : empty('No TC level in master data'),
      });
    };

    const paint = () => {
      const m = data.metrics;
      const f = data.freshness;
      const role = persona();
      const kpis = `<style>.kpis.nopad .k-label{padding-right:0}</style><div class="kpis nopad">
        ${kp({ label: 'Total alarms', value: m.total, sub: f.truncated ? 'newest in window' : 'in current window', href: '#/live', icon: 'layers', prov: 'direct' })}
        ${kp({ label: 'Critical', value: m.critical, sub: `${m.criticalPending} pending`, accent: 'critical', href: '#/live?quick=critical', icon: 'alert', prov: 'direct' })}
        ${kp({ label: 'Pending', value: m.pending, sub: 'Camview state 0', accent: 'warning', href: '#/live?quick=pending', icon: 'clock', prov: 'direct' })}
        ${kp({ label: 'Valid', value: m.valid, sub: `Ops marked ${m.opsValid}`, accent: 'good', href: '#/live?lastActionType=1', icon: 'check', prov: 'direct' })}
        ${kp({ label: 'Invalid', value: m.invalid, sub: `false-alarm rate ${fmt.pct(m.falseAlarmRate)}`, accent: 'violet', href: '#/live?lastActionType=2', icon: 'x', prov: 'direct' })}
        ${kp({ label: 'Exceptions', value: m.exception, href: '#/live?lastActionType=3', icon: 'bang', prov: 'direct' })}
        ${kp({ label: 'Suppressed', value: m.suppressed, href: '#/live?quick=suppressed', icon: 'eye', prov: 'direct' })}
        ${kp({ label: 'Ready for review', value: m.readyForReview, sub: 'no operator decision', href: '#/review?tab=pending', icon: 'work', prov: 'derived' })}
        ${kp({ label: 'Ready for client', value: m.readyForClient + m.awaitingApproval, sub: `${m.awaitingApproval} awaiting approval`, accent: 'info', href: '#/sharing', icon: 'share', prov: 'derived' })}
        ${kp({ label: 'Approved for client', value: m.approvedForClient, sub: 'not yet published', href: '#/sharing?tab=approved', icon: 'check', prov: 'derived' })}
        ${kp({ label: 'Shared with client', value: m.sharedWithClient, href: '#/sharing?tab=shared', icon: 'share', accent: 'good', prov: 'derived' })}
      </div>`;

      const attention = card({
        title: `${icon('zap')} Attention required`,
        sub: 'Explainable, derived from the alarms you can see',
        actions: `<span class="b outline">${data.alertCount}</span><a class="btn sm" href="#/insights">All alerts</a>`,
        flush: true,
        body: data.alerts.length ? `<div class="list">${data.alerts.slice(0, 7).map((a) => alertItem(a, { compact: true })).join('')}</div>`
          : empty('Nothing requires attention', 'No intelligent alert conditions are met right now.', 'check'),
      });

      const queue = card({
        title: `${icon('work')} Review queue`,
        actions: `<a class="btn sm" href="${QUEUE_TABS.find((t) => t[0] === queueTab)[2]}">Open</a>`,
        flush: true,
        body: `<div class="tabs in-card" role="tablist">${QUEUE_TABS.map(([k, l]) => `<button class="tab ${k === queueTab ? 'on' : ''}" role="tab" aria-selected="${k === queueTab}" data-q="${k}">${l}<span class="n">${data.reviewQueueCounts[k] ?? 0}</span></button>`).join('')}</div>
          <div class="list">${(data.reviewQueue[queueTab] || []).length ? data.reviewQueue[queueTab].map((a) => miniRow(a, `<span class="dim" style="font-size:11px">${fmt.dur(a.ageMinutes)}</span>`)).join('')
            : empty('Queue is empty', 'Nothing in this queue right now.', 'check')}</div>`,
      });

      const sq = data.sharingQueue;
      const sharing = card({
        title: `${icon('share')} Client sharing queue`, sub: 'Valid ≠ shared — nothing reaches a client without approval + publish',
        actions: '<a class="btn sm" href="#/sharing">Open Sharing Center</a>',
        body: `<div class="grid g-4" style="grid-template-columns:repeat(auto-fit,minmax(96px,1fr));gap:8px">
          ${[['Ready', sq.ready, 'ready_for_review', '#/sharing?tab=ready_for_review'], ['Approved', sq.approved, 'approved', '#/sharing?tab=approved'],
            ['Shared', sq.shared, 'shared', '#/sharing?tab=shared'], ['Acknowledged', sq.acknowledged, 'shared', '#/sharing?tab=shared'], ['Withdrawn', sq.withdrawn, 'withdrawn', '#/sharing?tab=withdrawn']]
            .map(([l, v, s, h]) => `<a class="kpi" href="${h}" style="padding:9px 10px"><div class="k-label" style="padding-right:0"><span class="sw" style="background:var(--vis-${s === 'ready_for_review' ? 'ready' : s})"></span>${l}</div><div class="k-value" style="font-size:19px">${fmt.n(v)}</div></a>`).join('')}</div>
          <div class="muted" style="margin-top:8px;font-size:11.5px">Four-eyes approval: <b>${data.policy.fourEyes ? 'on' : 'off'}</b> · two-step approval: <b>${data.policy.requireApproval ? 'on' : 'off'}</b></div>`,
      });

      const activity = activityCard();

      const statusDist = card({ title: 'Alarm status', sub: 'Camview lastActionType · click to filter', actions: prov('direct'),
        body: distRows(data.statusDistribution, (d) => VERDICT_COLORS[d.label] || 'var(--st-neutral)', (d) => (d.key === null || d.key === undefined ? null : `#/live?lastActionType=${d.key}`)) });
      const prioDist = card({ title: 'Priority distribution', sub: 'Click to filter', actions: prov('direct'),
        body: distRows(data.priorityDistribution, (d) => PRIORITY_COLORS[d.key] || 'var(--st-neutral)', (d) => (PRIORITY_COLORS[d.key] ? `#/live?priority=${encodeURIComponent(d.key)}` : null)) });
      const shiftDist = card({ title: 'Shift distribution', sub: 'Shift labels as reported by Camview · click to filter', actions: prov('direct'),
        body: distRows(data.shiftDistribution, () => 'var(--chart-1)', (d) => (d.key && d.key !== 'No shift' ? `#/live?${q({ shiftLabel: d.key })}` : null)) });

      const cams = card({
        title: `${icon('camera')} Most active cameras`, actions: '<a class="btn sm" href="#/cameras">All cameras</a>', flush: true,
        body: data.topCameras.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Camera</th><th>Location</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Pending</th><th>Latest</th></tr></thead><tbody>
          ${data.topCameras.map((c) => `<tr class="link" data-href="#/cameras/${encodeURIComponent(c.cameraId)}?projectId=${encodeURIComponent(pid)}" tabindex="0"><td class="mono">${esc(c.code)}${c.mapped ? '' : ' <span class="prov unavailable">UNMAPPED</span>'}</td><td class="dim ctx-path">${esc(c.location || '—')}</td>
          <td class="num">${c.count}</td><td class="num">${c.critical}</td><td class="num">${c.pending}</td><td class="dim">${fmt.rel(c.latest)}</td></tr>`).join('')}</tbody></table></div>` : empty('No camera activity'),
      });

      const recent = card({ title: `${icon('clock')} Recent alarm activity`, actions: '<a class="btn sm" href="#/live">Live Operations</a>', flush: true,
        body: data.recent.length ? `<div class="list">${data.recent.slice(0, 8).map((a) => miniRow(a)).join('')}</div>` : empty('No alarms yet') });

      const insights = card({ title: `${icon('info')} Operational insights`, actions: prov('derived'),
        body: data.insights.length ? `<ul class="why">${data.insights.map((i) => `<li>${esc(i.text)}</li>`).join('')}</ul>` : empty('Not enough data for insights') });

      const fresh = card({ title: `${icon('refresh')} Data freshness`, actions: `<button class="btn sm" data-refresh>${icon('refresh', 's')} Refresh</button>`,
        body: `<dl class="kv"><dt>Connection</dt><dd><span class="dot ${f.state === 'live' ? 'live' : f.state}" style="display:inline-block;margin-right:6px"></span>${esc(f.state.toUpperCase())}</dd>
          <dt>Last successful update</dt><dd class="num">${f.lastSuccessAt ? fmt.time(f.lastSuccessAt) + ' · ' + fmt.rel(f.lastSuccessAt) : '—'}</dd>
          ${f.lastAttemptAt ? `<dt>Last attempted update</dt><dd class="num">${fmt.time(f.lastAttemptAt)}</dd>` : ''}
          <dt>Data source</dt><dd>${f.mode === 'demo' ? '<span class="demo-flag">DEMO DATA</span>' : 'Camview listAlarms (live)'}</dd>
          <dt>Working window</dt><dd>${fmt.n(f.windowSize)} alarms${f.totalElements != null ? ` of ${fmt.n(f.totalElements)} reported by Camview` : ''}${f.truncated ? ' <span class="prov derived">NEWEST ONLY</span>' : ''}</dd>
          <dt>Server cache</dt><dd>${f.cacheSeconds}s · browser refresh every ${Math.round(pref('refreshMs', 15000) / 1000)}s</dd>
          ${f.lastError ? `<dt>Last error</dt><dd class="sla-attention">${esc(f.lastError.message)} <span class="muted">(${fmt.time(f.lastError.at)})</span></dd>` : ''}</dl>`,
      });

      const ctxQ = [contextCard(), qualityCard()].filter(Boolean);
      const contextRow = ctxQ.length ? `<div class="grid ${ctxQ.length === 2 ? 'g-main' : ''}" style="margin-top:14px">${ctxQ.join('')}</div>` : '';

      const hero = `<div class="hero">
        <div class="grow"><div class="eyebrow">CAMVIEW · COMMAND CENTER</div><h2>Operational overview</h2>
          <div class="meta"><span>${icon('tree', 's')} <b>${esc(p.code)}</b> ${esc(p.name || '')}</span><span id="clock" class="num"></span>
            <span><span class="dot ${f.state === 'live' ? 'live' : f.state}" style="display:inline-block;vertical-align:middle"></span> ${f.mode === 'demo' ? 'DEMO' : 'LIVE'} · updated ${f.lastSuccessAt ? fmt.time(f.lastSuccessAt) : '—'}</span>
            <span>${esc(session.user.roleName)} view</span></div></div>
        <div class="row"><a class="btn" href="#/brief">${icon('calendar', 's')} Daily brief</a><a class="btn" href="#/presentation">${icon('present', 's')} Present</a><button class="btn primary" data-refresh>${icon('refresh', 's')} Refresh</button></div></div>`;

      const banner = f.lastError ? `<div class="banner critical">${icon('alert')}<div class="grow"><b>Refresh failed.</b> ${esc(f.lastError.message)} Showing last successful data from ${f.lastSuccessAt ? fmt.time(f.lastSuccessAt) : 'never'}.</div><button class="btn sm" data-refresh>Retry</button></div>` : '';
      const skipped = data.skippedIntelligence.length ? `<div class="muted" style="font-size:11.5px;margin:-6px 0 12px">${icon('info', 's')} Some intelligence checks were skipped: ${data.skippedIntelligence.map((s) => esc(s.reason)).join(' ')}</div>` : '';

      let body;
      if (role === 'manager') {
        body = `${kpis}<div class="grid g-main">${activity}${attention}</div><div class="grid g-3" style="margin-top:14px">${statusDist}${prioDist}${shiftDist}</div>
          ${contextRow}<div class="grid g-main" style="margin-top:14px">${cams}${sharing}</div><div class="grid g-main" style="margin-top:14px">${queue}<div class="stack">${insights}${fresh}</div></div>`;
      } else if (role === 'supervisor') {
        body = `${kpis}<div class="grid g-main">${queue}${attention}</div><div class="grid g-main" style="margin-top:14px">${activity}${sharing}</div>
          <div class="grid g-3" style="margin-top:14px">${statusDist}${prioDist}${shiftDist}</div>${contextRow}<div class="grid g-main" style="margin-top:14px">${cams}<div class="stack">${insights}${fresh}</div></div>
          <div style="margin-top:14px">${recent}</div>`;
      } else {
        body = `${kpis}<div class="grid g-main">${attention}${queue}</div><div class="grid g-main" style="margin-top:14px">${recent}<div class="stack">${sharing}${insights}</div></div>
          <div class="grid g-main" style="margin-top:14px">${activity}${prioDist}</div><div class="grid g-3" style="margin-top:14px">${statusDist}${shiftDist}${fresh}</div>
          ${contextRow}<div style="margin-top:14px">${cams}</div>`;
      }
      el.innerHTML = hero + banner + skipped + sinceCard() + body;
      drawActivity();
      tick();
    };

    const tick = () => { const c = $('#clock', el); if (c) c.textContent = new Date().toLocaleString(undefined, { weekday: 'short', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' }); };
    const clock = setInterval(tick, 1000);
    ctx.onCleanup(() => clearInterval(clock));

    ctx.onCleanup(delegate(el, 'click', '[data-why]', (e, b) => { const a = data.alerts.find((x) => x.id === b.dataset.why); if (a) why(a); }));
    ctx.onCleanup(delegate(el, 'click', '[data-q]', (e, b) => { queueTab = b.dataset.q; ctx.setQuery({ queue: queueTab }); paint(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-act]', (e, b) => {
      actRange = b.dataset.act;
      ctx.setQuery({ act: actRange === '24h' ? '' : actRange });
      if (actRange === 'custom') { actData = null; actError = null; paint(); return; }
      loadActivity();
    }));
    ctx.onCleanup(delegate(el, 'click', '#act-apply', () => {
      const f = $('#act-from', el).value, t = $('#act-to', el).value;
      if (!f || !t || new Date(f) >= new Date(t)) return;
      actFrom = f; actTo = t;
      ctx.setQuery({ act: 'custom', actFrom: f, actTo: t });
      loadActivity();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-refresh]', async () => { await pollNow(); load(); loadExtras(); if (actRange !== '24h') loadActivity(); }));
    ctx.onCleanup(delegate(el, 'click', 'tr[data-href]', (e, tr) => { if (!e.target.closest('a,button')) location.hash = tr.dataset.href; }));
    ctx.onCleanup(delegate(el, 'keydown', 'tr[data-href]', (e, tr) => { if (e.key === 'Enter') location.hash = tr.dataset.href; }));
    ctx.onCleanup(on('data', () => { load(); if (Date.now() - extrasAt > 60000) loadExtras(); }));
    ctx.onCleanup(on('themechange', () => data && paint()));
    await load();
    loadExtras();
    if (actRange !== '24h' && actRange !== 'custom') loadActivity();
    else if (actRange === 'custom' && ctx.query.actFrom) loadActivity();
  },
};
