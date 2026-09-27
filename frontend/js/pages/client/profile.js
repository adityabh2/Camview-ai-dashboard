// pages/client/profile.js — MY ACCESS: who you are, what you can see and do, in plain language.
// Counts come only from the client endpoints (the client-visible dataset).

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, card, kpi, errorBox, skeleton, $ } from '../../core/ui.js';

const PLAIN = {
  'client.portal': 'View alerts shared with your organisation',
  'client.acknowledge': 'Acknowledge shared alerts, comment and request clarification',
  'client.evidence': 'View evidence approved for sharing',
  'client.report.view': 'View and download reports shared with you',
  'client.analytics': 'View analytics of your shared alerts',
  'notification.view': 'Receive in-app notifications',
  'presentation.view': 'Use presentation mode',
};

export default {
  async render(el, ctx) {
    setTitle('My Access', 'Your account, projects and permissions');
    el.innerHTML = skeleton(4, 40);
    try {
      const d = await api.get('/api/client/profile');
      const perms = d.user.permissions || [];
      const [alerts, reports] = await Promise.all([
        api.get('/api/client/alerts').catch(() => ({ items: [] })),
        perms.includes('client.report.view') ? api.get('/api/client/reports').catch(() => null) : Promise.resolve(null),
      ]);
      if (ctx.isStale()) return;
      const u = d.user;
      const evidenceCount = (alerts.items || []).reduce((n, a) => n + (a.evidence || []).length, 0);
      el.innerHTML = `<div class="page-head"><div><h2>My Access</h2><p>${icon('lock', 's')} You only see alerts the operations team has reviewed and explicitly shared with your organisation, for your assigned projects.</p></div></div>
        <div class="kpis">
          ${kpi({ label: 'My projects', value: d.projects.length, icon: 'tree' })}
          ${kpi({ label: 'My alerts', value: (alerts.items || []).length, sub: 'shared with you', icon: 'share', href: '#/client/alerts' })}
          ${kpi({ label: 'My reports', value: reports ? reports.items.length : '—', sub: reports ? 'shared with you' : 'not included in your role', icon: 'report', href: reports ? '#/client/reports' : undefined })}
          ${kpi({ label: 'My evidence', value: perms.includes('client.evidence') ? evidenceCount : '—', sub: perms.includes('client.evidence') ? 'approved items' : 'not included in your role', icon: 'image', href: perms.includes('client.evidence') ? '#/client/evidence' : undefined })}
        </div>
        <div class="grid g-2">
        ${card({ title: `${icon('user')} ${esc(u.name)}`, body: `<dl class="kv"><dt>Email</dt><dd>${esc(u.email)}</dd><dt>Organisation</dt><dd>${esc(u.client?.name || '—')}</dd>
          <dt>Role</dt><dd>${esc(u.roleName)}</dd><dt>My projects</dt><dd>${d.projects.map((p) => `<span class="b outline mono">${esc(p.code)}</span> ${esc(p.name && p.name !== p.code ? p.name : '')}`).join('<br>') || '—'}</dd></dl>
          <p class="muted" style="margin-top:12px">To change your password, open the user menu (your name, top right) › Change password. Notification and display settings are in <a href="#/preferences">Preferences</a>.</p>` })}
        ${card({ title: `${icon('key')} My permissions`, body: `<ul class="check-list">${perms.map((p) => `<li class="ok"><span class="m">✓</span><span>${esc(PLAIN[p] || p)}</span></li>`).join('')}</ul>
          <p class="muted" style="margin-top:12px">Anything not listed here is not available to your account. Ask your organisation's administrator if you need more access.</p>` })}</div>`;
    } catch (e) {
      if (ctx.isStale()) return;
      el.innerHTML = errorBox(e);
      $('[data-retry]', el)?.addEventListener('click', () => this.render(el, ctx));
    }
  },
};
