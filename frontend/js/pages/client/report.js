// pages/client/report.js — one client report (meta + sections), CSV and print.

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, card, errorBox, skeleton, toast, $ } from '../../core/ui.js';
import { reportDocument } from '../../components/reportdoc.js';

export function renderSections(sections) {
  return sections.map((s) => card({ title: esc(s.title), sub: s.note ? esc(s.note) : '', flush: s.kind === 'table',
    body: s.kind === 'kv'
      ? `<dl class="kv">${s.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v ?? '—')}</dd>`).join('')}</dl>`
      : s.rows.length ? `<div class="table-wrap"><table class="t"><thead><tr>${s.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead><tbody>${s.rows.map((r) => `<tr>${r.map((v) => `<td>${esc(v ?? '—')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`
        : '<div class="card-b muted">No rows.</div>' })).join('<div style="height:12px"></div>');
}

export default {
  async render(el, ctx) {
    const id = ctx.params.id;
    setTitle('Report', '<a href="#/client/reports">Reports</a>');
    el.innerHTML = skeleton(5, 40);
    const load = async () => {
      let r;
      try { r = await api.get(`/api/client/reports/${encodeURIComponent(id)}`); }
      catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = e.status === 404 ? `<div class="error-state">${icon('report')}<div class="e-t">Report not available</div><a class="btn" href="#/client/reports">Back to reports</a></div>` : errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
        return;
      }
      if (ctx.isStale()) return;
      const m = r.meta || {};
      setTitle(m.title || 'Report', '<a href="#/client/reports">Reports</a>');
      el.innerHTML = `<div class="page-head no-print"><div class="row" style="gap:12px"><a class="btn icon" href="#/client/reports" aria-label="Back to reports">${icon('left')}</a><h2 style="margin:0">Report</h2></div>
          <div class="row"><button class="btn" id="csv">${icon('download', 's')} CSV</button><button class="btn primary" id="print">${icon('report', 's')} Print / PDF</button></div></div>
        ${reportDocument(r, { showAudience: false })}`;
      $('#csv', el).addEventListener('click', async () => { try { await api.download(`/api/client/reports/${encodeURIComponent(id)}/csv`); } catch (e) { toast(e.message, 'error'); } });
      $('#print', el).addEventListener('click', () => window.print());
    };
    await load();
  },
};
