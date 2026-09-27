// pages/compare.js — COMPARE ALARMS side by side; differing rows highlighted.

import * as api from '../core/api.js';
import { currentProject } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, skeleton, priorityBadge, stateBadge, workflowBadge, visBadge, reviewBadge, contextPath, $ } from '../core/ui.js';
import { investigateHref } from '../components/alarms.js';

const node = (a, lvl) => a?.context?.[lvl]?.code || '—';

const ROWS = [
  ['Alarm type', (a) => esc(a.alarmTypeName), (a) => a.alarmType],
  ['Camera', (a) => `<a class="mono" href="#/cameras/${encodeURIComponent(a.cameraId)}?projectId=${a.projectId}">${esc(a.cameraCode)}</a> <span class="muted">${esc(a.cameraName || '')}</span>`, (a) => a.cameraId],
  ['Context', (a) => contextPath(a.context?.path || []), (a) => (a.context?.path || []).map((n) => n.code).join('/')],
  ['Project', (a) => esc(node(a, 'project')), (a) => node(a, 'project')],
  ['TC', (a) => esc(node(a, 'tc')), (a) => node(a, 'tc')],
  ['Centre', (a) => esc(node(a, 'centre')), (a) => node(a, 'centre')],
  ['Room', (a) => esc(node(a, 'room')), (a) => node(a, 'room')],
  ['Priority', (a) => priorityBadge(a.priority), (a) => a.priority],
  ['Camview status', (a) => stateBadge(a.lastActionType, a.lastActionLabel), (a) => a.lastActionType],
  ['Ops review', (a) => reviewBadge(a.review?.status), (a) => a.review?.status],
  ['Workflow', (a) => workflowBadge(a.workflowState, a.workflowLabel), (a) => a.workflowState],
  ['Client visibility', (a) => visBadge(a.visibility?.state, a.visibility?.label, a.visibility?.acknowledged), (a) => a.visibility?.state],
  ['Occurrences', (a) => `<span class="num">${fmt.n(a.totalTimesReported)}</span>`, (a) => a.totalTimesReported],
  ['First instance', (a) => `<span class="num">${fmt.dt(a.firstInstance)}</span>`, (a) => a.firstInstance],
  ['Last instance', (a) => `<span class="num">${fmt.dt(a.lastInstance)}</span>`, (a) => a.lastInstance],
  ['Duration', (a) => (a.spanMinutes != null ? fmt.dur(a.spanMinutes) : '—'), (a) => a.spanMinutes],
  ['Evidence', (a) => `${a.evidence?.images || 0} image(s)${a.evidence?.video ? ' · video' : ''}`, (a) => `${a.evidence?.images}/${a.evidence?.video}`],
  ['Ticket', (a) => (a.ticketId ? `#${esc(a.ticketId)}` : '—'), (a) => a.ticketId],
  ['Shift', (a) => esc(a.shiftLabel || '—'), (a) => a.shiftLabel],
];

export default {
  async render(el, ctx) {
    const pid = ctx.query.projectId || currentProject();
    const ids = [ctx.query.a || '', ctx.query.b || ''];
    setTitle('Compare alarms', `<a href="#/investigations">Investigations</a> / compare`);
    el.innerHTML = `<div class="page-head"><div><h2>Compare alarms</h2><p>Differences are highlighted. Only alarms in your scope can be compared.</p></div></div>
      <div class="filters">
        <input class="input" id="ca" placeholder="Alarm A (ALM-…)" value="${esc(ids[0])}" style="min-width:220px">
        <input class="input" id="cb" placeholder="Alarm B (ALM-…)" value="${esc(ids[1])}" style="min-width:220px">
        <button class="btn primary" id="cgo">${icon('compare', 's')} Compare</button>
      </div>
      <section class="card"><div class="card-b flush" id="cmp">${ids[0] && ids[1] ? skeleton(10, 22) : '<div class="empty"><div class="e-t">Enter two alarm IDs</div></div>'}</div></section>`;

    $('#cgo', el).addEventListener('click', () => {
      ctx.setQuery({ a: $('#ca', el).value.trim(), b: $('#cb', el).value.trim(), projectId: pid });
      ctx.navigate(location.hash);
    });
    if (!ids[0] || !ids[1]) return;

    const fetchOne = (id) => api.get(`/api/alarms/${encodeURIComponent(id)}`, { projectId: pid }).then((d) => d.alarm).catch((e) => ({ error: e }));
    const [A, B] = await Promise.all(ids.map(fetchOne));
    if (ctx.isStale()) return;
    const head = (a, id) => a.error
      ? `<th><span class="mono">${esc(id)}</span><div class="sla-attention" style="text-transform:none;letter-spacing:0">${a.error.status === 404 ? 'Alarm not available' : esc(a.error.message)}</div></th>`
      : `<th><a class="mono" href="${investigateHref(a)}">${esc(a.alarmId)}</a></th>`;
    const cell = (a, fn) => (a.error ? '<td class="muted">—</td>' : `<td>${fn(a)}</td>`);
    let diffs = 0;
    const body = ROWS.map(([label, render, key]) => {
      const differs = !A.error && !B.error && String(key(A)) !== String(key(B));
      if (differs) diffs++;
      return `<tr style="${differs ? 'background:var(--st-warning-bg)' : ''}"><td class="dim" style="width:160px">${esc(label)}${differs ? ' <span class="b outline" title="Values differ">≠</span>' : ''}</td>${cell(A, render)}${cell(B, render)}</tr>`;
    }).join('');
    $('#cmp', el).innerHTML = `<div class="table-wrap"><table class="t"><thead><tr><th>Field</th>${head(A, ids[0])}${head(B, ids[1])}</tr></thead><tbody>${body}</tbody></table></div>
      ${!A.error && !B.error ? `<div class="pager"><span class="grow muted">${diffs} of ${ROWS.length} fields differ</span></div>` : ''}`;
  },
};
