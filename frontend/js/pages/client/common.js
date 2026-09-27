// pages/client/common.js — small helpers for the client portal (client-safe data only).

import { esc, fmt, icon, priorityBadge } from '../../core/ui.js';

export const ctxCodes = (a) => (a.context || []).length
  ? `<span class="ctx-path">${a.context.map((c) => `<span title="${esc(c.level.toUpperCase())}${c.name && c.name !== c.code ? ' · ' + esc(c.name) : ''}">${esc(c.code)}</span>`).join('<span class="sep">/</span>')}</span>`
  : '<span class="muted">—</span>';

export const ackBadge = (a) => a.acknowledgedAt
  ? `<span class="b vis-shared" title="Acknowledged by ${esc(a.acknowledgedBy || '')}">${icon('check')}acknowledged</span>`
  : `<span class="b vis-ready_for_review">${icon('clock')}awaiting acknowledgement</span>`;

export const locationLine = (a) => (a.locationLabel
  ? `<span class="mono">${esc(a.locationLabel)}</span>${a.cameraName ? ` · ${esc(a.cameraName)}` : ''}` : ctxCodes(a));

export const alertHref = (a) => `#/client/alerts/${encodeURIComponent(a.alarmId)}`;

/** The shared alert frame (through the evidence proxy) with a play marker when a clip was shared. */
export const clientThumb = (a) => (a.imageUrl
  ? `<span class="q-thumb" title="Shared frame${a.hasVideo ? ' · video shared' : ''}"><img src="${esc(a.imageUrl)}" alt="" loading="lazy">${a.hasVideo ? `<span class="q-play">${icon('play', 's')}</span>` : ''}</span>`
  : `<span class="q-thumb none" title="No image shared">${icon('image')}</span>`);

export function alertCard(a) {
  const lvl = (k) => (a.context || []).find((c) => c.level === k);
  const centre = lvl('centre') || lvl('tc');
  const room = lvl('room');
  const cam = lvl('camera');
  return `<a class="pcard p-${esc(a.priority || 'unknown')}" href="${alertHref(a)}">
    ${a.imageUrl ? `<div class="pc-frame"><img src="${esc(a.imageUrl)}" alt="" loading="lazy">${a.hasVideo ? `<span class="q-play">${icon('play', 's')}</span>` : ''}</div>` : ''}
    <div class="row" style="gap:6px">${priorityBadge(a.priority)}<span class="grow"></span><span class="muted num">${fmt.dt(a.firstInstance)}</span></div>
    <div class="t1">${esc(a.alarmTypeName)}</div>
    ${a.exam ? `<div class="t2">${icon('calendar', 's')} ${esc(a.exam.name)}</div>` : ''}
    ${centre ? `<div class="t2">${icon('building', 's')} Centre <b class="mono">${esc(centre.code)}</b>${centre.name && centre.name !== centre.code ? ` · ${esc(centre.name)}` : ''}</div>` : ''}
    ${room ? `<div class="t2">${icon('tree', 's')} Room ${esc(room.code)}</div>` : ''}
    ${cam ? `<div class="t2">${icon('camera', 's')} <span class="mono">${esc(cam.code)}</span>${a.cameraName ? ` · ${esc(a.cameraName)}` : ''}</div>` : ''}
    <div class="pc-foot"><span>${a.evidence?.length ? `${icon('image', 's')} ${a.evidence.length} evidence` : '<span class="muted">No evidence shared</span>'}${a.ticketRef ? ` · <span class="mono">${esc(a.ticketRef)}</span>` : ''}</span><span class="btn sm primary">View ${icon('right', 's')}</span></div>
  </a>`;
}

export function alertRow(a) {
  return `<a class="li" href="${alertHref(a)}">${clientThumb(a)}${priorityBadge(a.priority)}
    <div class="grow"><div class="t1"><span class="mono">${esc(a.alarmId)}</span><span class="dim" style="font-weight:500">${esc(a.alarmTypeName)}</span></div>
      <div class="t2">${a.exam ? `<b>${esc(a.exam.name)}</b> · ` : ''}${a.ticketRef ? `<span class="mono">${esc(a.ticketRef)}</span> · ` : ''}${locationLine(a)} · shared ${fmt.rel(a.sharedAt)}${a.evidence?.length ? ` · ${icon('image', 's')} ${a.evidence.length}` : ''}</div></div>${ackBadge(a)}</a>`;
}
