// components/sharing.js — client sharing flows.
// Nothing is shared silently: request → approve (four-eyes) → client-safe
// preview → explicit confirmation → publish. The server re-checks every rule.

import * as api from '../core/api.js';
import { can } from '../core/state.js';
import { esc, icon, fmt, dialog, confirmDialog, toast, priorityBadge, visBadge, $, $$ } from '../core/ui.js';

const checksHtml = (checks) => `<ul class="check-list">${checks.map((c) => `<li class="${c.ok ? 'ok' : 'no'}"><span class="m">${c.ok ? '✓' : '✕'}</span><span>${esc(c.text)}</span></li>`).join('')}</ul>`;

/** Ask which client (when more than one) — returns clientId or null. */
async function pickClient(clients, title) {
  if (!clients.length) { toast('No client is assigned to this project. Assign one in Clients.', 'warning'); return null; }
  if (clients.length === 1) return clients[0].id;
  return new Promise((resolve) => {
    let done = false;
    dialog({
      title,
      body: `<div class="field"><label>Client</label><select class="select" id="pick-client">${clients.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select></div>`,
      actions: [{ label: 'Cancel', onClick: ({ close }) => { done = true; resolve(null); close(); } },
        { label: 'Continue', kind: 'primary', onClick: ({ close, el }) => { done = true; resolve(el.querySelector('#pick-client').value); close(); } }],
      onClose: () => { if (!done) resolve(null); },
    });
  });
}

export async function requestShare(alarmId, projectId, clients) {
  const clientId = await pickClient(clients, 'Request client sharing');
  if (!clientId) return false;
  const e = await api.post('/api/sharing/eligibility', { alarmIds: [alarmId], clientId, action: 'request', projectId });
  const r = e.results[0];
  const ok = await new Promise((resolve) => {
    let done = false;
    dialog({
      title: `${icon('share')} Mark ready for client review`,
      body: `<p>Request that <b class="mono">${esc(alarmId)}</b> be reviewed for sharing with <b>${esc(e.client.name)}</b>. Nothing becomes visible to the client yet — a supervisor must approve and publish it.</p>
        <div class="section-title">Eligibility</div>${checksHtml(r.checks)}`,
      actions: [{ label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
        { label: 'Request review', kind: 'primary', disabled: !r.eligible, onClick: ({ close }) => { done = true; resolve(true); close(); } }],
      onClose: () => { if (!done) resolve(false); },
    });
  });
  if (!ok) return false;
  try { await api.post('/api/sharing/request', { alarmId, clientId, projectId }); toast('Sent for supervisor approval', 'success'); return true; }
  catch (err) { toast(err.message, 'error'); return false; }
}

export async function approveShare(alarmId, projectId, clientId) {
  const e = await api.post('/api/sharing/eligibility', { alarmIds: [alarmId], clientId, action: 'approve', projectId });
  const r = e.results[0];
  const ok = await new Promise((resolve) => {
    let done = false;
    dialog({
      title: `${icon('check')} Approve for client`,
      body: `<p>Approve <b class="mono">${esc(alarmId)}</b> for sharing with <b>${esc(e.client.name)}</b>. Approval does not publish — publishing is a separate, confirmed step with a client-safe preview.</p>
        <div class="section-title">Checks</div>${checksHtml(r.checks)}`,
      actions: [{ label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
        { label: 'Approve', kind: 'primary', disabled: !r.eligible, onClick: ({ close }) => { done = true; resolve(true); close(); } }],
      onClose: () => { if (!done) resolve(false); },
    });
  });
  if (!ok) return false;
  try { await api.post('/api/sharing/approve', { alarmId, clientId, projectId }); toast('Approved for client', 'success'); return true; }
  catch (err) { toast(err.message, 'error'); return false; }
}

/** The Sharing Review Screen: exactly what the client will see + what stays internal. */
export async function publishFlow(alarmId, projectId, clientId) {
  let pv;
  try { pv = await api.get('/api/sharing/preview', { alarmId, clientId, projectId }); }
  catch (e) { toast(e.message, 'error'); return false; }
  const elig = pv.eligibility.publish;
  const sel = new Set(pv.evidence.filter((x) => x.shared).map((x) => x.key));
  const levels = new Set(pv.context.filter((c) => c.shared).map((c) => c.level));

  return new Promise((resolve) => {
    let done = false;
    const d = dialog({
      title: `${icon('share')} Share with client — review`,
      size: 'xl',
      body: `
      <div class="banner ${elig.eligible ? 'info' : 'critical'}">${icon(elig.eligible ? 'info' : 'alert')}<div class="grow">${elig.eligible
        ? `The client will be able to view this alert after publishing. Only the items marked <b>SHARED</b> below are sent. Everything else stays <b>INTERNAL</b>.`
        : 'This alert cannot be published yet — see the approval checks.'}</div></div>
      <div class="grid g-2">
        <div class="stack">
          <section class="card"><div class="card-h"><h3>1 · Alarm information</h3><div class="actions"><span class="b vis-shared">${icon('eye')}client sees</span></div></div><div class="card-b">
            <dl class="kv"><dt>Alarm</dt><dd class="mono">${esc(pv.alarm.alarmId)}</dd><dt>Type</dt><dd>${esc(pv.alarm.alarmTypeName)}</dd>
            <dt>Priority</dt><dd>${priorityBadge(pv.alarm.priority)}</dd><dt>Status shown</dt><dd>Validated <span class="muted">(internal: ${esc(pv.alarm.internalState)} · ops ${esc(pv.alarm.review)})</span></dd>
            <dt>Date / time</dt><dd>${fmt.dt(pv.alarm.firstInstance)} → ${fmt.time(pv.alarm.lastInstance)}</dd><dt>Occurrences</dt><dd>${fmt.n(pv.alarm.totalTimesReported)}</dd>
            ${pv.alarm.ticketId ? `<dt>Ticket</dt><dd>#${esc(pv.alarm.ticketId)} <span class="muted">(shown only if the client policy allows)</span></dd>` : ''}</dl></div></section>
          <section class="card"><div class="card-h"><h3>2 · Location context</h3><div class="actions muted">tick what the client may see</div></div><div class="card-b">
            ${pv.context.length ? pv.context.map((c) => `<label class="check" style="margin-bottom:6px"><input type="checkbox" data-lvl="${esc(c.level)}" ${levels.has(c.level) ? 'checked' : ''}><span class="b outline" style="min-width:74px">${esc(c.level.toUpperCase())}</span><span class="mono">${esc(c.code)}</span><span class="muted">${esc(c.name && c.name !== c.code ? c.name : '')}</span></label>`).join('') : '<span class="muted">No context available</span>'}</div></section>
          <section class="card"><div class="card-h"><h3>3 · Evidence</h3><div class="actions muted">granular: choose per item</div></div><div class="card-b">
            ${pv.evidence.length ? `<div class="gallery">${pv.evidence.map((x) => `<label class="thumb" style="cursor:pointer" title="${esc(x.key)}">
              ${x.kind === 'video' ? `<span class="play">${icon('video', 'l')}</span>` : `<img src="${esc(x.url)}" alt="">`}
              <span class="tag"><input type="checkbox" data-ev="${esc(x.key)}" ${sel.has(x.key) ? 'checked' : ''}> <span class="b ${sel.has(x.key) ? 'vis-shared' : 'vis-internal'}" data-evlabel="${esc(x.key)}">${sel.has(x.key) ? 'SHARED' : 'INTERNAL'}</span></span></label>`).join('')}</div>` : '<span class="muted">No evidence attached.</span>'}</div></section>
        </div>
        <div class="stack">
          <section class="card"><div class="card-h"><h3>4 · Client-safe summary</h3><div class="actions"><span class="prov derived" title="Generated from a template using only client-visible fields">${pv.summarySource === 'template' ? 'TEMPLATE' : 'EXISTING'}</span></div></div><div class="card-b">
            <textarea class="input" id="pv-summary" rows="4" style="width:100%" maxlength="2000">${esc(pv.summary)}</textarea>
            <div class="hint muted" style="margin-top:6px">Write for the client. Internal notes are never included automatically. This is not AI-generated.</div></div></section>
          <section class="card"><div class="card-h"><h3>5 · Client & recipients</h3></div><div class="card-b">
            <dl class="kv"><dt>Client</dt><dd><b>${esc(pv.client.name)}</b></dd><dt>Scope</dt><dd>${esc(pv.publishingScope)}</dd></dl>
            <div class="list" style="margin-top:8px;border:1px solid var(--border);border-radius:6px">${pv.recipients.length ? pv.recipients.map((u) => `<div class="li"><span class="avatar">${esc(u.name[0])}</span><div class="grow"><div class="t1">${esc(u.name)}</div><div class="t2">${esc(u.email)} · ${esc(u.role)}</div></div></div>`).join('') : '<div class="li muted">No active client users — the alert will be visible once users exist.</div>'}</div></div></section>
          <section class="card"><div class="card-h"><h3>6 · Internal information NOT shared</h3><div class="actions"><span class="b vis-internal">${icon('lock')}internal</span></div></div><div class="card-b">
            <ul class="check-list" id="pv-internal">${pv.internalNotShared.map((t) => `<li><span class="m">${icon('lock', 's')}</span><span>${esc(t)}</span></li>`).join('')}</ul></div></section>
          <section class="card"><div class="card-h"><h3>7 · Approval</h3><div class="actions">${visBadge(pv.publication?.status || 'internal')}</div></div><div class="card-b">
            ${pv.publication?.approvedBy ? `<div class="dim" style="margin-bottom:8px">Approved by <b>${esc(pv.publication.approvedBy)}</b> · ${fmt.dt(pv.publication.approvedAt)}</div>` : ''}
            ${checksHtml(elig.checks)}</div></section>
        </div>
      </div>`,
      actions: [
        { label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
        { label: `${icon('share')} Share with client`, kind: 'primary', disabled: !elig.eligible || !can('alarm.publish'), onClick: async ({ close, el }) => {
          const summary = el.querySelector('#pv-summary').value.trim();
          if (!summary) { toast('Write a client-safe summary first', 'warning'); return; }
          const evidence = [...el.querySelectorAll('[data-ev]:checked')].map((x) => x.dataset.ev);
          const context = [...el.querySelectorAll('[data-lvl]:checked')].map((x) => x.dataset.lvl);
          const ok = await confirmDialog({
            title: `Publish this alert to ${esc(pv.client.name)}?`,
            message: `<p><b class="mono">${esc(pv.alarm.alarmId)}</b> will become visible to <b>${pv.recipients.length}</b> user(s) of ${esc(pv.client.name)}.</p>
              <dl class="kv"><dt>Evidence shared</dt><dd>${evidence.length} of ${pv.evidence.length}</dd><dt>Context shared</dt><dd>${esc(context.join(', ') || 'none')}</dd></dl>
              <p class="muted">This is recorded in the audit trail. You can withdraw it later.</p>`,
            confirmLabel: 'Publish',
          });
          if (!ok) return;
          try {
            await api.post('/api/sharing/publish', { alarmId, clientId, projectId, clientSummary: summary, evidence, context, confirm: true });
            toast(`Shared with ${pv.client.name}`, 'success');
            done = true; resolve(true); close();
          } catch (err) { toast(err.message, 'error'); }
        } },
      ],
      onClose: () => { if (!done) resolve(false); },
    });
    // live-update labels as the user toggles evidence
    $$('[data-ev]', d.el).forEach((cb) => cb.addEventListener('change', () => {
      const lab = d.el.querySelector(`[data-evlabel="${cb.dataset.ev}"]`);
      lab.textContent = cb.checked ? 'SHARED' : 'INTERNAL';
      lab.className = `b ${cb.checked ? 'vis-shared' : 'vis-internal'}`;
    }));
    $$('.thumb img', d.el).forEach((img) => img.addEventListener('error', () => { img.replaceWith(Object.assign(document.createElement('span'), { className: 'muted', textContent: 'unavailable' })); }, { once: true }));
  });
}

export async function withdrawFlow(alarmId, projectId, clientId, clientName) {
  return new Promise((resolve) => {
    let done = false;
    dialog({
      title: `${icon('x')} Withdraw from client`,
      body: `<p><b>${esc(clientName || 'The client')}</b> will immediately lose access to <b class="mono">${esc(alarmId)}</b> and its evidence. The withdrawal is audited and the client is notified that an alert is no longer available.</p>
        <div class="field"><label>Reason (required, internal)</label><textarea class="input" id="wd-reason" rows="3"></textarea></div>`,
      actions: [{ label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
        { label: 'Withdraw', kind: 'danger', onClick: async ({ close, el }) => {
          const reason = el.querySelector('#wd-reason').value.trim();
          if (!reason) { toast('A reason is required', 'warning'); return; }
          try { await api.post('/api/sharing/withdraw', { alarmId, clientId, projectId, reason, confirm: true }); toast('Withdrawn from client', 'success'); done = true; resolve(true); close(); }
          catch (err) { toast(err.message, 'error'); }
        } }],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}

/** Controlled bulk: shows selected / valid / eligible / not eligible (+ why), then requests only eligible ones. */
export async function bulkRequest(alarmIds, projectId, clients) {
  const clientId = await pickClient(clients, 'Bulk client review');
  if (!clientId) return false;
  const e = await api.post('/api/sharing/eligibility', { alarmIds, clientId, action: 'request', projectId });
  const eligible = e.results.filter((r) => r.eligible);
  return new Promise((resolve) => {
    let done = false;
    dialog({
      title: `${icon('share')} Bulk review — ${esc(e.client.name)}`,
      size: 'lg',
      body: `<div class="kpis" style="grid-template-columns:repeat(5,1fr)">
          <div class="kpi"><div class="k-label">Selected</div><div class="k-value">${e.selected}</div></div>
          <div class="kpi accent-good"><div class="k-label">Valid</div><div class="k-value">${e.valid}</div></div>
          <div class="kpi accent-warning"><div class="k-label">Not valid / pending</div><div class="k-value">${e.selected - e.valid}</div></div>
          <div class="kpi accent-info"><div class="k-label">Eligible</div><div class="k-value">${e.eligible}</div></div>
          <div class="kpi accent-critical"><div class="k-label">Not eligible</div><div class="k-value">${e.notEligible}</div></div></div>
        <p class="muted">Only eligible alerts are sent for supervisor review. Bulk <b>publishing</b> is intentionally not possible — each alert needs its own client-safe preview.</p>
        <div class="list" style="border:1px solid var(--border);border-radius:8px;max-height:320px;overflow:auto">${e.results.map((r) => `<div class="li"><span class="b ${r.eligible ? 'vis-shared' : 'vis-withdrawn'}">${r.eligible ? 'eligible' : 'not eligible'}</span>
          <div class="grow"><div class="t1 mono">${esc(r.alarmId)} <span class="dim" style="font-family:var(--font)">${esc(r.alarmTypeName || '')}</span></div>
          ${r.eligible ? '' : `<div class="t2">${r.checks.filter((c) => !c.ok).map((c) => '✕ ' + esc(c.text)).join('<br>')}</div>`}</div></div>`).join('')}</div>`,
      actions: [{ label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
        { label: `Review eligible alerts (${eligible.length})`, kind: 'primary', disabled: !eligible.length, onClick: async ({ close }) => {
          try {
            const r = await api.post('/api/sharing/bulk-request', { alarmIds: eligible.map((x) => x.alarmId), clientId, projectId });
            toast(`${r.requested.length} sent for approval${r.skipped.length ? `, ${r.skipped.length} skipped` : ''}`, 'success');
            done = true; resolve(true); close();
          } catch (err) { toast(err.message, 'error'); }
        } }],
      onClose: () => { if (!done) resolve(false); },
    });
  });
}
