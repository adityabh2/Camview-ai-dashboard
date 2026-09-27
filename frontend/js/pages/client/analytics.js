// pages/client/analytics.js — client analytics, computed ONLY from alerts shared with this client.

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { bar } from '../../core/charts.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, bars, prov, PRIORITY_COLORS, $ } from '../../core/ui.js';

export default {
  async render(el, ctx) {
    setTitle('Analytics', 'Based only on alerts shared with you');
    el.innerHTML = skeleton(4, 80);
    const load = async () => {
      try {
        const d = await api.get('/api/client/analytics');
        if (ctx.isStale()) return;
        el.innerHTML = `<div class="banner info">${icon('info')}<div class="grow">Based on <b>${esc(d.basis)}</b>. Alerts that were not shared with you are never counted. ${prov('derived')}</div></div>
          <div class="kpis">${kpi({ label: 'Shared alerts', value: d.total, icon: 'share' })}${kpi({ label: 'Acknowledged', value: d.acknowledged, accent: 'good', icon: 'check', sub: d.total ? fmt.pct(d.acknowledged / d.total) + ' of shared' : '' })}</div>
          <div class="grid g-2">
            ${card({ title: 'By priority', body: bars(d.priorityDistribution.map((x) => ({ ...x, color: PRIORITY_COLORS[x.key], sw: PRIORITY_COLORS[x.key] }))) })}
            ${card({ title: 'By alert type', body: bars(d.typeDistribution) })}
          </div>
          <div style="margin-top:14px">${card({ title: 'Shared alerts by day raised', sub: 'Hover for counts',
            body: d.daily.length ? `<div class="chart"><canvas id="cl-daily" aria-label="Alerts per day"></canvas></div>
              <details style="margin-top:8px"><summary class="muted">Table view</summary><table class="t"><thead><tr><th>Date</th><th class="num">Alerts</th></tr></thead><tbody>${d.daily.map((x) => `<tr><td>${esc(x.date)}</td><td class="num">${x.count}</td></tr>`).join('')}</tbody></table></details>`
              : empty('No data yet') })}</div>`;
        if (d.daily.length) bar($('#cl-daily', el), { labels: d.daily.map((x) => fmt.date(x.date)), series: [{ label: 'Alerts', data: d.daily.map((x) => x.count), color: 'var(--chart-1)' }] });
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    await load();
  },
};
