// components/alarms.js — the enterprise alarm table columns and alarm cards.
// Alarm state, workflow state and client visibility are ALWAYS separate.

import { esc, fmt, icon, priorityBadge, stateBadge, workflowBadge, visBadge, evBadge, contextPath, newBadge, slaBadge } from '../core/ui.js';
import { thumb } from './queue.js';
import { projectCode } from '../core/state.js';

export const investigateHref = (a) => `#/investigations/${encodeURIComponent(a.alarmId)}?projectId=${encodeURIComponent(a.projectId)}`;

const COLS = {
  thumb: { label: '', render: (a) => `<span class="tcell">${thumb(a)}</span>` },
  priority: { label: 'Priority', sort: 'priority', render: (a) => `${priorityBadge(a.priority)}${a.flags?.new ? ' ' + newBadge() : ''}` },
  alarm: { label: 'Alarm', sort: 'alarmId', render: (a) => `<div class="cell-2"><a class="mono" href="${investigateHref(a)}">${esc(a.alarmId)}</a><span class="l2">${esc(a.alarmTypeName)}</span></div>` },
  type: { label: 'Type', sort: 'alarmTypeName', render: (a) => esc(a.alarmTypeName) },
  camera: { label: 'Camera', sort: 'cameraCode', render: (a) => `<div class="cell-2"><a class="mono" href="#/cameras/${encodeURIComponent(a.cameraId)}?projectId=${encodeURIComponent(a.projectId)}">${esc(a.cameraCode)}</a><span class="l2">${esc(a.cameraName || '')}</span></div>` },
  context: { label: 'Context', render: (a) => contextPath((a.context?.path || []).slice(0, -1), { max: 5 }) },
  project: { label: 'Project', render: (a) => esc(a.context?.path?.find((n) => n.level === 'project')?.code || a.projectId) },
  tc: { label: 'TC', render: (a) => esc(a.context?.path?.find((n) => n.level === 'tc')?.code || '—') },
  centre: { label: 'Centre', render: (a) => esc(a.context?.path?.find((n) => n.level === 'centre')?.code || '—') },
  state: { label: 'Status', sort: 'lastActionType', render: (a) => stateBadge(a.lastActionType, a.lastActionLabel) },
  workflow: { label: 'Workflow', sort: 'workflowState', render: (a) => `${workflowBadge(a.workflowState, a.workflowLabel)}${a.sla ? ' ' + slaBadge(a.sla) : ''}` },
  visibility: { label: 'Client', sort: 'visibility', render: (a) => visBadge(a.visibility?.state, null, a.visibility?.acknowledged) },
  occurrences: { label: 'Occ.', sort: 'totalTimesReported', num: true, render: (a) => `<span class="num" title="totalTimesReported">${fmt.n(a.totalTimesReported)}</span>` },
  shift: { label: 'Shift', sort: 'shiftLabel', render: (a) => `<span class="dim">${esc(a.shiftLabel || '—')}</span>` },
  first: { label: 'First seen', sort: 'firstInstance', render: (a) => `<span class="num dim">${fmt.dt(a.firstInstance)}</span>` },
  last: { label: 'Last seen', sort: 'lastInstance', render: (a) => `<div class="cell-2"><span class="num">${fmt.dt(a.lastInstance)}</span><span class="l2">${fmt.rel(a.lastInstance)}</span></div>` },
  evidence: { label: 'Evidence', render: (a) => evBadge(a.evidence) },
  assigned: { label: 'Assigned', render: (a) => (a.assignment ? `<span class="dim">${esc(a.assignment)}</span>` : '<span class="muted">—</span>') },
  review: { label: 'Ops review', render: (a) => `<span class="dim">${esc({ unreviewed: '—', acknowledged: 'Acknowledged', marked_valid: 'Valid', marked_invalid: 'Invalid', marked_exception: 'Exception' }[a.review?.status] || '—')}</span>` },
  actions: { label: '', render: (a) => `<a class="btn sm" href="${investigateHref(a)}">${icon('investigate', 's')} Investigate</a>` },
};

export const DEFAULT_COLUMNS = ['thumb', 'priority', 'alarm', 'camera', 'context', 'state', 'workflow', 'visibility', 'occurrences', 'shift', 'last', 'evidence'];

export function columns(keys = DEFAULT_COLUMNS) {
  return keys.map((k) => ({ key: k, ...COLS[k] }));
}

export function alarmCard(a, { action = 'investigate' } = {}) {
  const path = a.context?.path || [];
  const get = (lvl) => path.find((n) => n.level === lvl)?.code;
  const loc = [get('centre'), get('building') && `Building ${get('building')}`, get('floor') && `Floor ${get('floor')}`, get('room') && `Room ${get('room')}`].filter(Boolean).join(' · ');
  return `<article class="acard p-${esc(a.priority)}">
    <div class="acard-frame">${thumb(a)}</div>
    <div class="top">${priorityBadge(a.priority)}<span class="id">${esc(a.alarmId)}</span>${a.flags?.new ? newBadge() : ''}<span class="grow"></span>${evBadge(a.evidence)}</div>
    <div style="font-weight:650">${esc(a.alarmTypeName)}</div>
    <div class="ctx-path">${esc(get('project') || projectCode(a.projectId))}${get('tc') ? ` <span class="sep">/</span> ${esc(get('tc'))}` : ''}</div>
    ${loc ? `<div class="dim" style="font-size:12px">${esc(loc)}</div>` : ''}
    <div class="row" style="font-size:12px"><span class="mono">${esc(a.cameraCode)}</span><span class="muted">·</span><span>${fmt.n(a.totalTimesReported)} occurrence${a.totalTimesReported === 1 ? '' : 's'}</span><span class="muted">·</span><span class="num dim">${fmt.time(a.firstInstance)} → ${fmt.time(a.lastInstance)}</span></div>
    <div class="row">${stateBadge(a.lastActionType, a.lastActionLabel)}${visBadge(a.visibility?.state, null, a.visibility?.acknowledged)}</div>
    ${action === 'investigate' ? `<a class="btn sm primary" href="${investigateHref(a)}" style="align-self:flex-start">${icon('investigate', 's')} Investigate</a>` : ''}
  </article>`;
}

export function miniRow(a, extra = '') {
  return `<a class="li" href="${investigateHref(a)}">
    ${thumb(a)}${priorityBadge(a.priority)}
    <div class="grow"><div class="t1"><span class="mono">${esc(a.alarmId)}</span>${a.flags?.new ? newBadge() : ''}<span class="dim" style="font-weight:500">${esc(a.alarmTypeName)}</span></div>
      <div class="t2">${esc(a.cameraCode)} · ${contextPath((a.context?.path || []).slice(1, 4), { max: 3, unmappedNote: false })} · ${fmt.rel(a.lastInstance)}${a.assignment ? ' · ' + esc(a.assignment) : ''}</div></div>
    <div class="row tight">${extra}${stateBadge(a.lastActionType, a.lastActionLabel)}</div></a>`;
}
