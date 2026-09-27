// pages/work.js — MY WORK: "What needs my attention?"  Role-aware queues from /api/work.

import * as api from '../core/api.js';
import { currentProject, projectInfo, on, can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, delegate, $ } from '../core/ui.js';
import { miniRow } from '../components/alarms.js';

const SECTION_META = {
  pending: { icon: 'clock', accent: 'warning' }, mine: { icon: 'investigate', accent: 'info' },
  critical: { icon: 'alert', accent: 'critical' }, approvals: { icon: 'check', accent: 'info' },
  sharing: { icon: 'share', accent: 'good' }, evidence: { icon: 'image' }, escalations: { icon: 'zap', accent: 'critical' },
};

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('My Work', `${esc(projectInfo(pid).code)} · what needs my attention`);
    if (!pid) { el.innerHTML = empty('No project available', 'Ask an administrator for access to a project.', 'tree'); return; }
    el.innerHTML = skeleton(6, 50);
    let data;

    const load = async () => {
      try {
        data = await api.get('/api/work', { projectId: pid });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) { el.innerHTML = errorBox(e); $('[data-retry]', el)?.addEventListener('click', load); }
      }
    };

    const paint = () => {
      const s = data.sections;
      const counters = s.map((sec) => kpi({ label: sec.title, value: sec.count, icon: SECTION_META[sec.key]?.icon || 'work',
        accent: sec.count ? SECTION_META[sec.key]?.accent : '', href: `#sec-${sec.key}`, title: 'Derived from your in-scope alarms' })).join('')
        + (can('report.view') ? kpi({ label: 'My reports', value: data.reports.length, icon: 'report', href: '#sec-reports' }) : '')
        + kpi({ label: 'Unread notifications', value: data.unreadNotifications, icon: 'bell', href: '#/notifications', accent: data.unreadNotifications ? 'warning' : '' });

      const sections = s.map((sec) => card({
        id: `sec-${sec.key}`,
        title: `${icon(SECTION_META[sec.key]?.icon || 'work')} ${esc(sec.title)} <span class="b outline" style="margin-left:6px">${sec.count}</span>`,
        actions: sec.link ? `<a class="btn sm" href="${esc(sec.link)}">Open all</a>` : '',
        flush: true,
        body: sec.items.length
          ? `<div class="list" style="max-height:440px;overflow:auto">${sec.items.slice(0, 10).map((a) => miniRow(a, `<span class="dim" style="font-size:11px" title="Age">${fmt.dur(a.ageMinutes)}</span>`)).join('')}</div>${sec.count > Math.min(10, sec.items.length) ? `<div class="muted" style="padding:8px 14px;font-size:11.5px;border-top:1px solid var(--border)">Showing ${Math.min(10, sec.items.length)} of ${sec.count} — <a href="${esc(sec.link || '#/live')}">open all</a></div>` : ''}`
          : empty(sec.empty || 'Nothing here.', '', 'check'),
      })).join('');

      const reports = can('report.view') ? card({ id: 'sec-reports', title: `${icon('report')} My recent reports`, actions: '<a class="btn sm" href="#/reports">Report Center</a>', flush: true,
        body: data.reports.length ? `<div class="list">${data.reports.map((r) => `<a class="li" href="#/reports/${r.id}"><span class="b outline">${esc(r.audience)}</span><div class="grow"><div class="t1">${esc(r.title)}</div><div class="t2">${fmt.dt(r.generated_at)}</div></div></a>`).join('')}</div>`
          : empty('No reports generated yet', '', 'report') }) : '';

      el.innerHTML = `<div class="page-head"><div><h2>My Work</h2><p>Queues for <b>${esc(data.role)}</b> — only work inside your permissions and scope is shown.</p></div>
        <button class="btn" data-refresh>${icon('refresh', 's')} Refresh</button></div>
        <div class="kpis">${counters}</div>
        <div class="grid g-2">${sections}${reports}</div>`;
    };

    ctx.onCleanup(delegate(el, 'click', 'a[href^="#sec-"]', (e, a) => { e.preventDefault(); document.getElementById(a.getAttribute('href').slice(1))?.scrollIntoView({ behavior: 'smooth', block: 'start' }); }));
    ctx.onCleanup(delegate(el, 'click', '[data-refresh]', load));
    ctx.onCleanup(on('data', load));
    await load();
  },
};
