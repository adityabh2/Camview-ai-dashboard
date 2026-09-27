// pages/audit.js — AUDIT TRAIL (append-only; nothing here can be edited or deleted).

import * as api from '../core/api.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, pager, errorBox, skeleton, empty, debounce, $, $$ } from '../core/ui.js';

const PREFIXES = [['', 'All actions'], ['auth.', 'Sign-in / sign-out'], ['review.', 'Operator reviews'], ['share.', 'Client sharing'],
  ['client.', 'Client portal & client admin'], ['client.assignment', 'Client project assignment'], ['user.', 'Users'], ['role.', 'Roles'],
  ['settings.', 'Settings'], ['report.', 'Reports'], ['evidence.', 'Evidence'], ['alarm.', 'Alarms (view, assign, export)'],
  ['note.', 'Internal notes'], ['nomenclature.', 'Nomenclature'], ['dictionary.', 'Dictionary'], ['schedule.', 'Schedules'],
  ['alert_rule.', 'Alert rules'], ['shift.', 'Shift handover']];

const short = (v) => {
  if (v == null || v === '') return '<span class="muted">—</span>';
  const s = String(v);
  return `<span class="mono" style="font-size:11px" title="${esc(s)}">${esc(s.length > 60 ? s.slice(0, 60) + '…' : s)}</span>`;
};

export default {
  async render(el, ctx) {
    setTitle('Audit Trail', 'Administration · who did what, when');
    let q = { page: '1', size: '50', ...ctx.query };
    el.innerHTML = `<div class="page-head"><div><h2>Audit trail</h2><p>${icon('lock', 's')} Append-only — records cannot be edited or deleted (enforced by the database). Only real events are recorded.</p></div></div>
      <div class="filters">
        <select class="select" id="a-action" aria-label="Action">${PREFIXES.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('')}</select>
        <input class="input" id="a-user" placeholder="User name" value="${esc(q.user || '')}">
        <select class="select" id="a-rtype" aria-label="Resource type"><option value="">Any resource</option>${['alarm', 'user', 'role', 'client', 'report', 'settings', 'project', 'schedule', 'alert_rule', 'nomenclature', 'handover'].map((t) => `<option>${t}</option>`).join('')}</select>
        <input class="input" id="a-rid" placeholder="Resource id (e.g. ALM-…)" value="${esc(q.resourceId || '')}" data-page-search>
        <label class="muted">From <input class="input" type="date" id="a-since" value="${esc((q.since || '').slice(0, 10))}"></label>
        <label class="muted">To <input class="input" type="date" id="a-until" value="${esc((q.until || '').slice(0, 10))}"></label>
        <button class="btn ghost" id="a-clear">Clear</button>
      </div>
      <section class="card"><div class="card-b flush" id="a-body">${skeleton(8, 24)}</div><div id="a-pager"></div></section>`;
    $('#a-action', el).value = q.action || '';
    $('#a-rtype', el).value = q.resourceType || '';

    const load = async () => {
      try {
        const r = await api.get('/api/audit', q);
        if (ctx.isStale()) return;
        table($('#a-body', el), {
          rowKey: (x) => x.id,
          columns: [
            { label: 'Date / time', render: (x) => `<span class="num">${fmt.dt(x.at)}</span>` },
            { label: 'User', render: (x) => x.user_name ? esc(x.user_name) : '<span class="muted">system</span>' },
            { label: 'Action', render: (x) => `<span class="b outline mono">${esc(x.action)}</span>${x.details?.seed ? ' <span class="demo-flag" style="font-size:9px;padding:1px 5px" title="Seeded demo history">DEMO SEED</span>' : ''}${x.details?.migrated ? ' <span class="b outline">migrated</span>' : ''}` },
            { label: 'Resource', render: (x) => x.resource_type === 'alarm' && x.resource_id
              ? `<a class="mono" href="#/investigations/${encodeURIComponent(x.resource_id)}${x.project_id ? '?projectId=' + encodeURIComponent(x.project_id) : ''}">${esc(x.resource_id)}</a>`
              : `<span class="dim">${esc(x.resource_type || '—')}</span> ${short(x.resource_id)}` },
            { label: 'Old value', render: (x) => short(x.old_value) },
            { label: 'New value', render: (x) => short(x.new_value) },
            { label: 'Project', render: (x) => short(x.project_id) },
            { label: 'Client', render: (x) => short(x.client_id) },
            { label: 'Note', render: (x) => x.note ? `<span class="dim">${esc(x.note)}</span>` : '<span class="muted">—</span>' },
          ],
          rows: r.items,
          emptyHtml: empty('No audit records', 'No events match these filters.', 'audit'),
        });
        const size = +q.size;
        pager($('#a-pager', el), { page: +q.page, totalPages: Math.max(1, Math.ceil(r.total / size)), totalElements: r.total,
          onPage: (p) => { q.page = String(p); ctx.setQuery(q); load(); } });
      } catch (e) {
        if (ctx.isStale()) return;
        $('#a-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const setQ = (patch) => { q = { ...q, ...patch, page: '1' }; Object.keys(q).forEach((k) => (q[k] === '' || q[k] == null) && delete q[k]); q.size = q.size || '50'; ctx.setQuery(q); load(); };
    $('#a-action', el).addEventListener('change', (e) => setQ({ action: e.target.value }));
    $('#a-rtype', el).addEventListener('change', (e) => setQ({ resourceType: e.target.value }));
    $('#a-user', el).addEventListener('input', debounce((e) => setQ({ user: e.target.value.trim() }), 350));
    $('#a-rid', el).addEventListener('input', debounce((e) => setQ({ resourceId: e.target.value.trim() }), 350));
    $('#a-since', el).addEventListener('change', (e) => setQ({ since: e.target.value ? new Date(e.target.value + 'T00:00:00').toISOString() : '' }));
    $('#a-until', el).addEventListener('change', (e) => setQ({ until: e.target.value ? new Date(e.target.value + 'T23:59:59').toISOString() : '' }));
    $('#a-clear', el).addEventListener('click', () => { q = { page: '1', size: '50' }; $$('input', el).forEach((i) => (i.value = '')); $$('select', el).forEach((s) => (s.value = '')); ctx.setQuery(q); load(); });
    await load();
  },
};
