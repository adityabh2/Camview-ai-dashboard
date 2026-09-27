// pages/client/alerts.js — SHARED ALERTS (client dataset only).

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, fmt, table, errorBox, skeleton, empty, priorityBadge, debounce, $ } from '../../core/ui.js';
import { locationLine, ackBadge, alertHref, clientThumb } from './common.js';

export default {
  async render(el, ctx) {
    setTitle('Shared Alerts', 'Alerts reviewed and shared with you');
    let q = { priority: '', ack: '', exam: '', search: '', ...ctx.query };
    el.innerHTML = `<div class="page-head"><div><h2>Shared alerts</h2><p>Every alert delivered to your organisation, with its evidence.</p></div></div>
      <div class="filters">
        <input class="input" style="min-width:240px" id="ca-search" placeholder="Search alert ID, type or location" value="${esc(q.search)}" data-page-search>
        <select class="select" id="ca-exam" aria-label="Exam"><option value="">All exams</option></select>
        <select class="select" id="ca-prio" aria-label="Priority"><option value="">All priorities</option><option value="critical">Critical</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select>
        <select class="select" id="ca-ack" aria-label="Acknowledgement"><option value="">Any acknowledgement</option><option value="pending">Awaiting acknowledgement</option><option value="done">Acknowledged</option></select>
        <span class="grow"></span><span class="muted" id="ca-n"></span></div>
      <section class="card"><div class="card-b flush" id="ca-body">${skeleton(6, 26)}</div></section>`;
    $('#ca-prio', el).value = q.priority; $('#ca-ack', el).value = q.ack;

    const load = async () => {
      try {
        const r = await api.get('/api/client/alerts', q);
        if (ctx.isStale()) return;
        $('#ca-n', el).textContent = `${r.total} alert(s)`;
        const ex = $('#ca-exam', el);
        ex.innerHTML = '<option value="">All exams</option>' + (r.exams || []).map((e) => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('');
        ex.value = q.exam || '';
        table($('#ca-body', el), {
          columns: [
            { label: '', render: (a) => `<span class="tcell">${clientThumb(a)}</span>` },
            { label: 'Priority', render: (a) => priorityBadge(a.priority) },
            { label: 'Alert', render: (a) => `<div class="cell-2"><a class="mono" href="${alertHref(a)}">${esc(a.alarmId)}</a><span class="l2">${esc(a.alarmTypeName)}</span></div>` },
            { label: 'Exam', render: (a) => (a.exam ? esc(a.exam.name) : '<span class="muted">—</span>') },
            { label: 'Ticket', render: (a) => (a.ticketRef ? `<span class="mono">${esc(a.ticketRef)}</span>` : '<span class="muted">—</span>') },
            { label: 'Location', render: locationLine },
            { label: 'Raised', render: (a) => `<span class="num dim">${fmt.dt(a.firstInstance)}</span>` },
            { label: 'Shared', render: (a) => `<span class="num dim">${fmt.dt(a.sharedAt)}</span>` },
            { label: 'Evidence', render: (a) => a.evidence?.length ? String(a.evidence.length) : '<span class="muted">—</span>' },
            { label: 'Acknowledgement', render: ackBadge },
          ],
          rows: r.items,
          onRow: (a) => { location.hash = alertHref(a); },
          emptyHtml: empty('No shared alerts', (q.priority || q.ack || q.exam || q.search) ? 'Nothing matches these filters.' : 'Nothing has been shared with you yet.', 'share'),
        });
      } catch (e) {
        if (ctx.isStale()) return;
        $('#ca-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    const setQ = (p) => { q = { ...q, ...p }; ctx.setQuery(q); load(); };
    $('#ca-prio', el).addEventListener('change', (e) => setQ({ priority: e.target.value }));
    $('#ca-ack', el).addEventListener('change', (e) => setQ({ ack: e.target.value }));
    $('#ca-exam', el).addEventListener('change', (e) => setQ({ exam: e.target.value }));
    $('#ca-search', el).addEventListener('input', debounce((e) => setQ({ search: e.target.value.trim() }), 300));
    await load();
  },
};
