// pages/evidence.js — EVIDENCE CENTER. Opening evidence is audited; downloads need evidence.download.

import * as api from '../core/api.js';
import { currentProject, projectInfo, can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, pager, empty, errorBox, skeleton, priorityBadge, stateBadge, visBadge, delegate, $ } from '../core/ui.js';
import { lightbox } from '../components/evidence.js';
import { investigateHref } from '../components/alarms.js';

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Evidence', `${esc(projectInfo(pid).code)} · evidence review`);
    if (!pid) { el.innerHTML = empty('No project available', '', 'tree'); return; }
    let q = { page: '1', ...ctx.query };
    let data;

    el.innerHTML = `<div class="page-head"><div><h2>Evidence Center</h2><p>Images and video attached by Camview. Every view is recorded in the audit trail${can('evidence.download') ? '; downloads too' : ''}.</p></div></div>
      <div class="filters">
        <button class="chip ${q.pending === '1' ? 'on' : ''}" data-f="pending">Pending review only</button>
        <button class="chip ${q.quick === 'shared' ? 'on' : ''}" data-f="shared">Shared with a client</button>
        <select class="select" id="ev-prio" aria-label="Priority"><option value="">Any priority</option>${['critical', 'high', 'medium', 'low'].map((p) => `<option value="${p}" ${q.priority === p ? 'selected' : ''}>${p[0].toUpperCase() + p.slice(1)}</option>`).join('')}</select>
      </div>
      <div id="ev-body">${skeleton(4, 120)}</div><div id="ev-pager"></div>`;

    const load = async () => {
      try {
        const params = { projectId: pid, page: q.page, size: 24 };
        ['pending', 'priority', 'quick'].forEach((k) => { if (q[k]) params[k] = q[k]; });
        data = await api.get('/api/evidence', params);
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#ev-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const itemsOf = (a) => [...(a.imageUrls || []).map((u, i) => ({ kind: 'image', index: i, url: u })), ...(a.videoUrl ? [{ kind: 'video', index: 0, url: a.videoUrl }] : [])];

    const paint = () => {
      const body = $('#ev-body', el);
      if (!data.items.length) {
        body.innerHTML = empty('No evidence available', 'No alarms with evidence match these filters.', 'image');
      } else {
        body.innerHTML = `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(260px,1fr))">${data.items.map((a, i) => {
          const first = (a.imageUrls || [])[0];
          const shared = Object.entries(a.sharedEvidence || {});
          return `<article class="card">
            <button class="thumb" style="width:100%;border:0;border-radius:8px 8px 0 0" data-open="${i}" aria-label="Open evidence for ${esc(a.alarmId)}">
              ${first ? `<img src="${esc(first)}" alt="" loading="lazy">` : `<span class="play">${icon('play', 'l')}</span>`}
              <span class="tag b outline" style="background:rgba(0,0,0,.55);color:#fff">${icon('image')}${(a.imageUrls || []).length}${a.videoUrl ? ` · ${icon('video')}1` : ''}</span></button>
            <div class="card-b stack" style="gap:6px">
              <div class="row">${priorityBadge(a.priority)}<a class="mono" href="${investigateHref(a)}">${esc(a.alarmId)}</a></div>
              <div style="font-weight:600">${esc(a.alarmTypeName)}</div>
              <div class="dim" style="font-size:12px"><span class="mono">${esc(a.cameraCode)}</span> · ${fmt.dt(a.lastInstance)}</div>
              <div class="row">${stateBadge(a.lastActionType, a.lastActionLabel)}${visBadge(a.visibility?.state, null, a.visibility?.acknowledged)}</div>
              ${shared.length ? `<div class="muted" style="font-size:11.5px">${icon('share', 's')} ${shared.map(([k, names]) => `${esc(k)} → ${names.map(esc).join(', ')}`).join('; ')}</div>` : '<div class="muted" style="font-size:11.5px">No evidence shared with clients</div>'}
            </div></article>`;
        }).join('')}</div>`;
        body.querySelectorAll('.thumb img').forEach((img) => img.addEventListener('error', () => {
          const b = img.closest('.thumb'); b.classList.add('broken'); img.replaceWith(Object.assign(document.createElement('span'), { textContent: 'Evidence unavailable' }));
          b.dataset.broken = '1';
        }, { once: true }));
      }
      pager($('#ev-pager', el), { page: data.page, totalPages: data.totalPages, totalElements: data.totalElements, onPage: (pg) => { q.page = String(pg); ctx.setQuery(q); load(); } });
    };

    const setQ = (patch) => { q = { ...q, ...patch, page: '1' }; Object.keys(q).forEach((k) => (q[k] === '' || q[k] == null) && delete q[k]); ctx.setQuery(q); load(); };
    ctx.onCleanup(delegate(el, 'click', '[data-f]', (e, b) => {
      if (b.dataset.f === 'pending') setQ({ pending: q.pending === '1' ? '' : '1' });
      else setQ({ quick: q.quick === 'shared' ? '' : 'shared' });
      b.classList.toggle('on');
    }));
    $('#ev-prio', el).addEventListener('change', (e) => setQ({ priority: e.target.value }));
    ctx.onCleanup(delegate(el, 'click', '[data-open]', (e, b) => {
      const a = data.items[+b.dataset.open];
      const items = itemsOf(a);
      if (items.length) lightbox(items, 0, { alarmId: a.alarmId, canDownload: can('evidence.download'), title: `${a.alarmId} · ${a.cameraCode}` });
    }));
    await load();
  },
};
