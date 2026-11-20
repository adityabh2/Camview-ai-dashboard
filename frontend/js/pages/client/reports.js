// pages/client/reports.js — reports for this client: the daily reports and the final report of each exam arrive
// by themselves; reports the operations team prepares by hand appear here too.

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, empty, errorBox, skeleton, toast, delegate, $ } from '../../core/ui.js';

const kindOf = (x) => (/^Final report/.test(x.title) ? 'final' : /^Daily report/.test(x.title) ? 'daily' : 'manual');
const KIND = { final: 'Final report', daily: 'Daily report', manual: 'From the operations team' };

export default {
  async render(el, ctx) {
    setTitle('Reports', 'Reports for your organisation');
    el.innerHTML = skeleton(4, 40);
    const load = async () => {
      try {
        const r = await api.get('/api/client/reports');
        if (ctx.isStale()) return;
        const items = r.items || [];
        const finals = items.filter((x) => kindOf(x) === 'final');
        const rest = items.filter((x) => kindOf(x) !== 'final');
        const row = (x) => `<a class="rlist-row" href="#/client/reports/${x.id}">
            <span class="rlist-ic rlist-${kindOf(x)}">${icon('report')}</span>
            <div class="grow" style="min-width:0"><div class="t1">${esc(x.title)}</div><div class="t2 muted">${KIND[kindOf(x)]} · ${fmt.dt(x.generated_at)}</div></div>
            <button type="button" class="btn sm ghost" data-csv="${x.id}" aria-label="Download CSV">${icon('download', 's')} CSV</button>
            <span class="btn sm">Open</span></a>`;
        el.innerHTML = `<div class="page-head"><div><h2>Reports</h2>
            <p>A report of the day's alerts arrives every evening, and a final report when each exam ends. Each one contains only your own alerts.</p></div></div>
          ${finals.length ? `<div class="section-title">Final reports</div><div class="rlist">${finals.map(row).join('')}</div>` : ''}
          <div class="section-title">${finals.length ? 'Daily and other reports' : 'All reports'}</div>
          ${rest.length ? `<div class="rlist">${rest.map(row).join('')}</div>`
            : `<div class="card">${empty('No reports yet', 'Your first daily report arrives this evening if there were alerts today.', 'report')}</div>`}`;
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    ctx.onCleanup(delegate(el, 'click', '[data-csv]', async (e, b) => {
      e.preventDefault(); e.stopPropagation();
      try { await api.download(`/api/client/reports/${b.dataset.csv}/csv`); } catch (err) { toast(err.message, 'error'); }
    }));
    await load();
  },
};
