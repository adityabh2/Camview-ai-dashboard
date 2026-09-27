// pages/shift.js — SHIFT CONTROL + SHIFT HANDOVER. Handover notes are internal only.

import * as api from '../core/api.js';
import { currentProject, projectInfo, can, on } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, toast, prov, delegate, $ } from '../core/ui.js';
import { miniRow } from '../components/alarms.js';

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Shift Control', `${esc(projectInfo(pid).code)} · current shift & handover`);
    if (!pid) { el.innerHTML = empty('No project available', '', 'tree'); return; }
    el.innerHTML = skeleton(6, 50);
    let data;

    const load = async () => {
      try {
        data = await api.get('/api/shift', { projectId: pid, tzOffset: -new Date().getTimezoneOffset() });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) { el.innerHTML = errorBox(e); $('[data-retry]', el)?.addEventListener('click', load); }
      }
    };

    const paint = () => {
      const s = data.snapshot;
      const shifts = [...new Set([data.currentShift, ...data.handovers.flatMap((h) => [h.from_shift, h.to_shift])].filter(Boolean))];
      const form = can('shift.handover') ? card({ title: `${icon('shift')} New shift handover`, sub: 'Counts are captured automatically when you submit',
        body: `<div class="grid g-2"><div class="field"><label for="ho-from">From shift</label><input class="input" id="ho-from" list="ho-shifts" value="${esc(data.currentShift || '')}"></div>
          <div class="field"><label for="ho-to">To shift</label><input class="input" id="ho-to" list="ho-shifts"></div></div>
          <datalist id="ho-shifts">${shifts.map((x) => `<option value="${esc(x)}">`).join('')}</datalist>
          <div class="field"><label for="ho-notes">Handover notes <span class="b vis-internal">${icon('lock')}internal</span></label><textarea class="input" id="ho-notes" rows="4" placeholder="Open items, camera issues, follow-ups…"></textarea>
            <span class="hint">Operational notes stay internal — they are never shown to clients.</span></div>
          <button class="btn primary" id="ho-save">${icon('check', 's')} Record handover</button>` }) : '';

      const history = card({ title: `${icon('history')} Handover history`, flush: true,
        body: data.handovers.length ? `<div class="list">${data.handovers.map((h) => `<div class="li" style="flex-direction:column;align-items:stretch">
          <div class="row"><b>${esc(h.from_shift || '—')} → ${esc(h.to_shift || '—')}</b><span class="grow"></span><span class="muted num" style="font-size:11px">${fmt.dt(h.created_at)}</span></div>
          <div class="t2">by ${esc(h.created_by_name || '—')}</div>
          <div class="row" style="margin:6px 0">${Object.entries(h.snapshot || {}).map(([k, v]) => `<span class="b outline">${esc(k.replace(/([A-Z])/g, ' $1').toLowerCase())}: ${esc(v)}</span>`).join('')}</div>
          ${h.notes ? `<div style="white-space:pre-wrap" class="dim">${esc(h.notes)}</div>` : ''}</div>`).join('')}</div>` : empty('No handovers recorded yet', '', 'shift') });

      el.innerHTML = `<div class="page-head"><div><h2>Shift Control</h2>
          <p>Current shift: <b>${esc(data.currentShift || 'Unknown')}</b> ${prov('derived')} <span class="muted">— ${esc(data.currentShiftSource)}</span></p></div>
          <button class="btn" data-refresh>${icon('refresh', 's')} Refresh</button></div>
        <div class="kpis">
          ${kpi({ label: 'Active alarms (this shift today)', value: s.activeAlarms, icon: 'layers', prov: 'derived' })}
          ${kpi({ label: 'Pending reviews', value: s.pendingReviews, icon: 'clock', accent: s.pendingReviews ? 'warning' : '', href: '#/live?quick=pending' })}
          ${kpi({ label: 'Critical pending', value: s.criticalPending, icon: 'alert', accent: s.criticalPending ? 'critical' : '', href: '#/live?quick=critical' })}
          ${kpi({ label: 'Open investigations', value: s.openInvestigations, icon: 'investigate', href: '#/investigations?tab=investigating' })}
          ${kpi({ label: 'Client approvals', value: s.clientApprovals, icon: 'check', href: '#/sharing?tab=ready_for_review' })}
          ${kpi({ label: 'Ready to share', value: s.readyToShare, icon: 'share', href: '#/sharing' })}
        </div>
        <div class="grid g-main">
          <div class="stack">${card({ title: `${icon('alert')} Critical pending`, flush: true, actions: '<a class="btn sm" href="#/live?quick=critical">Open</a>',
            body: data.critical.length ? `<div class="list">${data.critical.map((a) => miniRow(a)).join('')}</div>` : empty('No critical alarms pending', '', 'check') })}${history}</div>
          <div class="stack">${form}</div>
        </div>`;

      $('#ho-save', el)?.addEventListener('click', async () => {
        const fromShift = $('#ho-from', el).value.trim(), toShift = $('#ho-to', el).value.trim();
        if (!fromShift || !toShift) return toast('Enter both shifts', 'warning');
        try {
          await api.post('/api/handovers', { projectId: pid, fromShift, toShift, notes: $('#ho-notes', el).value });
          toast('Handover recorded', 'success');
          load();
        } catch (e) { toast(e.message, 'error'); }
      });
    };

    ctx.onCleanup(delegate(el, 'click', '[data-refresh]', load));
    ctx.onCleanup(on('data', () => { if (!document.activeElement?.closest?.('#ho-notes,#ho-from,#ho-to')) load(); }));
    await load();
  },
};
