// pages/tickets.js — TICKETS: created automatically from VALID decisions (one per alert).
// Controlled delivery happens here with one click; delivered tickets can be withdrawn.
// Every ticket shows its alert as Camview reports it NOW (status + photo/video with fresh links);
// opening a ticket plays the live video and photos in place.

import * as api from '../core/api.js';
import { on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, pager, errorBox, skeleton, empty, toast, confirmDialog, dialog, priorityBadge, delegate, $, $$ } from '../core/ui.js';
import { deliveryBadge, reviewHref, labelFromContext } from '../components/queue.js';
import { mountEvidence } from '../components/player.js';

// live photo (or a video tile) for one ticket row; click opens the ticket's evidence
const preview = (t) => {
  const l = t.live || {};
  const tag = l.source === 'live' ? '<span class="tk-src live">LIVE</span>'
    : '<span class="tk-src" title="Alert no longer in Camview’s list — saved copy">SAVED</span>';
  if (!l.evidenceCount) return `<div class="tk-thumb none">${icon('image', 's')}<span>No media</span></div>`;
  return `<button class="tk-thumb" data-open="${t.id}" aria-label="Open photo and video for ${esc(t.ref)}">
    ${l.imageUrl ? `<img src="${esc(l.imageUrl)}" alt="" loading="lazy">` : ''}
    ${l.hasVideo ? `<span class="tk-play">${icon('play', 's')}</span>` : ''}${tag}</button>`;
};

async function openTicket(id, onClose) {
  let player = null;
  const d = dialog({ title: 'Ticket', size: 'xl', body: skeleton(6, 30), onClose: () => { player?.destroy(); onClose?.(); } });
  try {
    const r = await api.get(`/api/tickets/${id}`);
    if (!d.el.isConnected) return;
    const { ticket: t, alarm: a, freshness: f } = r;
    const live = a.source !== 'snapshot';
    d.el.querySelector('.d-h h2').innerHTML = `<span class="mono">${esc(t.ref)}</span> · ${esc(a.alarmTypeName || t.alarmId)}`;
    d.el.querySelector('.d-b').innerHTML = `
      <div class="row" style="margin-bottom:10px;gap:8px;flex-wrap:wrap">
        <span class="row tight"><span class="dot ${live ? esc(f?.state || 'live') : 'delayed'}"></span>${live ? `Live from Camview · updated ${fmt.rel(f?.lastSuccessAt)}` : 'Saved copy — this alert is no longer in Camview’s list'}</span>
        ${priorityBadge(a.priority)}<span class="b">${esc(a.lastActionLabel || '—')} in Camview</span>
        ${deliveryBadge(t.status === 'cancelled' ? 'withdrawn' : t.deliveryStatus)}
        <span class="grow"></span><a class="btn sm" href="${reviewHref(a)}">${icon('external', 's')} Open alert workspace</a>
      </div>
      <div class="tk-ev">
        <section class="card"><div class="card-h"><h3>${icon('video')} Video / clip</h3></div><div class="card-b" id="tk-video"></div></section>
        <section class="card"><div class="card-h"><h3>${icon('image')} Photos</h3></div><div class="card-b" id="tk-images"></div></section>
      </div>
      <dl class="tk-facts">
        <dt>Alert</dt><dd class="mono">${esc(a.alarmId)}</dd>
        <dt>Location</dt><dd>${esc(a.locationLabel || '—')}</dd>
        <dt>Centre</dt><dd>${esc(a.centreName || a.centreCode || '—')}</dd>
        <dt>Camera</dt><dd>${esc(a.cameraName || a.cameraId || '—')}</dd>
        <dt>First / last seen</dt><dd>${fmt.dt(a.firstInstance)} → ${fmt.dt(a.lastInstance)} · ${esc(a.totalTimesReported || 1)}×</dd>
        <dt>Exam / client</dt><dd>${esc(t.examName || '—')} / ${esc(t.clientName || '—')}</dd>
        <dt>Validated</dt><dd>${esc(t.validatedBy || '—')} · ${fmt.dt(t.validatedAt)}</dd>
      </dl>`;
    if (r.evidence.length) {
      player = mountEvidence($('#tk-video', d.el), $('#tk-images', d.el), r.evidence, { alarmId: a.alarmId, canDownload: r.can.download, title: `${t.ref} · ${a.alarmId}` });
    } else {
      $('#tk-video', d.el).innerHTML = empty('No video', 'Camview has no clip for this alert, or you lack evidence access.', 'video');
      $('#tk-images', d.el).innerHTML = empty('No photos', '', 'image');
    }
  } catch (e) {
    if (d.el.isConnected) d.el.querySelector('.d-b').innerHTML = errorBox(e, { retry: false });
  }
}

const TABS = [['', 'All open'], ['ready', 'Ready to send'], ['needs_client', 'Choose client'], ['delivered', 'Delivered'],
  ['withdrawn', 'Withdrawn'], ['failed', 'Delivery failed'], ['not_deliverable', 'Not deliverable']];
const SIZE = 50;

export default {
  async render(el, ctx) {
    setTitle('Tickets', 'Created automatically from VALID alerts');
    const q = { delivery: '', client: '', exam: '', search: '', status: 'open', page: 1, ...ctx.query };
    q.page = +q.page || 1;
    let data = null;
    const busy = new Set();

    el.innerHTML = `
      <div class="page-head"><div><h2>Tickets</h2><p>One ticket per VALID alert — never duplicated. INVALID and EXCEPTION alerts never become client tickets.</p></div>
        <div class="stack" style="align-items:flex-end;gap:4px"><div id="t-live" class="muted row tight"></div><div id="t-mode" class="muted"></div></div></div>
      <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab ${k === q.delivery ? 'on' : ''}" role="tab" data-tab="${k}">${l}<span class="n" data-n="${k}">·</span></button>`).join('')}</div>
      <div class="filters">
        <input class="input" style="min-width:220px" id="t-search" data-page-search placeholder="Ticket ref or alert ID" value="${esc(q.search)}">
        <select class="select" id="t-client" aria-label="Client"><option value="">All clients</option></select>
        <select class="select" id="t-exam" aria-label="Exam"><option value="">All exams</option></select>
        <label class="check"><input type="checkbox" id="t-cancel" ${q.status === '' ? 'checked' : ''}> Include cancelled</label>
        <span class="grow"></span><button class="btn sm primary hidden" id="t-sendall">${icon('share', 's')} Send all ready</button>
      </div>
      <section class="card"><div class="card-b flush" id="t-body">${skeleton(8, 30)}</div><div class="card-b" id="t-pager"></div></section>`;

    const load = async () => {
      try {
        const p = { page: q.page, size: SIZE };
        ['delivery', 'client', 'exam', 'search', 'status'].forEach((k) => { if (q[k]) p[k] = q[k]; });
        data = await api.get('/api/tickets', p);
        if (ctx.isStale()) return;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) $('#t-body', el).innerHTML = errorBox(e);
      }
    };

    const paint = () => {
      const fill = (sel, list, cur) => { const s = $(sel, el); s.innerHTML = s.options[0].outerHTML + list.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join(''); s.value = cur || ''; };
      fill('#t-client', data.clients, q.client);
      fill('#t-exam', data.exams, q.exam);
      const total = Object.values(data.counts).reduce((s, n) => s + n, 0);
      TABS.forEach(([k]) => { const n = $(`[data-n="${k}"]`, el); if (n) n.textContent = k ? (data.counts[k] || 0) : total; });
      const fr = Object.values(data.freshness || {});
      const worst = fr.find((f) => f.state === 'disconnected') || fr.find((f) => f.state === 'delayed') || fr[0];
      $('#t-live', el).innerHTML = worst ? `<span class="dot ${esc(worst.state)}"></span>${worst.state === 'live' ? 'Live' : esc(worst.state)} · Camview read ${fmt.rel(worst.lastSuccessAt)}` : '';
      $('#t-mode', el).innerHTML = data.deliveryTrigger === 'arrival' ? `${icon('zap', 's')} Every alert is delivered on arrival` : data.deliveryMode === 'automatic' ? `${icon('zap', 's')} Automatic delivery is on` : `${icon('share', 's')} Controlled delivery — send with one click`;
      $('#t-sendall', el).classList.toggle('hidden', !(data.canSend && q.delivery === 'ready' && data.items.some((t) => t.deliveryStatus === 'ready' && t.status === 'open')));
      table($('#t-body', el), {
        rowKey: (t) => t.id,
        columns: [
          { label: 'Evidence', render: preview },
          { label: 'Ticket', render: (t) => `<div class="cell-2"><b class="mono">${esc(t.ref)}</b><span class="l2">${t.status === 'cancelled' ? '<span class="sla-attention">cancelled</span>' : fmt.rel(t.createdAt)}</span></div>` },
          { label: 'Alert', render: (t) => `<div class="cell-2"><a href="${reviewHref({ alarmId: t.alarmId, projectId: t.projectId })}" title="${esc(t.alarmId)}">${esc(t.snapshot.alarmTypeName || t.alarmId)}</a><span class="l2 mono">${esc(labelFromContext(t.snapshot.context, t.projectId, t.snapshot.cameraId))}</span>${t.live?.source === 'live' ? `<span class="l2">Camview: <b>${esc(t.live.lastActionLabel || '—')}</b> · last seen ${fmt.rel(t.live.lastInstance)} · ${esc(t.live.totalTimesReported || 1)}×</span>` : ''}</div>` },
          { label: 'Priority', render: (t) => priorityBadge(t.live?.priority || t.snapshot.priority) },
          { label: 'Exam', render: (t) => esc(t.examName || '—') },
          { label: 'Client', render: (t) => esc(t.clientName || (t.snapshot.clients || []).map((c) => c.name).join(', ') || '—') },
          { label: 'Validated', render: (t) => `<div class="cell-2"><span>${esc(t.validatedBy || '—')}</span><span class="l2">${fmt.dt(t.validatedAt)}</span></div>` },
          { label: 'Delivery', render: (t) => `${deliveryBadge(t.status === 'cancelled' ? 'withdrawn' : t.deliveryStatus)}${t.deliveredAt ? `<div class="l2 muted" style="font-size:11px">${fmt.dt(t.deliveredAt)}</div>` : ''}` },
          { label: '', render: (t) => t.status !== 'open' ? '' : `<div class="row tight" style="justify-content:flex-end">
              ${data.canSend && ['ready', 'withdrawn', 'failed'].includes(t.deliveryStatus) ? `<button class="btn sm primary" data-send="${t.id}">${icon('share', 's')} Send</button>` : ''}
              ${data.canSend && t.deliveryStatus === 'needs_client' ? `<a class="btn sm" href="${reviewHref({ alarmId: t.alarmId, projectId: t.projectId })}">Choose client</a>` : ''}
              ${data.canWithdraw && t.deliveryStatus === 'delivered' ? `<button class="btn sm" data-withdraw="${t.id}">Withdraw</button>` : ''}</div>` },
        ],
        rows: data.items,
        emptyHtml: empty('No tickets here', 'Tickets are created automatically when an alert is marked VALID.', 'report'),
      });
      pager($('#t-pager', el), { page: data.page, totalPages: data.totalPages, totalElements: data.totalElements, size: SIZE, onPage: (p) => { q.page = p; ctx.setQuery({ page: p }); load(); } });
    };

    const set = (patch) => { Object.assign(q, patch, { page: 1 }); ctx.setQuery({ ...patch, page: 1 }); load(); };
    const send = async (id, quiet = false) => {
      if (busy.has(id)) return;
      busy.add(id);
      try {
        const r = await api.post(`/api/tickets/${id}/send`, {});
        if (!quiet) toast(`${r.ticket.ref} sent to ${r.ticket.clientName || 'client'}`, 'success');
        return true;
      } catch (e) { toast(e.message, 'error'); return false; } finally { busy.delete(id); }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { $$('[data-tab]', el).forEach((x) => x.classList.toggle('on', x === b)); set({ delivery: b.dataset.tab }); }));
    ctx.onCleanup(delegate(el, 'click', '[data-open]', (e, b) => openTicket(+b.dataset.open, load)));
    ctx.onCleanup(delegate(el, 'click', '[data-send]', async (e, b) => { b.disabled = true; await send(+b.dataset.send); load(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-withdraw]', async (e, b) => {
      const t = data.items.find((x) => x.id === +b.dataset.withdraw);
      if (!await confirmDialog({ title: 'Withdraw from client?', message: `${esc(t.clientName || 'The client')} will no longer see ${esc(t.ref)}. The ticket stays open and can be sent again.`, confirmLabel: 'Withdraw', danger: true })) return;
      try { await api.post(`/api/tickets/${t.id}/withdraw`, {}); toast('Withdrawn', 'success'); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    $('#t-sendall', el).addEventListener('click', async () => {
      const ready = data.items.filter((t) => t.deliveryStatus === 'ready' && t.status === 'open');
      if (!await confirmDialog({ title: `Send ${ready.length} tickets?`, message: 'Each ticket is delivered to its mapped client. Already-delivered tickets are not sent twice.', confirmLabel: 'Send all' })) return;
      let ok = 0;
      for (const t of ready) ok += (await send(t.id, true)) ? 1 : 0;
      toast(`${ok} of ${ready.length} sent`, ok === ready.length ? 'success' : 'warning');
      load();
    });
    $('#t-client', el).addEventListener('change', (e) => set({ client: e.target.value }));
    $('#t-exam', el).addEventListener('change', (e) => set({ exam: e.target.value }));
    $('#t-cancel', el).addEventListener('change', (e) => set({ status: e.target.checked ? '' : 'open' }));
    let t;
    $('#t-search', el).addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => set({ search: e.target.value.trim() }), 300); });
    ctx.onCleanup(on('data', () => { if (!busy.size && !document.querySelector('.overlay')) load(); }));
    await load();
  },
};
