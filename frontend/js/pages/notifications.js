// pages/notifications.js — ALERT INBOX (internal and client users).
// Notifications come only from real events; delivery is in-app only.
// Non-critical items can be snoozed; critical items can never be snoozed.

import * as api from '../core/api.js';
import { isClient } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { status } from '../core/live.js';
import { esc, icon, fmt, card, empty, errorBox, skeleton, toast, delegate, sevBadge, $, $$ } from '../core/ui.js';

// [key, label, filter]
const TABS = [
  ['all', 'All', () => true],
  ['critical', 'Critical', (n) => n.severity === 'critical'],
  ['unread', 'Unread', (n) => !n.read],
  ['assigned', 'Assigned', (n) => n.category === 'investigation'],
  ['pending', 'Pending', (n) => n.category === 'operational'],
  ['approval', 'Approval', (n) => n.category === 'approval'],
  ['client', 'Client', (n) => n.category === 'client'],
  ['system', 'System', (n) => n.category === 'system'],
];
const CH_LABEL = { in_app: 'In-app', email: 'Email', sms: 'SMS', push: 'Push' };
const SNOOZE = [[15, '15 minutes'], [30, '30 minutes'], [60, '1 hour']];

function snoozeSelect(n, attr) {
  if (n && n.severity === 'critical') {
    return `<span class="b outline" title="Critical notifications can't be snoozed" style="height:26px">${icon('lock')}can't snooze</span>`;
  }
  return `<select class="select" ${attr} aria-label="Snooze" style="height:26px;font-size:12px;padding:0 6px;width:118px">
    <option value="">Snooze…</option>
    ${SNOOZE.map(([m, l]) => `<option value="${m}">${l}</option>`).join('')}
    <option value="" disabled title="Requires a configured shift schedule">End of shift — needs shift schedule</option>
  </select>`;
}

export default {
  async render(el, ctx) {
    setTitle('Alert Inbox', isClient() ? 'Client portal' : 'Your notifications');
    let tab = TABS.some((t) => t[0] === ctx.query.tab) ? ctx.query.tab : 'all';
    let archived = ctx.query.archived === '1';
    let snoozed = ctx.query.snoozed === '1';
    const selected = new Set();
    let data;
    el.innerHTML = skeleton(6, 40);

    const load = async () => {
      try {
        const params = {};
        if (archived) params.archived = '1';
        if (snoozed) params.snoozed = '1';
        data = await api.get('/api/notifications', params);
        if (ctx.isStale()) return;
        [...selected].forEach((id) => { if (!data.items.some((n) => String(n.id) === id)) selected.delete(id); });
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const paint = () => {
      status.unread = data.unread;
      const fn = TABS.find((t) => t[0] === tab)[2];
      const items = data.items.filter(fn);
      const count = (f) => data.items.filter(f).length;
      el.innerHTML = `<div class="page-head"><div><h2>Alert Inbox</h2><p>Created only from real events — shares, approvals, assignments, critical alarms, rules, refresh failures, schedules. Critical items can't be snoozed.</p></div>
          <div class="row">
            <label class="check"><input type="checkbox" id="nt-snz" ${snoozed ? 'checked' : ''}> Show snoozed</label>
            <label class="check"><input type="checkbox" id="nt-arch" ${archived ? 'checked' : ''}> Show archived</label>
            <button class="btn" data-mark="read" data-ids="all">${icon('check', 's')} Mark all read</button>
            <a class="btn ghost" href="#/preferences">${icon('settings', 's')} Preferences</a></div></div>
        <div class="tabs" role="tablist">${TABS.map(([k, l, f]) => `<button class="tab ${k === tab ? 'on' : ''}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${l}<span class="n">${count(f)}</span></button>`).join('')}</div>
        <div class="grid g-side">
          <section class="card">
            <div class="card-h"><label class="check"><input type="checkbox" id="nt-all" ${items.length && items.every((n) => selected.has(String(n.id))) ? 'checked' : ''} aria-label="Select all"> <span class="muted" style="font-size:12px">${selected.size ? `${selected.size} selected` : 'Select'}</span></label>
              <div class="actions ${selected.size ? '' : 'hidden'}" id="nt-bulk">
                <button class="btn sm" data-bulk="read">${icon('check', 's')} Mark read</button>
                <button class="btn sm" data-bulk="archive">${icon('x', 's')} Archive</button>
                ${snoozeSelect(null, 'data-bulksnooze')}</div></div>
            <div class="card-b flush">${items.length ? `<div class="list">${items.map((n) => `<div class="li" style="${n.read ? '' : 'background:var(--accent-soft)'}">
              <input type="checkbox" data-sel="${n.id}" ${selected.has(String(n.id)) ? 'checked' : ''} aria-label="Select notification" style="margin-top:3px">
              <div style="display:flex;flex-direction:column;gap:4px;min-width:92px">${sevBadge(n.severity || 'info')}<span class="b outline">${esc(n.category)}</span></div>
              <div class="grow"><div class="t1">${n.read ? '' : '<span class="b new">NEW</span>'}${esc(n.title)}${n.archived ? ' <span class="b outline">archived</span>' : ''}${n.snoozedUntil ? ` <span class="b outline">${icon('clock')}snoozed until ${fmt.time(n.snoozedUntil)}</span>` : ''}</div>
                ${n.body ? `<div class="dim" style="font-size:12px;margin-top:2px">${esc(n.body)}</div>` : ''}<div class="t2">${fmt.dt(n.createdAt)} · ${fmt.rel(n.createdAt)}</div></div>
              <div class="row tight" style="align-items:flex-start;flex-wrap:nowrap;flex-shrink:0">${n.link ? `<a class="btn sm" href="${esc(n.link)}" data-openid="${n.id}">Open</a>` : ''}
                ${n.read ? '' : `<button class="btn sm ghost" data-mark="read" data-ids="${n.id}">Mark read</button>`}
                ${n.archived ? '' : snoozeSelect(n, `data-snooze="${n.id}"`)}
                ${n.archived ? '' : `<button class="btn sm ghost" data-mark="archive" data-ids="${n.id}" aria-label="Archive">${icon('x', 's')}</button>`}</div></div>`).join('')}</div>`
              : empty('No notifications', tab === 'all' ? (archived ? 'Nothing archived.' : 'You are all caught up.') : 'Nothing in this tab.', 'bell')}</div>
          </section>
          <div class="stack">
          ${card({ title: `${icon('bell')} Delivery channels`, body: `<ul class="check-list">${Object.entries(data.channels || {}).map(([k, v]) => `<li class="${v ? 'ok' : 'no'}"><span class="m">${v ? '✓' : '–'}</span><span><b>${esc(CH_LABEL[k] || k)}</b> — ${v ? 'active' : 'not integrated yet'}</span></li>`).join('')}</ul>
            <div class="muted" style="font-size:11.5px;margin-top:8px">Only in-app delivery is available. Other channels are shown as unavailable rather than pretending to send.</div>` })}
          ${card({ title: `${icon('clock')} Snooze rules`, body: `<ul class="why"><li>Snooze hides a non-critical notification for 15 min, 30 min or 1 hour; it returns automatically.</li><li>Critical notifications can't be snoozed or switched off.</li><li>"Until end of shift" needs a configured shift schedule.</li></ul>` })}
          </div>
        </div>`;
      $('#nt-arch', el).addEventListener('change', (e) => { archived = e.target.checked; ctx.setQuery({ archived: archived ? '1' : '' }); load(); });
      $('#nt-snz', el).addEventListener('change', (e) => { snoozed = e.target.checked; ctx.setQuery({ snoozed: snoozed ? '1' : '' }); load(); });
      $('#nt-all', el).addEventListener('change', (e) => { items.forEach((n) => (e.target.checked ? selected.add(String(n.id)) : selected.delete(String(n.id)))); paint(); });
    };

    const doSnooze = async (ids, minutes) => {
      try {
        const r = await api.post('/api/notifications/mark', { action: 'snooze', ids, minutes: Number(minutes) });
        toast(r.criticalNotSnoozed ? `Snoozed. ${r.criticalNotSnoozed} critical notification(s) were not snoozed.` : `Snoozed for ${minutes} min`, r.criticalNotSnoozed ? 'warning' : 'success');
        selected.clear();
        load();
      } catch (err) { toast(err.message, 'error'); }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; ctx.setQuery({ tab: tab === 'all' ? '' : tab }); paint(); }));
    ctx.onCleanup(delegate(el, 'change', '[data-sel]', (e, b) => { b.checked ? selected.add(b.dataset.sel) : selected.delete(b.dataset.sel); paint(); }));
    ctx.onCleanup(delegate(el, 'change', '[data-snooze]', (e, s) => { if (s.value) doSnooze([s.dataset.snooze], s.value); }));
    ctx.onCleanup(delegate(el, 'change', '[data-bulksnooze]', (e, s) => { if (s.value) doSnooze([...selected], s.value); }));
    ctx.onCleanup(delegate(el, 'click', '[data-bulk]', async (e, b) => {
      try {
        await api.post('/api/notifications/mark', { action: b.dataset.bulk, ids: [...selected] });
        toast(b.dataset.bulk === 'archive' ? 'Archived' : 'Marked read', 'success');
        selected.clear();
        load();
      } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-mark]', async (e, b) => {
      try {
        const ids = b.dataset.ids === 'all' ? 'all' : [b.dataset.ids];
        await api.post('/api/notifications/mark', { action: b.dataset.mark, ids });
        if (b.dataset.mark === 'archive') toast('Archived');
        load();
      } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-openid]', (e, a) => { api.post('/api/notifications/mark', { action: 'read', ids: [a.dataset.openid] }).catch(() => {}); }));
    await load();
  },
};
