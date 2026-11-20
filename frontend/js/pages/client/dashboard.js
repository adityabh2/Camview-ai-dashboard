// pages/client/dashboard.js — CLIENT PORTAL home. Only data delivered to this client.
// Read by exam-board officials: the organisation and its project codes first, then the one thing that needs
// them (alerts to acknowledge), then the latest alerts and the exams.

import * as api from '../../core/api.js';
import { session, can } from '../../core/state.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, bars, PRIORITY_COLORS, $ } from '../../core/ui.js';
import { alertCard } from './common.js';

const codeHead = (code) => String(code || '').split('/').map((s) => s.trim()).filter(Boolean)
  .map((s) => `<span>${esc(s)}</span>`).join('<i aria-hidden="true">/</i>');

export default {
  async render(el, ctx) {
    setTitle('Overview', esc(session.user.client?.name || ''));
    el.innerHTML = skeleton(5, 60);
    const load = async () => {
      try {
        const d = await api.get('/api/client/overview');
        if (ctx.isStale()) return;
        const m = d.metrics;
        const waiting = m.awaitingAcknowledgement || 0;
        const coded = d.projects.filter((p) => p.code && p.code !== p.externalId);
        el.innerHTML = `
          <header class="cp-mast">
            <div class="cp-org">
              <h2>${esc(d.client?.name || 'Your organisation')}</h2>
              ${coded.length ? `<div class="cp-codes">${coded.map((p) => `<div class="dm-code cp-code" title="${esc(p.code)}">${codeHead(p.code)}</div>`).join('')}</div>`
                : `<div class="muted">${d.projects.length ? `${d.projects.length} project${d.projects.length === 1 ? '' : 's'}` : 'No project assigned yet'}</div>`}
            </div>
            ${can('presentation.view') ? `<a class="btn" href="#/presentation">${icon('present', 's')} Present</a>` : ''}
          </header>

          <section class="cp-attn ${waiting ? 'is-open' : 'is-clear'}" aria-live="polite">
            <span class="cp-attn-ic">${icon(waiting ? 'clock' : 'check')}</span>
            <div class="grow">${waiting
              ? `<b>${fmt.n(waiting)} new alert${waiting === 1 ? '' : 's'} you have not opened yet.</b><div class="muted">Open each one to see the evidence. Opening an alert marks it as read and lets the operations team know.</div>`
              : `<b>You are up to date.</b><div class="muted">You have opened every alert delivered to you. New ones appear here as they arrive.</div>`}</div>
            ${waiting ? '<a class="btn primary" href="#/client/alerts?ack=pending">Open new alerts</a>' : '<a class="btn" href="#/client/alerts">All alerts</a>'}
          </section>

          <div class="kpis cp-kpis">
            ${kpi({ label: 'Alerts received', value: m.shared, href: '#/client/alerts', icon: 'share' })}
            ${kpi({ label: 'Not opened yet', title: 'Alerts you have not opened yet', value: waiting, accent: waiting ? 'warning' : '', href: '#/client/alerts?ack=pending', icon: 'clock' })}
            ${kpi({ label: 'Read', value: m.acknowledged, accent: 'good', href: '#/client/alerts?ack=done', icon: 'check' })}
            ${kpi({ label: 'Alert types', value: (d.typeDistribution || []).length, href: '#/client/alerts', icon: 'layers' })}
          </div>

          <div class="grid g-main">
            ${card({ title: 'Latest alerts', sub: `${fmt.n(m.withEvidence)} with photo or video evidence`, actions: '<a class="btn sm" href="#/client/alerts">All alerts</a>',
              body: d.recent.length ? `<div class="pcards cl-cards">${d.recent.map(alertCard).join('')}</div>` : empty('No alerts yet', 'Alerts from your exam centres appear here as soon as they are delivered to you. There is nothing to set up on your side.', 'share') })}
            <div class="stack">
              ${card({ title: 'Your exams', flush: true,
                body: d.exams?.length ? `<div class="list">${d.exams.map((e) => `<a class="li cp-exam" href="#/client/alerts?exam=${encodeURIComponent(e.id)}">
                    <div class="grow" style="min-width:0"><div class="t1">${esc(e.name)}</div><div class="t2 mono" style="overflow-wrap:anywhere">${esc(e.code)}</div></div>
                    <div class="cp-exam-n"><b class="num">${fmt.n(e.alerts)}</b><span class="muted">${e.latest ? fmt.rel(e.latest) : 'alerts'}</span></div></a>`).join('')}</div>`
                  : empty('No exams yet', 'Your exams appear here once the operations team maps them to your organisation.', 'calendar') })}
              ${card({ title: 'By alert type', body: (d.typeDistribution || []).length ? bars(d.typeDistribution) : '<span class="muted">No alerts yet</span>' })}
              ${card({ title: 'Reports', actions: can('client.report.view') ? '<a class="btn sm" href="#/client/reports">All reports</a>' : '', flush: true,
                body: d.reports.length ? `<div class="list">${d.reports.map((r) => `<a class="li" href="#/client/reports/${r.id}">${icon('report')}<div class="grow"><div class="t1">${esc(r.title)}</div><div class="t2">${fmt.dt(r.generated_at)}</div></div></a>`).join('')}</div>` : empty('No reports shared yet', 'Reports the operations team shares with you appear here.', 'report') })}
            </div>
          </div>
          <p class="cp-foot muted">${icon('lock', 's')} You see only alerts delivered to ${esc(d.client?.name || 'your organisation')}, for your own exams. Internal notes and operator details are never shown.</p>`;
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    await load();
  },
};
