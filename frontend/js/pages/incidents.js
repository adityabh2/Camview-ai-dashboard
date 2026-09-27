// pages/incidents.js — INCIDENTS: related alerts handled once.
// Suggestions are computed by the server from the alerts you can see and always say WHY they were made
// (same centre within a time window, or the same alert type repeated on one camera) — no score, no AI.
// Incidents are patched in place when data changes (morph), so an open menu or a half-typed search stays put.

import * as api from '../core/api.js';
import { on, can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, empty, errorBox, skeleton, toast, dialog, morph, delegate, $, $$ } from '../core/ui.js';
import { alertRow, thumb } from '../components/queue.js';

const TABS = [['active', 'Active'], ['open', 'Open'], ['investigating', 'Investigating'], ['resolved', 'Resolved'], ['closed', 'Closed'], ['all', 'All']];
const WINDOWS = [10, 15, 30, 60, 120];
const SIZE = 50;
const SEV_ICON = { critical: 'alert', high: 'bang', medium: 'info', low: 'info' };
const ST_ICON = { open: 'clock', investigating: 'search', resolved: 'check', closed: 'lock' };
const ST_LABEL = { open: 'Open', investigating: 'Investigating', resolved: 'Resolved', closed: 'Closed' };

export const sevBadgeInc = (s) => `<span class="b p-${esc(s)}" title="Incident severity">${icon(SEV_ICON[s] || 'info')}${esc(s)}</span>`;
export const statusBadgeInc = (s) => `<span class="b inc-st st-${esc(s)}" title="Incident status">${icon(ST_ICON[s] || 'info')}${esc(ST_LABEL[s] || s)}</span>`;
export const incidentHref = (id) => `#/incidents/${encodeURIComponent(id)}`;

const span = (a, b) => {
  if (!a || !b) return '';
  const m = Math.max(1, Math.round((new Date(b) - new Date(a)) / 60000));
  return m < 90 ? `${m} min` : fmt.dur(m);
};

/** Opens the "create incident" dialog for a suggestion (or chosen alerts); resolves with the new incident or null. */
export function createDialog({ suggestion, alarms, owners = [] }) {
  return new Promise((resolve) => {
    let done = false;
    const sev = suggestion?.severity || 'medium';
    const d = dialog({
      title: `${icon('layers')} Create incident`,
      body: `<div class="stack">
        ${suggestion ? `<div class="banner info" style="margin:0">${icon('info')}<div><b>Why these alerts:</b> ${esc(suggestion.reason)}<div class="muted" style="margin-top:2px">${esc(suggestion.ruleText)}</div></div></div>` : ''}
        <div class="field"><label for="ic-title">Title</label><input class="input" id="ic-title" maxlength="200" value="${esc(suggestion?.title || '')}" autofocus></div>
        <div class="row" style="gap:12px;align-items:flex-start">
          <div class="field grow"><label for="ic-sev">Severity</label><select class="select" id="ic-sev">${['critical', 'high', 'medium', 'low'].map((s) => `<option value="${s}" ${s === sev ? 'selected' : ''}>${s[0].toUpperCase() + s.slice(1)}</option>`).join('')}</select><span class="hint">Suggested from the highest alert priority.</span></div>
          <div class="field grow"><label for="ic-owner">Owner</label><select class="select" id="ic-owner"><option value="">Unassigned</option>${owners.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('')}</select></div>
        </div>
        <p class="muted" style="margin:0">${esc((suggestion?.count ?? alarms?.length) || 0)} alert(s) will be linked. An alert can be part of only one open incident.</p>
      </div>`,
      actions: [
        { label: 'Cancel', onClick: ({ close }) => { done = true; resolve(null); close(); } },
        { label: 'Create incident', kind: 'primary', onClick: async ({ el: box, close }) => {
          const btn = box.querySelector('[data-act="1"]');
          btn.disabled = true;
          try {
            const payload = { title: $('#ic-title', box).value.trim(), severity: $('#ic-sev', box).value, ownerId: $('#ic-owner', box).value || undefined };
            if (suggestion) payload.suggestionId = suggestion.id; else payload.alarms = alarms;
            const r = await api.post('/api/incidents', payload);
            done = true;
            toast(`${r.incident.ref} created`, 'success');
            resolve(r.incident);
            close();
          } catch (e) { btn.disabled = false; toast(e.message, 'error'); }
        } },
      ],
      onClose: () => { if (!done) resolve(null); },
    });
    return d;
  });
}

export default {
  async render(el, ctx) {
    setTitle('Incidents', 'Related alerts handled once');
    const q = { status: 'active', owner: '', search: '', gap: '30', page: 1, ...ctx.query };
    q.page = +q.page || 1;
    let list = null, sugg = null, suggErr = null, busy = false, showAll = false;
    const FIRST = 6;               // suggestions shown before "Show all" (a phone screen stays usable)
    const manage = can('alarm.investigate');

    el.innerHTML = `
      <div class="page-head"><div><h2>Incidents</h2><p>One situation, handled once: related alerts grouped with an owner, a status and a timeline.</p></div>
        <div class="row"><span class="muted" id="i-fresh"></span></div></div>
      <div id="i-kpis" class="kpis">${skeleton(1, 60)}</div>
      <div class="row inc-sec-h"><div class="section-title grow" style="margin:0">${icon('layers', 's')} Suggested incidents</div>
        <label class="row tight muted" style="font-size:12px">Alerts within <select class="select sm" id="i-gap" aria-label="Correlation window">${WINDOWS.map((w) => `<option value="${w}" ${String(w) === String(q.gap) ? 'selected' : ''}>${w} min</option>`).join('')}</select> of each other</label></div>
      <div id="i-sugg">${skeleton(2, 90)}</div>
      <div class="section-title">${icon('report', 's')} Incidents</div>
      <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab ${k === q.status ? 'on' : ''}" role="tab" aria-selected="${k === q.status}" data-tab="${k}">${l}<span class="n" data-n="${k}">·</span></button>`).join('')}</div>
      <div class="filters">
        <input class="input" style="min-width:220px" id="i-search" data-page-search placeholder="Ref, title, centre or alert ID" value="${esc(q.search)}">
        <select class="select" id="i-owner" aria-label="Owner"><option value="">Any owner</option><option value="me">Mine</option><option value="none">Unassigned</option></select>
      </div>
      <section class="card"><div class="card-b flush" id="i-list">${skeleton(6, 30)}</div><div class="card-b" id="i-pager"></div></section>`;
    $('#i-sugg', el).addEventListener('error', (e) => { if (e.target?.tagName === 'IMG') e.target.closest('.q-thumb')?.classList.add('broken'); }, true);

    const kpis = () => {
      const c = list?.counts || {};
      morph($('#i-kpis', el), `
        ${kpi({ label: 'Open', value: c.open ?? null, icon: 'clock', accent: c.open ? 'warning' : '', href: '#/incidents?status=open' })}
        ${kpi({ label: 'Investigating', value: c.investigating ?? null, icon: 'search', href: '#/incidents?status=investigating' })}
        ${kpi({ label: 'Resolved today', value: list ? list.resolvedToday : null, icon: 'check', accent: 'good', href: '#/incidents?status=resolved' })}
        ${kpi({ label: 'Suggested', value: sugg ? sugg.total : null, icon: 'layers', sub: sugg ? `alerts within ${esc(sugg.settings.gapMinutes)} min` : '', title: 'Groups of related alerts not yet in an incident' })}`);
    };

    const suggCard = (s) => {
      const more = s.count - s.preview.length;
      return `<article class="inc-sg p-${esc(s.severity)}" data-key="${esc(s.id)}">
        <div class="inc-sg-h">${sevBadgeInc(s.severity)}<span class="b outline" title="${esc(s.ruleText)}">${icon(s.rule === 'centre' ? 'building' : 'camera', 's')}${s.rule === 'centre' ? 'Same centre' : 'Same camera · type'}</span><span class="grow"></span><b class="num">${esc(s.count)}</b><span class="muted">alerts</span></div>
        <div class="inc-sg-reason">${esc(s.reason)}</div>
        <div class="inc-sg-meta"><span title="Centre">${icon('building', 's')} ${esc(s.centreLabel || '—')}</span>
          <span title="${esc(fmt.dt(s.firstAt))} → ${esc(fmt.dt(s.lastAt))}">${icon('clock', 's')} ${fmt.time(s.firstAt)} – ${fmt.time(s.lastAt)} · ${esc(span(s.firstAt, s.lastAt))} · ${fmt.rel(s.lastAt)}</span></div>
        <div class="inc-sg-thumbs">${s.preview.map((a) => `<a href="#/alerts/${encodeURIComponent(a.alarmId)}?projectId=${encodeURIComponent(a.projectId ?? '')}" data-key="${esc(a.alarmId)}" title="${esc(a.alarmTypeName)} · ${esc(fmt.dt(a.lastInstance))}">${thumb(a)}</a>`).join('')}${more > 0 ? `<span class="inc-more">+${more}</span>` : ''}</div>
        <div class="inc-sg-f">${manage ? `<button class="btn sm primary" data-create="${esc(s.id)}">${icon('plus', 's')} Create incident</button>` : ''}<button class="btn sm" data-view="${esc(s.id)}">${icon('eye', 's')} View alerts</button></div>
      </article>`;
    };

    const paintSugg = () => {
      const box = $('#i-sugg', el);
      if (suggErr && !sugg) { box.innerHTML = errorBox(suggErr, { retry: false }); return; }
      if (!sugg) return;
      if (!sugg.settings.enabled) { morph(box, `<div class="card" data-key="off">${empty('Correlation is turned off', 'Advanced correlation (ENABLE_ADVANCED_CORRELATION) is disabled in this installation. Incidents can still be created from chosen alerts.', 'layers')}</div>`); return; }
      morph(box, sugg.items.length
        ? `<div class="inc-sg-grid" data-key="grid">${(showAll ? sugg.items : sugg.items.slice(0, FIRST)).map(suggCard).join('')}</div>
           ${sugg.items.length > FIRST ? `<div class="inc-sg-more" data-key="more"><button class="btn sm" data-more>${showAll ? 'Show fewer' : `Show all ${sugg.items.length} suggestions`}</button></div>` : ''}
           <p class="muted inc-note" data-key="note">${icon('info', 's')} ${esc(sugg.explanation)}${sugg.total > sugg.items.length ? ` Showing the ${sugg.items.length} most urgent of ${sugg.total}.` : ''}</p>`
        : `<div class="card" data-key="empty">${empty('No related alerts right now', esc(sugg.explanation), 'check')}</div>`);
    };

    const row = (i) => `<tr class="link" data-key="${esc(i.id)}" data-href="${incidentHref(i.id)}" tabindex="0">
      <td><a class="mono" href="${incidentHref(i.id)}"><b>${esc(i.ref)}</b></a></td>
      <td><div class="cell-2"><span class="inc-title">${esc(i.title)}</span>${i.reason ? `<span class="l2 muted">${esc(i.reason)}</span>` : ''}</div></td>
      <td>${sevBadgeInc(i.severity)}</td>
      <td>${statusBadgeInc(i.status)}</td>
      <td>${i.owner ? esc(i.owner.name) : '<span class="muted">Unassigned</span>'}</td>
      <td class="num">${esc(i.alarmCount)}</td>
      <td><div class="cell-2"><span class="mono">${esc(i.centre || '—')}</span><span class="l2 muted">${esc(i.project?.code || i.projectId || '')}</span></div></td>
      <td title="${esc(fmt.dt(i.updatedAt))}">${fmt.rel(i.updatedAt)}</td></tr>`;

    const paintList = () => {
      if (!list) return;
      TABS.forEach(([k]) => { const n = $(`[data-n="${k}"]`, el); if (n) n.textContent = list.counts[k] ?? 0; });
      const own = $('#i-owner', el);
      const key = list.owners.map((o) => o.id).join('|');
      if (own.dataset.owners !== key && document.activeElement !== own) {      // rebuilt only when the people changed
        own.dataset.owners = key;
        own.innerHTML = '<option value="">Any owner</option><option value="me">Mine</option><option value="none">Unassigned</option>'
          + list.owners.map((o) => `<option value="${esc(o.id)}">${esc(o.name)}</option>`).join('');
        own.value = q.owner || '';
      }
      const body = $('#i-list', el);
      if (!list.items.length) {
        const any = (list.counts.all || 0) > 0;
        morph(body, `<div data-key="empty">${empty(any ? 'No incidents match' : 'No incidents yet',
          any ? 'Try another tab, owner or search.' : (manage ? 'Create one from a suggestion above: related alerts become one incident with an owner, a status and a timeline.' : 'Incidents are created by people with the investigate permission.'), 'layers')}</div>`);
      } else {
        morph(body, `<div class="table-wrap" data-key="table"><table class="t inc-table"><thead><tr><th scope="col">Ref</th><th scope="col">Title</th><th scope="col">Severity</th><th scope="col">Status</th><th scope="col">Owner</th><th scope="col" class="num">Alerts</th><th scope="col">Centre</th><th scope="col">Updated</th></tr></thead>
          <tbody>${list.items.map(row).join('')}</tbody></table></div>`);
      }
      const pg = $('#i-pager', el);
      pg.classList.toggle('hidden', list.totalPages <= 1);
      if (list.totalPages > 1) {
        pg.innerHTML = `<div class="pager"><span>Page <b class="num">${list.page}</b> of <b class="num">${list.totalPages}</b> · ${fmt.n(list.totalElements)} incidents</span>
          <button class="btn sm" data-p="${list.page - 1}" ${list.page <= 1 ? 'disabled' : ''} aria-label="Previous page">${icon('left', 's')}</button>
          <button class="btn sm" data-p="${list.page + 1}" ${!list.hasNext ? 'disabled' : ''} aria-label="Next page">${icon('right', 's')}</button></div>`;
      }
    };

    const loadList = async () => {
      try {
        const p = { page: q.page, size: SIZE, status: q.status, tzOffset: -new Date().getTimezoneOffset() };
        ['owner', 'search'].forEach((k) => { if (q[k]) p[k] = q[k]; });
        const r = await api.get('/api/incidents', p);
        if (ctx.isStale()) return;
        list = r;
        paintList();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!list) $('#i-list', el).innerHTML = errorBox(e, { retry: false });
      }
    };
    const loadSugg = async () => {
      try {
        const r = await api.get('/api/incidents/suggestions', { gapMinutes: q.gap });
        if (ctx.isStale()) return;
        sugg = r; suggErr = null;
        const fr = Object.values(r.freshness || {});
        const worst = fr.find((f) => f.state === 'disconnected') || fr.find((f) => f.state === 'delayed') || fr[0];
        $('#i-fresh', el).innerHTML = worst ? `<span class="dot ${esc(worst.state)}"></span>${worst.state === 'live' ? 'Live' : esc(worst.state)} · Camview read ${fmt.rel(worst.lastSuccessAt)}` : '';
      } catch (e) { if (!ctx.isStale()) suggErr = e; }
      if (!ctx.isStale()) paintSugg();
    };
    const load = async () => { await Promise.all([loadList(), loadSugg()]); if (!ctx.isStale()) kpis(); };

    const set = (patch) => { Object.assign(q, patch, { page: 1 }); ctx.setQuery({ ...patch, page: 1 }); loadList(); };
    const find = (id) => sugg?.items.find((s) => s.id === id);

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { $$('[data-tab]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-selected', x === b); }); set({ status: b.dataset.tab }); }));
    ctx.onCleanup(delegate(el, 'click', '[data-p]', (e, b) => { q.page = +b.dataset.p; ctx.setQuery({ page: q.page }); loadList(); }));
    ctx.onCleanup(delegate(el, 'click', 'tr[data-href]', (e, tr) => { if (!e.target.closest('a,button')) location.hash = tr.dataset.href; }));
    ctx.onCleanup(delegate(el, 'keydown', 'tr[data-href]', (e, tr) => { if (e.key === 'Enter') location.hash = tr.dataset.href; }));
    ctx.onCleanup(delegate(el, 'click', '[data-create]', async (e, b) => {
      const s = find(b.dataset.create);
      if (!s) return;
      busy = true;
      const inc = await createDialog({ suggestion: s, owners: list?.owners || [] });
      busy = false;
      if (inc) location.hash = incidentHref(inc.id); else load();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-more]', () => { showAll = !showAll; paintSugg(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-view]', async (e, b) => {
      const s = find(b.dataset.view);
      if (!s) return;
      const d = dialog({ title: `${icon('layers')} ${esc(s.count)} related alerts`, size: 'xl', body: skeleton(5, 40),
        actions: manage ? [{ label: 'Create incident', kind: 'primary', onClick: async ({ close }) => { close(); const inc = await createDialog({ suggestion: s, owners: list?.owners || [] }); if (inc) location.hash = incidentHref(inc.id); } }] : [] });
      try {
        const r = await api.get('/api/incidents/suggestions', { gapMinutes: q.gap, id: s.id });
        if (!d.el.isConnected) return;
        d.el.querySelector('.d-b').innerHTML = `<div class="banner info">${icon('info')}<div><b>${esc(r.suggestion.reason)}</b><div class="muted">${esc(r.suggestion.ruleText)}</div></div></div>
          <div class="qrows inc-alerts">${r.suggestion.alarmsFull.map((a) => alertRow(a)).join('')}</div>`;
      } catch (err) {
        if (d.el.isConnected) d.el.querySelector('.d-b').innerHTML = errorBox(err, { retry: false });
      }
    }));
    $('#i-gap', el).addEventListener('change', (e) => { q.gap = e.target.value; ctx.setQuery({ gap: q.gap }); sugg = null; $('#i-sugg', el).innerHTML = skeleton(2, 90); loadSugg().then(kpis); });
    $('#i-owner', el).addEventListener('change', (e) => set({ owner: e.target.value }));
    let t;
    $('#i-search', el).addEventListener('input', (e) => { clearTimeout(t); t = setTimeout(() => set({ search: e.target.value.trim() }), 300); });
    ctx.onCleanup(() => clearTimeout(t));
    ctx.onCleanup(on('data', () => { if (!busy && !document.querySelector('.overlay')) load(); }));
    await load();
  },
};
