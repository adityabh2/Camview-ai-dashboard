// pages/analytics.js — ANALYTICS CENTER: "What patterns exist?"
// Everything is derived from the alarms in the chosen range that the user may
// see. Camview's verdict and the Ops team's verdict are shown side by side.

import * as api from '../core/api.js';
import { currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { bar } from '../core/charts.js';
import { esc, icon, fmt, kpi, card, errorBox, skeleton, empty, bars, legend, prov, delegate,
  PRIORITY_COLORS, VERDICT_COLORS, $, $$ } from '../core/ui.js';

const RANGES = [['today', 'Today'], ['24h', '24 hours'], ['7d', '7 days'], ['30d', '30 days'], ['custom', 'Custom']];
const LIST_TABS = [['camviewInvalid', 'Invalid (Camview)'], ['camviewValid', 'Valid (Camview)'], ['opsInvalid', 'Marked invalid by Ops'], ['opsValid', 'Marked valid by Ops']];
const OPS_LABEL = { marked_valid: 'Valid', marked_invalid: 'Invalid', marked_exception: 'Exception', acknowledged: 'Acknowledged', unreviewed: 'Unreviewed' };

const CMP_LEVELS = [['project', 'Projects'], ['tc', 'TCs'], ['centre', 'Centres']];
const PRIOS = ['critical', 'high', 'medium', 'low'];
const CMP_ONE = { project: 'project', tc: 'TC', centre: 'centre' };
const CMP_MANY = { project: 'projects', tc: 'TCs', centre: 'centres' };

const localIso = (d) => new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Analytics', `${esc(projectInfo(pid).code)} · patterns and verdicts`);
    let range = RANGES.some((r) => r[0] === ctx.query.range) ? ctx.query.range : '24h';
    let from = ctx.query.from || localIso(new Date(Date.now() - 7 * 86400000));
    let to = ctx.query.to || localIso(new Date());
    let listTab = 'camviewInvalid';
    let levelTab = 'byTc';
    let d = null;
    let view = ctx.query.tab === 'compare' ? 'compare' : 'overview';
    let cmpLevel = CMP_LEVELS.some((x) => x[0] === ctx.query.level) ? ctx.query.level : 'project';
    let cmpSel = new Set((ctx.query.ids || '').split(',').filter(Boolean));
    let cmp = null;

    el.innerHTML = `
      <div class="page-head"><div><h2>Analytics</h2><p id="an-sub">Loading…</p></div>
        <div class="row">
          <div class="seg" role="group" aria-label="Range">${RANGES.map(([k, l]) => `<button data-range="${k}" class="${k === range ? 'on' : ''}">${l}</button>`).join('')}</div>
          <span id="an-custom" class="row ${range === 'custom' ? '' : 'hidden'}"><input class="input" type="datetime-local" id="an-from" value="${esc(from)}" aria-label="From"><span class="muted">→</span><input class="input" type="datetime-local" id="an-to" value="${esc(to)}" aria-label="To"><button class="btn sm" id="an-apply">Apply</button></span>
          <button class="btn" id="an-print">${icon('report', 's')} Print</button>
        </div></div>
      <div class="tabs" role="tablist" id="an-tabs">
        <button class="tab ${view === 'overview' ? 'on' : ''}" role="tab" aria-selected="${view === 'overview'}" data-view="overview">${icon('chart', 's')} Overview</button>
        <button class="tab ${view === 'compare' ? 'on' : ''}" role="tab" aria-selected="${view === 'compare'}" data-view="compare">${icon('compare', 's')} Compare</button>
      </div>
      <div id="an-body">${skeleton(8, 50)}</div>`;
    const bodyEl = $('#an-body', el);

    const rangeParams = () => {
      const params = { range, tzOffset: -new Date().getTimezoneOffset() };
      if (range === 'custom') { params.from = new Date(from).toISOString(); params.to = new Date(to).toISOString(); }
      return params;
    };
    // History drilldown for the current range (+ extra filters)
    const hist = (extra = {}) => {
      const r = d?.range || cmp?.range;
      const qs = new URLSearchParams(Object.entries({ from: r?.start ? new Date(r.start).toISOString() : '', to: r?.end ? new Date(r.end).toISOString() : '', ...extra })
        .filter(([, v]) => v !== undefined && v !== null && v !== ''));
      return `#/history?${qs.toString()}`;
    };
    const linkAttr = (href) => (href ? `data-href="${esc(href)}" tabindex="0" role="link" style="cursor:pointer" title="Open these alarms in History"` : '');

    const reload = () => (view === 'compare' ? loadCompare() : load());

    const loadCompare = async () => {
      bodyEl.innerHTML = skeleton(6, 40);
      try {
        cmp = await api.get('/api/compare', { projectId: pid, level: cmpLevel, ...rangeParams() });
        if (!ctx.isStale()) paintCompare();
      } catch (e) {
        if (ctx.isStale()) return;
        bodyEl.innerHTML = errorBox(e);
        $('[data-retry]', bodyEl)?.addEventListener('click', loadCompare);
      }
    };

    const paintCompare = () => {
      const all = cmp.rows;
      const rows = cmpSel.size ? all.filter((r) => cmpSel.has(r.code)) : all;
      const lvlLabel = CMP_LEVELS.find((x) => x[0] === cmpLevel)[1];
      const max = Math.max(1, ...rows.map((r) => r.total));
      const truncated = all.some((r) => r.truncated);
      $('#an-sub', el).innerHTML = `Compare ${esc(CMP_MANY[cmpLevel])} · ${esc(cmp.range.label)} · ${fmt.dt(cmp.range.start)} → ${fmt.dt(cmp.range.end)} · only data you are authorized to see ${prov('derived')}`;
      const filterFor = (r) => (cmpLevel === 'project' || r.code === 'Unmapped' ? null : hist({ [cmpLevel]: r.code }));
      const chartRows = rows.slice(0, 12);
      bodyEl.innerHTML = `
        <div class="filters">
          <div class="seg" role="group" aria-label="Compare level">${CMP_LEVELS.map(([k, l]) => `<button data-cmplevel="${k}" class="${k === cmpLevel ? 'on' : ''}">${l}</button>`).join('')}</div>
          <span class="muted" style="font-size:12px">${cmpLevel === 'project' ? 'All projects you can access' : `Within ${esc(projectInfo(pid).code)}`}</span>
        </div>
        ${all.length ? `<div class="filters" role="group" aria-label="Choose ${esc(lvlLabel)} to compare">
          <button class="chip ${cmpSel.size ? '' : 'on'}" data-cmpall>All <span class="n">${all.length}</span></button>
          ${all.map((r) => `<button class="chip ${cmpSel.has(r.code) ? 'on' : ''}" data-cmpsel="${esc(r.code)}" aria-pressed="${cmpSel.has(r.code)}">${esc(r.code)} <span class="n">${fmt.n(r.total)}</span></button>`).join('')}</div>` : ''}
        ${truncated ? `<div class="banner warning">${icon('info')}<div class="grow">Some figures are based on the newest alarms fetched from Camview for the range.</div></div>` : ''}
        ${rows.length ? `
        ${card({ title: `${icon('compare')} ${esc(lvlLabel)} comparison`, sub: 'Alarm volume, status and priority — click a row to open its alarms', flush: true, actions: prov('derived'),
          body: `<div class="table-wrap"><table class="t"><thead><tr><th>${esc(lvlLabel.replace(/s$/, ''))}</th><th style="min-width:140px">Alarm volume</th><th class="num">Total</th><th class="num">Critical</th><th class="num">Pending</th><th class="num">Valid</th><th class="num">Invalid</th><th class="num">Exception</th><th class="num">Shared</th><th class="num">False-alarm rate</th><th class="num">Cameras</th></tr></thead><tbody>
            ${rows.map((r) => `<tr class="${filterFor(r) ? 'link' : ''}" ${filterFor(r) ? `data-href="${esc(filterFor(r))}" tabindex="0"` : ''}>
              <td><span class="mono">${esc(r.code)}</span>${r.name && r.name !== r.code ? `<div class="muted" style="font-size:11px">${esc(r.name)}</div>` : ''}</td>
              <td><span class="bar-track" style="display:block"><span class="bar-fill" style="display:block;width:${Math.max(1, (r.total / max) * 100)}%"></span></span></td>
              <td class="num">${fmt.n(r.total)}</td><td class="num">${fmt.n(r.critical)}</td><td class="num">${fmt.n(r.pending)}</td><td class="num">${fmt.n(r.valid)}</td>
              <td class="num">${fmt.n(r.invalid)}</td><td class="num">${fmt.n(r.exception)}</td><td class="num">${fmt.n(r.shared)}</td><td class="num">${fmt.pct(r.falseAlarmRate)}</td><td class="num">${fmt.n(r.cameras)}</td></tr>`).join('')}
          </tbody></table></div>` })}
        <div style="margin-top:14px">${card({ title: `${icon('chart')} Priority by ${esc(CMP_ONE[cmpLevel])}`, sub: rows.length > 12 ? 'Showing the 12 largest — select entities above to focus' : 'Alarms per priority · hover for counts', actions: prov('direct'),
          body: `${legend(PRIOS.map((k) => ({ label: k[0].toUpperCase() + k.slice(1), color: PRIORITY_COLORS[k] })))}<div class="chart lg"><canvas id="an-cmp"></canvas></div>` })}</div>`
        : empty('Nothing to compare', `No ${esc(CMP_MANY[cmpLevel])} have alarms in this range.`, 'compare')}`;
      if (chartRows.length) {
        bar($('#an-cmp', el), {
          labels: chartRows.map((r) => r.code),
          series: PRIOS.map((k) => ({ label: k, data: chartRows.map((r) => (r.priority || {})[k] || 0), color: PRIORITY_COLORS[k] })),
          onClick: (i) => { const h = filterFor(chartRows[i]); if (h) location.hash = h; },
        });
      }
    };

    const load = async () => {
      bodyEl.innerHTML = d ? bodyEl.innerHTML : skeleton(8, 50);
      const params = { projectId: pid, range, tzOffset: -new Date().getTimezoneOffset() };
      if (range === 'custom') { params.from = new Date(from).toISOString(); params.to = new Date(to).toISOString(); }
      try {
        d = await api.get('/api/analytics', params);
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        bodyEl.innerHTML = errorBox(e);
        $('[data-retry]', bodyEl)?.addEventListener('click', load);
      }
    };

    const LVL_KEY = { byTc: 'tc', byCentre: 'centre' };
    const levelTable = (rows, label, key) => (rows.length ? `<div class="table-wrap" style="max-height:320px"><table class="t"><thead><tr><th>${label}</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Pending</th><th class="num">Valid</th><th class="num">Invalid</th><th class="num">False-alarm rate</th></tr></thead><tbody>
      ${rows.map((r) => `<tr ${key && r.code !== 'Unmapped' ? `class="link" data-href="${esc(hist({ [key]: r.code }))}" tabindex="0" title="Open these alarms in History"` : ''}><td><span class="mono">${esc(r.code)}</span>${r.name && r.name !== r.code ? `<div class="muted" style="font-size:11px">${esc(r.name)}</div>` : ''}</td><td class="num">${fmt.n(r.total)}</td><td class="num">${fmt.n(r.critical)}</td><td class="num">${fmt.n(r.pending)}</td><td class="num">${fmt.n(r.valid)}</td><td class="num">${fmt.n(r.invalid)}</td><td class="num">${fmt.pct(r.falseAlarmRate)}</td></tr>`).join('')}
      </tbody></table></div>` : empty('No data'));

    const paint = () => {
      const m = d.metrics;
      const v = d.verdicts;
      const hourBucket = d.range.bucket === 'hour';
      $('#an-sub', el).innerHTML = `${esc(d.range.label)} · ${fmt.dt(d.range.start)} → ${fmt.dt(d.range.end)} · ${fmt.n(d.count)} alarms raised${d.truncated ? ` <span class="prov derived" title="Only the newest alarms were fetched from Camview">BASED ON NEWEST ${fmt.n(v.source?.alarmsScanned || d.count)}</span>` : ''}`;

      // heatmap
      const hm = d.heatmap;
      const heat = hm.max ? `<div class="heat" role="grid" aria-label="Alarms by date and hour">
        <span></span>${Array.from({ length: 24 }, (_, h) => `<span class="hl" style="text-align:center">${h % 3 === 0 ? String(h).padStart(2, '0') : ''}</span>`).join('')}
        ${hm.dates.map((date, i) => `<span class="hl">${new Date(date + 'T00:00:00').toLocaleDateString(undefined, { day: '2-digit', month: 'short' })}</span>${hm.rows[i].map((c, h) => {
          const a = c ? 0.15 + 0.85 * (c / hm.max) : 0;
          return `<span class="hc" role="gridcell" tabindex="${c ? 0 : -1}" data-hd="${date}" data-hh="${h}" title="${new Date(date + 'T00:00:00').toLocaleDateString()} ${String(h).padStart(2, '0')}:00 — ${c} alarm${c === 1 ? '' : 's'}" style="${c ? `background:color-mix(in srgb, var(--chart-1) ${Math.round(a * 100)}%, var(--panel-3))` : ''}"></span>`;
        }).join('')}`).join('')}</div>
        <div class="row muted" style="font-size:11px;margin-top:8px">Fewer <span class="sw" style="background:color-mix(in srgb,var(--chart-1) 15%,var(--panel-3))"></span><span class="sw" style="background:color-mix(in srgb,var(--chart-1) 55%,var(--panel-3))"></span><span class="sw" style="background:var(--chart-1)"></span> More · max ${hm.max} per hour · click a cell to open history</div>`
        : empty('No activity in this period');

      // calendar (daily) — pad to week start
      const days = d.daily;
      const first = days.length ? new Date(days[0].date + 'T00:00:00') : null;
      const pad = first ? (first.getDay() + 6) % 7 : 0;
      const dmax = Math.max(1, ...days.map((x) => x.total));
      const cal = days.length ? `<div class="cal" style="margin-bottom:4px">${['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((w) => `<div class="muted" style="font-size:10.5px;text-align:center">${w}</div>`).join('')}</div>
        <div class="cal">${Array.from({ length: pad }, () => '<div class="d out"></div>').join('')}${days.map((x) => `<div class="d" tabindex="0" role="button" data-day="${x.date}" title="${x.date}: ${x.total} alarms (${x.valid} valid, ${x.invalid} invalid, ${x.pending} pending)" style="${x.total ? `box-shadow:inset 0 -3px 0 color-mix(in srgb,var(--chart-1) ${Math.round(20 + 80 * x.total / dmax)}%,transparent)` : ''}">
          <div class="dn">${new Date(x.date + 'T00:00:00').toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}</div><div class="dc">${x.total || '<span class="muted">0</span>'}</div></div>`).join('')}</div>` : empty('No days');

      const vt = v.camview, vo = v.ops;
      const verdicts = `
        <div class="section-title">Camview verdict vs Ops verdict</div>
        <div class="grid g-2">
          ${card({ title: 'Camview verdict', sub: 'lastActionType of alarms raised in the range', actions: prov('direct'), body: `
            <div class="kpis" style="grid-template-columns:repeat(3,1fr);margin-bottom:10px">
              ${kpi({ label: 'Resolved', value: vt.resolved, sub: 'valid + invalid' })}
              ${kpi({ label: 'False-alarm rate', value: fmt.pct(vt.falseAlarmRate), sub: 'invalid ÷ resolved', accent: 'violet' })}
              ${kpi({ label: 'Valid rate', value: fmt.pct(vt.validRate), accent: 'good' })}</div>
            <div class="split" role="img" aria-label="Verdict split">${[['valid', 'Valid'], ['pending', 'Pending'], ['invalid', 'Invalid'], ['exception', 'Exception']].filter(([k]) => vt[k]).map(([k, l]) => `<div style="flex:${vt[k]};background:${VERDICT_COLORS[l]}" title="${l}: ${vt[k]}"></div>`).join('')}</div>
            ${legend([['valid', 'Valid'], ['pending', 'Pending'], ['invalid', 'Invalid'], ['exception', 'Exception']].map(([k, l]) => ({ label: l, color: VERDICT_COLORS[l], value: vt[k] })))}` })}
          ${card({ title: 'Ops team verdict', sub: 'Decisions recorded in Command Center in the range', actions: prov('derived'), body: `
            <div class="kpis" style="grid-template-columns:repeat(3,1fr)">
              ${kpi({ label: 'Marked invalid by Ops', value: vo.marked_invalid, sub: `${fmt.pct(vo.falseAlarmRate)} of ops decisions`, accent: 'violet' })}
              ${kpi({ label: 'Marked valid by Ops', value: vo.marked_valid, accent: 'good' })}
              ${kpi({ label: 'Exception / ack', value: `${vo.marked_exception} / ${vo.acknowledged}` })}
              ${kpi({ label: 'Review coverage', value: fmt.pct(vo.coverage), sub: `${vo.reviewedOfRaised} of ${vt.total} raised` })}
              ${kpi({ label: 'Median time to review', value: vo.medianMinutesToReview == null ? '—' : fmt.dur(vo.medianMinutesToReview), sub: 'raised → first decision' })}
              ${kpi({ label: 'Agreement with Camview', value: fmt.pct(vo.agreement.rate), sub: vo.agreement.compared ? `${vo.agreement.agreed} of ${vo.agreement.compared} match` : 'no overlapping decisions' })}
            </div>` })}
        </div>
        <div class="grid g-main" style="margin-top:14px">
          ${card({ title: `${icon('users')} Ops team activity`, sub: 'Review actions per operator in the range', flush: true,
            body: v.operators.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Operator</th><th class="num">Actions</th><th class="num">Valid</th><th class="num">Invalid</th><th class="num">Exception</th><th class="num">Ack</th><th class="num">Reopen</th><th class="num">Notes</th><th>Last active</th></tr></thead><tbody>
              ${v.operators.map((o) => `<tr><td><b>${esc(o.operator)}</b></td><td class="num">${o.actions}</td><td class="num">${o.mark_valid}</td><td class="num">${o.mark_invalid}</td><td class="num">${o.mark_exception}</td><td class="num">${o.acknowledge}</td><td class="num">${o.reopen}</td><td class="num">${o.note}</td><td class="dim">${fmt.rel(o.lastAt)}</td></tr>`).join('')}</tbody></table></div>` : empty('No operator activity in this range') })}
          ${card({ title: 'Noisiest cameras (invalid alarms)', sub: 'Most invalid alarms — candidates for tuning', flush: true,
            body: v.noisyCameras.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Camera · location</th><th class="num">Invalid</th><th class="num">Total</th><th class="num">Rate</th></tr></thead><tbody>${v.noisyCameras.map((c) => `<tr><td>${esc(c.name)}</td><td class="num">${c.invalid}</td><td class="num">${c.total}</td><td class="num">${fmt.pct(c.falseAlarmRate)}</td></tr>`).join('')}</tbody></table></div>` : empty('No invalid alarms') })}
        </div>
        <div style="margin-top:14px">${card({ title: `${icon('report')} Alert lists`, flush: true,
          actions: '<button class="btn sm" id="an-listcsv">' + icon('download', 's') + ' Export list</button>',
          body: `<div class="tabs in-card">${LIST_TABS.map(([k, l]) => `<button class="tab ${k === listTab ? 'on' : ''}" data-list="${k}">${l}<span class="n">${v.listTotals[k]}</span></button>`).join('')}</div><div id="an-list"></div>` })}</div>`;

      bodyEl.innerHTML = `
        <div class="kpis">
          ${kpi({ label: 'Alarms raised', value: m.total, icon: 'layers' })}
          ${kpi({ label: 'Critical', value: m.critical, icon: 'alert', accent: 'critical' })}
          ${kpi({ label: 'Pending', value: m.pending, icon: 'clock', accent: 'warning' })}
          ${kpi({ label: 'Valid', value: m.valid, icon: 'check', accent: 'good' })}
          ${kpi({ label: 'Invalid', value: m.invalid, sub: `false-alarm rate ${fmt.pct(m.falseAlarmRate)}`, icon: 'x', accent: 'violet' })}
          ${kpi({ label: 'Exceptions', value: m.exception, icon: 'bang' })}
          ${kpi({ label: 'Suppressed', value: d.suppressed, icon: 'eye' })}
          ${kpi({ label: 'Repeated', value: m.repeated, icon: 'zap' })}
          ${kpi({ label: 'Shared with client', value: m.sharedWithClient, icon: 'share', accent: 'info' })}
        </div>
        <div class="grid g-main">
          ${card({ title: `${icon('chart')} Alarm volume`, sub: hourBucket ? 'Last 24 hours, per hour, by priority' : 'Per day, by Camview status',
            body: hourBucket
              ? `${legend(['critical', 'high', 'medium', 'low'].map((k) => ({ label: k[0].toUpperCase() + k.slice(1), color: PRIORITY_COLORS[k] })))}<div class="chart lg"><canvas id="an-vol"></canvas></div>`
              : `${legend(['Valid', 'Pending', 'Invalid', 'Exception'].map((k) => ({ label: k, color: VERDICT_COLORS[k] })))}<div class="chart lg"><canvas id="an-vol"></canvas></div>` })}
          <div class="stack">
            ${card({ title: 'Status (Camview)', sub: 'Click a row to open those alarms', actions: prov('direct'), body: bars(d.statusDistribution.map((x) => ({ ...x, color: VERDICT_COLORS[x.label] || 'var(--st-neutral)', sw: VERDICT_COLORS[x.label] })), { onClickAttr: (x) => linkAttr(x.key == null ? null : hist({ lastActionType: x.key })) }) })}
            ${card({ title: 'Priority', sub: 'Click a row to open those alarms', actions: prov('direct'), body: bars(d.priorityDistribution.map((x) => ({ ...x, color: PRIORITY_COLORS[x.key] || 'var(--st-neutral)', sw: PRIORITY_COLORS[x.key] })), { onClickAttr: (x) => linkAttr(PRIORITY_COLORS[x.key] ? hist({ priority: x.key }) : null) }) })}
          </div>
        </div>
        <div class="grid g-3" style="margin-top:14px">
          ${card({ title: 'Alarm type', actions: prov('direct'), body: bars(d.typeDistribution, { onClickAttr: (x) => linkAttr(x.key && x.key !== 'Unknown' ? hist({ search: x.key }) : null) }) })}
          ${card({ title: 'Shift', sub: 'Labels as reported by Camview', actions: prov('direct'), body: bars(d.shiftDistribution, { onClickAttr: (x) => linkAttr(x.key && x.key !== 'No shift' ? hist({ shiftLabel: x.key }) : null) }) })}
          ${card({ title: 'Recurrence', sub: 'Alarms by totalTimesReported', actions: prov('derived'), body: bars(d.recurrence.map((x) => ({ ...x, label: `${x.label} report${x.label === '1' ? '' : 's'}` }))) })}
        </div>
        <div class="section-title">Where</div>
        ${card({ title: `${icon('tree')} By location`, sub: 'Alarms and false-alarm rate per TC and centre', flush: true,
          body: `<div class="tabs in-card">${[['byTc', 'TC'], ['byCentre', 'Centre']].map(([k, l]) => `<button class="tab ${k === levelTab ? 'on' : ''}" data-lvltab="${k}">${l}<span class="n">${d[k].length}</span></button>`).join('')}</div>
            <div id="an-level">${levelTable(d[levelTab], { byTc: 'TC', byCentre: 'Centre' }[levelTab], LVL_KEY[levelTab])}</div>` })}
        <div class="grid g-main" style="margin-top:14px">
          ${card({ title: `${icon('camera')} Top cameras`, flush: true, actions: '<a class="btn sm" href="#/cameras">All cameras</a>',
            body: d.topCameras.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Camera</th><th>Location</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Occurrences</th><th>Latest</th></tr></thead><tbody>
              ${d.topCameras.map((c) => `<tr class="link" tabindex="0" data-href="#/cameras/${encodeURIComponent(c.cameraId)}?projectId=${encodeURIComponent(pid)}"><td class="mono">${esc(c.code)}${c.mapped ? '' : ' <span class="prov unavailable">UNMAPPED</span>'}</td><td class="ctx-path">${esc(c.location || '—')}</td><td class="num">${c.count}</td><td class="num">${c.critical}</td><td class="num">${c.occurrences}</td><td class="dim">${fmt.rel(c.latest)}</td></tr>`).join('')}</tbody></table></div>` : empty('No camera activity') })}
          ${card({ title: `${icon('share')} Client-shared activity`, sub: 'Alerts published to clients per day', actions: prov('derived'),
            body: d.clientShared.length ? `<div class="chart sm"><canvas id="an-shared"></canvas></div>${bars(d.clientShared.map((x) => ({ label: x.date, count: x.count })), { color: 'var(--vis-shared)' })}` : empty('Nothing shared in this range', 'No alerts were published to clients.') })}
        </div>
        <div class="section-title">When</div>
        <div class="grid g-main">
          ${card({ title: `${icon('grid')} Activity heatmap`, sub: 'Date × hour of day (your time zone)', body: heat })}
          ${card({ title: `${icon('calendar')} Alarm calendar`, sub: 'Click a day to open history', body: cal })}
        </div>
        ${verdicts}`;

      paintList();
      if (hourBucket) {
        bar($('#an-vol', el), {
          labels: d.hourly.map((h) => new Date(h.start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })),
          series: ['critical', 'high', 'medium', 'low'].map((k) => ({ label: k, data: d.hourly.map((h) => h[k]), color: PRIORITY_COLORS[k] })),
          stacked: true,
        });
      } else {
        bar($('#an-vol', el), {
          labels: d.daily.map((x) => new Date(x.date + 'T00:00:00').toLocaleDateString(undefined, { day: '2-digit', month: 'short' })),
          series: [['valid', 'Valid'], ['pending', 'Pending'], ['invalid', 'Invalid'], ['exception', 'Exception']].map(([k, l]) => ({ label: l, data: d.daily.map((x) => x[k]), color: VERDICT_COLORS[l] })),
          stacked: true,
          onClick: (i) => openDay(d.daily[i].date),
        });
      }
      if (d.clientShared.length) {
        bar($('#an-shared', el), { labels: d.clientShared.map((x) => x.date.slice(5)), series: [{ label: 'Shared', data: d.clientShared.map((x) => x.count), color: 'var(--vis-shared)' }] });
      }
    };

    const paintList = () => {
      const rows = d.verdicts.lists[listTab] || [];
      const host = $('#an-list', el);
      if (!host) return;
      host.innerHTML = rows.length ? `<div class="table-wrap" style="max-height:380px"><table class="t"><thead><tr><th>Raised</th><th>Alarm</th><th>Type</th><th>Location</th><th>Priority</th><th>Camview</th><th>Ops review</th><th>Reviewed by</th></tr></thead><tbody>
        ${rows.map((r) => `<tr class="link" tabindex="0" data-href="#/investigations/${encodeURIComponent(r.alarmId)}?projectId=${encodeURIComponent(pid)}"><td class="num dim">${fmt.dt(r.firstInstance)}</td><td class="mono">${esc(r.alarmId)}</td><td>${esc(r.alarmTypeName || '—')}</td><td><div class="cell-2"><span>${esc(r.hall || '—')}</span><span class="l2">${esc(r.cameraName || '')}</span></div></td><td>${esc(r.priority || '—')}</td><td>${esc(r.camviewStatus)}</td><td>${esc(OPS_LABEL[r.opsStatus] || r.opsStatus)}</td><td>${esc(r.reviewedBy || '—')}</td></tr>`).join('')}
        </tbody></table></div>${d.verdicts.listTotals[listTab] > rows.length ? `<div class="muted" style="padding:8px 14px">Showing ${rows.length} of ${d.verdicts.listTotals[listTab]}</div>` : ''}` : empty('Nothing in this list for the range');
    };

    const openDay = (date, hour) => {
      const start = new Date(`${date}T${hour == null ? '00' : String(hour).padStart(2, '0')}:00:00`);
      const end = new Date(start.getTime() + (hour == null ? 86400000 : 3600000) - 1000);
      location.hash = `#/history?from=${encodeURIComponent(start.toISOString())}&to=${encodeURIComponent(end.toISOString())}`;
    };

    const csvCell = (v) => `"${String(v ?? '').replace(/"/g, '""')}"`;
    ctx.onCleanup(delegate(el, 'click', '#an-listcsv', () => {
      const rows = d.verdicts.lists[listTab] || [];
      const head = ['Raised', 'Alarm ID', 'Type', 'Location', 'Camera', 'Priority', 'Camview verdict', 'Ops review', 'Reviewed by', 'Reviewed at'];
      const csv = [head, ...rows.map((r) => [r.firstInstance, r.alarmId, r.alarmTypeName, r.hall, r.cameraName, r.priority, r.camviewStatus, OPS_LABEL[r.opsStatus] || r.opsStatus, r.reviewedBy, r.reviewedAt])]
        .map((r) => r.map(csvCell).join(',')).join('\n');
      const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url; a.download = `camview-${listTab}-${range}.csv`; a.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-lvltab]', (e, b) => {
      levelTab = b.dataset.lvltab;
      $$('[data-lvltab]', el).forEach((x) => x.classList.toggle('on', x === b));
      $('#an-level', el).innerHTML = levelTable(d[levelTab], { byTc: 'TC', byCentre: 'Centre' }[levelTab], LVL_KEY[levelTab]);
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-list]', (e, b) => { listTab = b.dataset.list; $$('[data-list]', el).forEach((x) => x.classList.toggle('on', x === b)); paintList(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-range]', (e, b) => {
      range = b.dataset.range;
      $$('[data-range]', el).forEach((x) => x.classList.toggle('on', x === b));
      $('#an-custom', el).classList.toggle('hidden', range !== 'custom');
      if (range !== 'custom') { ctx.setQuery({ range, from: '', to: '' }); reload(); }
    }));
    $('#an-apply', el).addEventListener('click', () => {
      from = $('#an-from', el).value; to = $('#an-to', el).value;
      if (!from || !to || new Date(from) >= new Date(to)) return;
      ctx.setQuery({ range, from, to }); reload();
    });
    ctx.onCleanup(delegate(el, 'click', '[data-view]', (e, b) => {
      if (b.dataset.view === view) return;
      view = b.dataset.view;
      $$('[data-view]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
      ctx.setQuery({ tab: view === 'compare' ? 'compare' : '', level: view === 'compare' ? cmpLevel : '' });
      bodyEl.innerHTML = skeleton(8, 50);
      reload();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-cmplevel]', (e, b) => {
      cmpLevel = b.dataset.cmplevel; cmpSel = new Set();
      ctx.setQuery({ level: cmpLevel, ids: '' });
      loadCompare();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-cmpsel]', (e, b) => {
      const code = b.dataset.cmpsel;
      cmpSel.has(code) ? cmpSel.delete(code) : cmpSel.add(code);
      ctx.setQuery({ ids: [...cmpSel].join(',') });
      paintCompare();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-cmpall]', () => { cmpSel = new Set(); ctx.setQuery({ ids: '' }); paintCompare(); }));
    ctx.onCleanup(delegate(el, 'click', '.bar-row[data-href]', (e, r) => { location.hash = r.dataset.href; }));
    ctx.onCleanup(delegate(el, 'keydown', '.bar-row[data-href], tr[data-href]', (e, r) => { if (e.key === 'Enter') location.hash = r.dataset.href; }));
    $('#an-print', el).addEventListener('click', () => window.print());
    ctx.onCleanup(delegate(el, 'click', '[data-hd]', (e, c) => openDay(c.dataset.hd, +c.dataset.hh)));
    ctx.onCleanup(delegate(el, 'keydown', '[data-hd]', (e, c) => { if (e.key === 'Enter') openDay(c.dataset.hd, +c.dataset.hh); }));
    ctx.onCleanup(delegate(el, 'click', '[data-day]', (e, c) => openDay(c.dataset.day)));
    ctx.onCleanup(delegate(el, 'keydown', '[data-day]', (e, c) => { if (e.key === 'Enter') openDay(c.dataset.day); }));
    ctx.onCleanup(delegate(el, 'click', 'tr[data-href]', (e, tr) => { location.hash = tr.dataset.href; }));
    await reload();
  },
};
