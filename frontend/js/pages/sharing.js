// pages/sharing.js — CLIENT SHARING CENTER. Valid ≠ Shared: every step is explicit and re-checked on the server.

import * as api from '../core/api.js';
import { currentProject, projectInfo, can, on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, pager, empty, errorBox, skeleton, debounce, delegate, visBadge, $, $$ } from '../core/ui.js';
import { columns, investigateHref } from '../components/alarms.js';
import { requestShare, approveShare, publishFlow, withdrawFlow, bulkRequest } from '../components/sharing.js';

const TABS = [['candidates', 'Ready for client review', 'Validated alarms not yet in any client pipeline'],
  ['ready_for_review', 'Awaiting approval', 'Requested — supervisor approval needed'],
  ['approved', 'Approved', 'Approved — ready to publish with a client-safe preview'],
  ['shared', 'Shared', 'Visible to the client'],
  ['withdrawn', 'Withdrawn', 'Client access removed']];
const FILTERS = ['tc', 'centre', 'priority', 'search', 'from', 'to', 'clientId'];

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Client Sharing', `${esc(projectInfo(pid).code)} · controlled client publication`);
    if (!pid) { el.innerHTML = empty('No project available', '', 'tree'); return; }
    let q = { tab: 'candidates', page: '1', ...ctx.query };
    if (!TABS.some((t) => t[0] === q.tab)) q.tab = 'candidates';
    let data = null;
    const selected = new Set();

    el.innerHTML = `<div class="page-head"><div><h2>Client Sharing Center</h2>
        <p><b>Valid ≠ Shared.</b> A validated alarm stays <b>INTERNAL</b> until someone requests review, a supervisor approves it, and an authorised user publishes it after a client-safe preview.</p></div>
        <div class="row" id="sh-policy"></div></div>
      <div class="tabs" role="tablist" id="sh-tabs"></div>
      <div class="filters">
        <input class="input" style="min-width:230px" placeholder="Search alarm, type, camera…" id="sh-search" data-page-search value="${esc(q.search || '')}">
        <select class="select" id="sh-priority" aria-label="Priority"><option value="">Any priority</option>${['critical', 'high', 'medium', 'low'].map((p) => `<option ${q.priority === p ? 'selected' : ''} value="${p}">${p[0].toUpperCase() + p.slice(1)}</option>`).join('')}</select>
        <input class="input" id="sh-tc" placeholder="TC code" value="${esc(q.tc || '')}" style="width:110px">
        <input class="input" id="sh-centre" placeholder="Centre code" value="${esc(q.centre || '')}" style="width:120px">
        <select class="select" id="sh-client" aria-label="Client"><option value="">All clients</option></select>
        <input class="input" type="date" id="sh-from" value="${esc((q.from || '').slice(0, 10))}" aria-label="From date">
        <input class="input" type="date" id="sh-to" value="${esc((q.to || '').slice(0, 10))}" aria-label="To date">
        <button class="btn ghost" id="sh-clear">Clear</button>
        <span class="grow"></span>
        <div id="sh-bulk" class="row hidden"><span class="b outline" id="sh-seln"></span><button class="btn primary" id="sh-bulkgo">${icon('share', 's')} Review for client</button><button class="btn ghost" id="sh-selclear">Clear</button></div>
      </div>
      <p class="muted" id="sh-tabdesc" style="margin:-4px 0 10px"></p>
      <section class="card"><div class="card-b flush" id="sh-body">${skeleton(6, 28)}</div><div id="sh-pager"></div></section>`;

    const load = async () => {
      try {
        const params = { projectId: pid, tab: q.tab, page: q.page, size: 50 };
        FILTERS.forEach((k) => { if (q[k]) params[k] = k === 'to' && q[k].length === 10 ? q[k] + 'T23:59:59' : q[k]; });
        const r = await api.get('/api/sharing', params);
        if (ctx.isStale()) return;
        data = r;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#sh-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const actionsFor = (row) => {
      const a = row.alarm, p = row.publication, out = [];
      if (q.tab === 'candidates' && (can('alarm.validate') || can('alarm.publish') || can('alarm.approve'))) out.push(`<button class="btn sm" data-act="request" data-id="${esc(a.alarmId)}">Request review</button>`);
      if (q.tab === 'candidates' && can('alarm.publish') && !data.policy.requireApproval) out.push(`<button class="btn sm primary" data-act="publish" data-id="${esc(a.alarmId)}">Review & share</button>`);
      if (q.tab === 'ready_for_review' && can('alarm.approve')) out.push(`<button class="btn sm primary" data-act="approve" data-id="${esc(a.alarmId)}" data-client="${esc(p.clientId)}">Approve</button>`);
      if (q.tab === 'approved' && can('alarm.publish')) out.push(`<button class="btn sm primary" data-act="publish" data-id="${esc(a.alarmId)}" data-client="${esc(p.clientId)}">${icon('share', 's')} Review & share</button>`);
      if (['ready_for_review', 'approved', 'shared'].includes(q.tab) && can('alarm.withdraw')) out.push(`<button class="btn sm danger" data-act="withdraw" data-id="${esc(a.alarmId)}" data-client="${esc(p.clientId)}" data-cname="${esc(p.clientName)}">Withdraw</button>`);
      return `<div class="row tight">${out.join('')}<a class="btn sm ghost" href="${investigateHref(a)}">Open</a></div>`;
    };

    const pubInfo = (p) => {
      if (!p) return '<span class="b vis-internal">' + icon('lock') + 'Internal</span>';
      const bits = [];
      if (p.requestedBy) bits.push(`requested by ${esc(p.requestedBy)} ${fmt.rel(p.requestedAt)}`);
      if (p.approvedBy) bits.push(`approved by ${esc(p.approvedBy)} ${fmt.rel(p.approvedAt)}`);
      if (p.sharedBy) bits.push(`shared by ${esc(p.sharedBy)} ${fmt.dt(p.sharedAt)}`);
      if (p.acknowledgedAt) bits.push(`<b>acknowledged</b> by ${esc(p.acknowledgedBy)} ${fmt.rel(p.acknowledgedAt)}`);
      else if (p.viewedAt) bits.push(`viewed ${fmt.rel(p.viewedAt)}`);
      if (p.withdrawnBy) bits.push(`withdrawn by ${esc(p.withdrawnBy)}: “${esc(p.withdrawReason || '')}”`);
      return `<div class="cell-2">${visBadge(p.status, p.statusLabel, !!p.acknowledgedAt)} <b style="font-size:12px">${esc(p.clientName)}</b><span class="l2" style="white-space:normal;max-width:360px">${bits.join(' · ')}</span></div>`;
    };

    const paint = () => {
      const pol = data.policy;
      $('#sh-policy', el).innerHTML = `<span class="b outline" title="Approve before publish">${icon(pol.requireApproval ? 'check' : 'x')}Two-step approval ${pol.requireApproval ? 'on' : 'off'}</span>
        <span class="b outline" title="Approver must differ from validator">${icon(pol.fourEyes ? 'check' : 'x')}Four-eyes ${pol.fourEyes ? 'on' : 'off'}</span>
        <span class="b outline" title="What counts as valid for sharing">Valid source: ${esc(pol.validSource)}</span>
        ${pol.requireEvidence ? '<span class="b outline">Evidence required</span>' : ''}${pol.requireContext ? '<span class="b outline">Mapped context required</span>' : ''}`;
      $('#sh-tabs', el).innerHTML = TABS.map(([k, l]) => `<button class="tab ${k === q.tab ? 'on' : ''}" role="tab" aria-selected="${k === q.tab}" data-tab="${k}">${l}<span class="n">${data.counts[k] ?? 0}</span></button>`).join('');
      $('#sh-tabdesc', el).textContent = TABS.find((t) => t[0] === q.tab)[2];
      const cs = $('#sh-client', el);
      if (cs.options.length <= 1) cs.innerHTML = '<option value="">All clients</option>' + data.clients.map((c) => `<option value="${esc(c.id)}" ${q.clientId === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('');
      const cols = [...columns(['priority', 'alarm', 'camera', 'context', 'state', 'workflow']),
        { label: q.tab === 'candidates' ? 'Visibility' : 'Client publication', render: (r) => pubInfo(r.publication) },
        { label: 'Evidence', render: (r) => (r.alarm.evidence?.count ? `${r.alarm.evidence.images} img${r.alarm.evidence.video ? ' · video' : ''}` : '<span class="muted">—</span>') },
        { label: '', render: actionsFor }];
      const wrapped = cols.map((c, i) => (i < 6 ? { ...c, render: (r) => c.render(r.alarm) } : c));
      const canBulk = q.tab === 'candidates' && (can('alarm.validate') || can('alarm.publish') || can('alarm.approve'));
      table($('#sh-body', el), {
        columns: wrapped, rows: data.items, rowKey: (r) => r.alarm.alarmId,
        selectable: canBulk, selected, onSelect: (id, on_) => { on_ ? selected.add(id) : selected.delete(id); paintBulk(); },
        emptyHtml: empty({ candidates: 'No alerts ready for client review', ready_for_review: 'Nothing awaiting approval', approved: 'No approved alerts waiting to be published', shared: 'No shared alerts', withdrawn: 'Nothing withdrawn' }[q.tab], 'Try clearing the filters.', 'share'),
      });
      pager($('#sh-pager', el), { page: data.page, totalPages: data.totalPages, totalElements: data.totalElements, onPage: (pg) => { q.page = String(pg); ctx.setQuery(q); load(); } });
      paintBulk();
    };

    const paintBulk = () => {
      $('#sh-bulk', el).classList.toggle('hidden', !selected.size || q.tab !== 'candidates');
      $('#sh-seln', el).textContent = `${selected.size} selected`;
    };

    const setQ = (patch) => { q = { ...q, ...patch, page: '1' }; Object.keys(q).forEach((k) => (q[k] === '' || q[k] == null) && delete q[k]); ctx.setQuery(q); load(); };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { selected.clear(); setQ({ tab: b.dataset.tab }); }));
    $('#sh-search', el).addEventListener('input', debounce((e) => setQ({ search: e.target.value.trim() }), 300));
    ['priority', 'client', 'from', 'to'].forEach((k) => $(`#sh-${k}`, el).addEventListener('change', (e) => setQ({ [k === 'client' ? 'clientId' : k]: e.target.value })));
    ['tc', 'centre'].forEach((k) => $(`#sh-${k}`, el).addEventListener('input', debounce((e) => setQ({ [k]: e.target.value.trim() }), 400)));
    $('#sh-clear', el).addEventListener('click', () => {
      FILTERS.forEach((k) => delete q[k]);
      $$('.filters input', el).forEach((i) => { i.value = ''; });
      $$('.filters select', el).forEach((s) => { s.value = ''; });
      setQ({});
    });
    $('#sh-selclear', el).addEventListener('click', () => { selected.clear(); paint(); });
    $('#sh-bulkgo', el).addEventListener('click', async () => {
      if (await bulkRequest([...selected], pid, data.clients)) { selected.clear(); load(); }
    });
    ctx.onCleanup(delegate(el, 'click', '[data-act]', async (e, b) => {
      const id = b.dataset.id, clientId = b.dataset.client;
      let changed = false;
      if (b.dataset.act === 'request') changed = await requestShare(id, pid, data.clients);
      if (b.dataset.act === 'approve') changed = await approveShare(id, pid, clientId);
      if (b.dataset.act === 'publish') {
        let cid = clientId;
        if (!cid) cid = data.clients.length === 1 ? data.clients[0].id : null;
        if (!cid) { changed = await requestShare(id, pid, data.clients); }
        else changed = await publishFlow(id, pid, cid);
      }
      if (b.dataset.act === 'withdraw') changed = await withdrawFlow(id, pid, clientId, b.dataset.cname);
      if (changed) load();
    }));
    ctx.onCleanup(on('data', () => { if (!document.querySelector('.overlay')) load(); }));
    await load();
  },
};
