// pages/client/alert.js — CLIENT ALERT DETAIL. Only approved information and evidence.
// Not the internal investigation workspace. Includes the client ↔ operations
// conversation (comment / clarification request / response).

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, toast, priorityBadge, $ } from '../../core/ui.js';
import { session } from '../../core/state.js';
import { mountEvidence } from '../../components/player.js';
import { ackBadge } from './common.js';

export default {
  async render(el, ctx) {
    const id = ctx.params.id;
    setTitle('Shared Alert', `<a href="#/client/alerts">Shared alerts</a> / <span class="mono">${esc(id)}</span>`);
    el.innerHTML = skeleton(6, 40);

    const load = async () => {
      let a;
      try {
        a = await api.get(`/api/client/alerts/${encodeURIComponent(id)}`);
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = e.status === 404
          ? `<div class="error-state">${icon('search')}<div class="e-t">Alert not available</div><div>This alert is not available to you.</div><a class="btn" href="#/client/alerts">Back to shared alerts</a></div>`
          : errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
        return;
      }
      if (ctx.isStale()) return;
      const items = (a.evidence || []).map((e) => ({ kind: e.kind, index: e.index, url: e.url }));
      const msgs = a.messages || [];
      const commented = msgs.some((m) => m.audience === 'client');
      const responded = msgs.some((m) => m.audience === 'internal');
      const chip = (on, label, ic) => `<span class="b ${on ? 'vis-shared' : 'outline'}" title="${on ? '' : 'Not yet'}">${icon(on ? ic : 'clock')}${label}${on ? '' : ' — not yet'}</span>`;
      const statusChips = `<div class="row">${chip(!!a.viewedAt, 'Viewed', 'eye')}${chip(!!a.acknowledgedAt, 'Acknowledged', 'check')}${chip(commented, 'Commented', 'edit')}${chip(responded, 'Responded', 'share')}</div>`;
      const org = session.user?.client?.name || 'Your organisation';
      const thread = `<div class="chat" aria-live="polite">${msgs.length ? msgs.map((m) => `<div class="msg ${m.audience === 'client' ? 'mine' : ''}">
          <div class="who"><b>${esc(m.author || '')}</b> · ${m.audience === 'client' ? esc(org) : 'Operations team'} · ${esc(m.kindLabel || m.kind)} · <span class="num">${fmt.dt(m.createdAt)}</span></div>
          <div style="white-space:pre-wrap">${esc(m.body)}</div></div>`).join('')
        : '<div class="muted">No messages yet.</div>'}</div>
        ${a.canComment ? `<div style="margin-top:12px;border-top:1px solid var(--border);padding-top:12px">
          <div class="row" style="margin-bottom:6px"><label for="msg-kind" class="muted" style="font-size:12px">Type</label>
            <select class="select" id="msg-kind"><option value="comment">Comment</option><option value="clarification">Request clarification</option></select></div>
          <textarea class="input" id="msg-body" rows="3" maxlength="2000" style="width:100%" placeholder="Write to the operations team…"></textarea>
          <div class="row" style="margin-top:6px"><span class="muted grow" style="font-size:11.5px">Visible to the operations team and your organisation. Recorded in the audit trail.</span><button class="btn primary" id="msg-send">${icon('share', 's')} Send</button></div></div>`
        : '<div class="muted" style="margin-top:10px;font-size:12px">Your role can read this conversation but not post to it.</div>'}`;
      el.innerHTML = `
        <div class="page-head" style="align-items:center"><div class="row" style="gap:12px">
          <a class="btn icon" href="#/client/alerts" aria-label="Back">${icon('left')}</a>
          <div><div class="muted" style="font-size:10.5px;letter-spacing:.14em;font-weight:800">SHARED ALERT</div><h2 class="mono">${esc(a.alarmId)}</h2>
            <div class="row" style="margin-top:4px">${a.exam ? `<span class="b outline">${esc(a.exam.name)}</span>` : ''}${a.ticketRef ? `<span class="b outline mono">${esc(a.ticketRef)}</span>` : ''}${priorityBadge(a.priority)}<span class="b vis-shared">${icon('check')}${esc(a.status)}</span>${ackBadge(a)}<span class="dim">${esc(a.alarmTypeName)}</span></div></div></div></div>
        <div class="grid g-side">
          <div class="stack">
            ${card({ title: `${icon('info')} Summary`, body: `<div style="white-space:pre-wrap;font-size:13.5px">${esc(a.summary || 'No summary provided.')}</div>` })}
            <div class="rv-media cl-media">
              <section class="card"><div class="card-h"><h3>${icon('video')} Video</h3></div><div class="card-b" id="cl-video"></div></section>
              <section class="card"><div class="card-h"><h3>${icon('image')} Images</h3><div class="sub">click to enlarge</div></div><div class="card-b" id="cl-images"></div></section>
            </div>
            ${card({ title: `${icon('users')} Conversation with the operations team`, sub: 'Comments, clarification requests and responses about this alert', body: thread })}
          </div>
          <div class="stack">
            ${card({ title: `${icon('eye')} Status`, body: statusChips })}
            ${card({ title: 'Details', body: `<dl class="kv">
              ${a.exam ? `<dt>Exam</dt><dd><b>${esc(a.exam.name)}</b></dd>` : ''}
              ${a.ticketRef ? `<dt>Ticket ref</dt><dd class="mono"><b>${esc(a.ticketRef)}</b></dd>` : ''}
              ${a.locationLabel ? `<dt>Location</dt><dd class="mono"><b>${esc(a.locationLabel)}</b></dd>` : ''}
              ${a.cameraName ? `<dt>Camera</dt><dd>${esc(a.cameraName)}</dd>` : ''}
              <dt>Alert</dt><dd class="mono">${esc(a.alarmId)}</dd><dt>Type</dt><dd>${esc(a.alarmTypeName)}</dd><dt>Priority</dt><dd>${priorityBadge(a.priority)}</dd>
              <dt>Status</dt><dd>${esc(a.status)}</dd>
              ${(a.context || []).map((c) => `<dt>${esc(c.level === 'tc' ? c.level.toUpperCase() : c.level[0].toUpperCase() + c.level.slice(1))}</dt><dd><span class="mono">${esc(c.code)}</span>${c.name && c.name !== c.code ? ` <span class="muted">${esc(c.name)}</span>` : ''}</dd>`).join('')}
              <dt>First detected</dt><dd class="num">${fmt.dt(a.firstInstance)}</dd><dt>Last detected</dt><dd class="num">${fmt.dt(a.lastInstance)}</dd>
              <dt>Occurrences</dt><dd class="num">${fmt.n(a.totalTimesReported)}</dd>
              ${a.shiftLabel ? `<dt>Shift</dt><dd>${esc(a.shiftLabel)}</dd>` : ''}
              ${a.ticketId ? `<dt>Ticket</dt><dd>#${esc(a.ticketId)}</dd>` : ''}
              <dt>Shared</dt><dd class="num">${fmt.dt(a.sharedAt)}</dd></dl>` })}
            ${card({ title: `${icon('check')} Acknowledgement`, body: a.acknowledgedAt
              ? `<div>${ackBadge(a)}</div><div class="dim" style="margin-top:8px">By <b>${esc(a.acknowledgedBy)}</b> · ${fmt.dt(a.acknowledgedAt)}</div>${a.ackComment ? `<div class="banner info" style="margin-top:8px">“${esc(a.ackComment)}”</div>` : ''}`
              : a.canAcknowledge
                ? `<p class="dim">Let the operations team know you have seen this alert.</p><div class="field"><label for="ack-c">Comment (optional)</label><textarea class="input" id="ack-c" rows="3" maxlength="2000"></textarea></div><button class="btn primary" id="ack">${icon('check', 's')} Acknowledge</button>`
                : '<div class="muted">Awaiting acknowledgement. Your role can view alerts but not acknowledge them.</div>' })}
          </div>
        </div>`;
      mountEvidence($('#cl-video', el), $('#cl-images', el), items, { log: false, title: a.alarmId });
      $('#msg-send', el)?.addEventListener('click', async () => {
        const body = $('#msg-body', el).value.trim();
        if (!body) return toast('Write a message first', 'warning');
        const btn = $('#msg-send', el);
        btn.disabled = true;
        try {
          await api.post(`/api/client/alerts/${encodeURIComponent(id)}/messages`, { kind: $('#msg-kind', el).value, body });
          toast('Sent to the operations team', 'success');
          load();
        } catch (e) { toast(e.message, 'error'); btn.disabled = false; }
      });
      $('#ack', el)?.addEventListener('click', async () => {
        try { await api.post(`/api/client/alerts/${encodeURIComponent(id)}/acknowledge`, { comment: $('#ack-c', el).value.trim() }); toast('Acknowledged — the operations team has been notified', 'success'); load(); }
        catch (e) { toast(e.message, 'error'); }
      });
    };
    await load();
  },
};
