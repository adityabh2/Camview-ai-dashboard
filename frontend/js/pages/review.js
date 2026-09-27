// pages/review.js — REVIEW QUEUE: "What needs review, approval or sharing right now?"
// Tabs map to server-side filters on the same /api/alarms endpoint (scope + RBAC enforced there).

import * as api from '../core/api.js';
import { currentProject, projectInfo, on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, pager, errorBox, skeleton, empty, delegate, slaBadge, $, $$ } from '../core/ui.js';
import { columns, investigateHref } from '../components/alarms.js';

const TABS = [
  { key: 'pending', label: 'Pending', params: { quick: 'pending', sort: 'priority', dir: 'asc' },
    empty: ['No pending reviews.', 'Every pending alarm in your scope has an operator decision.'] },
  { key: 'investigating', label: 'Under investigation', params: { workflowState: 'INVESTIGATING', sort: 'lastInstance', dir: 'desc' },
    empty: ['No investigations.', 'No alarm is currently assigned or marked as an exception.'] },
  { key: 'approval', label: 'Ready for approval', params: { workflowState: 'READY_FOR_APPROVAL', sort: 'lastInstance', dir: 'desc' },
    empty: ['Nothing awaiting approval.', 'No client-sharing request is waiting for a supervisor.'] },
  { key: 'client', label: 'Ready for client', params: { workflowState: 'READY_FOR_CLIENT', sort: 'lastInstance', dir: 'desc' },
    empty: ['No ready-to-share alerts.', 'Validated alarms appear here until someone requests client review.'] },
  { key: 'shared', label: 'Recently shared', params: { visibility: 'shared', sort: 'lastInstance', dir: 'desc' },
    empty: ['No shared alerts.', 'Nothing has been published to a client in the monitored window.'] },
  { key: 'withdrawn', label: 'Withdrawn', params: { visibility: 'withdrawn', sort: 'lastInstance', dir: 'desc' },
    empty: ['No withdrawn alerts.', 'Alerts withdrawn from a client appear here.'] },
];

const AGE_COL = {
  key: 'age', label: 'Age', sort: 'ageMinutes',
  render: (a) => (a.ageMinutes == null ? '<span class="muted">—</span>'
    : `<div class="cell-2"><span class="num">${fmt.dur(a.ageMinutes)}</span>${a.sla ? `<span>${slaBadge(a.sla)}</span>` : ''}</div>`),
};

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Review Queue', `${esc(projectInfo(pid).code)} · review → approve → share`);
    let tab = TABS.some((t) => t.key === ctx.query.tab) ? ctx.query.tab : 'pending';
    let page = +(ctx.query.page || 1);
    let ctrl = null;

    el.innerHTML = `
      <div class="page-head"><div><h2>Review Queue</h2><p>Only work inside your permissions and scope is shown. Valid ≠ shared — nothing reaches a client without approval and publishing.</p></div>
        <div class="row"><a class="btn" href="#/sharing">${icon('share', 's')} Client Sharing Center</a></div></div>
      <div class="tabs" role="tablist" id="rq-tabs">${TABS.map((t) => `<button class="tab ${t.key === tab ? 'on' : ''}" role="tab" aria-selected="${t.key === tab}" data-tab="${t.key}">${esc(t.label)}<span class="n" data-count="${t.key}">…</span></button>`).join('')}</div>
      <section class="card"><div id="rq-body" class="card-b flush">${skeleton(8, 28)}</div><div id="rq-pager"></div></section>`;

    const counts = async () => {
      await Promise.all(TABS.map(async (t) => {
        try {
          const r = await api.get('/api/alarms', { projectId: pid, ...t.params, size: 1 });
          const n = $(`[data-count="${t.key}"]`, el);
          if (n) n.textContent = fmt.n(r.totalElements);
        } catch { const n = $(`[data-count="${t.key}"]`, el); if (n) n.textContent = '—'; }
      }));
    };

    const load = async ({ silent = false } = {}) => {
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      const t = TABS.find((x) => x.key === tab);
      if (!silent) $('#rq-body', el).innerHTML = skeleton(8, 28);
      try {
        const r = await api.get('/api/alarms', { projectId: pid, ...t.params, page: String(page), size: '25' }, { signal: ctrl.signal });
        if (ctx.isStale()) return;
        table($('#rq-body', el), {
          columns: [...columns(['priority', 'alarm', 'camera', 'context']), AGE_COL, ...columns(['evidence', 'assigned', 'state', 'workflow', 'visibility'])],
          rows: r.items,
          onRow: (a) => { location.hash = investigateHref(a); },
          rowClass: (a) => (a.flags?.new ? 'is-new' : ''),
          emptyHtml: empty(t.empty[0], t.empty[1], 'check'),
        });
        pager($('#rq-pager', el), { page: r.page, totalPages: r.totalPages, totalElements: r.totalElements, size: r.size,
          onPage: (pg) => { page = pg; ctx.setQuery({ tab, page: String(pg) }); load(); } });
        const n = $(`[data-count="${tab}"]`, el);
        if (n) n.textContent = fmt.n(r.totalElements);
      } catch (e) {
        if (e.name === 'AbortError' || ctx.isStale()) return;
        $('#rq-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', () => load());
      }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => {
      tab = b.dataset.tab; page = 1;
      $$('[data-tab]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); });
      ctx.setQuery({ tab, page: '' });
      load();
    }));
    ctx.onCleanup(on('data', () => { load({ silent: true }); counts(); }));
    counts();
    await load();
  },
};
