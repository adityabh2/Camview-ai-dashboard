// pages/incident.js — ONE INCIDENT: its alerts as Camview reports them now, owner, status and timeline.
// Lifecycle: Open → Investigating → Resolved (with how) → Closed; Reopen brings it back while none of its
// alerts joined another open incident. Every change and comment lands in the timeline and the audit trail.
// Patched in place on live updates (morph); a half-written comment is never touched.

import * as api from '../core/api.js';
import { on, can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, empty, errorBox, skeleton, toast, dialog, confirmDialog, morph, delegate, $ } from '../core/ui.js';
import { alertRow } from '../components/queue.js';
import { sevBadgeInc, statusBadgeInc } from './incidents.js';

const EV_ICON = { created: 'plus', status: 'refresh', owner: 'user', comment: 'edit', alarm_added: 'plus', alarm_removed: 'trash', severity: 'bang', title: 'edit' };
// what each status button does: [target status, label, icon, kind]
const MOVES = {
  open: [['investigating', 'Start investigating', 'search', 'primary'], ['resolved', 'Resolve', 'check', 'good'], ['closed', 'Close', 'lock', '']],
  investigating: [['resolved', 'Resolve', 'check', 'good'], ['closed', 'Close', 'lock', '']],
  resolved: [['closed', 'Close', 'lock', ''], ['open', 'Reopen', 'refresh', '']],
  closed: [['open', 'Reopen', 'refresh', '']],
};

export default {
  async render(el, ctx) {
    const id = ctx.params.id;
    let data = null, busy = false;
    setTitle('Incident', `<a href="#/incidents">Incidents</a> › ${esc(id)}`);
    el.innerHTML = `<div class="card"><div class="card-b">${skeleton(8, 34)}</div></div>`;
    el.addEventListener('error', (e) => { if (e.target?.tagName === 'IMG') e.target.closest('.q-thumb')?.classList.add('broken'); }, true);

    const load = async () => {
      try {
        const r = await api.get(`/api/incidents/${encodeURIComponent(id)}`);
        if (ctx.isStale()) return;
        data = r;
        paint();
      } catch (e) {
        if (ctx.isStale() || data) return;
        el.innerHTML = e.status === 404
          ? `<div class="card">${empty('Incident not available', 'It may not exist, or it belongs to a project outside your access.', 'lock')}<div class="row" style="justify-content:center;padding-bottom:20px"><a class="btn" href="#/incidents">${icon('left', 's')} Back to incidents</a></div></div>`
          : errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const actions = (inc, manage) => {
      if (!manage) return '<span class="muted inc-ro">' + icon('lock', 's') + ' Read only — changing incidents needs the investigate permission.</span>';
      return `<div class="row inc-actions" data-key="actions">
        ${(MOVES[inc.status] || []).map(([to, label, ic, kind]) => `<button class="btn sm ${kind}" data-move="${to}">${icon(ic, 's')} ${label}</button>`).join('')}
        <span class="grow"></span>
        <label class="row tight muted" style="font-size:12px">Owner <select class="select sm" id="inc-owner" aria-label="Owner"><option value="">Unassigned</option>${data.owners.map((o) => `<option value="${esc(o.id)}" ${String(o.id) === String(inc.owner?.id ?? '') ? 'selected' : ''}>${esc(o.name)}</option>`).join('')}</select></label>
        <label class="row tight muted" style="font-size:12px">Severity <select class="select sm" id="inc-sev" aria-label="Severity">${data.severities.map((s) => `<option value="${s}" ${s === inc.severity ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('')}</select></label>
        <button class="btn sm ghost" id="inc-rename">${icon('edit', 's')} Rename</button></div>`;
    };

    const alertItem = (a, manage, n) => `<div class="inc-al" data-key="${esc(a.alarmId)}">${alertRow(a)}
      <div class="inc-al-f muted">${a.source === 'snapshot' ? '<span class="b outline" title="No longer in Camview’s current list — saved copy from when it was linked">SAVED COPY</span>' : ''}
        <span>Added ${fmt.rel(a.addedAt)}${a.addedBy ? ` by ${esc(a.addedBy)}` : ''}</span>
        ${manage && n > 1 ? `<button class="btn sm ghost" data-remove="${esc(a.alarmId)}" aria-label="Remove ${esc(a.alarmId)} from the incident">${icon('x', 's')} Remove</button>` : ''}</div></div>`;

    const event = (ev) => `<li class="inc-ev k-${esc(ev.kind)}" data-key="ev:${esc(ev.id)}">
      <div class="row tight"><span class="inc-ev-ic">${icon(EV_ICON[ev.kind] || 'info', 's')}</span><b>${esc(ev.userName || 'System')}</b><span class="when" title="${esc(fmt.dt(ev.at))}">${fmt.dt(ev.at)}</span></div>
      <div class="${ev.kind === 'comment' ? 'inc-comment' : 'dim'}">${esc(ev.body)}</div></li>`;

    const paint = () => {
      const inc = data.incident;
      const manage = !!data.can?.manage && can('alarm.investigate');
      const comment = !!data.can?.comment;
      setTitle(inc.ref, `<a href="#/incidents">Incidents</a> › <span class="mono">${esc(inc.ref)}</span>`);
      morph(el, `
        <div class="page-head inc-head" data-key="head"><div class="grow" style="min-width:0">
          <div class="row tight"><span class="mono muted">${esc(inc.ref)}</span>${sevBadgeInc(inc.severity)}${statusBadgeInc(inc.status)}</div>
          <h2 class="inc-h2">${esc(inc.title)}</h2>
          <p>${esc(inc.project?.code || inc.projectId)}${inc.centre ? ` · centre <span class="mono">${esc(inc.centre)}</span>` : ''} · created ${fmt.dt(inc.createdAt)} by ${esc(inc.createdBy || '—')} · owner <b>${esc(inc.owner?.name || 'unassigned')}</b></p></div>
          <a class="btn sm" href="#/incidents">${icon('left', 's')} All incidents</a></div>
        <div class="card inc-bar" data-key="bar"><div class="card-b">${actions(inc, manage)}</div></div>
        ${inc.reason ? `<div class="banner info" data-key="why">${icon('info')}<div><b>Why these alerts:</b> ${esc(inc.reason)}</div></div>` : ''}
        ${inc.resolution ? `<div class="banner inc-res" data-key="res">${icon('check')}<div><b>Resolution${inc.resolvedAt ? ` · ${fmt.dt(inc.resolvedAt)}` : ''}:</b> ${esc(inc.resolution)}</div></div>` : ''}
        <div class="grid g-side inc-grid" data-key="grid">
          <section class="card" data-key="alerts"><div class="card-h"><h3>${icon('alert', 's')} Linked alerts</h3><span class="b outline num">${esc(data.alarms.length)}</span>
            <div class="actions">${manage && ['open', 'investigating'].includes(inc.status) ? `<form class="row tight" id="inc-add" data-morph-skip><input class="input" id="inc-add-id" placeholder="Add alert ID" aria-label="Alert ID to add" style="width:150px"><button class="btn sm" type="submit">${icon('plus', 's')} Add</button></form>` : ''}</div></div>
            ${data.hiddenAlarms ? `<div class="card-b muted" data-key="hidden">${icon('lock', 's')} ${esc(data.hiddenAlarms)} linked alert${data.hiddenAlarms === 1 ? ' is' : 's are'} outside your access and not shown.</div>` : ''}
            <div class="qrows inc-alerts">${data.alarms.length ? data.alarms.map((a) => alertItem(a, manage, data.alarms.length)).join('') : `<div data-key="none">${empty('No alerts to show', 'The alerts of this incident are outside your access.', 'lock')}</div>`}</div></section>
          <section class="card" data-key="timeline"><div class="card-h"><h3>${icon('history', 's')} Timeline</h3></div><div class="card-b">
            ${comment ? `<form class="inc-cbox" id="inc-cform" data-morph-skip data-key="cbox"><textarea class="input" id="inc-ctext" rows="3" maxlength="4000" placeholder="Add a comment for the team…" aria-label="Comment"></textarea><div class="row" style="justify-content:flex-end;margin-top:6px"><button class="btn sm primary" type="submit">${icon('edit', 's')} Comment</button></div></form>` : ''}
            <ul class="timeline inc-tl">${data.events.slice().reverse().map(event).join('')}</ul></div></section>
        </div>`);
    };

    const change = async (patch, okMsg) => {
      if (busy) return false;
      busy = true;
      try {
        const r = await api.put(`/api/incidents/${encodeURIComponent(id)}`, patch);
        data.incident = { ...data.incident, ...r.incident };
        data.events = r.events;
        paint();
        if (okMsg) toast(okMsg, 'success');
        return true;
      } catch (e) { toast(e.message, 'error'); await load(); return false; } finally { busy = false; }
    };

    const resolveDialog = () => new Promise((resolve) => {
      let done = false;
      dialog({
        title: `${icon('check')} Resolve ${esc(data.incident.ref)}`,
        body: `<div class="field"><label for="inc-rtext">How was it resolved?</label><textarea class="input" id="inc-rtext" rows="4" maxlength="2000" autofocus>${esc(data.incident.resolution || '')}</textarea><span class="hint">Kept with the incident and shown in the timeline.</span></div>`,
        actions: [
          { label: 'Cancel', onClick: ({ close }) => { done = true; resolve(null); close(); } },
          { label: 'Resolve', kind: 'good', onClick: ({ el: box, close }) => {
            const v = $('#inc-rtext', box).value.trim();
            if (!v) { toast('Say how the incident was resolved.', 'warning'); return; }
            done = true; resolve(v); close();
          } },
        ],
        onClose: () => { if (!done) resolve(null); },
      });
    });

    ctx.onCleanup(delegate(el, 'click', '[data-move]', async (e, b) => {
      const to = b.dataset.move;
      if (to === 'resolved') {
        const text = await resolveDialog();
        if (text) change({ status: 'resolved', resolution: text }, `${data.incident.ref} resolved`);
        return;
      }
      if (to === 'closed' && !await confirmDialog({ title: `Close ${esc(data.incident.ref)}?`, message: 'Its alerts are released and can join another incident. You can reopen it later.', confirmLabel: 'Close incident' })) return;
      change({ status: to }, { investigating: 'Investigation started', closed: 'Incident closed', open: 'Incident reopened' }[to]);
    }));
    ctx.onCleanup(delegate(el, 'change', '#inc-owner', (e, s) => change({ ownerId: s.value || null }, 'Owner updated')));
    ctx.onCleanup(delegate(el, 'change', '#inc-sev', (e, s) => change({ severity: s.value }, 'Severity updated')));
    ctx.onCleanup(delegate(el, 'click', '#inc-rename', () => {
      dialog({
        title: `${icon('edit')} Rename incident`,
        body: `<div class="field"><label for="inc-ntitle">Title</label><input class="input" id="inc-ntitle" maxlength="200" value="${esc(data.incident.title)}" autofocus></div>`,
        actions: [{ label: 'Cancel', onClick: ({ close }) => close() },
          { label: 'Save', kind: 'primary', onClick: async ({ el: box, close }) => { const v = $('#inc-ntitle', box).value.trim(); if (!v) return; close(); change({ title: v }, 'Renamed'); } }],
      });
    }));
    ctx.onCleanup(delegate(el, 'submit', '#inc-cform', async (e, f) => {
      e.preventDefault();
      const ta = $('#inc-ctext', f);
      const text = ta.value.trim();
      if (!text || busy) return;
      busy = true;
      try {
        const r = await api.post(`/api/incidents/${encodeURIComponent(id)}/comments`, { body: text });
        ta.value = '';
        data.events = r.events;
        paint();
      } catch (err) { toast(err.message, 'error'); } finally { busy = false; }
    }));
    ctx.onCleanup(delegate(el, 'submit', '#inc-add', async (e, f) => {
      e.preventDefault();
      const inp = $('#inc-add-id', f);
      const aid = inp.value.trim();
      if (!aid || busy) return;
      busy = true;
      try {
        await api.post(`/api/incidents/${encodeURIComponent(id)}/alarms`, { add: [{ alarmId: aid, projectId: data.incident.projectId }] });
        inp.value = '';
        toast(`${aid} added`, 'success');
      } catch (err) { toast(err.message, 'error'); } finally { busy = false; }
      load();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-remove]', async (e, b) => {
      e.preventDefault();
      const aid = b.dataset.remove;
      if (!await confirmDialog({ title: 'Remove alert from incident?', message: `${esc(aid)} will no longer be part of ${esc(data.incident.ref)}. The alert itself is not changed.`, confirmLabel: 'Remove' })) return;
      try { await api.post(`/api/incidents/${encodeURIComponent(id)}/alarms`, { remove: [aid] }); toast('Removed', 'success'); } catch (err) { toast(err.message, 'error'); }
      load();
    }));
    ctx.onCleanup(on('data', () => { if (!busy && !document.querySelector('.overlay')) load(); }));
    await load();
  },
};
