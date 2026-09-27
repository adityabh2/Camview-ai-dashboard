// pages/client/evidence.js — all evidence approved for sharing, across shared alerts.

import * as api from '../../core/api.js';
import { setTitle } from '../../core/layout.js';
import { esc, icon, fmt, card, empty, errorBox, skeleton, priorityBadge, $ } from '../../core/ui.js';
import { gallery, bindGallery } from '../../components/evidence.js';
import { alertHref } from './common.js';

export default {
  async render(el, ctx) {
    setTitle('Evidence', 'Evidence approved for sharing with you');
    el.innerHTML = skeleton(4, 120);
    const load = async () => {
      try {
        const r = await api.get('/api/client/alerts');
        if (ctx.isStale()) return;
        const withEv = r.items.filter((a) => a.evidence?.length);
        if (!withEv.length) { el.innerHTML = empty('No shared evidence', 'Evidence appears here when an alert is shared with approved images or video.', 'image'); return; }
        el.innerHTML = `<div class="page-head"><div><h2>Evidence</h2><p>${withEv.length} shared alert(s) with approved evidence. Only items approved for sharing are shown.</p></div></div>
          <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(340px,1fr))">${withEv.map((a, i) => card({
            title: `<a class="mono" href="${alertHref(a)}">${esc(a.alarmId)}</a>`, sub: `${esc(a.alarmTypeName)} · ${fmt.dt(a.firstInstance)}`,
            actions: priorityBadge(a.priority), body: `<div data-g="${i}">${gallery(a.evidence)}</div>` })).join('')}</div>`;
        withEv.forEach((a, i) => bindGallery($(`[data-g="${i}"]`, el), a.evidence, { log: false, title: a.alarmId }));
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };
    await load();
  },
};
