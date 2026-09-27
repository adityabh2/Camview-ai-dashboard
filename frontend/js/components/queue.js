// components/queue.js — shared pieces of the automated review flow:
// decision / delivery badges, the "where" line, the alert row and the
// in-memory queue order used by the review screen's previous / next.

import { esc, icon, fmt, priorityBadge } from '../core/ui.js';

export const DECISIONS = {
  pending: ['Pending review', 'clock', 'v-pending'],
  valid: ['Valid', 'check', 'v-valid'],
  invalid: ['Invalid', 'x', 'v-invalid'],
  exception: ['Exception', 'bang', 'v-exception'],
};

export const decisionBadge = (d, source) => {
  const [l, i, c] = DECISIONS[d] || DECISIONS.pending;
  const src = source === 'camview' ? ' title="Status from Camview (no operator decision yet)"' : source === 'operator' ? ' title="Operator decision"' : '';
  return `<span class="b dec ${c}"${src}>${icon(i)}${l}${source === 'camview' && d !== 'pending' ? ' · Camview' : ''}</span>`;
};

export const DELIVERY = {
  ready: ['Ready to send', 'clock', 'vis-ready_for_review'],
  needs_client: ['Choose client', 'bang', 'vis-ready_for_review'],
  delivered: ['Delivered', 'share', 'vis-shared'],
  withdrawn: ['Withdrawn', 'x', 'vis-withdrawn'],
  failed: ['DELIVERY FAILED — retry', 'alert', 'vis-withdrawn'],
  not_deliverable: ['Not deliverable', 'lock', 'vis-internal'],
  none: ['Not delivered', 'lock', 'vis-internal'],
};

export const deliveryBadge = (s) => {
  const [l, i, c] = DELIVERY[s] || DELIVERY.none;
  return `<span class="b ${c}">${icon(i)}${l}</span>`;
};

export const ticketChip = (t) => (t && t.ref
  ? `<span class="b outline mono" title="Ticket ${esc(t.ref)}${t.status === 'cancelled' ? ' (cancelled)' : ''}">${icon('report')}${esc(t.ref)}${t.status === 'cancelled' ? ' · cancelled' : ''}</span>`
  : '');

/** Nomenclature naming: "PROJECT-07 - TC-0701 - CAM-106" (+ camera name when known). */
export function labelFromContext(ctx, projectId, cameraId) {
  const project = ctx?.project?.code || String(projectId ?? '');
  const tc = ctx?.tc?.code || 'TC not mapped';
  return `${project} - ${tc} - ${cameraId ? `CAM-${cameraId}` : 'camera unknown'}`;
}

export function where(a) {
  return a.locationLabel || labelFromContext(Object.fromEntries((a.context?.path || []).map((n) => [n.level, n])), a.projectId, a.cameraId);
}

/** Camview's own camera name (subLocation, e.g. "Camera1"); empty when it is already part of the location label. */
export const cameraName = (a) => (a.cameraName && !/^Camera \d+$/.test(a.cameraName) && !(a.locationLabel || '').includes(a.cameraName) ? a.cameraName : '');

/** Camera connection / recording chips from the REAL health source (never from alarm names). */
const H_CAM = { online: ['● ONLINE', 'good'], offline: ['● OFFLINE', 'bad'], unknown: ['? UNKNOWN', 'unknown'] };
const H_REC = { recording: ['● RECORDING', 'good'], not_recording: ['⚠ NOT RECORDING', 'warn'], unknown: ['? UNKNOWN', 'unknown'] };
export function healthChips(h, part = 'both') {
  if (!h || !h.available) return part === 'both' ? '<span class="h-chip unknown" title="No real camera-health source connected">HEALTH N/A</span>' : '<span class="h-chip unknown">? UNKNOWN</span>';
  const stale = h.conditions?.includes('HEARTBEAT_STALE') ? ' <span class="h-chip warn" title="No recent status report">STATUS STALE</span>' : '';
  const failed = h.conditions?.includes('FRAME_SYNC_FAILED') ? ' <span class="h-chip warn" title="Camview could not sync frames from this camera (frameSyncStatus = FAILED)">FRAME SYNC FAILED</span>' : '';
  const [cl, cc] = H_CAM[h.camera?.state] || H_CAM.unknown;
  const [rl, rc] = H_REC[h.recording?.state] || H_REC.unknown;
  const src = h.source === 'camview' ? 'Camera connection as Camview reports it (frameSyncStatus)' : 'Camera connection';
  const cam = `<span class="h-chip ${cc}" title="${src}">${cl}</span>`;
  // Camview does not report recording: no "unknown" recording chip on every row when it is the source
  const rec = h.source === 'camview' && (h.recording?.state || 'unknown') === 'unknown' && part === 'both' ? '' : `<span class="h-chip ${rc}" title="Recording">${rl}</span>`;
  return part === 'camera' ? cam + failed + stale : part === 'recording' ? rec : cam + rec + failed + stale;
}

/** The status Camview reported WITH a camera event (a fact about that moment, not the camera's current state). */
const EVENT_STATUS = { OFFLINE: ['● CAMERA OFFLINE', 'bad'], ONLINE: ['● CAMERA ONLINE', 'good'] };
export function eventChip(a) {
  const st = (a.alarmEvent?.status || '').toUpperCase();
  const [l, c] = EVENT_STATUS[st] || [`● ${esc((a.alarmEvent?.reason || a.alarmTypeName || 'CAMERA EVENT').toUpperCase())}`, 'unknown'];
  return `<span class="h-chip ${c}" title="Status Camview reported with this event (${esc(a.alarmEvent?.reason || '')}) — not the camera's current state">${l}</span>`;
}

/** Row thumbnail: the alert frame Camview attached (video marker when a clip exists) or a camera-status tile. */
export function thumb(a) {
  if (a.eventKind === 'camera_status') {
    const st = (a.alarmEvent?.status || '').toLowerCase();
    return `<span class="q-thumb ev ${st === 'offline' || st === 'online' ? st : ''}" title="Camera status event — Camview attaches no image or video">${icon('camera')}<small>${st === 'offline' ? 'OFFLINE' : st === 'online' ? 'ONLINE' : 'STATUS'}</small></span>`;
  }
  if (a.imageUrl) return `<span class="q-thumb" title="Alert frame from Camview${a.hasVideo ? ' · video clip attached' : ''}"><img src="${esc(a.imageUrl)}" alt="" loading="lazy">${a.hasVideo ? `<span class="q-play">${icon('play', 's')}</span>` : ''}</span>`;
  return `<span class="q-thumb none" title="No image attached">${icon('image')}</span>`;
}

export const evidenceAvail = (ev) => `<span class="ev-av ${ev?.video ? 'yes' : 'no'}">${icon('video', 's')} ${ev?.video ? 'Video' : 'No video'}</span>`
  + `<span class="ev-av ${ev?.images ? 'yes' : 'no'}">${icon('image', 's')} ${ev?.images ? `${ev.images} image${ev.images === 1 ? '' : 's'}` : 'No image'}</span>`;

export const reviewHref = (a) => `#/alerts/${encodeURIComponent(a.alarmId)}?projectId=${encodeURIComponent(a.projectId ?? '')}`;

const centreLine = (a) => {
  const c = a.context?.centre;
  const name = c?.name && c.name !== c.code ? c.name : a.centreName;
  const room = a.context?.room?.code || a.cameraSubLocation;
  return name || room ? `<span class="muted">${esc([name, room].filter(Boolean).join(' · '))}</span>` : '';
};

export function alertRow(a, { isNew = false, selected = false } = {}) {
  const who = [a.exam?.name, a.client?.name || (a.clients?.length > 1 ? `${a.clients.length} clients` : null)].filter(Boolean).join(' · ');
  const isEvent = a.eventKind === 'camera_status';
  return `<a class="qrow ${isNew ? 'new' : ''} ${selected ? 'sel' : ''} ${isEvent ? 'camev' : ''}" href="${reviewHref(a)}" data-id="${esc(a.alarmId)}" data-key="${esc(a.alarmId)}">
    <span class="q-p">${priorityBadge(a.priority)}</span>
    <span class="q-main">${thumb(a)}<span class="q-text"><span class="t1">${esc(a.alarmTypeName)}${isNew ? '<span class="b new">NEW</span>' : ''}</span>
      <span class="t2"><span class="mono">${esc(where(a))}</span>${cameraName(a) ? ` · ${esc(cameraName(a))}` : ''}${a.totalTimesReported > 1 ? ` · <b>${a.totalTimesReported}×</b>` : ''}</span>
      <span class="t3">${centreLine(a)}${isEvent ? eventChip(a) : ''}${healthChips(a.health)}${isEvent ? '' : evidenceAvail(a.evidence)}</span></span></span>
    <span class="q-who">${who ? esc(who) : '<span class="nm-miss">CLIENT NOT MAPPED</span>'}</span>
    <span class="q-st">${decisionBadge(a.decision, a.decisionSource)}${ticketChip(a.ticket)}${a.decisionSource === 'operator' && a.review?.by ? `<span class="q-by" title="Decided by the operations team">by ${esc(a.review.by)}</span>` : ''}</span>
    <span class="q-when num" title="${esc(fmt.dt(a.lastInstance))}">${fmt.time(a.lastInstance)}<br><span class="muted">${fmt.rel(a.lastInstance)}</span></span>
  </a>`;
}

/** The order the operator last saw in the queue — used for previous / next / "next pending". */
export const queueNav = { ids: [], items: [], query: {} };

export function rememberQueue(items, query) {
  queueNav.items = items.map((a) => ({ alarmId: a.alarmId, projectId: a.projectId, decision: a.decision }));
  queueNav.ids = queueNav.items.map((a) => a.alarmId);
  queueNav.query = { ...query };
}

export function queueHref(q = queueNav.query) {
  const qs = new URLSearchParams(Object.entries(q || {}).filter(([, v]) => v !== '' && v != null)).toString();
  return `#/alerts${qs ? '?' + qs : ''}`;
}
