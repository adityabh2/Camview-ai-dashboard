// pages/brief.js — DAILY OPERATIONS BRIEF (#/brief?date=YYYY-MM-DD)
// A one-page, printable summary of one day for one project. Every figure is
// DERIVED from alarm data and Command Center records — nothing is AI-generated.

import * as api from '../core/api.js';
import { currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { bar } from '../core/charts.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, legend, prov, delegate, PRIORITY_COLORS, $ } from '../core/ui.js';

const PRIOS = ['critical', 'high', 'medium', 'low'];
const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const hm = (iso) => new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', ...(document.body.dataset.tz === 'utc' ? { timeZone: 'UTC' } : {}) });
const shiftDay = (s, n) => { const d = new Date(s + 'T00:00:00'); d.setDate(d.getDate() + n); return ymd(d); };

// Provenance tag in the tile's sub-line (keeps long labels readable).
const kp = (o) => kpi({ ...o, prov: undefined, sub: `${o.prov ? prov(o.prov) + ' ' : ''}${o.sub || ''}` });

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Daily Operations Brief', `${esc(projectInfo(pid).code)} · one day at a glance`);
    if (!pid) { el.innerHTML = empty('No project available', '', 'tree'); return; }
    const today = ymd(new Date());
    let date = /^\d{4}-\d{2}-\d{2}$/.test(ctx.query.date || '') ? ctx.query.date : today;
    if (date > today) date = today;
    let d = null;

    el.innerHTML = `
      <div class="page-head">
        <div><h2>Daily Operations Brief</h2><p id="br-sub">Loading…</p></div>
        <div class="row no-print">
          <button class="btn icon" id="br-prev" aria-label="Previous day">${icon('left')}</button>
          <input class="input" type="date" id="br-date" value="${esc(date)}" max="${esc(today)}" aria-label="Date">
          <button class="btn icon" id="br-next" aria-label="Next day">${icon('right')}</button>
          <button class="btn" id="br-today">Today</button>
          <button class="btn primary" id="br-print">${icon('report', 's')} Print</button>
        </div>
      </div>
      <div id="br-body">${skeleton(6, 56)}</div>`;
    const bodyEl = $('#br-body', el);

    const syncControls = () => {
      $('#br-date', el).value = date;
      $('#br-next', el).disabled = date >= today;
      $('#br-today', el).disabled = date === today;
    };

    const load = async () => {
      syncControls();
      bodyEl.innerHTML = skeleton(6, 56);
      try {
        d = await api.get('/api/brief', { projectId: pid, date, tzOffset: -new Date().getTimezoneOffset() });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        bodyEl.innerHTML = errorBox(e);
        $('[data-retry]', bodyEl)?.addEventListener('click', load);
      }
    };

    const setDate = (v) => {
      if (!v || v > today) return;
      date = v;
      ctx.setQuery({ date: date === today ? '' : date });
      load();
    };

    const hourRange = (start) => {
      const s = new Date(start);
      return { from: s.toISOString(), to: new Date(s.getTime() + 3599000).toISOString() };
    };

    const paint = () => {
      const t = d.totals;
      const dayLabel = new Date(date + 'T00:00:00').toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
      $('#br-sub', el).innerHTML = `${esc(dayLabel)} · ${esc(d.project.code)} ${esc(d.project.name || '')} ${prov('derived')}`;
      const dayStart = new Date(date + 'T00:00:00');
      const dayHistory = `#/history?from=${encodeURIComponent(dayStart.toISOString())}&to=${encodeURIComponent(new Date(dayStart.getTime() + 86399000).toISOString())}`;
      const hourly = (d.hourly || []).filter((h) => ymd(new Date(h.start)) === date);
      const peak = d.peakPeriod;
      const total = t.alarms || 0;

      const headline = total
        ? `On ${esc(dayLabel)}, <b>${fmt.n(total)}</b> alarm${total === 1 ? ' was' : 's were'} raised for ${esc(d.project.code)}, of which <b>${fmt.n(t.critical)}</b> critical.
           Camview reports <b>${fmt.n(t.valid)}</b> valid, <b>${fmt.n(t.invalid)}</b> invalid, <b>${fmt.n(t.exception)}</b> exception and <b>${fmt.n(t.pending)}</b> still pending.
           Operators validated <b>${fmt.n(t.validatedByOps)}</b> alarm${t.validatedByOps === 1 ? '' : 's'} and <b>${fmt.n(t.sharedWithClients)}</b> ${t.sharedWithClients === 1 ? 'was' : 'were'} shared with clients.
           ${peak ? `The busiest hour started at <b>${esc(hm(peak.start))}</b> with <b>${fmt.n(peak.count)}</b> alarms.` : ''}`
        : `No alarms were raised for ${esc(d.project.code)} on ${esc(dayLabel)}.`;

      bodyEl.innerHTML = `
        <div class="banner info">${icon('info')}<div class="grow">${headline}<div class="muted" style="font-size:11.5px;margin-top:4px">Data-derived summary built from alarm records and Command Center actions — not AI-generated and no causes are inferred.</div></div></div>
        ${d.truncated ? `<div class="banner warning">${icon('info')}<div class="grow">Camview returned more alarms than the analytics limit; figures are based on the newest alarms fetched for this day.</div></div>` : ''}
        <style>.kpis.nopad .k-label{padding-right:0}</style><div class="kpis nopad">
          ${kp({ label: 'Total alarms', value: t.alarms, icon: 'layers', href: dayHistory, prov: 'direct' })}
          ${kp({ label: 'Critical', value: t.critical, icon: 'alert', accent: 'critical', href: `${dayHistory}&priority=critical`, prov: 'direct' })}
          ${kp({ label: 'Pending', value: t.pending, icon: 'clock', accent: 'warning', href: `${dayHistory}&lastActionType=0`, prov: 'direct' })}
          ${kp({ label: 'Valid', value: t.valid, icon: 'check', accent: 'good', href: `${dayHistory}&lastActionType=1`, prov: 'direct' })}
          ${kp({ label: 'Invalid', value: t.invalid, icon: 'x', accent: 'violet', href: `${dayHistory}&lastActionType=2`, prov: 'direct' })}
          ${kp({ label: 'Exceptions', value: t.exception, icon: 'bang', href: `${dayHistory}&lastActionType=3`, prov: 'direct' })}
          ${kp({ label: 'Suppressed', value: t.suppressed, icon: 'eye', prov: 'direct' })}
          ${kp({ label: 'Validated by Ops', value: t.validatedByOps, icon: 'check', accent: 'good', prov: 'derived' })}
          ${kp({ label: 'Shared with clients', value: t.sharedWithClients, icon: 'share', accent: 'info', prov: 'derived' })}
          ${kp({ label: 'Open investigations', value: d.openInvestigations, sub: 'now, in the monitored window', icon: 'investigate', href: '#/investigations', prov: 'derived' })}
        </div>
        <div class="grid g-main">
          ${card({ title: `${icon('chart')} Hourly activity`, sub: 'Alarms raised per hour, by priority · click an hour to open it in History', actions: prov('derived'),
            body: hourly.some((h) => h.total) ? `${legend(PRIOS.map((k) => ({ label: k[0].toUpperCase() + k.slice(1), color: PRIORITY_COLORS[k] })))}<div class="chart lg"><canvas id="br-hourly" aria-label="Hourly activity"></canvas></div>`
              : empty('No activity on this day', '', 'chart') })}
          <div class="stack">
            ${card({ title: `${icon('clock')} Peak period`, actions: prov('derived'),
              body: peak ? `<div class="row" style="align-items:baseline;gap:10px"><span class="num" style="font-size:26px;font-weight:700">${esc(hm(peak.start))}</span><span class="dim">– ${esc(hm(new Date(new Date(peak.start).getTime() + 3600000).toISOString()))}</span></div>
                <div class="dim" style="margin-top:4px"><b class="num">${fmt.n(peak.count)}</b> alarms in the busiest hour</div>
                <a class="btn sm" style="margin-top:10px" href="#/history?from=${encodeURIComponent(hourRange(peak.start).from)}&to=${encodeURIComponent(hourRange(peak.start).to)}">Open these alarms</a>`
                : '<div class="muted">No peak — no alarms on this day.</div>' })}
            ${card({ title: `${icon('camera')} Highest observed activity`, sub: 'Cameras with the most alarms this day (neutral activity measure)', actions: prov('derived'), flush: true,
              body: d.highestActivity.length ? `<div class="list">${d.highestActivity.map((c, i) => `<a class="li" href="#/cameras/${encodeURIComponent(c.cameraId)}?projectId=${encodeURIComponent(pid)}">
                  <span class="b outline num">${i + 1}</span><div class="grow"><div class="t1 mono">${esc(c.code)}</div><div class="t2">${esc(c.location || 'Location not mapped')}</div></div>
                  <div style="text-align:right"><div class="num"><b>${fmt.n(c.count)}</b> alarms</div><div class="muted" style="font-size:11px">${fmt.n(c.critical)} critical · ${fmt.n(c.occurrences)} reports</div></div></a>`).join('')}</div>`
                : empty('No camera activity') })}
          </div>
        </div>
        <div class="grid g-2" style="margin-top:14px">
          ${card({ title: 'By priority', actions: prov('direct'), body: distList(d.byPriority, (x) => PRIORITY_COLORS[x.key] || 'var(--st-neutral)', (x) => (PRIORITY_COLORS[x.key] ? `${dayHistory}&priority=${encodeURIComponent(x.key)}` : null)) })}
          ${card({ title: 'By shift', sub: 'Shift labels as reported by Camview', actions: prov('direct'), body: distList(d.byShift, () => 'var(--chart-1)', (x) => (x.key && x.key !== 'No shift' ? `${dayHistory}&shiftLabel=${encodeURIComponent(x.key)}` : null)) })}
        </div>
        <div class="muted" style="font-size:11px;margin-top:14px">Generated ${esc(fmt.dt(new Date().toISOString()))} · CAMVIEW Command Center · internal use</div>`;

      if (hourly.some((h) => h.total)) {
        bar($('#br-hourly', el), {
          labels: hourly.map((h) => new Date(h.start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })),
          series: PRIOS.map((k) => ({ label: k, data: hourly.map((h) => h[k] || 0), color: PRIORITY_COLORS[k] })),
          stacked: true,
          onClick: (i) => { const r = hourRange(hourly[i].start); location.hash = `#/history?from=${encodeURIComponent(r.from)}&to=${encodeURIComponent(r.to)}`; },
        });
      }
    };

    $('#br-prev', el).addEventListener('click', () => setDate(shiftDay(date, -1)));
    $('#br-next', el).addEventListener('click', () => setDate(shiftDay(date, 1)));
    $('#br-today', el).addEventListener('click', () => setDate(today));
    $('#br-date', el).addEventListener('change', (e) => setDate(e.target.value));
    $('#br-print', el).addEventListener('click', () => window.print());
    await load();
  },
};

function distList(items, colorFn, hrefFn) {
  if (!items || !items.length) return empty('No data');
  const total = items.reduce((s, x) => s + x.count, 0) || 1;
  const max = Math.max(1, ...items.map((x) => x.count));
  return `<div class="bars">${items.map((x) => {
    const c = colorFn(x);
    const href = hrefFn(x);
    const tag = href ? 'a' : 'div';
    return `<${tag} class="bar-row" ${href ? `href="${esc(href)}"` : ''} style="grid-template-columns:minmax(80px,140px) 1fr 92px;color:inherit;text-decoration:none" title="${esc(x.label)}: ${x.count}">
      <span class="lab"><span class="sw" style="background:${c}"></span>${esc(x.label)}</span>
      <span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(1, (x.count / max) * 100)}%;background:${c}"></span></span>
      <span class="num" style="text-align:right">${fmt.n(x.count)} <span class="muted" style="font-size:11px">${fmt.pct(x.count / total)}</span></span></${tag}>`;
  }).join('')}</div>`;
}
