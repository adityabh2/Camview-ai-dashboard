// pages/watchlist.js — MY WATCHLIST: personalised operational view of the alarms,
// cameras and locations you chose to follow. Counts come from the live working
// window; entities outside your current scope are hidden by the server.

import * as api from '../core/api.js';
import { on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, empty, toast, delegate, $ } from '../core/ui.js';
import { miniRow } from '../components/alarms.js';

const TYPE_LABEL = { alarm: 'Alarms', camera: 'Cameras', project: 'Projects', tc: 'TCs', centre: 'Centres', room: 'Rooms' };
const TYPE_ICON = { alarm: 'investigate', camera: 'camera', project: 'tree', tc: 'tree', centre: 'building', room: 'building' };

function openLink(w) {
  const pid = encodeURIComponent(w.projectId);
  if (w.entityType === 'alarm') return `#/investigations/${encodeURIComponent(w.entityId)}?projectId=${pid}`;
  if (w.entityType === 'camera') return `#/cameras/${encodeURIComponent(w.entityId)}?projectId=${pid}`;
  if (['tc', 'centre'].includes(w.entityType)) return `#/live?${w.entityType}=${encodeURIComponent(w.entityId)}&projectId=${pid}`;
  if (w.entityType === 'room') return `#/context?node=${encodeURIComponent(w.entityId)}&projectId=${pid}`;
  return '#/command';
}

export default {
  async render(el, ctx) {
    setTitle('Watchlist', 'Alarms, cameras and locations you follow');
    el.innerHTML = skeleton(6, 60);

    const load = async () => {
      let d;
      try { d = await api.get('/api/watchlist'); }
      catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
        return;
      }
      if (ctx.isStale()) return;
      const head = `<div class="page-head"><div><h2>My watchlist</h2><p>You get an in-app notification when a new alarm arrives on anything you watch. Counts cover the monitored window. <span class="prov derived">DERIVED</span></p></div></div>`;
      if (!d.items.length) {
        el.innerHTML = head + card({ body: empty('You are not watching anything yet',
          'Use the <b>Watch</b> button on an investigation, a camera page, or a node in the Nomenclature explorer (project, TC, TC, centre or room).', 'eye') });
        return;
      }
      const byType = {};
      d.items.forEach((w) => { (byType[w.entityType] = byType[w.entityType] || []).push(w); });
      const order = ['alarm', 'camera', 'room', 'centre', 'tc', 'project'];
      el.innerHTML = head + order.filter((t) => byType[t]).map((t) => `
        <div class="section-title">${icon(TYPE_ICON[t], 's')} ${esc(TYPE_LABEL[t])} <span class="b outline">${byType[t].length}</span></div>
        <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(320px,1fr))">${byType[t].map((w) => `
          <section class="card"><div class="card-h">
            <span class="b outline">${esc(w.entityType.toUpperCase())}</span><h3 class="mono" style="margin:0">${esc(w.label || w.entityId)}</h3>
            <div class="actions"><a class="btn sm" href="${openLink(w)}">Open</a><button class="btn sm ghost" data-remove="${esc(w.entityType)}|${esc(w.entityId)}" aria-label="Stop watching ${esc(w.label || w.entityId)}">${icon('x', 's')}</button></div></div>
            <div class="card-b" style="padding-bottom:6px">
              <div class="grid g-4" style="gap:6px">
                <div class="kpi" style="padding:8px 10px"><div class="k-label">Alarms</div><div class="k-value" style="font-size:18px">${fmt.n(w.alarms)}</div></div>
                <div class="kpi accent-warning" style="padding:8px 10px"><div class="k-label">Pending</div><div class="k-value" style="font-size:18px">${fmt.n(w.pending)}</div></div>
                <div class="kpi accent-critical" style="padding:8px 10px"><div class="k-label">Critical</div><div class="k-value" style="font-size:18px">${fmt.n(w.critical)}</div></div>
                <div class="kpi accent-info" style="padding:8px 10px"><div class="k-label">New</div><div class="k-value" style="font-size:18px">${fmt.n(w.newSinceWatched)}</div></div>
              </div>
              <div class="muted" style="font-size:11px;margin:6px 0 2px">Watching since ${fmt.dt(w.createdAt)} · "New" = raised after you started watching</div>
            </div>
            ${w.latest ? `<div class="list" style="border-top:1px solid var(--border)">${miniRow(w.latest)}</div>` : `<div class="card-b muted" style="border-top:1px solid var(--border)">No alarm in the monitored window.</div>`}
          </section>`).join('')}</div>`).join('');
    };

    ctx.onCleanup(delegate(el, 'click', '[data-remove]', async (e, b) => {
      const [entityType, entityId] = b.dataset.remove.split('|');
      try { await api.post('/api/watchlist/remove', { entityType, entityId }); toast('Removed from watchlist', 'success'); load(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(on('data', () => load()));
    await load();
  },
};
