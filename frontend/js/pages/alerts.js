// pages/alerts.js — INTELLIGENT ALERT CENTER. Every alert is derived and explains itself.

import * as api from '../core/api.js';
import { currentProject, projectInfo, on, can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, card, empty, errorBox, skeleton, alertItem, why, delegate, prov, $ } from '../core/ui.js';

const TABS = [['', 'All'], ['operational', 'Operational'], ['investigation', 'Investigation'], ['approval', 'Approval'],
  ['client', 'Client Sharing'], ['system', 'System']];

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Intelligent Alerts', `${esc(projectInfo(pid).code)} · explainable alerts`);
    if (!pid) { el.innerHTML = empty('No project available', '', 'tree'); return; }
    let tab = ctx.query.category || '';
    let data;
    el.innerHTML = skeleton(6, 50);

    const load = async () => {
      try {
        data = await api.get('/api/alerts', { projectId: pid });
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) { el.innerHTML = errorBox(e); $('[data-retry]', el)?.addEventListener('click', load); }
      }
    };

    const paint = () => {
      const counts = {};
      data.items.forEach((a) => { counts[a.category] = (counts[a.category] || 0) + 1; });
      const items = tab ? data.items.filter((a) => a.category === tab) : data.items;
      el.innerHTML = `<div class="page-head"><div><h2>Intelligent Alert Center</h2>
          <p>Derived from the alarms you can see. No opaque scores — open <b>Why?</b> on any alert for the complete logic. ${prov('derived')}</p></div>
          <div class="row">${can('alert.view') ? `<a class="btn" href="#/rules">${icon('rules', 's')} Alert rules</a>` : ''}<button class="btn" data-refresh>${icon('refresh', 's')} Refresh</button></div></div>
        <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab ${k === tab ? 'on' : ''}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${l}<span class="n">${k ? counts[k] || 0 : data.items.length}</span></button>`).join('')}</div>
        <div class="grid g-side">
          ${card({ flush: true, body: items.length ? `<div class="list">${items.map((a) => alertItem(a)).join('')}</div>` : empty('No alerts in this category', 'No conditions are met right now.', 'check') })}
          <div class="stack">
            ${card({ title: `${icon('info')} Checks that did not run`, sub: 'Honest about missing data',
              body: data.skipped.length ? `<ul class="check-list">${data.skipped.map((s) => `<li class="no"><span class="m">–</span><span><b>${esc(s.type.replace(/_/g, ' '))}</b>: ${esc(s.reason)}</span></li>`).join('')}</ul>` : '<div class="muted">All intelligence checks had enough data to run.</div>' })}
            ${card({ title: `${icon('settings')} Thresholds in use`, sub: 'Configurable in Settings › Workflow',
              body: `<dl class="kv">${[['Repeated activity', `≥${data.policy.repeatThreshold} reports within ${data.policy.repeatWindowMinutes} min`],
                ['High camera activity', `≥${data.policy.cameraActivityThreshold} alarms in ${data.policy.cameraActivityWindowMinutes} min`],
                ['Related alarms', `≥${data.policy.relatedMinCount} in ${data.policy.relatedWindowMinutes} min (same room/centre)`],
                ['Activity spike', `≥${data.policy.spikeRatio}× baseline and ≥${data.policy.spikeMinCount} alarms`],
                ['Activity surge', `≥${data.policy.stormCount} alarms in ${data.policy.stormWindowSeconds} s`],
                ['Long pending', data.policy.longPendingMinutes ? `${data.policy.longPendingMinutes} min` : 'Not configured']]
                .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>` })}
          </div>
        </div>`;
    };

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; ctx.setQuery({ category: tab }); paint(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-why]', (e, b) => { const a = data.items.find((x) => x.id === b.dataset.why); if (a) why(a); }));
    ctx.onCleanup(delegate(el, 'click', '[data-refresh]', load));
    ctx.onCleanup(on('data', load));
    await load();
  },
};
