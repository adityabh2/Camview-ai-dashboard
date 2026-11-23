// pages/client/alerts.js — SHARED ALERTS (client dataset only).

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, fmt, table, errorBox, skeleton, empty, priorityBadge, typeBadge, debounce, $ } from '../../core/ui.js';
import { locationLine, ackBadge, alertHref, clientThumb } from './common.js';

export default {
  async render(el, ctx) {
    setTitle('Alerts', 'Alerts delivered to your organisation');
    let q = { ack: '', exam: '', search: '', type: '', ...ctx.query };
    el.innerHTML = `<div class="page-head"><div><h2>Alerts</h2><p>Every alert delivered to your organisation, with its evidence. Opening an alert marks it as read.</p></div></div>
      <div class="q-types" id="ca-types" role="group" aria-label="Alert type"></div>
      <div class="filters">
        <input class="input" style="min-width:240px" id="ca-search" placeholder="Search alert ID, type or location" value="${esc(q.search)}" data-page-search>
        <select class="select" id="ca-exam" aria-label="Exam"><option value="">All exams</option></select>
        <select class="select" id="ca-ack" aria-label="Acknowledgement"><option value="">Read and unread</option><option value="pending">Not opened yet</option><option value="done">Read</option></select>
        <span class="grow"></span><span class="muted" id="ca-n"></span></div>
      <section class="card"><div class="card-b flush" id="ca-body">${skeleton(6, 26)}</div></section>`;
    $('#ca-ack', el).value = q.ack;

    const load = async () => {
      try {
        const r = await api.get('/api/client/alerts', q);
        if (ctx.isStale()) return;
        $('#ca-n', el).textContent = `${r.total} alert(s)`;
        $('#ca-types', el).innerHTML = (r.types || []).length ? `<button type="button" class="tchip ${q.type ? '' : 'on'}" data-ctype="">All types</button>`
          + r.types.map((t) => `<button type="button" class="tchip ${q.type === t.name ? 'on' : ''}" data-ctype="${esc(t.name)}" title="${esc(t.name)}"><b>${esc(t.short)}</b><span>${esc(t.name)}</span><span class="n">${t.count}</span></button>`).join('') : '';
        const ex = $('#ca-exam', el);
        ex.innerHTML = '<option value="">All exams</option>' + (r.exams || []).map((e) => `<option value="${esc(e.id)}">${esc(e.name)}</option>`).join('');
        ex.value = q.exam || '';
        table($('#ca-body', el), {
          columns: [
            { label: '', render: (a) => `<span class="tcell">${clientThumb(a)}</span>` },
            { label: 'Type', render: (a) => typeBadge(a) },
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
          emptyHtml: empty('No alerts', (q.type || q.ack || q.exam || q.search) ? 'Nothing matches these filters.' : 'No alerts yet. Alerts from your exam centres appear here as soon as they are delivered to you.', 'share'),
        });
      } catch (e) {
        if (ctx.isStale()) return;
        $('#ca-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    const setQ = (p) => { q = { ...q, ...p }; ctx.setQuery(q); load(); };
    $('#ca-ack', el).addEventListener('change', (e) => setQ({ ack: e.target.value }));
    el.addEventListener('click', (e) => { const b = e.target.closest('[data-ctype]'); if (b) setQ({ type: b.dataset.ctype }); });
    $('#ca-exam', el).addEventListener('change', (e) => setQ({ exam: e.target.value }));
    $('#ca-search', el).addEventListener('input', debounce((e) => setQ({ search: e.target.value.trim() }), 300));
    await load();
  },
};
