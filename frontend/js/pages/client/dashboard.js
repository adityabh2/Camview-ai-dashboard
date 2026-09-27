// pages/client/dashboard.js — CLIENT COMMAND CENTER. Only data shared with this client.

import * as api from '../../core/api.js';
import { session, can } from '../../core/state.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, bars, PRIORITY_COLORS, $ } from '../../core/ui.js';
import { alertCard } from './common.js';

export default {
  async render(el, ctx) {
    setTitle('Client Command Center', esc(session.user.client?.name || ''));
    el.innerHTML = skeleton(5, 60);
    const load = async () => {
      try {
        const d = await api.get('/api/client/overview');
        if (ctx.isStale()) return;
        const m = d.metrics;
        el.innerHTML = `
          <div class="hero"><div class="grow"><div class="eyebrow">CAMVIEW · CLIENT PORTAL</div><h2>${esc(d.client?.name || 'Client')}</h2>
            <div class="meta"><span>${icon('tree', 's')} ${d.projects.map((p) => `<b>${esc(p.code)}</b> ${esc(p.name && p.name !== p.code ? p.name : '')}`).join(' · ') || 'No projects assigned'}</span>
            <span>${icon('lock', 's')} You see only alerts that have been reviewed and shared with you.</span></div></div>
            ${can('presentation.view') ? `<a class="btn" href="#/presentation">${icon('present', 's')} Present</a>` : ''}</div>
          <div class="kpis">
            ${kpi({ label: 'Shared alerts', value: m.shared, href: '#/client/alerts', icon: 'share' })}
            ${kpi({ label: 'Critical shared', value: m.critical, accent: 'critical', href: '#/client/alerts?priority=critical', icon: 'alert' })}
            ${kpi({ label: 'To acknowledge', title: 'Shared alerts awaiting your acknowledgement', value: m.awaitingAcknowledgement, accent: 'warning', href: '#/client/alerts?ack=pending', icon: 'clock' })}
            ${kpi({ label: 'Acknowledged', value: m.acknowledged, accent: 'good', href: '#/client/alerts?ack=done', icon: 'check' })}
            ${kpi({ label: 'With evidence', value: m.withEvidence, href: '#/client/evidence', icon: 'image' })}
            ${kpi({ label: 'Unread notifications', value: d.unreadNotifications, href: '#/notifications', icon: 'bell' })}
          </div>
          <div class="section-title">${icon('calendar', 's')} My exams</div>
          ${d.exams?.length ? `<div class="exam-tiles" style="margin-bottom:16px">${d.exams.map((e) => `<a class="etile" href="#/client/alerts?exam=${encodeURIComponent(e.id)}">
              <div class="t1">${esc(e.name)}</div><div class="t2 mono">${esc(e.code)}</div>
              <div class="row" style="margin-top:8px"><span><b class="num">${e.alerts}</b> alert${e.alerts === 1 ? '' : 's'}</span>${e.critical ? `<span class="sla-attention">· ${e.critical} critical</span>` : ''}<span class="muted">${e.latest ? '· latest ' + fmt.rel(e.latest) : ''}</span></div></a>`).join('')}</div>`
            : `<div class="card" style="margin-bottom:16px">${empty('No exams yet', 'Your exams appear here once the operations team maps them to your organisation.', 'calendar')}</div>`}
          <div class="grid g-main">
            ${card({ title: `${icon('share')} Recent alerts`, actions: '<a class="btn sm" href="#/client/alerts">All alerts</a>',
              body: d.recent.length ? `<div class="pcards cl-cards">${d.recent.map(alertCard).join('')}</div>` : empty('No alerts yet', 'Alerts appear here the moment the operations team confirms them as VALID for your exams. Nothing to do on your side.', 'share') })}
            <div class="stack">
              ${card({ title: 'Priority of shared alerts', body: bars(d.priorityDistribution.map((x) => ({ ...x, color: PRIORITY_COLORS[x.key], sw: PRIORITY_COLORS[x.key] }))) })}
              ${card({ title: `${icon('report')} Reports`, actions: can('client.report.view') ? '<a class="btn sm" href="#/client/reports">All reports</a>' : '', flush: true,
                body: d.reports.length ? `<div class="list">${d.reports.map((r) => `<a class="li" href="#/client/reports/${r.id}">${icon('report')}<div class="grow"><div class="t1">${esc(r.title)}</div><div class="t2">${fmt.dt(r.generated_at)}</div></div></a>`).join('')}</div>` : empty('No reports shared yet') })}
            </div>
          </div>`;
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    await load();
  },
};
