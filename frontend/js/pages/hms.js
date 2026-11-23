// pages/hms.js — HMS board: alerts per classification (CamviewAI feature list), in real time.
// All projects: a project x alert-type table (one row per project code) and the totals per type.
// One project: its tiles per alert type — LIVE now (by the alert's own time), today, in the window, pending.
// Re-reads on every data change (push updates); one read at a time, so it never piles up.

import * as api from '../core/api.js';
import { on, pref, setPref } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, errorBox, skeleton, morph, $ } from '../core/ui.js';

const codeParts = (c) => String(c || '').split('/').map((s) => s.trim()).filter(Boolean);

export default {
  async render(el, ctx) {
    setTitle('HMS', 'Alerts per classification, in real time');
    let codes = [];
    let selCode = pref('dashCode', '') || '';
    let showAll = pref('hmsShowAll', false);
    let data = null, busy = false, again = false;
    el.innerHTML = `
      <div class="page-head hms-head"><div><h2>Alerts by classification</h2>
        <p id="hms-fresh">Live counts for every alert type. Updates by itself as Camview sends alerts.</p></div>
        <label class="check" style="margin:0"><input type="checkbox" id="hms-all" ${showAll ? 'checked' : ''}> Show types with no alerts</label></div>
      <nav class="dm-tabs" id="hms-codes" aria-label="Project"></nav>
      <div class="kpis hms-kpis" id="hms-kpis">${skeleton(1, 58)}</div>
      <section id="hms-matrix"></section>
      <h3 class="hms-sub" id="hms-sub">By alert type</h3>
      <div class="hms-grid" id="hms-grid">${skeleton(6, 90)}</div>`;

    const tabs = () => {
      $('#hms-codes', el).innerHTML = codes.length ? `<button type="button" class="dm-tab ${selCode ? '' : 'on'}" data-hcode="">All projects</button>`
        + codes.map((c) => `<button type="button" class="dm-tab ${selCode === c.code ? 'on' : ''}" data-hcode="${esc(c.code)}" title="${esc(c.code)}">${esc(codeParts(c.code)[0] || c.code)}<span class="muted">${esc(codeParts(c.code).slice(1, 3).join('/'))}</span></button>`).join('') : '';
    };

    const tile = (t) => `<a class="hms-tile ${t.live ? 'is-live' : ''} ${t.known ? '' : 'is-unknown'}" data-key="t:${esc(t.type)}"
        href="#/alerts?status=all&range=all&type=${encodeURIComponent(t.type ?? '')}" title="Open these alerts">
        <div class="hms-top"><span class="hms-code">${esc(t.short)}</span>${t.live ? `<span class="hms-live"><i></i>${fmt.n(t.live)} live</span>` : `<span class="muted hms-when">${t.latestAt ? fmt.rel(t.latestAt) : 'none yet'}</span>`}</div>
        <div class="hms-name">${esc(t.name)}</div>
        <div class="hms-nums"><div><b class="num">${fmt.n(t.today)}</b><span>today</span></div><div><b class="num">${fmt.n(t.total)}</b><span>in window</span></div><div><b class="num">${fmt.n(t.pending)}</b><span>pending</span></div></div>
        <div class="hms-bar"><span style="width:${data.totals.total ? Math.max(2, (t.total / data.totals.total) * 100) : 0}%"></span></div></a>`;

    // project x alert type: which exam produces which alerts (cells shaded by volume)
    const matrix = () => {
      const rows = data.byCode || [];
      if (selCode || !rows.length) return '';
      const used = data.types.filter((t) => t.total);
      const max = Math.max(1, ...rows.flatMap((r) => Object.values(r.types)));
      return `<div class="card hms-matrix"><div class="card-h"><h3>By project</h3><div class="sub">Alerts per classification for each project code · click a project to see its tiles</div></div>
        <div class="table-wrap"><table class="t hms-t"><thead><tr><th>Project</th><th class="num">Live</th><th class="num">Today</th><th class="num">Total</th>
          ${used.map((t) => `<th class="num" title="${esc(t.name)}">${esc(t.short)}</th>`).join('')}</tr></thead><tbody>
          ${rows.map((r) => `<tr class="${r.code ? 'link' : ''}" ${r.code ? `data-hcode="${esc(r.code)}" tabindex="0"` : ''}>
            <td><b>${esc(codeParts(r.label)[0] || r.label)}</b> <span class="muted">${esc(codeParts(r.label).slice(1, 3).join('/'))}</span></td>
            <td class="num">${r.live ? `<span class="hms-live"><i></i>${fmt.n(r.live)}</span>` : '<span class="muted">0</span>'}</td>
            <td class="num">${fmt.n(r.today)}</td><td class="num"><b>${fmt.n(r.total)}</b></td>
            ${used.map((t) => { const n = r.types[String(t.type)] || 0; return `<td class="num hms-cell" style="--a:${n ? 0.08 + 0.55 * (n / max) : 0}">${n ? fmt.n(n) : '<span class="muted">·</span>'}</td>`; }).join('')}</tr>`).join('')}
        </tbody></table></div></div>`;
    };

    const paint = () => {
      const tt = data.totals, cam = data.cameras;
      $('#hms-fresh', el).innerHTML = `Updated ${data.updatedAt ? fmt.time(data.updatedAt) : '—'} · ${fmt.n(data.projects)} project${data.projects === 1 ? '' : 's'}
        · live = happened in the last ${data.liveMinutes} min${data.feedsFailing ? ` · <span class="sla-attention">${data.feedsFailing} feed(s) not arriving</span>` : ''}`;
      morph($('#hms-kpis', el), [
        kpi({ label: 'Live now', value: tt.live, accent: tt.live ? 'good' : '', icon: 'zap', sub: `last ${data.liveMinutes} minutes` }),
        kpi({ label: 'Today', value: tt.today, icon: 'clock' }),
        kpi({ label: 'In the window', value: tt.total, icon: 'alert' }),
        kpi({ label: 'Waiting for a decision', value: tt.pending, accent: tt.pending ? 'warning' : '', icon: 'bang', href: '#/alerts?status=pending' }),
        kpi({ label: 'Cameras online', value: cam.online, accent: 'good', icon: 'camera', sub: `${fmt.n(cam.seen)} cameras seen` }),
        kpi({ label: 'Cameras offline', value: cam.offline + cam.syncFailed, accent: cam.offline + cam.syncFailed ? 'critical' : '', icon: 'camera',
          sub: cam.syncFailed ? `${fmt.n(cam.syncFailed)} frame sync failed` : 'as Camview reports them' }),
      ].join(''));
      morph($('#hms-matrix', el), matrix());
      const sel = codes.find((c) => c.code === selCode);
      $('#hms-sub', el).textContent = sel ? `${codeParts(sel.code)[0]} · by alert type` : 'All projects · by alert type';
      const rows = data.types.filter((t) => showAll || t.total);
      morph($('#hms-grid', el), rows.length ? rows.map(tile).join('')
        : '<div class="card" style="grid-column:1/-1"><div class="card-b muted">No alerts in the loaded window for this selection.</div></div>');
    };

    const load = async () => {
      if (busy) { again = true; return; }       // one read at a time: data events never pile up requests
      busy = true;
      try {
        const q = { tzOffset: -new Date().getTimezoneOffset() };
        if (selCode) q.code = selCode;
        const [c, d] = await Promise.all([          // codes and counts in parallel: the board shows at once
          codes.length ? Promise.resolve(null) : api.get('/api/project-codes').catch(() => ({ items: [] })),
          api.get('/api/hms', q),
        ]);
        if (c) {
          codes = (c.items || []).filter((x) => !x.archived);
          if (selCode && !codes.some((x) => x.code === selCode)) { selCode = ''; setPref('dashCode', ''); }
        }
        tabs();
        data = d;
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (!ctx.isStale() && !data) $('#hms-grid', el).innerHTML = errorBox(e);
      } finally {
        busy = false;
        $('#hms-grid', el)?.classList.remove('is-loading');
        if (again && !ctx.isStale()) { again = false; load(); }
      }
    };

    const pick = (code) => {
      selCode = code; setPref('dashCode', selCode);
      tabs();
      $('#hms-grid', el).classList.add('is-loading');   // the old numbers dim at once while the new ones load
      load();
    };
    el.addEventListener('click', (e) => { const b = e.target.closest('[data-hcode]'); if (b) pick(b.dataset.hcode); });
    el.addEventListener('keydown', (e) => { const r = e.target.closest('tr[data-hcode]'); if (r && e.key === 'Enter') pick(r.dataset.hcode); });
    $('#hms-all', el).addEventListener('change', (e) => { showAll = e.target.checked; setPref('hmsShowAll', showAll); if (data) paint(); });
    ctx.onCleanup(on('data', () => load()));                 // real time: every data change re-reads the counts
    const timer = setInterval(() => { if (!document.hidden) load(); }, 30000);   // "live" ages even without new data
    ctx.onCleanup(() => clearInterval(timer));
    await load();
  },
};
