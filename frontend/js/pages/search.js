// pages/search.js — SMART SEARCH: type what you are looking for in plain words; the interpreted
// filters are shown as removable chips BEFORE anything is opened, with a live preview of the first
// results. Works without any AI key (deterministic parser, core/nlsearch.js); "Ask AI to interpret"
// appears only when the server has an AI key, and its answer is shown as chips for confirmation too.

import * as api from '../core/api.js';
import { session } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, empty, errorBox, skeleton, toast, $ } from '../core/ui.js';
import { alertRow } from '../components/queue.js';
import * as nl from '../core/nlsearch.js';

const EXAMPLES = [
  'critical pending mobile phone today',
  'valid alerts yesterday',
  'camera offline events last 2 hours',
  'false alarms last 7 days',
  'high priority pending this week',
];

export default {
  async render(el, ctx) {
    setTitle('Smart search', 'Plain-language search over the alerts you can see');
    let vocab = null;
    let filters = {};
    let source = 'text';              // 'text' | 'ai' | 'edited'
    let text = String(ctx.query.q || '');
    let ctrl = null;

    el.innerHTML = `
      <div class="page-head"><div><h2>Smart search</h2>
        <p>Describe the alerts you want, for example “critical pending mobile phone at 9111 today”. The filters are shown before anything is opened.</p></div></div>
      <section class="card ns-box">
        <div class="card-b stack">
          <div class="ns-input">
            ${icon('search')}
            <input class="input" id="ns-q" autocomplete="off" spellcheck="false" aria-label="Describe the alerts you are looking for"
              placeholder="e.g. valid lab activity in indore yesterday" value="${esc(text)}">
            <button class="btn primary" id="ns-open" disabled>${icon('arrow', 's')} Open in Alerts</button>
          </div>
          <div class="ns-examples" aria-label="Examples"><span class="muted">Try:</span>
            ${EXAMPLES.map((x) => `<button class="chip" data-ex="${esc(x)}">${esc(x)}</button>`).join('')}</div>
          <div class="ns-interp" id="ns-interp" aria-live="polite"></div>
          <div class="row" id="ns-actions"></div>
        </div>
      </section>
      <section class="card" style="margin-top:14px">
        <div class="card-h"><div><h3>${icon('alert')} First results</h3><div class="sub" id="ns-count">Type a search to see a preview.</div></div>
          <div class="actions"><a class="btn sm ghost" id="ns-all" href="#/alerts">All results in Alerts ${icon('arrow', 's')}</a></div></div>
        <div id="ns-rows" class="qrows">${empty('Nothing to preview yet', 'The first 10 matching alerts appear here as you type.', 'search')}</div>
      </section>`;
    $('#ns-rows', el).addEventListener('error', (e) => { if (e.target?.tagName === 'IMG') e.target.closest('.q-thumb')?.classList.add('broken'); }, true);

    const input = $('#ns-q', el);
    const aiOn = () => !!vocab?.ai && session.features?.ENABLE_AI !== false;

    const paintChips = () => {
      const list = nl.chips(filters, vocab);
      const box = $('#ns-interp', el);
      if (!text.trim() && !list.length) { box.innerHTML = ''; }
      else if (!list.length) {
        box.innerHTML = `<div class="ns-none">${icon('info', 's')} Nothing recognised yet — try an alert type, a decision (pending, valid…), a priority, a centre or camera code, or a date (today, yesterday, last 2 hours).</div>`;
      } else {
        box.innerHTML = `<div class="ns-label">${source === 'ai' ? `${icon('cpu', 's')} Interpreted by AI — check before opening:` : 'Interpreted as:'}</div>
          <div class="ns-chips">${list.map((c) => `<span class="ns-chip" data-key="${esc(c.key)}">${esc(c.label)}<button class="ns-x" data-rm="${esc(c.key)}" aria-label="Remove ${esc(c.label)}" title="Remove">${icon('x', 's')}</button></span>`).join('')}</div>
          <div class="ns-note muted">${esc(implicitNote())}</div>`;
      }
      const href = nl.toAlertsHref(filters);
      $('#ns-open', el).disabled = !list.length;
      $('#ns-all', el).setAttribute('href', list.length ? href : '#/alerts');
      $('#ns-actions', el).innerHTML = aiOn() && text.trim()
        ? `<button class="btn sm" id="ns-ai">${icon('cpu', 's')} Ask AI to interpret</button><span class="muted" style="font-size:11.5px">Sends your text and the list of names you can see to the AI provider; the result is shown here for confirmation.</span>`
        : '';
    };

    const implicitNote = () => {
      const bits = [];
      if (!filters.status) bits.push('every decision');
      if (!filters.from && !filters.to && !filters.hours) bits.push('all dates');
      if (!filters.kind) bits.push('alerts only (not camera status events)');
      return bits.length ? `Not mentioned, so included: ${bits.join(', ')}.` : '';
    };

    const preview = async () => {
      const list = nl.chips(filters, vocab);
      const rows = $('#ns-rows', el);
      if (!list.length) {
        rows.innerHTML = empty('Nothing to preview yet', 'The first 10 matching alerts appear here as you type.', 'search');
        $('#ns-count', el).textContent = 'Type a search to see a preview.';
        return;
      }
      if (ctrl) ctrl.abort();
      ctrl = new AbortController();
      rows.innerHTML = skeleton(4, 40);
      $('#ns-count', el).textContent = 'Looking…';
      try {
        const params = nl.toQueueParams(filters, { size: filters.city ? 50 : 10 });
        const r = await api.get('/api/queue', params, { signal: ctrl.signal });
        if (ctx.isStale()) return;
        let items = r.items || [];
        let total = r.totalElements ?? items.length;
        if (filters.city) {                                   // a city spanning several centres: narrow the preview here too
          const codes = new Set(((vocab?.cities || []).find((c) => c.name === filters.city) || {}).centres || []);
          const before = items.length;
          items = items.filter((a) => codes.has((a.context?.path || []).find((n) => n.level === 'centre')?.code) || codes.has(a.centreCode));
          if (items.length !== before) total = null;
        }
        $('#ns-count', el).textContent = total == null
          ? `${items.length} match${items.length === 1 ? '' : 'es'} in the first ${params.size} results`
          : `${total.toLocaleString()} matching ${filters.kind === 'camera_status' ? 'camera status event' : 'alert'}${total === 1 ? '' : 's'}${total > 10 ? ' · showing the first 10' : ''}`;
        rows.innerHTML = items.length ? items.slice(0, 10).map((a) => alertRow(a)).join('')
          : empty('No matching alerts', 'Remove a filter above to widen the search.', 'check');
      } catch (e) {
        if (e.name === 'AbortError' || ctx.isStale()) return;
        rows.innerHTML = errorBox(e);
        $('[data-retry]', rows)?.addEventListener('click', preview);
      }
    };

    const reparse = () => {
      source = 'text';
      filters = vocab ? nl.parse(text, vocab).filters : {};
      paintChips();
      preview();
      ctx.setQuery({ q: text.trim() });
    };

    let t;
    input.addEventListener('input', () => { text = input.value; clearTimeout(t); t = setTimeout(reparse, 250); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        clearTimeout(t);
        text = input.value;
        filters = vocab ? nl.parse(text, vocab).filters : {};
        if (nl.chips(filters, vocab).length) location.hash = nl.toAlertsHref(filters);
        else reparse();
      }
    });
    el.addEventListener('click', async (e) => {
      const ex = e.target.closest('[data-ex]');
      if (ex) { input.value = text = ex.dataset.ex; reparse(); input.focus(); return; }
      const rm = e.target.closest('[data-rm]');
      if (rm) { filters = nl.remove(filters, rm.dataset.rm); source = 'edited'; paintChips(); preview(); return; }
      if (e.target.closest('#ns-open')) { location.hash = nl.toAlertsHref(filters); return; }
      const aiBtn = e.target.closest('#ns-ai');
      if (aiBtn) {
        aiBtn.disabled = true;
        aiBtn.classList.add('loading');
        try {
          const r = await api.post('/api/search/interpret', { text: text.trim(), tzOffset: -new Date().getTimezoneOffset() });
          if (ctx.isStale()) return;
          filters = nl.fromServer(r.filters || {});
          source = 'ai';
          paintChips();
          preview();
          if (r.dropped?.length) toast(`The AI suggested values that do not exist here (${r.dropped.join(', ')}); they were left out.`);
        } catch (err) {
          toast(err.message, 'error');
          aiBtn.disabled = false;
          aiBtn.classList.remove('loading');
        }
      }
    });

    $('#ns-interp', el).innerHTML = skeleton(1, 28);
    try {
      vocab = await nl.loadVocabulary({ force: true });
    } catch (e) {
      if (ctx.isStale()) return;
      $('#ns-interp', el).innerHTML = errorBox(e);
      $('[data-retry]', el)?.addEventListener('click', () => ctx.navigate(location.hash));
      return;
    }
    if (ctx.isStale()) return;
    reparse();
    input.focus();
  },
};
