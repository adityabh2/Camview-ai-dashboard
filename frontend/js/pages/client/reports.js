// pages/client/reports.js — reports shared with this client.

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, card, empty, errorBox, skeleton, toast, delegate, $ } from '../../core/ui.js';

export default {
  async render(el, ctx) {
    setTitle('Reports', 'Reports shared with you');
    el.innerHTML = skeleton(4, 40);
    const load = async () => {
      try {
        const r = await api.get('/api/client/reports');
        if (ctx.isStale()) return;
        el.innerHTML = `<div class="page-head"><div><h2>Reports</h2><p>Reports built only from alerts shared with you.</p></div></div>` + card({ flush: true,
          body: r.items.length ? `<div class="list">${r.items.map((x) => `<div class="li"><span>${icon('report')}</span><div class="grow"><div class="t1"><a href="#/client/reports/${x.id}">${esc(x.title)}</a></div><div class="t2">${fmt.dt(x.generated_at)}</div></div>
            <a class="btn sm" href="#/client/reports/${x.id}">Open</a><button class="btn sm" data-csv="${x.id}">${icon('download', 's')} CSV</button></div>`).join('')}</div>`
            : empty('No reports yet', 'Reports appear here when the operations team shares one with you.', 'report') });
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    ctx.onCleanup(delegate(el, 'click', '[data-csv]', async (e, b) => {
      try { await api.download(`/api/client/reports/${b.dataset.csv}/csv`); } catch (err) { toast(err.message, 'error'); }
    }));
    await load();
  },
};
