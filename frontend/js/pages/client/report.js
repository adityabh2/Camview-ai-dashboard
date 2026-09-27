// pages/client/report.js — one client report (meta + sections), CSV and print.

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, toast, $ } from '../../core/ui.js';

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
      el.innerHTML = `<div class="page-head"><div><h2>${esc(m.title)}</h2><p>${esc(m.visibility || '')}</p></div>
          <div class="row no-print"><button class="btn" id="csv">${icon('download', 's')} CSV</button><button class="btn" id="print">${icon('report', 's')} Print / PDF</button></div></div>
        ${card({ body: `<dl class="kv"><dt>Audience</dt><dd>${esc(m.audience)}</dd><dt>Client</dt><dd>${esc(m.client || m.scope)}</dd><dt>Data range</dt><dd>${esc(m.dataRange)}</dd>
          <dt>Generated</dt><dd>${fmt.dt(m.generatedAt)} by ${esc(m.generatedBy)}</dd><dt>Records</dt><dd>${esc(m.recordCount)}</dd></dl>` })}
        <div style="height:12px"></div>${renderSections(r.sections || [])}`;
      $('#csv', el).addEventListener('click', async () => { try { await api.download(`/api/client/reports/${encodeURIComponent(id)}/csv`); } catch (e) { toast(e.message, 'error'); } });
      $('#print', el).addEventListener('click', () => window.print());
    };
    await load();
  },
};
