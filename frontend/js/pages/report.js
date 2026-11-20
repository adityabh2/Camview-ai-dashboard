// pages/report.js — REPORT VIEW (/reports/:id). Printable; every report shows
// audience, scope, range, generator and visibility.

import * as api from '../core/api.js';
import { can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, errorBox, skeleton, toast, $ } from '../core/ui.js';
import { reportDocument } from '../components/reportdoc.js';

export function reportBody(r) {
  return reportDocument(r);
}

export default {
  async render(el, ctx) {
    const id = ctx.params.id;
    setTitle('Report', `<a href="#/reports">Reports</a> / #${esc(id)}`);
    el.innerHTML = skeleton(8, 40);
    let r;
    const load = async () => {
      try { r = await api.get(`/api/reports/${encodeURIComponent(id)}`); if (!ctx.isStale()) paint(); }
      catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = e.status === 404 ? `<div class="error-state">${icon('report')}<div class="e-t">Report not available</div><a class="btn" href="#/reports">Back to reports</a></div>` : errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    const paint = () => {
      const client = r.meta?.audience === 'client';
      setTitle(r.meta?.title || 'Report', `<a href="#/reports">Reports</a> / #${esc(id)}`);
      el.innerHTML = `
        <div class="page-head no-print"><div class="row" style="gap:12px"><a class="btn icon" href="#/reports" aria-label="Back to reports">${icon('left')}</a><div><h2>Report #${esc(id)}</h2></div></div>
          <div class="row">
            ${client && can('report.share') ? `<button class="btn ${r.sharedWithClient ? '' : 'primary'}" id="rp-share">${icon('share', 's')} ${r.sharedWithClient ? 'Stop sharing' : 'Share with client'}</button>` : ''}
            ${client && r.sharedWithClient ? `<span class="b vis-shared">${icon('share')}shared with client</span>` : ''}
            ${can('report.export') ? `<button class="btn" id="rp-csv">${icon('download', 's')} CSV</button>` : ''}
            <button class="btn" id="rp-print">${icon('report', 's')} Print / PDF</button></div></div>
        ${reportBody(r)}`;
      $('#rp-print', el).addEventListener('click', () => window.print());
      $('#rp-csv', el)?.addEventListener('click', async () => {
        try { await api.download(`/api/reports/${encodeURIComponent(id)}/csv`); } catch (e) { toast(e.message, 'error'); }
      });
      $('#rp-share', el)?.addEventListener('click', async () => {
        try { await api.post(`/api/reports/${encodeURIComponent(id)}/share`, { share: !r.sharedWithClient }); toast(r.sharedWithClient ? 'No longer shared' : 'Shared with client', 'success'); load(); }
        catch (e) { toast(e.message, 'error'); }
      });
    };
    await load();
  },
};
