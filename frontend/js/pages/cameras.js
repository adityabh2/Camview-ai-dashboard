// pages/cameras.js — CAMERA INTELLIGENCE: neutral activity rankings.
// Rankings describe activity only; no camera is labelled "good" or "bad".

import * as api from '../core/api.js';
import { currentProject, projectInfo, on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, table, pager, errorBox, skeleton, empty, debounce, delegate, prov, $ } from '../core/ui.js';

const RANKS = [
  ['count', 'Highest alarm activity'],
  ['recent', 'Most recent activity'],
  ['occurrences', 'Highest recurrence'],
  ['code', 'By camera code'],
];

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Cameras', `${esc(projectInfo(pid).code)} · camera intelligence`);
    let sort = ctx.query.sort || 'count';
    let search = ctx.query.search || '';
    let show = ctx.query.show || 'active';          // active = cameras with alerts · all · unmapped
    let page = 1;
    const SIZE = 50;
    let data = null;

    el.innerHTML = `
      <div class="page-head"><div><h2>Camera intelligence</h2><p>Alarm activity per camera in the monitored window. Rankings are neutral activity measures.</p></div></div>
      <div id="cm-kpis"></div>
      <div class="tabs" role="tablist" id="cm-tabs">${RANKS.map(([k, l]) => `<button class="tab ${k === sort ? 'on' : ''}" role="tab" aria-selected="${k === sort}" data-sort="${k}">${l}</button>`).join('')}</div>
      <div class="filters"><input class="input" style="min-width:280px" id="cm-search" data-page-search placeholder="Search camera code, name or location…  ( / )" value="${esc(search)}">
        <div class="seg" role="group" aria-label="Show cameras" id="cm-show">${[['active', 'With alerts'], ['all', 'All cameras'], ['unmapped', 'Unmapped']].map(([k, l]) => `<button type="button" data-show="${k}" class="${k === show ? 'on' : ''}">${l} <span class="n" data-shown="${k}"></span></button>`).join('')}</div></div>
      <section class="card"><div class="card-b flush" id="cm-body">${skeleton(8, 26)}</div><div id="cm-pager"></div></section>`;

    const paint = () => {
      const s = search.toLowerCase();
      const kind = (c) => (show === 'active' ? c.count > 0 : show === 'unmapped' ? !c.mapped : true);
      const matching = data.items.filter((c) => !s || [c.code, c.name, c.location, String(c.cameraId)].some((v) => String(v || '').toLowerCase().includes(s)));
      [['active', (c) => c.count > 0], ['all', () => true], ['unmapped', (c) => !c.mapped]].forEach(([k, f]) => { const n = $(`[data-shown="${k}"]`, el); if (n) n.textContent = fmt.n(matching.filter(f).length); });
      const all = matching.filter(kind);
      const pages = Math.max(1, Math.ceil(all.length / SIZE));
      page = Math.min(page, pages);
      const rows = all.slice((page - 1) * SIZE, page * SIZE);
      const active = data.items.filter((c) => c.count > 0).length;
      const unmapped = data.items.filter((c) => !c.mapped).length;
      $('#cm-kpis', el).innerHTML = `<div class="kpis">
        ${kpi({ label: 'Cameras', value: data.items.length, sub: `${fmt.n(data.masterCameras)} in master data`, icon: 'camera' })}
        ${kpi({ label: 'With alarms', value: active, sub: 'in current window', icon: 'zap', accent: 'info' })}
        ${kpi({ label: 'Alarms', value: data.items.reduce((t, c) => t + c.count, 0), icon: 'layers' })}
        ${kpi({ label: 'Critical', value: data.items.reduce((t, c) => t + c.critical, 0), icon: 'alert', accent: 'critical' })}
        ${kpi({ label: 'Pending', value: data.items.reduce((t, c) => t + c.pending, 0), icon: 'clock', accent: 'warning' })}
        ${kpi({ label: 'Unmapped', value: unmapped, sub: 'not in nomenclature', icon: 'tree', href: '#/context?tab=quality' })}
      </div>`;
      table($('#cm-body', el), {
        columns: [
          { label: 'Camera', render: (c) => `<div class="cell-2"><span class="mono" style="font-weight:600">${esc(c.code)}</span><span class="l2">${esc(c.name || '')}</span></div>` },
          { label: 'Location', render: (c) => `<span class="ctx-path">${esc(c.location || '—')}</span>` },
          { label: 'Mapping', render: (c) => (c.mapped ? `<span class="b outline">${icon('check')}mapped</span>` : '<span class="prov unavailable">UNMAPPED</span>') },
          { label: 'Alarms', num: true, render: (c) => `<span class="num">${fmt.n(c.count)}</span>` },
          { label: 'Critical', num: true, render: (c) => `<span class="num">${fmt.n(c.critical)}</span>` },
          { label: 'Pending', num: true, render: (c) => `<span class="num">${fmt.n(c.pending)}</span>` },
          { label: 'Occurrences', num: true, render: (c) => `<span class="num">${fmt.n(c.occurrences)}</span>` },
          { label: 'Latest', render: (c) => (c.latest ? `<div class="cell-2"><span class="num">${fmt.dt(c.latest)}</span><span class="l2">${fmt.rel(c.latest)}</span></div>` : '<span class="muted">No alarms</span>') },
        ],
        rows,
        rowKey: (c) => String(c.cameraId),
        onRow: (c) => { location.hash = `#/cameras/${encodeURIComponent(c.cameraId)}?projectId=${encodeURIComponent(pid)}`; },
        emptyHtml: empty('No cameras found', search ? 'Nothing matches the search.' : show === 'active' ? 'No camera has alerts in the current window.' : 'No cameras in master data and no alarms received yet.', 'camera'),
      });
      pager($('#cm-pager', el), { page, totalPages: pages, totalElements: all.length, size: SIZE, onPage: (p) => { page = p; paint(); el.scrollIntoView({ block: 'start' }); } });
    };

    const load = async () => {
      try {
        data = await api.get('/api/cameras', { projectId: pid, sort });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#cm-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-sort]', (e, b) => {
      sort = b.dataset.sort;
      el.querySelectorAll('[data-sort]').forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
      ctx.setQuery({ sort });
      load();
    }));
    $('#cm-search', el).addEventListener('input', debounce((e) => { search = e.target.value.trim(); page = 1; ctx.setQuery({ search }); if (data) paint(); }, 200));
    ctx.onCleanup(delegate(el, 'click', '[data-show]', (e, b) => {
      show = b.dataset.show; page = 1; ctx.setQuery({ show: show === 'active' ? '' : show });
      el.querySelectorAll('[data-show]').forEach((x) => x.classList.toggle('on', x === b));
      if (data) paint();
    }));
    void prov;
    ctx.onCleanup(on('data', load));
    await load();
  },
};
