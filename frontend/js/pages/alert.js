// pages/alert.js — ALERT REVIEW. Evidence first; context is resolved automatically;
// the only human action is the decision: VALID / INVALID / EXCEPTION.
// VALID creates the ticket (once) and routes it to the exam's client — delivered
// automatically or with one click, depending on Settings › Delivery.
// Keys: V / I / E decide · N next alert · [ ] previous / next · ← → evidence · Space play · Esc back.

import * as api from '../core/api.js';
import { on, pref, setPref, projectCode, session } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, empty, errorBox, skeleton, toast, priorityBadge, stateBadge, confirmDialog, $, $$ } from '../core/ui.js';
import { mountEvidence } from '../components/player.js';
import { status as liveStatus } from '../core/live.js';
import { decisionBadge, deliveryBadge, ticketChip, queueNav, queueHref, reviewHref, where, cameraName, healthChips } from '../components/queue.js';

const RESULT_LABEL = { valid: 'Marked VALID', invalid: 'Marked INVALID', exception: 'Marked EXCEPTION' };

export default {
  async render(el, ctx) {
    const id = ctx.params.id;
    const projectId = ctx.query.projectId || '';
    let data = null, player = null, busy = false, last = null, advanceTimer = null, noteOpen = false;
    const known = new Set(queueNav.ids);
    setTitle('Review alert', `<a href="${queueHref()}">Alerts</a> › ${esc(id)}`);
    el.innerHTML = `<div class="card"><div class="card-b">${skeleton(8, 34)}</div></div>`;

    const load = async () => {
      try {
        data = await api.get(`/api/queue/${encodeURIComponent(id)}`, { projectId });
        if (ctx.isStale()) return;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = e.status === 404
          ? `<div class="card">${empty('Alert not available', 'It may be outside your access, or no longer in the current data window.', 'lock')}<div class="row" style="justify-content:center;padding-bottom:20px"><a class="btn" href="${queueHref()}">${icon('left', 's')} Back to alerts</a></div></div>`
          : errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const position = () => {
      const i = queueNav.ids.indexOf(id);
      return { i, n: queueNav.ids.length, prev: queueNav.items[i - 1], next: queueNav.items[i + 1] };
    };

    const nodeVal = (n) => `<span class="mono">${esc(n.code)}</span>${n.name && n.name !== n.code ? `<div class="muted rv-nm">${esc(n.name)}</div>` : ''}`;
    const ctxRows = (a) => {
      const c = a.context || {};
      const nm = '<span class="nm-miss">NOT MAPPED</span>';
      const fromCamview = '<span class="prov direct" title="Sent by Camview with the camera (centerCode / center / subLocation)">CAMVIEW</span>';
      const centre = a.centreCode ? `<span class="mono">${esc(a.centreCode)}</span> ${fromCamview}${a.centreName ? `<div class="muted rv-nm">${esc(a.centreName)}</div>` : ''}` : nm;
      // Only the levels that exist for this record, in the order the data arrives: Camview sends project, [TC],
      // centre (+ city / state), sub-location and camera; imported master data may add building and floor.
      const src = (n) => (n?.source === 'camview' ? ` ${fromCamview}` : '');
      const place = [a.cameraCity, a.cameraState].filter(Boolean).join(', ');
      const rows = [
        ['Client', a.client ? `<b>${esc(a.client.name)}</b>` : a.clients?.length > 1 ? `<span class="sla-approaching">${a.clients.length} clients — chosen on VALID</span>` : '<span class="nm-miss">CLIENT NOT MAPPED</span>'],
        ['Exam', a.exam ? `<b>${esc(a.exam.name)}</b> <span class="muted mono">${esc(a.exam.code || '')}</span>` : '<span class="nm-miss">EXAM NOT MAPPED</span>'],
        ['Project', c.project && !c.project.unmapped ? nodeVal(c.project) + src(c.project) : `<span class="mono">${esc(projectCode(a.projectId))}</span>${projectCode(a.projectId) === String(a.projectId) ? ' <span class="muted">Camview project id — no code set yet</span>' : ''}`],
        c.tc ? ['TC', nodeVal(c.tc) + src(c.tc)] : null,
        ['Centre', c.centre ? nodeVal(c.centre) + src(c.centre) : centre],
        place ? ['City / State', `${esc(place)} ${fromCamview}`] : null,
        c.building ? ['Building', nodeVal(c.building)] : null,
        c.floor ? ['Floor', nodeVal(c.floor)] : null,
        [c.room?.source === 'camview' || (!c.room && a.cameraSubLocation) ? 'Sub-location' : 'Room', c.room ? nodeVal(c.room) + src(c.room) : a.cameraSubLocation ? `${esc(a.cameraSubLocation)} ${fromCamview}` : nm],
        ['Camera', `<span class="mono">${esc(a.cameraCode || `CAM-${a.cameraId}`)}</span> ${a.cameraNumber && a.cameraCode === a.cameraNumber ? '<span class="prov direct" title="Camview\'s own camera number (camera.cameraNumber)">CAMVIEW</span>' : ''}<span class="muted"> · Camview id ${esc(a.cameraId)}${a.deviceId ? ` · device ${esc(a.deviceId)}` : ''}</span>${a.cameraName && !/^Camera \d+$/.test(a.cameraName) ? `<div class="muted rv-nm">${esc(a.cameraName)}</div>` : ''}`],
      ];
      return rows.filter(Boolean).map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join('')
        + (!c.mapped && !a.centreCode ? '<div class="banner warning" style="grid-column:1/-1;margin:8px 0 0">MAPPING NOT FOUND — this camera is not in the nomenclature and Camview sent no location for it.</div>' : '');
    };

    // Camera connection and recording are separate facts from a REAL health source; the alarm is only an event.
    const healthPanel = (a) => {
      const h = a.health || { available: false, camera: {}, recording: {}, conditions: [] };
      const ev = a.evidence || {};
      const event = a.alarmEvent;
      return `<dl class="kv rv-kv">
          <dt>Camera</dt><dd>${h.available ? healthChips(h, 'camera') : '<span class="nm-miss">CAMERA HEALTH UNAVAILABLE</span>'}
            ${h.camera?.lastHeartbeatAt ? `<div class="muted rv-nm">Last heartbeat ${fmt.time(h.camera.lastHeartbeatAt)} · ${fmt.rel(h.camera.lastHeartbeatAt)}</div>` : ''}</dd>
          <dt>Recording</dt><dd>${h.available ? healthChips(h, 'recording') : '<span class="h-chip unknown">? UNKNOWN</span>'}${h.source === 'camview' && (h.recording?.state || 'unknown') === 'unknown' ? ' <span class="muted">not reported by Camview</span>' : ''}
            ${h.recording?.lastRecordingAt ? `<div class="muted rv-nm">Last recording ${fmt.time(h.recording.lastRecordingAt)} · ${fmt.rel(h.recording.lastRecordingAt)}</div>` : ''}</dd>
          ${h.lastEvent ? `<dt>Last change</dt><dd><span class="mono">${esc(h.lastEvent.type)}</span> <span class="muted">${fmt.rel(h.lastEvent.at)}</span></dd>` : ''}
          <dt>Event evidence</dt><dd>${ev.video ? `${icon('video', 's')} Video attached` : '<span class="nm-miss">NO VIDEO ATTACHED</span>'} · ${ev.images ? `${icon('image', 's')} ${ev.images} image${ev.images === 1 ? '' : 's'}` : '<span class="nm-miss">NO IMAGE</span>'}</dd>
          ${event ? `<dt>Alarm reported</dt><dd>${esc(event.reason || '')}${event.status ? ` <span class="b outline">${esc(event.status)}</span>` : ''}${event.at ? ` <span class="muted">at ${fmt.dt(event.at)}</span>` : ''}
            <div class="muted rv-nm">What the alarm said at that moment — not the camera's current state.</div></dd>` : ''}
        </dl>
        ${h.available ? (h.source === 'camview' ? '<div class="muted" style="font-size:11.5px;margin-top:6px">Connection as Camview reports it with every alarm (camera.frameSyncStatus), refreshed with the live feed. Nothing is inferred from alarm names.</div>' : '')
          : '<div class="muted" style="font-size:11.5px;margin-top:6px">No camera status received yet. In live mode Camview\'s frameSyncStatus is imported with the first refresh (Administration › Settings › Camera health). Nothing is inferred from alarm names.</div>'}`;
    };

    // Evidence first. An alert shows its own video + frame (with what the AI detected drawn on it). A camera
    // status event has no media by nature: it says so, and shows the latest frame Camview has from that camera.
    const mediaSection = (a) => {
      const det = data.detections;
      if (data.evidence.length) {
        return `<div class="rv-media ${a.evidence?.video ? '' : 'img-main'}">
            <section class="card"><div class="card-h"><h3>${icon('video')} Video / clip</h3><div class="sub">${a.evidence?.video ? 'Recorded clip from Camview' : ''}</div></div><div class="card-b" id="rv-video"></div></section>
            <section class="card"><div class="card-h"><h3>${icon('image')} Image evidence</h3><div class="sub">${det?.summary ? `Detected: <b>${esc(det.summary)}</b>${det.models?.length ? ` <span class="muted">· ${esc(det.models.join(', '))} · boxes drawn on the frame</span>` : ''}` : a.evidence?.images ? `${a.evidence.images} image${a.evidence.images === 1 ? '' : 's'} · click to enlarge` : ''}</div></div><div class="card-b" id="rv-images"></div></section>
          </div>`;
      }
      const ev = a.alarmEvent;
      const camEv = data.cameraEvidence;
      const head = a.eventKind === 'camera_status'
        ? `<div class="rv-noev camev">${icon('camera')}<b>CAMERA STATUS EVENT</b><span class="muted">Camview reported <b>${esc(ev?.reason || a.alarmTypeName)}</b>${ev?.status ? ` · status <b>${esc(ev.status)}</b>` : ''}${ev?.at ? ` · ${fmt.dt(ev.at)}` : ''}. Camera status events carry no image or video; the camera's current connection is shown under Camera &amp; recording.</span></div>`
        : `<div class="rv-noev">${icon('video')}<b>VIDEO UNAVAILABLE</b><span class="sep">·</span>${icon('image')}<b>IMAGE UNAVAILABLE</b><span class="muted">Camview attached no video or image to this alert.</span></div>`;
      if (!camEv?.evidence?.length) return head;
      const from = `From <a href="${reviewHref(camEv)}">${esc(camEv.alarmTypeName || camEv.alarmId)}</a> · ${fmt.dt(camEv.at)} · not evidence of this event`;
      return `${head}<div class="rv-media rv-camev ${camEv.evidence.some((e) => e.kind === 'video') ? '' : 'img-main'}">
          <section class="card"><div class="card-h"><h3>${icon('video')} Latest clip from this camera</h3><div class="sub">${from}</div></div><div class="card-b" id="rv-video"></div></section>
          <section class="card"><div class="card-h"><h3>${icon('image')} Latest frame from this camera</h3><div class="sub">${from}</div></div><div class="card-b" id="rv-images"></div></section>
        </div>`;
    };

    const ticketPanel = () => {
      const t = data.ticket;
      const a = data.alarm;
      if (!t) {
        return `<div class="muted" style="font-size:12.5px">No ticket yet. A ticket is created automatically when this alert is marked <b>VALID</b>${a.client ? ` and routed to <b>${esc(a.client.name)}</b>` : ''}.</div>`;
      }
      const ds = t.status === 'cancelled' ? 'withdrawn' : t.deliveryStatus;
      return `<dl class="kv" style="grid-template-columns:96px 1fr">
          <dt>Ticket</dt><dd>${ticketChip(t)}</dd>
          <dt>Delivery</dt><dd>${deliveryBadge(ds)}${t.deliveryNote ? `<div class="muted" style="font-size:11.5px;margin-top:3px">${esc(t.deliveryNote)}</div>` : ''}</dd>
          <dt>Client</dt><dd>${esc(t.clientName || '—')}</dd>
          <dt>Validated</dt><dd>${esc(t.validatedBy || '—')} <span class="muted">${fmt.rel(t.validatedAt)}</span></dd>
          ${t.deliveredAt ? `<dt>Delivered</dt><dd>${fmt.dt(t.deliveredAt)} <span class="muted">${esc(t.deliveredBy || '')}</span></dd>` : ''}
        </dl>
        ${data.publications.filter((p) => p.status === 'shared').map((p) => `<div class="muted" style="font-size:11.5px">${icon('eye', 's')} ${esc(p.clientName)}: ${p.viewedAt ? `viewed ${fmt.rel(p.viewedAt)}` : 'not viewed yet'}${p.acknowledgedAt ? ` · acknowledged ${fmt.rel(p.acknowledgedAt)}` : ''}</div>`).join('')}
        <div class="row" style="margin-top:10px">
          ${t.status === 'open' && ['ready', 'needs_client', 'withdrawn', 'failed'].includes(t.deliveryStatus) && data.can.send ? `<button class="btn primary sm" data-send>${icon('share', 's')} Send to client</button>` : ''}
          ${t.status === 'open' && t.deliveryStatus === 'delivered' && data.can.withdraw ? `<button class="btn sm" data-withdraw>${icon('x', 's')} Withdraw</button>` : ''}
        </div>`;
    };

    const resultStrip = () => {
      if (!last) return '';
      const d = last.delivery || {};
      const t = last.ticket;
      let msg = `<b>${RESULT_LABEL[last.result]}</b>`;
      if (last.result === 'valid' && t) {
        msg += ` · ticket <b class="mono">${esc(t.ref)}</b> ${last.ticketCreated ? 'created' : '(existing)'}${last.exam ? ` · exam <b>${esc(last.exam.name)}</b>` : ''}`;
        if (d.status === 'delivered') msg += ` · delivered to <b>${esc(d.clientName || t.clientName || 'client')}</b>`;
        else if (d.status === 'ready') msg += ` · ready to send to <b>${esc(d.clientName || t.clientName || 'client')}</b>`;
        else if (d.status === 'needs_client') msg += ' · this exam has several clients — choose one:';
        else if (d.status === 'not_deliverable') msg += ` · not delivered: ${esc(d.reason || 'client cannot receive this alert')}`;
      } else if (last.result === 'invalid') {
        msg += d.status === 'withdrawn' ? ' · removed from the client view' : ' · never sent to a client';
      } else if (last.result === 'exception') {
        msg += ' · kept in exception monitoring';
      }
      const pick = d.status === 'needs_client'
        ? `<select class="select sm" id="rv-client" aria-label="Client">${(d.options || []).map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join('')}</select>
           <button class="btn sm primary" data-pick>${icon('check', 's')} Route</button>` : '';
      const send = d.status === 'ready' && data.can.send ? `<button class="btn sm primary" data-send>${icon('share', 's')} Send now</button>` : '';
      return `<div class="rv-result ${last.result}" role="status">${icon(last.result === 'valid' ? 'check' : last.result === 'invalid' ? 'x' : 'bang')}<span class="grow">${msg}</span>${pick}${send}</div>`;
    };

    // Automatic flow (manual review off): Camview's status decides; VALID alerts go to the client by themselves.
    const autoBar = () => {
      const a = data.alarm;
      const t = data.ticket;
      const pos = position();
      let msg;
      if (a.decision === 'valid') {
        msg = t?.deliveryStatus === 'delivered' ? `VALID in Camview · sent to <b>${esc(t.clientName || 'client')}</b> automatically${t.deliveredAt ? ` ${fmt.rel(t.deliveredAt)}` : ''} · ticket <b class="mono">${esc(t.ref)}</b>`
          : !a.clients?.length ? 'VALID in Camview · <b>no client is mapped to this project</b> — add one in Clients and it is sent automatically'
            : a.clients.length > 1 && !t?.clientId ? 'VALID in Camview · this project maps to several clients — choose one in Tickets'
              : data.autoShareValid ? 'VALID in Camview · will be sent to the client on the next refresh' : 'VALID in Camview · automatic sending is turned off (Settings)';
      } else if (a.decision === 'pending') msg = data.deliveryTrigger === 'arrival'
        ? (t?.deliveryStatus === 'delivered' ? `Delivered to <b>${esc(t.clientName || 'client')}</b> on arrival${t.deliveredAt ? ` ${fmt.rel(t.deliveredAt)}` : ''} · ticket <b class="mono">${esc(t.ref)}</b> · Camview status pending` : 'PENDING in Camview · delivered to the client on arrival (next refresh)')
        : 'PENDING in Camview · sent automatically if Camview marks it VALID';
      else msg = `${esc(a.decision.toUpperCase())} in Camview · never sent to clients`;
      return `<div class="rv-result ${a.decision === 'pending' ? '' : a.decision}" role="status">${icon(a.decision === 'valid' ? 'check' : a.decision === 'pending' ? 'clock' : 'x')}<span class="grow">${msg}</span>
          <button class="btn sm" data-next title="Next alert (N)">Next ${icon('right', 's')}</button></div>
        <div class="rv-foot muted"><span>${icon('zap', 's')} Automatic: no marking needed — the status comes from Camview</span>${pos.i >= 0 ? `<span>${pos.i + 1} of ${pos.n} in the list</span>` : ''}</div>`;
    };

    const bar = () => {
      if (data.alarm.eventKind === 'camera_status') {       // camera online / offline: KPI + health only, never a ticket
        const pos = position();
        return `<div class="rv-result" role="status">${icon('camera')}<span class="grow"><b>Camera status event</b> — counted in the <a href="#/monitoring?tab=health">Cameras</a> KPI and camera health. It is not an alert to decide and never becomes a ticket.</span>
          <button class="btn sm" data-next title="Next (N)">Next ${icon('right', 's')}</button></div>
          <div class="rv-foot muted">${pos.i >= 0 ? `<span>${pos.i + 1} of ${pos.n} in the list</span>` : ''}</div>`;
      }
      if (!data.manualReview) return autoBar();
      const a = data.alarm;
      const c = data.can;
      const cur = a.decisionSource === 'operator' ? a.decision : null;
      const btn = (res, cls, ic, label, key) => (c[res] ? `<button class="btn ${cls} rv-dec ${cur === res ? 'current' : ''}" data-decide="${res}" aria-pressed="${cur === res}" ${busy ? 'disabled' : ''}>${icon(ic)} ${label}<span class="kbd">${key}</span></button>` : '');
      const any = c.valid || c.invalid || c.exception;
      const pos = position();
      return `${resultStrip()}
        <div class="rv-actions">
          ${any ? `<div class="rv-decs">${btn('valid', 'good', 'check', 'VALID', 'V')}${btn('invalid', 'invalid', 'x', 'INVALID', 'I')}${btn('exception', 'exception', 'bang', 'EXCEPTION', 'E')}</div>`
            : `<div class="banner info grow" style="margin:0">${icon('lock')}<div>You can view this alert but your role cannot decide it.</div></div>`}
          <div class="rv-nav">
            <a class="btn rv-next ${pos.prev ? '' : 'disabled'}" ${pos.prev ? `href="${reviewHref(pos.prev)}"` : 'aria-disabled="true"'} title="Previous alert ([)" aria-label="Previous alert">${icon('left', 's')}<span class="hide-sm"> Previous</span></a>
            <button class="btn rv-next" data-next ${busy ? 'disabled' : ''} title="Next pending alert (N)">Next ${icon('right', 's')}</button>
          </div>
        </div>
        ${any && (data.requireRemarks || noteOpen) ? `<input class="input rv-note" id="rv-note" maxlength="2000" placeholder="${data.requireRemarks ? 'Remark (required)' : 'Note (internal, never shown to clients)'}" ${data.requireRemarks ? 'required aria-required="true"' : ''}>` : ''}
        <div class="rv-foot muted">
          ${queueNav.items.length ? `<span class="rv-count"><b>Reviewed ${queueNav.items.filter((x) => x.decision !== 'pending').length} / ${queueNav.items.length}</b></span>` : ''}
          <label class="check"><input type="checkbox" id="rv-auto" ${pref('autoAdvance', true) ? 'checked' : ''}> Auto-next</label>
          ${any && !data.requireRemarks && !noteOpen ? `<button class="linkish" data-note>${icon('edit', 's')} Add note</button>` : ''}
          <span class="hide-sm">${data.deliveryTrigger === 'arrival' ? `${icon('zap', 's')} Delivered on arrival · INVALID / EXCEPTION withdraws` : data.deliveryMode === 'automatic' ? `${icon('zap', 's')} VALID → client automatically` : `${icon('share', 's')} VALID → one-click Send`}</span>
          <span class="hide-sm">Keys: V · I · E · N</span>
        </div>`;
    };

    const liveTag = () => {
      const st = liveStatus.state;
      return st === 'live' ? '<span class="live-tag live"><span class="dot live"></span>LIVE</span>'
        : st === 'delayed' ? '<span class="live-tag delayed">⚠ DATA DELAYED</span>'
          : st === 'disconnected' ? '<span class="live-tag disconnected">✕ DISCONNECTED</span>' : '<span class="live-tag">CONNECTING</span>';
    };

    const paint = () => {
      const a = data.alarm;
      const pos = position();
      setTitle(a.alarmTypeName || 'Review alert', `<a href="${queueHref()}">Alerts</a> › <span class="mono">${esc(a.alarmId)}</span>`);
      player?.destroy();
      const proj = a.context?.project && !a.context.project.unmapped ? (a.context.project.name && a.context.project.name !== a.context.project.code ? `${a.context.project.code} · ${a.context.project.name}` : a.context.project.code) : projectCode(a.projectId);
      const who = [a.client?.name || (a.clients?.length ? `${a.clients.length} clients` : 'CLIENT NOT MAPPED'), a.exam?.name || 'EXAM NOT MAPPED', proj];
      el.innerHTML = `
        <div class="rv">
          <div class="rv-head">
            <a class="btn icon" href="${queueHref()}" aria-label="Back to alerts (Esc)">${icon('left')}</a>
            <div class="grow" style="min-width:0">
              <div class="rv-kicker"><span id="rv-live">${liveTag()}</span>${who.map((x) => `<span class="${/NOT MAPPED/.test(x) ? 'nm-miss' : ''}">${esc(x)}</span>`).join('<span class="sep">|</span>')}</div>
              <div class="row" style="gap:8px" id="rv-title">${priorityBadge(a.priority)}<h2>${esc(a.alarmTypeName)}</h2>${decisionBadge(a.decision, a.decisionSource)}${ticketChip(data.ticket)}</div>
              <div class="rv-loc"><b class="mono">${esc(where(a))}</b>${cameraName(a) ? ` · ${esc(cameraName(a))}` : ''}</div>
              <div class="muted rv-sub"><span class="mono">${esc(a.alarmId)}</span>${a.alarmIdDerived ? ' <span class="prov derived" title="Camview sent this event without an alarm ID; the ID is built from its project, camera, type and first time">ID DERIVED</span>' : ''} ·${fmt.dt(a.firstInstance)}${a.lastInstance !== a.firstInstance ? ` → ${fmt.time(a.lastInstance)}` : ''} (${fmt.rel(a.lastInstance)})${a.totalTimesReported > 1 ? ` · reported <b>${a.totalTimesReported}×</b>` : ''}</div>
            </div>
            <div class="row tight">
              <span id="rv-new"></span><span id="rv-incident"></span>
              <a class="btn sm icon ${pos.prev ? '' : 'disabled'}" ${pos.prev ? `href="${reviewHref(pos.prev)}"` : 'aria-disabled="true"'} aria-label="Previous alert ([)">${icon('left', 's')}</a>
              <a class="btn sm icon ${pos.next ? '' : 'disabled'}" ${pos.next ? `href="${reviewHref(pos.next)}"` : 'aria-disabled="true"'} aria-label="Next alert in list (])">${icon('right', 's')}</a>
              ${data.can.details ? `<a class="btn sm" href="#/investigations/${encodeURIComponent(a.alarmId)}?projectId=${esc(a.projectId)}">${icon('investigate', 's')} Details</a>` : ''}
            </div>
          </div>
          ${mediaSection(a)}
          <div class="rv-grid">
            <section class="card"><div class="card-h"><h3>${icon('tree')} Alert context</h3><div class="sub">Resolved automatically</div></div>
              <div class="card-b"><dl class="kv rv-kv">${ctxRows(a)}</dl></div></section>
            <aside class="stack">
              <section class="card"><div class="card-h"><h3>${icon('camera')} Camera &amp; recording</h3></div><div class="card-b">${healthPanel(a)}</div></section>
              <section class="card"><div class="card-h"><h3>${icon('share')} Client routing</h3><div class="sub">Automatic</div></div><div class="card-b" id="rv-ticket">${ticketPanel()}</div></section>
              <section class="card"><div class="card-b"><dl class="kv" style="grid-template-columns:110px 1fr">
                <dt>Camview status</dt><dd>${stateBadge(a.lastActionType, a.lastActionLabel)}</dd>
                ${a.ticketId ? `<dt>Camview ticket</dt><dd>#${esc(a.ticketId)}</dd>` : ''}
                ${a.shiftLabel ? `<dt>Shift</dt><dd>${esc(a.shiftLabel)}</dd>` : ''}
                ${a.review?.status && a.review.status !== 'unreviewed' ? `<dt>Decided by</dt><dd>${esc(a.review.by || '—')} <span class="muted">${fmt.rel(a.review.at)}</span>${a.review.note ? `<div class="muted" style="font-size:11.5px">“${esc(a.review.note)}”</div>` : ''}</dd>` : ''}
              </dl></div></section>
            </aside>
          </div>
          <div class="rv-bar" id="rv-bar">${bar()}</div>
        </div>`;
      const own = data.evidence.length > 0;
      const items = own ? data.evidence : data.cameraEvidence?.evidence || [];
      const mediaAlarm = own ? a.alarmId : data.cameraEvidence?.alarmId;
      if (session.features?.ENABLE_INCIDENTS) {                // the incident this alert belongs to, if any
        api.get('/api/incidents', { alarm: a.alarmId, status: 'all', size: 1 }).then((r) => {
          const inc = r.items?.[0];
          const box = $('#rv-incident', el);
          if (inc && box && !ctx.isStale()) box.innerHTML = `<a class="btn sm" href="#/incidents/${encodeURIComponent(inc.id)}" title="Part of this incident">${icon('layers', 's')} ${esc(inc.ref)} · ${esc(inc.statusLabel || inc.status)}</a>`;
        }).catch(() => { /* best effort */ });
      }
      player = !items.length ? null : mountEvidence($('#rv-video', el), $('#rv-images', el), items,
        { alarmId: mediaAlarm, canDownload: data.can.download, title: `${mediaAlarm} · ${a.cameraCode || ''}`, boxes: own ? data.detections?.boxes || [] : [] });
    };

    const repaintSide = () => {
      $('#rv-ticket', el).innerHTML = ticketPanel();
      $('#rv-bar', el).innerHTML = bar();
    };

    const nextPending = async () => {
      const pos = position();
      if (!data?.manualReview) {                         // automatic flow: simply the next alert in the list
        if (queueNav.items[pos.i + 1]) return queueNav.items[pos.i + 1];
      }
      const after = queueNav.items.slice(pos.i + 1).find((x) => x.decision === 'pending' && x.alarmId !== id);
      if (after) return after;
      try {
        const r = await api.get('/api/queue', { ...queueNav.query, status: 'pending', size: 5 });
        return r.items.find((x) => x.alarmId !== id) || null;
      } catch { return null; }
    };

    const goNext = async () => {
      clearTimeout(advanceTimer);
      const n = await nextPending();
      if (ctx.isStale()) return;
      if (n) location.hash = reviewHref(n);
      else { toast(data?.manualReview ? 'Queue clear — nothing pending' : 'End of the list', 'success'); location.hash = queueHref(); }
    };

    const decide = async (result, clientId) => {
      if (busy || !data) return;
      const a = data.alarm;
      const note = $('#rv-note', el)?.value.trim() || null;
      if (data.requireRemarks && !note && !clientId) { toast('A remark is required by policy', 'warning'); $('#rv-note', el)?.focus(); return; }
      if (result !== 'valid' && data.ticket?.status === 'open' && data.ticket.deliveryStatus === 'delivered') {
        const ok = await confirmDialog({ title: 'Remove from the client?', message: `This alert was delivered to ${esc(data.ticket.clientName || 'the client')}. Marking it ${result.toUpperCase()} withdraws it from their view and cancels ticket ${esc(data.ticket.ref)}.`, confirmLabel: `Mark ${result}`, danger: true });
        if (!ok) return;
      }
      busy = true;
      $$('[data-decide]', el).forEach((b) => { b.disabled = true; });
      try {
        const r = await api.post(`/api/queue/${encodeURIComponent(a.alarmId)}/decide`, { result, note, clientId: clientId || null, projectId: a.projectId });
        if (ctx.isStale()) return;
        last = r;
        a.decision = result; a.decisionSource = 'operator';
        a.review = { ...(a.review || {}), ...(r.review || {}) };
        data.ticket = r.ticket;
        const qi = queueNav.items.find((x) => x.alarmId === a.alarmId);
        if (qi) qi.decision = result;
        busy = false;
        paintHeadBadges();
        repaintSide();
        const d = r.delivery?.status;
        const done = result !== 'valid' || d === 'delivered' || d === 'not_deliverable' || (d === 'ready' && !data.can.send);
        if (done && pref('autoAdvance', true)) {
          toast(`${RESULT_LABEL[result]}${r.ticket && result === 'valid' ? ` · ${r.ticket.ref}` : ''}${d === 'delivered' ? ' · delivered' : ''}`, 'success');
          advanceTimer = setTimeout(goNext, 900);
        }
      } catch (e) {
        busy = false;
        toast(e.code === 'remarks_required' ? 'A remark is required by policy' : e.message, 'error');
        if (e.code === 'delivery_failed') {                      // decision saved, delivery not: show it, allow retry
          a.decision = result; a.decisionSource = 'operator';
          try { data.ticket = (await api.get(`/api/queue/${encodeURIComponent(a.alarmId)}`, { projectId: a.projectId })).ticket; } catch { /* keep */ }
          paintHeadBadges();
        }
        repaintSide();
      }
    };

    const paintHeadBadges = () => {
      const h = $('#rv-title', el);
      if (!h) return;
      h.innerHTML = `${priorityBadge(data.alarm.priority)}<h2>${esc(data.alarm.alarmTypeName)}</h2>${decisionBadge(data.alarm.decision, data.alarm.decisionSource)}${ticketChip(data.ticket)}`;
    };

    const send = async (clientId) => {
      const t = data.ticket;
      if (!t || busy) return;
      busy = true;
      try {
        const r = await api.post(`/api/tickets/${t.id}/send`, clientId ? { clientId } : {});
        if (ctx.isStale()) return;
        data.ticket = r.ticket;
        if (last) last.delivery = { status: r.ticket.deliveryStatus, clientName: r.ticket.clientName };
        toast(`Sent to ${r.ticket.clientName || 'client'} · ${r.ticket.ref}`, 'success');
        busy = false;
        paintHeadBadges();
        repaintSide();
        if (pref('autoAdvance', true) && r.ticket.deliveryStatus === 'delivered') advanceTimer = setTimeout(goNext, 900);
      } catch (e) { busy = false; toast(e.message, 'error'); }
    };

    const withdraw = async () => {
      const t = data.ticket;
      const ok = await confirmDialog({ title: 'Withdraw from client?', message: `The client will no longer see ${esc(t.ref)}. The decision (VALID) and ticket stay.`, confirmLabel: 'Withdraw', danger: true });
      if (!ok) return;
      try {
        const r = await api.post(`/api/tickets/${t.id}/withdraw`, {});
        data.ticket = r.ticket; last = null;
        toast('Withdrawn from the client view', 'success');
        repaintSide();
      } catch (e) { toast(e.message, 'error'); }
    };

    el.addEventListener('click', (e) => {
      const d = e.target.closest('[data-decide]');
      if (d) return decide(d.dataset.decide);
      if (e.target.closest('[data-next]')) return goNext();
      if (e.target.closest('[data-send]')) return send();
      if (e.target.closest('[data-withdraw]')) return withdraw();
      if (e.target.closest('[data-pick]')) return decide('valid', $('#rv-client', el).value);
      if (e.target.closest('[data-note]')) { noteOpen = true; $('#rv-bar', el).innerHTML = bar(); $('#rv-note', el)?.focus(); }
    });
    el.addEventListener('change', (e) => { if (e.target.id === 'rv-auto') setPref('autoAdvance', e.target.checked); });

    const keys = (e) => {
      if (!data || e.ctrlKey || e.metaKey || e.altKey) return;
      if (document.activeElement?.matches('input:not([type=checkbox]):not([type=radio]):not([type=range]), textarea, select')) return;
      if (document.querySelector('.overlay, .lightbox, .palette')) return;
      const k = e.key.toLowerCase();
      if (k === ' ' && document.activeElement?.matches('button, a, input')) return;
      const pos = position();
      if ((!data.manualReview || data.alarm.eventKind === 'camera_status') && ['v', 'i', 'e'].includes(k)) return;
      if (k === 'v' && data.can.valid) decide('valid');
      else if (k === 'i' && data.can.invalid) decide('invalid');
      else if (k === 'e' && data.can.exception) decide('exception');
      else if (k === 'n') goNext();
      else if (k === ']' && pos.next) location.hash = reviewHref(pos.next);
      else if (k === '[' && pos.prev) location.hash = reviewHref(pos.prev);
      else if (k === 'arrowright') player?.next();
      else if (k === 'arrowleft') player?.prev();
      else if (k === ' ') { e.preventDefault(); player?.togglePlay(); }
      else if (k === 'escape') location.hash = queueHref();
    };
    document.addEventListener('keydown', keys);
    ctx.onCleanup(() => { document.removeEventListener('keydown', keys); clearTimeout(advanceTimer); player?.destroy(); });
    // live refresh never disturbs the review in progress: only the ticket panel is refreshed, and only when idle.
    ctx.onCleanup(on('status', () => {
      const lt = $('#rv-live', el);
      if (lt) lt.innerHTML = liveTag();
    }));
    ctx.onCleanup(on('data', async () => {
      if (busy || !data || document.activeElement?.id === 'rv-note') return;
      try {                                                   // new alerts are announced, never forced on the reviewer
        const q = await api.get('/api/queue', { status: 'pending', size: 100 });
        if (ctx.isStale()) return;
        if (!known.size) q.items.forEach((x) => known.add(x.alarmId));
        const fresh = q.items.filter((x) => !known.has(x.alarmId) && x.alarmId !== id).length;
        const box = $('#rv-new', el);
        if (box) box.innerHTML = fresh ? `<a class="btn sm new-alerts" href="${queueHref({ status: 'pending' })}">${icon('bell', 's')} ${fresh} new alert${fresh === 1 ? '' : 's'} · View</a>` : '';
      } catch { /* announcement is best effort */ }
      try {
        const fresh = await api.get(`/api/queue/${encodeURIComponent(id)}`, { projectId });
        if (ctx.isStale() || busy) return;
        if (JSON.stringify(fresh.ticket) !== JSON.stringify(data.ticket)) { data.ticket = fresh.ticket; $('#rv-ticket', el).innerHTML = ticketPanel(); }
      } catch { /* keep showing the last data */ }
    }));

    await load();
  },
};
