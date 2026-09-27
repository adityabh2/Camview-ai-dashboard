// pages/reports.js — REPORTS, two steps: which exam, and who it is for.
//   Client  → only the alerts delivered to that exam's client (valid results), shareable with the client.
//   Team    → every alert of the exam with every verdict (pending / valid / invalid / exception), internal.

import * as api from '../core/api.js';
import { can, currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, empty, toast, delegate, $ } from '../core/ui.js';
import { reportBody } from './report.js';

const PERIODS = [['today', 'Today'], ['7d', 'Last 7 days'], ['30d', 'Last 30 days'], ['all', 'Whole exam']];

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Reports', `${esc(projectInfo(pid).code)} · reports`);
    el.innerHTML = skeleton(8, 40);
    let meta;
    let preview = null;
    let audience = can('report.share') || can('alarm.publish') ? 'client' : 'team';
    let period = 'today';

    const loadList = async () => {
      try {
        meta = await api.get('/api/reports');
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', loadList);
      }
    };

    const range = () => {
      const now = new Date();
      if (period === 'all') return {};
      const from = new Date(now);
      if (period === 'today') from.setHours(0, 0, 0, 0);
      else from.setDate(now.getDate() - (period === '7d' ? 7 : 30));
      return { from: from.toISOString(), to: now.toISOString() };
    };

    const exam = () => (meta.exams || []).find((e) => e.id === $('#rb-exam', el)?.value) || null;

    const payload = () => {
      const e = exam();
      const filters = { ...range() };
      if (e) filters.exam = e.id;
      return audience === 'client'
        ? { type: 'client_shared', filters, projectId: e?.projectIds?.[0] || pid, clientId: e?.clientId }
        : { type: 'project', filters, projectId: e?.projectIds?.[0] || pid };
    };

    const previewHtml = (r) => `<section class="card"><div class="card-h"><h3>${icon('eye')} Preview</h3><div class="actions muted">Not saved</div></div><div class="card-b">${reportBody(r)}</div></section>`;

    const paint = () => {
      const exams = meta.exams || [];
      const canClient = can('report.share') || can('alarm.publish');
      el.innerHTML = `
        <div class="page-head"><div><h2>Reports</h2><p>Pick the exam and who the report is for. Client reports contain only the alerts delivered to that client.</p></div></div>
        <div class="grid g-side">
          <div class="stack">
            ${meta.canGenerate ? card({ title: `${icon('report')} New report`, body: `
              <div class="grid g-2">
                <div class="field"><label for="rb-exam">Exam</label><select class="select" id="rb-exam">${exams.length ? exams.map((e) => `<option value="${esc(e.id)}">${esc(e.name)}${e.clientName ? ` · ${esc(e.clientName)}` : ''}</option>`).join('') : '<option value="">No exam yet — set the project code in Settings</option>'}</select></div>
                <div class="field"><label>Period</label><div class="seg">${PERIODS.map(([k, l]) => `<button class="btn sm ${k === period ? 'primary' : ''}" data-period="${k}">${l}</button>`).join('')}</div></div>
              </div>
              <div class="field"><label>For</label>
                ${canClient ? `<label class="check" style="margin-bottom:6px"><input type="radio" name="rb-aud" value="client" ${audience === 'client' ? 'checked' : ''}> <b>The client</b> — only the alerts delivered to <span id="rb-client-name">${esc(exam()?.clientName || 'the exam\'s client')}</span> (valid results). Can be shared with the client.</label>` : ''}
                <label class="check"><input type="radio" name="rb-aud" value="team" ${audience === 'team' ? 'checked' : ''}> <b>Our team</b> — every alert of the exam with every verdict (pending, valid, invalid, exception). Internal only.</label></div>
              <div class="row"><span class="grow muted" style="font-size:11.5px">Preview first — nothing is saved until you generate.</span>
                <button class="btn" id="rb-preview">${icon('eye', 's')} Preview</button><button class="btn primary" id="rb-generate">${icon('report', 's')} Generate</button></div>` })
              : card({ title: 'New report', body: empty('Read-only', 'Your role can view reports but not generate them.', 'lock') })}
            <div id="rb-out">${preview ? previewHtml(preview) : ''}</div>
          </div>
          ${card({ title: `${icon('history')} Generated reports`, flush: true, body: meta.items.length ? `<div class="list">${meta.items.map((r) => `<div class="li">
              <span class="b ${r.audience === 'client' ? 'vis-shared' : 'vis-internal'}">${icon(r.audience === 'client' ? 'share' : 'lock')}${r.audience === 'client' ? 'client' : 'team'}</span>
              <div class="grow"><div class="t1"><a href="#/reports/${r.id}">${esc(r.title)}</a></div>
                <div class="t2">#${r.id} · ${esc(r.generated_by_name || '')} · ${fmt.dt(r.generated_at)}${r.client_name ? ` · ${esc(r.client_name)}` : ''}${r.audience === 'client' ? (r.shared_with_client ? ' · <b>shared</b>' : ' · not shared') : ''}</div></div>
              <div class="row tight">
                ${can('report.export') ? `<button class="btn sm ghost" data-csv="${r.id}" aria-label="Export CSV">${icon('download', 's')}</button>` : ''}
                ${r.audience === 'client' && meta.canShare ? `<button class="btn sm ${r.shared_with_client ? '' : 'primary'}" data-share="${r.id}" data-on="${r.shared_with_client ? 0 : 1}">${r.shared_with_client ? 'Unshare' : 'Share with client'}</button>` : ''}
              </div></div>`).join('')}</div>` : empty('No reports yet', 'Generated reports appear here.', 'report') })}
        </div>`;
      $('#rb-exam', el)?.addEventListener('change', () => { const n = $('#rb-client-name', el); if (n) n.textContent = exam()?.clientName || "the exam's client"; });
    };

    ctx.onCleanup(delegate(el, 'click', '[data-period]', (e, b) => { period = b.dataset.period; el.querySelectorAll('[data-period]').forEach((x) => x.classList.toggle('primary', x === b)); }));
    ctx.onCleanup(delegate(el, 'change', '[name="rb-aud"]', (e, r) => { audience = r.value; }));
    ctx.onCleanup(delegate(el, 'click', '#rb-preview', async () => {
      try { preview = await api.post('/api/reports/preview', payload()); $('#rb-out', el).innerHTML = previewHtml(preview); $('#rb-out', el).scrollIntoView({ behavior: 'smooth' }); }
      catch (e) { toast(e.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '#rb-generate', async () => {
      try { const r = await api.post('/api/reports', payload()); toast('Report generated (recorded in the audit trail)', 'success'); location.hash = `#/reports/${r.id}`; }
      catch (e) { toast(e.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-csv]', async (e, b) => {
      try { await api.download(`/api/reports/${b.dataset.csv}/csv`); } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-share]', async (e, b) => {
      try { await api.post(`/api/reports/${b.dataset.share}/share`, { share: b.dataset.on === '1' }); toast(b.dataset.on === '1' ? 'Shared with client' : 'No longer shared', 'success'); loadList(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    await loadList();
  },
};
