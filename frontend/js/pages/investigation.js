// pages/investigation.js — INVESTIGATION WORKSPACE (/investigations/:alarmId)
// "What happened?"  alarm → context → evidence → timeline → related → decide.
// Alarm state (Camview), ops validation, workflow and client visibility are
// shown as four separate things. Internal notes are separate from the
// client-safe summary and never shared automatically.

import * as api from '../core/api.js';
import { can, currentProject, projectCode } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, card, empty, errorBox, skeleton, toast, dialog, confirmDialog, delegate, prov, lifecycle,
  priorityBadge, stateBadge, workflowBadge, visBadge, reviewBadge, contextPath, slaBadge, alertItem, why, $, $$ } from '../core/ui.js';
import { watchButton } from './camera.js';
import { gallery, bindGallery } from '../components/evidence.js';
import { requestShare, approveShare, publishFlow, withdrawFlow } from '../components/sharing.js';
import { investigateHref } from '../components/alarms.js';

const REVIEW_ACTIONS = [
  ['acknowledge', 'Acknowledge', 'eye', 'alarm.investigate', ''],
  ['mark_valid', 'Mark valid', 'check', 'alarm.validate', 'good'],
  ['mark_invalid', 'Mark invalid', 'x', 'alarm.invalidate', ''],
  ['mark_exception', 'Mark exception', 'bang', 'alarm.exception', ''],
  ['reopen', 'Reopen', 'refresh', 'alarm.validate', 'ghost'],
];

const row = (label, value, kind = 'direct') => (value === undefined || value === null || value === '' ? '' :
  `<dt>${esc(label)}</dt><dd>${value} ${kind ? prov(kind) : ''}</dd>`);

export default {
  async render(el, ctx) {
    const id = ctx.params.id;
    const pid = ctx.query.projectId || currentProject();
    setTitle('Investigation', `<a href="#/live">Live</a> / <span class="mono">${esc(id)}</span>`);
    el.innerHTML = skeleton(8, 40);
    let d;
    let noteKind = 'internal';
    let io = null;
    let whyAlerts = [];
    ctx.onCleanup(() => io && io.disconnect());

    // WHY ALERT: the explainable intelligent alerts whose related records include this alarm.
    const loadWhy = async (a) => {
      const host = $('#why-host', el);
      if (!host) return;
      if (!can('alert.view')) { host.innerHTML = '<div class="card-b muted">Your role cannot view intelligent alerts.</div>'; return; }
      try {
        const r = await api.get('/api/alerts', { projectId: a.projectId });
        if (ctx.isStale() || !host.isConnected) return;
        whyAlerts = r.items.filter((x) => (x.related || []).includes(a.alarmId));
        host.innerHTML = whyAlerts.length
          ? `<div class="list">${whyAlerts.map((x) => alertItem(x)).join('')}</div>`
          : `<div class="card-b">${empty('No intelligent alert currently references this alarm', 'Alerts are derived from the monitored window — open Intelligent Alerts to see all of them.', 'check')}</div>`;
      } catch (e) {
        if (host.isConnected) host.innerHTML = `<div class="card-b">${errorBox(e, { retry: false })}</div>`;
      }
    };
    ctx.onCleanup(delegate(el, 'click', '[data-why]', (e, b) => { const x = whyAlerts.find((w) => w.id === b.dataset.why); if (x) why(x); }));
    ctx.onCleanup(delegate(el, 'click', '[data-reply]', async (e, b) => {
      const clientId = b.dataset.reply;
      const box = el.querySelector(`[data-reply-text="${CSS.escape(clientId)}"]`);
      const body = (box?.value || '').trim();
      if (!body) { toast('Write a reply first', 'warning'); return; }
      b.disabled = true;
      try {
        await api.post('/api/sharing/respond', { alarmId: d.alarm.alarmId, clientId, projectId: d.alarm.projectId, body });
        toast('Reply sent to the client', 'success');
        load();
      } catch (err) { toast(err.message, 'error'); b.disabled = false; }
    }));

    const load = async () => {
      try {
        d = await api.get(`/api/alarms/${encodeURIComponent(id)}`, { projectId: pid });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = e.status === 404
          ? `<div class="error-state">${icon('search')}<div class="e-t">Alarm not available</div><div>It may be outside your access scope, older than the monitored window, or the ID is wrong.</div><div class="row"><button class="btn" id="back">Back</button><a class="btn" href="#/history">Alarm History</a></div></div>`
          : errorBox(e);
        $('#back', el)?.addEventListener('click', () => history.back());
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const paint = () => {
      const a = d.alarm;
      const ctxd = a.context || {};
      const get = (lvl) => ctxd[lvl];
      const img = d.evidenceItems;
      const pubs = a.publications || [];

      const header = `
        <div class="page-head" style="align-items:center">
          <div class="row" style="gap:12px">
            <button class="btn icon" id="back" aria-label="Back">${icon('left')}</button>
            <div><div class="muted" style="font-size:10.5px;letter-spacing:.14em;font-weight:800">INVESTIGATION</div>
              <h2 class="mono" style="font-size:20px">${esc(a.alarmId)}</h2>
              <div class="row" style="margin-top:4px">${priorityBadge(a.priority)}${stateBadge(a.lastActionType, a.lastActionLabel)}${workflowBadge(a.workflowState, a.workflowLabel)}${visBadge(a.visibility?.state, a.visibility?.label, a.visibility?.acknowledged)}
                <span class="dim">${esc(a.alarmTypeName)}</span><span class="muted">·</span><a class="mono" href="#/cameras/${encodeURIComponent(a.cameraId)}?projectId=${a.projectId}">${esc(a.cameraCode)}</a><span class="muted">·</span><span class="num dim">${fmt.dt(a.firstInstance)}</span></div></div>
          </div>
          <div class="row">
            <span id="inv-watch"></span>
            <button class="btn" id="bm" aria-pressed="${d.bookmarked}">${icon('star', 's')} ${d.bookmarked ? 'Bookmarked' : 'Bookmark'}</button>
            <a class="btn" href="#/compare?a=${encodeURIComponent(a.alarmId)}&projectId=${a.projectId}">${icon('compare', 's')} Compare</a>
            ${can('report.generate') ? `<button class="btn" id="rep">${icon('report', 's')} Investigation report</button>` : ''}
          </div>
        </div>`;

      const banners = `${a.source === 'snapshot' ? `<div class="banner warning">${icon('info')}<div class="grow">This alarm is no longer in the monitored window. Showing the <b>snapshot saved when it was reviewed/shared</b> — Camview's current state may differ. ${prov('derived')}</div></div>` : ''}
        ${a.flags?.criticalPending ? `<div class="banner critical">${icon('alert')}<div class="grow"><b>Critical and pending.</b> No operator decision has been recorded yet.</div>${a.sla ? slaBadge(a.sla) : ''}</div>` : ''}`;

      const states = `<div class="states">
        <div><div class="lbl">Alarm state · Camview</div>${stateBadge(a.lastActionType, a.lastActionLabel)}<div class="src">lastActionType = ${a.lastActionType ?? '—'} · alarmState = ${esc(a.alarmState ?? '—')} · read-only</div></div>
        <div><div class="lbl">Ops validation · Command Center</div>${reviewBadge(a.review?.status)}<div class="src">${a.review?.by ? `by ${esc(a.review.by)} · ${fmt.dt(a.review.at)}` : 'no operator decision yet'}</div></div>
        <div><div class="lbl">Workflow</div>${workflowBadge(a.workflowState, a.workflowLabel)}<div class="src">${a.assignment ? `assigned to ${esc(a.assignment.name)}` : 'unassigned'}${a.sla ? ' · ' + slaBadge(a.sla) : ''}</div></div>
        <div><div class="lbl">Client visibility</div>${visBadge(a.visibility?.state, a.visibility?.label, a.visibility?.acknowledged)}<div class="src">${pubs.length ? pubs.map((p) => `${esc(p.clientName)}: ${esc(p.statusLabel)}`).join(' · ') : 'Internal — not visible to any client'}</div></div>
      </div>
      <div class="card" style="padding:10px 14px;margin-bottom:14px"><div class="muted" style="font-size:10.5px;letter-spacing:.12em;font-weight:800">LIFECYCLE</div>${lifecycle(a.workflowState)}</div>`;

      const summary = card({ title: `${icon('info')} Alarm summary`, body: `<dl class="kv">
        ${row('Alarm ID', `<span class="mono">${esc(a.alarmId)}</span>`)}
        ${row('Alarm type', `${esc(a.alarmTypeName)} <span class="muted">(type ${esc(a.alarmType)})</span>`, a.alarmTypeKnown ? 'direct' : 'derived')}
        ${row('Priority', `${priorityBadge(a.priority)} <span class="muted">(value ${esc(a.priorityLevel)}${a.priorityConfirmed ? '' : ' · label not confirmed'})</span>`)}
        ${row('Alarm state', `${esc(a.alarmState ?? '—')}`)}
        ${row('Last action', stateBadge(a.lastActionType, a.lastActionLabel))}
        ${row('First instance', `<span class="num">${fmt.dt(a.firstInstance)}</span>`)}
        ${row('Last instance', `<span class="num">${fmt.dt(a.lastInstance)}</span> <span class="muted">${fmt.rel(a.lastInstance)}</span>`)}
        ${row('Total times reported', `<span class="num">${fmt.n(a.totalTimesReported)}</span>`)}
        ${row('Duration', a.spanMinutes != null ? fmt.dur(a.spanMinutes) : null, 'derived')}
        ${row('Ticket', a.ticketId ? `#${esc(a.ticketId)}` : null)}
        ${row('Shift', esc(a.shiftLabel))}
        ${row('Suppressed', a.suppressed ? `Yes${a.suppressionTrigger ? ' · trigger: ' + esc(a.suppressionTrigger) : ''}` : 'No')}
        ${row('Evidence', `${a.evidence.images} image(s)${a.evidence.video ? ' · video' : ''}`)}
        ${row('Project', `${esc(get('project')?.code || projectCode(a.projectId))} <span class="muted">${esc(get('project')?.name || '')}</span>`, get('project')?.unmapped ? 'unavailable' : 'derived')}
      </dl>` });

      const all = ['project', 'tc', 'centre', 'building', 'floor', 'room', 'camera'];
      const levels = all.filter((lvl) => get(lvl));                       // only the levels that exist for this camera
      const context = card({ title: `${icon('tree')} Nomenclature context`, sub: ctxd.mapped ? (ctxd.camera?.source === 'camview' ? 'Built from Camview camera data' : 'Resolved from master data') : 'Camera not in master data',
        actions: ctxd.mapped ? prov('derived') : prov('unavailable'),
        body: `<div style="margin-bottom:10px">${contextPath(ctxd.path || [])}</div>
          <dl class="kv">${levels.map((lvl) => {
            const n = get(lvl);
            const href = n.id ? `#/context?node=${encodeURIComponent(n.id)}&projectId=${a.projectId}` : null;
            return `<dt>${lvl === 'tc' ? lvl.toUpperCase() : lvl[0].toUpperCase() + lvl.slice(1)}</dt><dd>${href ? `<a class="mono" href="${href}">${esc(n.code)}</a>` : `<span class="mono">${esc(n.code)}</span>`}${n.name && n.name !== n.code ? ` <span class="muted">${esc(n.name)}</span>` : ''}${n.unmapped ? ' <span class="prov unavailable">UNMAPPED</span>' : ''}</dd>`;
          }).join('')}</dl>` });

      const cc = a.contextCompleteness || { percent: 0, available: [], missing: levels, expected: levels };
      const LBL = { project: 'Project', tc: 'TC', centre: 'Centre', building: 'Building', floor: 'Floor', room: 'Room', camera: 'Camera' };
      const completeness = card({ title: `${icon('shield')} Context completeness`, sub: 'Data quality against the levels this project has — not an AI confidence score', actions: prov('derived'),
        body: `<div class="complete" role="img" aria-label="Context completeness ${cc.percent} percent">
            <div class="track">${(cc.expected || all).map((lvl) => `<span class="seg ${cc.available.includes(lvl) ? 'on' : ''}" title="${LBL[lvl]}: ${cc.available.includes(lvl) ? 'available' : 'missing'}"></span>`).join('')}</div>
            <b class="num" style="font-size:18px">${cc.percent}%</b></div>
          <div class="grid g-2" style="margin-top:10px;gap:8px">
            <div><div class="muted" style="font-size:10.5px;font-weight:800;letter-spacing:.1em">AVAILABLE</div><ul class="check-list" style="margin-top:4px">${cc.available.map((l) => `<li class="ok"><span class="m">✓</span>${LBL[l]}</li>`).join('') || '<li class="muted">None</li>'}</ul></div>
            <div><div class="muted" style="font-size:10.5px;font-weight:800;letter-spacing:.1em">MISSING</div><ul class="check-list" style="margin-top:4px">${cc.missing.map((l) => `<li class="no"><span class="m" style="color:var(--st-warning)">⚠</span>${LBL[l]}</li>`).join('') || '<li class="ok"><span class="m">✓</span>Nothing missing</li>'}</ul></div>
          </div>
          ${cc.missing.length ? `<div class="muted" style="font-size:11px;margin-top:8px">Missing levels come from the nomenclature master data — fix them in <a href="#/data-quality">Data Quality</a> / Nomenclature import.</div>` : ''}` });

      const whyCard = card({ title: `${icon('zap')} Why alert`, sub: 'Intelligent alerts that reference this alarm', actions: prov('derived'), flush: true,
        body: `<div id="why-host">${skeleton(2, 30)}</div>` });

      const evidence = card({ title: `${icon('image')} Evidence`, sub: can('evidence.view') ? 'Click to open focus mode (zoom, pan, fullscreen)' : '',
        actions: prov('direct'),
        body: can('evidence.view') ? gallery(img.map((x) => ({ ...x, shared: pubs.some((p) => p.status === 'shared' && p.evidence.some((e) => e.shared && e.kind === x.kind && e.index === x.index)) })), { showShared: true })
          : empty('No access to evidence', 'Your role cannot view evidence.', 'lock') });

      const events = [];
      events.push({ at: a.firstInstance, t: 'First reported', s: `${esc(a.cameraCode)} · ${esc(a.alarmTypeName)}`, k: 'direct' });
      if (a.totalTimesReported > 1) events.push({ at: a.lastInstance, t: `Last reported (${a.totalTimesReported} occurrences)`, s: a.spanMinutes != null ? `${fmt.dur(a.spanMinutes)} after the first report` : '', k: 'direct' });
      (d.audit || []).slice().reverse().forEach((e) => events.push({ at: e.created_at, t: labelAction(e.action), s: `${esc(e.operator || 'system')}${e.note ? ' — “' + esc(e.note) + '”' : ''}`, k: 'audit' }));
      events.sort((x, y) => (x.at || '').localeCompare(y.at || ''));
      const timeline = card({ title: `${icon('clock')} Timeline`, sub: 'Camview events + Command Center actions',
        body: `<ul class="timeline">${events.map((ev) => `<li class="${ev.k === 'direct' ? '' : 'muted'}"><div class="when">${fmt.dt(ev.at)}</div><div><b>${esc(ev.t)}</b> ${ev.k === 'direct' ? prov('direct') : ''}</div><div class="dim">${ev.s}</div></li>`).join('')}</ul>` });

      const occ = a.totalTimesReported > 1
        ? `<ul class="why"><li>${a.totalTimesReported} occurrences (totalTimesReported)</li><li>Same camera ${esc(a.cameraCode)}</li>${a.spanMinutes != null ? `<li>Within ${fmt.dur(a.spanMinutes)} (firstInstance → lastInstance)</li>` : ''}<li>Current state: ${esc(a.lastActionLabel)}</li>${a.flags?.repeated ? '<li>Meets the repeated-activity threshold</li>' : ''}</ul>`
        : '<div class="muted">Reported once.</div>';
      const occurrences = card({ title: `${icon('layers')} Occurrences`, actions: prov('derived'), body: occ });

      const related = card({ title: `${icon('share')} Related activity`, sub: 'Every relationship explains itself', flush: true,
        body: (d.related || []).length ? `<div class="list">${d.related.map((r) => `<a class="li" href="${investigateHref(r.alarm)}">${priorityBadge(r.alarm.priority)}
          <div class="grow"><div class="t1"><span class="mono">${esc(r.alarm.alarmId)}</span><span class="dim" style="font-weight:500">${esc(r.alarm.alarmTypeName)}</span></div>
          <div class="t2">${r.reasons.map(esc).join(' · ')}</div></div>${stateBadge(r.alarm.lastActionType, r.alarm.lastActionLabel)}</a>`).join('')}</div>`
          : empty('No related activity', 'No alarms from the same camera (24 h) or the same room/centre (1 h) in the monitored window.') });

      const notes = can('alarm.investigate') ? card({ title: `${icon('lock')} Internal notes`, sub: 'Never visible to clients', actions: '<span class="b vis-internal">' + icon('lock') + 'internal</span>', flush: true,
        body: `${can('alarm.comment') ? `<div style="padding:12px 14px;border-bottom:1px solid var(--border)">
            <div class="seg" style="margin-bottom:8px">${[['internal', 'Internal note'], ['operator', 'Operator note'], ...(can('alarm.approve') ? [['supervisor', 'Supervisor note']] : [])].map(([k, l]) => `<button data-nk="${k}" class="${k === noteKind ? 'on' : ''}">${l}</button>`).join('')}</div>
            <textarea class="input" id="note" rows="2" style="width:100%" placeholder="Add a note for the investigation team…"></textarea>
            <div class="row" style="margin-top:6px"><span class="muted grow" style="font-size:11px">Notes go to the internal audit trail. For the client, write a client-safe summary when sharing.</span><button class="btn sm primary" id="add-note">Add note</button></div></div>` : ''}
          <div class="list">${d.notes.length ? d.notes.map((n) => `<div class="li"><span class="b outline">${esc(n.kindLabel)}</span><div class="grow"><div class="t1">${esc(n.author)} <span class="muted num" style="font-weight:400">${fmt.dt(n.createdAt)}</span></div><div style="white-space:pre-wrap">${esc(n.body)}</div></div></div>`).join('') : '<div class="li muted">No notes yet.</div>'}</div>` }) : '';

      // ---------------- action panel
      const reviewBtns = REVIEW_ACTIONS.filter(([, , , perm]) => can(perm)).map(([act, lbl, ic, , kind]) => {
        const cur = a.review?.status;
        const disabled = (act === 'mark_valid' && cur === 'marked_valid') || (act === 'mark_invalid' && cur === 'marked_invalid') || (act === 'mark_exception' && cur === 'marked_exception') || (act === 'reopen' && cur === 'unreviewed') || (act === 'acknowledge' && cur !== 'unreviewed');
        return `<button class="btn ${kind}" data-review="${act}" ${disabled ? 'disabled' : ''}>${icon(ic, 's')} ${lbl}</button>`;
      }).join('');
      const decide = card({ title: `${icon('check')} Review & validate`, sub: 'Recorded in Command Center — Camview’s own state is not changed',
        body: reviewBtns ? `<div class="row">${reviewBtns}</div><div class="muted" style="font-size:11.5px;margin-top:8px">Valid does not share anything with a client. Four-eyes approval is <b>${d.policy.fourEyes ? 'on' : 'off'}</b>.</div>`
          : '<div class="muted">Your role can view this alarm but not validate it.</div>' });

      const assign = can('alarm.assign') ? card({ title: `${icon('user')} Assignment`,
        body: `<div class="row"><select class="select grow" id="assignee"><option value="">Unassigned</option></select><button class="btn" id="assign-btn">Assign</button></div>
          <div class="muted" style="font-size:11.5px;margin-top:6px">${a.assignment ? `Assigned to <b>${esc(a.assignment.name)}</b> by ${esc(a.assignment.assignedBy)} · ${fmt.dt(a.assignment.assignedAt)}` : 'Only users whose scope covers this alarm can be assigned.'}</div>` })
        : (a.assignment ? card({ title: `${icon('user')} Assignment`, body: `Assigned to <b>${esc(a.assignment.name)}</b>` }) : '');

      const sharing = card({ title: `${icon('share')} Client sharing`, sub: d.valid ? 'Alarm counts as valid for sharing' : 'Not valid for sharing yet', flush: true,
        body: d.sharing.length ? `<div class="list">${d.sharing.map((s) => {
          const pub = s.publication;
          const st = pub?.status || 'internal';
          const act = [];
          if (can('alarm.validate') || can('alarm.publish') || can('alarm.approve')) {
            if (st === 'internal' || st === 'withdrawn') act.push(`<button class="btn sm" data-share="request" data-client="${esc(s.client.id)}" ${s.actions.request.eligible ? '' : 'disabled'} title="${esc(s.actions.request.checks.filter((c) => !c.ok).map((c) => c.text).join('; '))}">Request review</button>`);
          }
          if (can('alarm.approve') && st === 'ready_for_review') act.push(`<button class="btn sm primary" data-share="approve" data-client="${esc(s.client.id)}" ${s.actions.approve.eligible ? '' : 'disabled'} title="${esc(s.actions.approve.checks.filter((c) => !c.ok).map((c) => c.text).join('; '))}">Approve</button>`);
          if (can('alarm.publish') && (st === 'approved' || (!d.policy.requireApproval && st !== 'shared'))) act.push(`<button class="btn sm primary" data-share="publish" data-client="${esc(s.client.id)}">${icon('share', 's')} Review & share</button>`);
          if (can('alarm.withdraw') && ['shared', 'approved', 'ready_for_review'].includes(st)) act.push(`<button class="btn sm danger" data-share="withdraw" data-client="${esc(s.client.id)}" data-cname="${esc(s.client.name)}">Withdraw</button>`);
          const blockers = (st === 'internal' || st === 'withdrawn') ? s.actions.request.checks.filter((c) => !c.ok) : st === 'ready_for_review' ? s.actions.approve.checks.filter((c) => !c.ok) : [];
          const msgs = s.messages || [];
          const chips = pub && ['shared', 'withdrawn'].includes(st) ? `<div class="row tight" style="margin-top:4px">${[
            ['Viewed', pub.viewedAt, 'eye'], ['Acknowledged', pub.acknowledgedAt, 'check'],
            ['Commented', msgs.find((m) => m.audience === 'client')?.createdAt, 'user'],
            ['Responded', msgs.find((m) => m.audience === 'internal')?.createdAt, 'share']]
            .map(([l, at, ic]) => `<span class="b ${at ? 'vis-shared' : 'outline'}" title="${at ? `${l} ${fmt.dt(at)}` : `Not ${l.toLowerCase()} yet`}">${icon(at ? ic : 'clock')}${l}${at ? '' : ' — no'}</span>`).join('')}</div>` : '';
          const canReply = st === 'shared' && (can('alarm.publish') || can('alarm.approve'));
          const thread = msgs.length || canReply ? `<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
            <div class="muted" style="font-size:10.5px;font-weight:800;letter-spacing:.1em;margin-bottom:6px">CLIENT CONVERSATION · visible to ${esc(s.client.name)}</div>
            ${msgs.length ? `<div class="chat">${msgs.map((m) => `<div class="msg ${m.audience === 'internal' ? 'mine' : ''}"><div class="who">${esc(m.author || '')} · ${esc(m.kindLabel)} · ${fmt.dt(m.createdAt)}</div><div style="white-space:pre-wrap">${esc(m.body)}</div></div>`).join('')}</div>` : '<div class="muted" style="font-size:12px">No messages yet.</div>'}
            ${canReply ? `<div class="row" style="margin-top:8px;align-items:flex-end"><textarea class="input grow" rows="2" data-reply-text="${esc(s.client.id)}" placeholder="Reply to ${esc(s.client.name)} — visible to the client, never includes internal notes" aria-label="Reply to ${esc(s.client.name)}"></textarea><button class="btn sm primary" data-reply="${esc(s.client.id)}">Send</button></div>` : ''}
          </div>` : '';
          return `<div class="li" style="flex-direction:column;align-items:stretch">
            <div class="row"><b>${esc(s.client.name)}</b><span class="grow"></span>${visBadge(st, pub?.statusLabel, !!pub?.acknowledgedAt)}</div>${chips}
            ${pub ? `<div class="t2">${pub.requestedBy ? `Requested by ${esc(pub.requestedBy)} ${fmt.rel(pub.requestedAt)}` : ''}${pub.approvedBy ? ` · approved by ${esc(pub.approvedBy)}` : ''}${pub.sharedBy ? ` · shared by ${esc(pub.sharedBy)} ${fmt.dt(pub.sharedAt)}` : ''}${pub.viewedAt ? ` · viewed ${fmt.rel(pub.viewedAt)}` : ''}${pub.acknowledgedAt ? ` · <b>acknowledged</b> by ${esc(pub.acknowledgedBy)} ${fmt.rel(pub.acknowledgedAt)}` : ''}${pub.withdrawnBy ? ` · withdrawn by ${esc(pub.withdrawnBy)}: ${esc(pub.withdrawReason)}` : ''}</div>` : ''}
            ${pub?.ackComment ? `<div class="banner info" style="margin:6px 0 0">${icon('user')}<div>Client comment: “${esc(pub.ackComment)}”</div></div>` : ''}
            ${blockers.length && act.length ? `<ul class="check-list" style="margin-top:6px">${blockers.map((c) => `<li class="no"><span class="m">✕</span>${esc(c.text)}</li>`).join('')}</ul>` : ''}
            <div class="row" style="margin-top:6px">${act.join('')}</div>${thread}</div>`;
        }).join('')}</div>` : empty('No client assigned', 'No client is assigned to this project. Map clients to projects in Clients.', 'building') });

      const safeSummary = card({ title: `${icon('shield')} Client-safe summary`, actions: '<span class="prov derived" title="Template using only client-visible fields — not AI">TEMPLATE</span>',
        body: `<div style="white-space:pre-wrap">${esc(d.clientSafeSummary)}</div><div class="muted" style="font-size:11px;margin-top:6px">Generated from a fixed template using only fields a client may see. Edit it in the share review before publishing.</div>` });

      const ai = card({ title: `${icon('cpu')} AI investigation assistant`, actions: '<span class="b outline">V2</span>',
        body: `<div class="v2" style="padding:14px"><div class="tag">NOT ENABLED</div><div>No AI provider is integrated, so nothing here is AI-generated. When enabled (V2) it will only use data you are authorised to see, and clearly mark SOURCE FACTS, DERIVED data and AI SUMMARY.</div></div>` });

      const audit = (d.audit || []).length ? card({ title: `${icon('audit')} Audit trail`, sub: 'Append-only', flush: true,
        body: `<div class="list">${d.audit.map((e) => `<div class="li"><span class="num muted" style="font-size:11px;width:120px">${fmt.dt(e.created_at)}</span><div class="grow"><div class="t1">${esc(labelAction(e.action))}</div><div class="t2">${esc(e.operator || 'system')}${e.clientId ? ' · ' + esc(e.clientId) : ''}${e.note ? ' — ' + esc(e.note) : ''}</div></div></div>`).join('')}</div>` }) : '';

      const NAV = [['alarm', 'Alarm'], ['context', 'Context'], ['why', 'Why alert'], ['evidence', 'Evidence'], ['timeline', 'Timeline'],
        ['related', 'Related'], ['workflow', 'Workflow'], ['visibility', 'Client visibility']];
      const caseNav = `<nav class="tabs" id="case-nav" aria-label="Case sections" style="position:sticky;top:58px;z-index:5;background:var(--bg);margin-bottom:14px">
        ${NAV.map(([k, l], i) => `<button class="tab" data-jump="${k}">${i ? '<span class="muted" aria-hidden="true">→</span> ' : ''}${l}</button>`).join('')}</nav>`;
      const sec = (k, html) => `<section id="case-${k}" data-sec="${k}" style="scroll-margin-top:110px">${html}</section>`;

      el.innerHTML = `${header}${banners}${states}${caseNav}
        <div class="grid g-side">
          <div class="stack">
            ${sec('alarm', summary)}
            ${sec('context', `<div class="stack">${context}<div class="grid g-2">${completeness}${occurrences}</div></div>`)}
            ${sec('why', whyCard)}
            ${sec('evidence', evidence)}
            ${sec('timeline', timeline)}
            ${sec('related', related)}
            ${notes}
          </div>
          <div class="stack">
            ${sec('workflow', `<div class="stack">${decide}${assign}</div>`)}
            ${sec('visibility', `<div class="stack">${sharing}${safeSummary}</div>`)}
            ${ai}${audit}
          </div>
        </div>`;

      // One-click case view navigator: smooth scroll + highlight the section in view.
      $$('[data-jump]', el).forEach((b) => b.addEventListener('click', () => {
        el.querySelector(`#case-${b.dataset.jump}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }));
      if (io) io.disconnect();
      if ('IntersectionObserver' in window) {
        io = new IntersectionObserver((entries) => {
          entries.filter((x) => x.isIntersecting).forEach((x) => {
            $$('[data-jump]', el).forEach((b) => b.classList.toggle('on', b.dataset.jump === x.target.dataset.sec));
          });
        }, { rootMargin: '-120px 0px -60% 0px' });
        $$('[data-sec]', el).forEach((s) => io.observe(s));
      }

      watchButton($('#inv-watch', el), { entityType: 'alarm', entityId: a.alarmId, label: a.alarmId, projectId: String(a.projectId) });
      loadWhy(a);

      bindGallery(el, img, { alarmId: a.alarmId, canDownload: d.canDownloadEvidence, title: `${a.alarmId} · ${a.cameraCode}` });
      $('#back', el).addEventListener('click', () => (history.length > 1 ? history.back() : (location.hash = '#/live')));
      $('#bm', el).addEventListener('click', async () => { await api.post(`/api/alarms/${encodeURIComponent(id)}/bookmark`, { on: !d.bookmarked, projectId: pid }); toast(d.bookmarked ? 'Bookmark removed' : 'Bookmarked', 'success'); load(); });
      $('#rep', el)?.addEventListener('click', async () => {
        try { const r = await api.post('/api/reports', { type: 'investigation', alarmId: id, projectId: a.projectId }); toast('Investigation report generated', 'success'); location.hash = `#/reports/${r.id}`; }
        catch (e) { toast(e.message, 'error'); }
      });
      if (can('alarm.assign')) {
        api.get('/api/users/assignable', { projectId: a.projectId }).then((r) => {
          const s = $('#assignee', el);
          if (!s) return;
          s.innerHTML = '<option value="">Unassigned</option>' + r.items.map((u) => `<option value="${esc(u.id)}" ${a.assignment?.userId === u.id ? 'selected' : ''}>${esc(u.name)} · ${esc(u.role)}</option>`).join('');
        }).catch(() => {});
        $('#assign-btn', el).addEventListener('click', async () => {
          try { await api.post(`/api/alarms/${encodeURIComponent(id)}/assign`, { userId: $('#assignee', el).value || null, projectId: pid }); toast('Assignment updated', 'success'); load(); }
          catch (e) { toast(e.message, 'error'); }
        });
      }
      $('#add-note', el)?.addEventListener('click', async () => {
        const body = $('#note', el).value.trim();
        if (!body) return toast('Write a note first', 'warning');
        try { await api.post(`/api/alarms/${encodeURIComponent(id)}/notes`, { kind: noteKind, body, projectId: pid }); toast('Note added', 'success'); load(); }
        catch (e) { toast(e.message, 'error'); }
      });
    };

    ctx.onCleanup(delegate(el, 'click', '[data-nk]', (e, b) => { noteKind = b.dataset.nk; $$('[data-nk]', el).forEach((x) => x.classList.toggle('on', x === b)); }));
    ctx.onCleanup(delegate(el, 'click', '[data-review]', async (e, b) => {
      const action = b.dataset.review;
      const labels = { acknowledge: 'Acknowledge', mark_valid: 'Mark valid', mark_invalid: 'Mark invalid', mark_exception: 'Mark exception', reopen: 'Reopen' };
      let note = null;
      if (action !== 'acknowledge') {
        const ok = await new Promise((resolve) => {
          let done = false;
          dialog({ title: `${labels[action]} — ${esc(id)}`,
            body: `<p class="dim">This records an operator decision in Command Center (audited). It does not change Camview's own alarm state${action === 'mark_valid' ? ', and does <b>not</b> share anything with a client' : ''}.</p>
              <div class="field"><label>Reason / note (optional, internal)</label><textarea class="input" id="rv-note" rows="3"></textarea></div>`,
            actions: [{ label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
              { label: labels[action], kind: action === 'mark_invalid' ? 'danger' : 'primary', onClick: ({ close, el: dd }) => { note = $('#rv-note', dd).value.trim() || null; done = true; resolve(true); close(); } }],
            onClose: () => { if (!done) resolve(false); } });
        });
        if (!ok) return;
      }
      try { await api.post(`/api/alarms/${encodeURIComponent(id)}/review`, { action, note, projectId: pid }); toast(`${labels[action]} recorded`, 'success'); load(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-share]', async (e, b) => {
      const a = d.alarm;
      const clientId = b.dataset.client;
      const act = b.dataset.share;
      let changed = false;
      if (act === 'request') changed = await requestShare(a.alarmId, a.projectId, d.sharing.map((s) => s.client).filter((c) => c.id === clientId));
      if (act === 'approve') changed = await approveShare(a.alarmId, a.projectId, clientId);
      if (act === 'publish') changed = await publishFlow(a.alarmId, a.projectId, clientId);
      if (act === 'withdraw') changed = await withdrawFlow(a.alarmId, a.projectId, clientId, b.dataset.cname);
      if (changed) load();
    }));
    await load();
  },
};

function labelAction(a) {
  return ({
    acknowledge: 'Acknowledged', mark_valid: 'Marked valid', mark_invalid: 'Marked invalid', mark_exception: 'Marked exception',
    reopen: 'Reopened', note: 'Note', 'share.request': 'Client review requested', 'share.approve': 'Approved for client',
    'share.publish': 'Shared with client', 'share.withdraw': 'Withdrawn from client', 'client.view': 'Client viewed',
    'client.acknowledge': 'Client acknowledged', 'alarm.assign': 'Assigned', 'alarm.unassign': 'Unassigned', 'note.add': 'Internal note added',
    'alarm.view': 'Viewed', 'evidence.view': 'Evidence viewed', 'evidence.download': 'Evidence downloaded',
  })[a] || a;
}
