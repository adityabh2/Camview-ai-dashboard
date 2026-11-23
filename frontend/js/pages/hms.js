// pages/hms.js — HMS board: alerts per classification (CamviewAI feature list), in real time.
// One tile per alert type: LIVE now (by the alert's own time), today, in the window, waiting for a decision.
// Re-reads on every data change (push updates), so the numbers follow Camview as alerts arrive.

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
    let data = null;
    el.innerHTML = `
      <div class="page-head"><div><h2>HMS · alerts by classification</h2>
        <p id="hms-fresh">Live counts for every alert type. Updates by itself as Camview sends alerts.</p></div>
        <label class="check" style="margin:0"><input type="checkbox" id="hms-all" ${showAll ? 'checked' : ''}> Show types with no alerts</label></div>
      <nav class="dm-tabs" id="hms-codes" aria-label="Project"></nav>
      <div class="kpis" id="hms-kpis" style="margin-top:14px">${skeleton(1, 58)}</div>
      <div class="hms-grid" id="hms-grid">${skeleton(6, 90)}</div>`;

    const tile = (t) => `<a class="hms-tile ${t.live ? 'is-live' : ''} ${t.known ? '' : 'is-unknown'}" data-key="t:${esc(t.type)}"
        href="#/alerts?status=all&range=all&type=${encodeURIComponent(t.type ?? '')}" title="Open these alerts">
        <div class="hms-top"><span class="hms-code">${esc(t.short)}</span>${t.live ? `<span class="hms-live"><i></i>${fmt.n(t.live)} live</span>` : ''}</div>
        <div class="hms-name">${esc(t.name)}</div>
        <div class="hms-big num">${fmt.n(t.today)}<span>today</span></div>
        <div class="hms-foot"><span><b class="num">${fmt.n(t.total)}</b> in window</span><span><b class="num">${fmt.n(t.pending)}</b> pending</span>
          <span class="muted">${t.latestAt ? fmt.rel(t.latestAt) : 'none yet'}</span></div></a>`;

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
      const rows = data.types.filter((t) => showAll || t.total);
      morph($('#hms-grid', el), rows.length ? rows.map(tile).join('')
        : '<div class="card" style="grid-column:1/-1"><div class="card-b muted">No alerts in the loaded window for this selection.</div></div>');
    };

    const load = async () => {
      try {
        if (!codes.length) {
          try { codes = ((await api.get('/api/project-codes')).items || []).filter((c) => !c.archived); } catch { codes = []; }
          if (selCode && !codes.some((c) => c.code === selCode)) { selCode = ''; setPref('dashCode', ''); }
        }
        $('#hms-codes', el).innerHTML = codes.length ? `<button type="button" class="dm-tab ${selCode ? '' : 'on'}" data-hcode="">All projects</button>`
          + codes.map((c) => `<button type="button" class="dm-tab ${selCode === c.code ? 'on' : ''}" data-hcode="${esc(c.code)}" title="${esc(c.code)}">${esc(codeParts(c.code)[0] || c.code)}<span class="muted">${esc(codeParts(c.code).slice(1, 3).join('/'))}</span></button>`).join('') : '';
        const q = { tzOffset: -new Date().getTimezoneOffset() };
        if (selCode) q.code = selCode;
        data = await api.get('/api/hms', q);
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale() || data) return;
        $('#hms-grid', el).innerHTML = errorBox(e);
      }
    };

    el.addEventListener('click', (e) => {
      const b = e.target.closest('[data-hcode]');
      if (!b) return;
      selCode = b.dataset.hcode; setPref('dashCode', selCode); load();
    });
    $('#hms-all', el).addEventListener('change', (e) => { showAll = e.target.checked; setPref('hmsShowAll', showAll); if (data) paint(); });
    ctx.onCleanup(on('data', () => load()));                 // real time: every data change re-reads the counts
    const timer = setInterval(() => { if (!document.hidden) load(); }, 30000);   // "live" ages even without new data
    ctx.onCleanup(() => clearInterval(timer));
    await load();
  },
};
