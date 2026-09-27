// pages/assistant.js — AI ASSISTANT (V2): ask questions about the alerts you can see.
// The answer comes from Claude on the server, which reads data only through permission-checked
// tools running as YOU (same RBAC / scope as the Alerts queue). The key never reaches the browser.
// Answers are rendered safely: escaped first, then only **bold**, "- " bullets and line breaks.

import * as api from '../core/api.js';
import { session } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, empty, errorBox, skeleton, toast, $ } from '../core/ui.js';

const SUGGESTED = [
  'What needs attention right now?',
  'Which centres have the most mobile phone alerts today?',
  'Summarise alert <id>',
  'Cameras offline?',
];

const reEsc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const alertHref = (f) => `#/alerts/${encodeURIComponent(f.alarmId)}?projectId=${encodeURIComponent(f.projectId ?? '')}`;

/** Escape, then allow **bold**, bullet lines and line breaks; cited alert ids become links. */
export function formatAnswer(text, facts = []) {
  let html = esc(text || '');
  html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
  const lines = html.split('\n');
  const out = [];
  let list = null;
  for (const raw of lines) {
    const line = raw.trimEnd();
    const m = line.match(/^\s*(?:[-*•]|\d+[.)])\s+(.*)$/);
    if (m) { (list ||= []).push(`<li>${m[1]}</li>`); continue; }
    if (list) { out.push(`<ul>${list.join('')}</ul>`); list = null; }
    out.push(line.trim() ? `<p>${line}</p>` : '');
  }
  if (list) out.push(`<ul>${list.join('')}</ul>`);
  html = out.join('');
  // link alert ids the server confirmed as facts (ids are escaped the same way as the text)
  const byId = new Map(facts.filter((f) => f.alarmId).map((f) => [esc(f.alarmId), f]));
  if (byId.size) {
    const rx = new RegExp(`(^|[^\\w-])(${[...byId.keys()].sort((a, b) => b.length - a.length).map(reEsc).join('|')})(?![\\w-])`, 'g');
    html = html.replace(rx, (_, pre, id) => `${pre}<a class="ai-id mono" href="${esc(alertHref(byId.get(id)))}">${id}</a>`);
  }
  return html;
}

export default {
  async render(el, ctx) {
    setTitle('AI Assistant', 'Answers from the alerts you can see');
    const alarmCtx = ctx.query.alarm ? { alarmId: String(ctx.query.alarm), projectId: ctx.query.projectId ? String(ctx.query.projectId) : null } : null;
    let status = null;
    let convId = ctx.query.c || null;
    let convs = [];
    let busy = false;
    let thread = [];                  // [{role, text, facts, usage, error}]

    el.innerHTML = `<div class="ai-page">${skeleton(4, 40)}</div>`;
    try {
      status = await api.get('/api/ai/status');
    } catch (e) {
      if (ctx.isStale()) return;
      el.innerHTML = errorBox(e);
      $('[data-retry]', el)?.addEventListener('click', () => ctx.navigate(location.hash));
      return;
    }
    if (ctx.isStale()) return;

    if (!status.configured || !status.installed) {
      el.innerHTML = setupCard(status);
      const q = $('#ai-ns', el);
      $('#ai-ns-form', el)?.addEventListener('submit', (e) => { e.preventDefault(); location.hash = `#/search?q=${encodeURIComponent(q.value.trim())}`; });
      return;
    }

    el.innerHTML = `
      <div class="ai-page">
        <div class="ai-layout" id="ai-layout">
          <aside class="ai-side card" id="ai-side" aria-label="Conversations">
            <div class="ai-side-h"><button class="btn sm primary" id="ai-new">${icon('plus', 's')} New conversation</button>
              <button class="btn sm ghost icon ai-side-close" id="ai-side-close" aria-label="Close conversations">${icon('x', 's')}</button></div>
            <div class="ai-convs" id="ai-convs">${skeleton(4, 28)}</div>
          </aside>
          <section class="ai-main card">
            <div class="ai-top">
              <button class="btn sm ghost ai-side-open" id="ai-side-open" aria-controls="ai-side">${icon('menu', 's')} Conversations</button>
              <span class="ai-model muted" title="Model used on the server">${icon('cpu', 's')} ${esc(status.model)}</span>
              <span class="grow"></span>
              <a class="btn sm ghost" href="#/search">${icon('search', 's')} Smart search</a>
            </div>
            <div class="ai-msgs" id="ai-msgs" aria-live="polite"></div>
            <form class="ai-compose" id="ai-form">
              ${alarmCtx ? `<div class="ai-ctx" id="ai-ctx">${icon('alert', 's')} About alert <a class="mono" href="${esc(alertHref(alarmCtx))}">${esc(alarmCtx.alarmId)}</a>
                <button type="button" class="ns-x" id="ai-ctx-x" aria-label="Stop asking about this alert">${icon('x', 's')}</button></div>` : ''}
              <div class="ai-compose-row">
                <textarea class="input" id="ai-input" rows="2" maxlength="4000" placeholder="Ask about alerts, centres, cameras… (Enter to send, Shift+Enter for a new line)" aria-label="Your question"></textarea>
                <button class="btn primary" id="ai-send" type="submit">${icon('arrow', 's')} Send</button>
              </div>
              <div class="ai-foot muted">Answers use only data you are allowed to see, read through the same permission checks as the Alerts queue. Always check the cited alerts.</div>
            </form>
          </section>
        </div>
      </div>`;

    const msgs = $('#ai-msgs', el);
    const input = $('#ai-input', el);
    let ctxAlarm = alarmCtx;
    if (alarmCtx) input.value = `Summarise this alert (${alarmCtx.alarmId})`;

    const paintConvs = () => {
      $('#ai-convs', el).innerHTML = convs.length
        ? convs.map((c) => `<a class="ai-conv ${c.id === convId ? 'on' : ''}" href="#/assistant?c=${encodeURIComponent(c.id)}" data-conv="${esc(c.id)}">
            <span class="t">${esc(c.title || 'Conversation')}</span><span class="muted">${esc(fmt.rel(c.updatedAt))}</span></a>`).join('')
        : `<div class="muted ai-conv-empty">No conversations yet.</div>`;
    };
    const loadConvs = async () => {
      try { convs = (await api.get('/api/ai/conversations')).items || []; } catch { convs = []; }
      if (!ctx.isStale()) paintConvs();
    };

    const bubble = (m) => {
      if (m.role === 'user') return `<div class="ai-msg user"><div class="ai-b">${esc(m.text).replace(/\n/g, '<br>')}</div></div>`;
      if (m.pending) {
        return `<div class="ai-msg bot pending"><div class="ai-av">${icon('cpu', 's')}</div><div class="ai-b"><div class="ai-typing" aria-label="Working"><span></span><span></span><span></span></div>
          <div class="muted" style="font-size:11.5px">Reading the alerts you can see…</div></div></div>`;
      }
      if (m.error) return `<div class="ai-msg bot"><div class="ai-av err">${icon('alert', 's')}</div><div class="ai-b ai-err" role="alert">${esc(m.error)}</div></div>`;
      const facts = m.facts || [];
      const u = m.usage || {};
      return `<div class="ai-msg bot"><div class="ai-av">${icon('cpu', 's')}</div><div class="ai-b">
        <div class="ai-text">${formatAnswer(m.text, facts)}</div>
        ${facts.length ? `<div class="ai-facts"><div class="ai-facts-h">${icon('check', 's')} Alerts cited — open to verify</div>
          ${facts.map((f) => `<a class="ai-fact" href="${esc(alertHref(f))}"><span class="mono">${esc(f.alarmId)}</span>${f.label ? `<span class="muted">${esc(f.label)}</span>` : ''}</a>`).join('')}</div>` : ''}
        <div class="ai-meta muted"><span class="ai-tag">AI SUMMARY</span>${u.tools?.length ? ` · read: ${esc([...new Set(u.tools)].join(', '))}` : ''}${u.input != null ? ` · ${esc(u.input)} in / ${esc(u.output)} out tokens` : ''}</div>
      </div></div>`;
    };

    const paintThread = () => {
      if (!thread.length) {
        msgs.innerHTML = `<div class="ai-empty">${icon('cpu', 'l')}
          <h3>Ask about your alerts</h3>
          <p class="muted">Answers are built only from alerts, centres and cameras you may see. Informal English or Hinglish is fine.</p>
          <div class="ai-sugg">${SUGGESTED.map((s) => `<button class="chip" type="button" data-sugg="${esc(s)}">${esc(s)}</button>`).join('')}</div></div>`;
        return;
      }
      msgs.innerHTML = thread.map(bubble).join('');
      msgs.scrollTop = msgs.scrollHeight;
    };

    const openConv = async (id) => {
      convId = id;
      paintConvs();
      msgs.innerHTML = skeleton(3, 40);
      try {
        const c = await api.get(`/api/ai/conversations/${encodeURIComponent(id)}`);
        if (ctx.isStale() || convId !== id) return;
        thread = c.messages.map((m) => ({ role: m.role, text: m.text, facts: m.facts, usage: m.usage }));
      } catch (e) {
        if (ctx.isStale()) return;
        thread = [];
        convId = null;
        toast(e.message, 'error');
      }
      paintThread();
    };

    const send = async (textIn) => {
      const text = String(textIn ?? input.value).trim();
      if (!text || busy) return;
      busy = true;
      $('#ai-send', el).disabled = true;
      input.value = '';
      thread.push({ role: 'user', text }, { role: 'assistant', pending: true });
      paintThread();
      try {
        const r = await api.post('/api/ai/ask', {
          conversationId: convId, message: text, tzOffset: -new Date().getTimezoneOffset(),
          alarmId: ctxAlarm?.alarmId, projectId: ctxAlarm?.projectId,
        });
        if (ctx.isStale()) return;
        thread[thread.length - 1] = { role: 'assistant', text: r.answer, facts: r.facts, usage: r.usage };
        const isNew = convId !== r.conversationId;
        convId = r.conversationId;
        ctx.setQuery({ c: convId });
        if (isNew) loadConvs(); else paintConvs();
      } catch (e) {
        if (ctx.isStale()) return;
        thread[thread.length - 1] = { role: 'assistant', error: e.message || 'The assistant could not answer.' };
        if (e.status === 429) toast(e.message, 'error');
      } finally {
        if (!ctx.isStale()) {
          busy = false;
          $('#ai-send', el).disabled = false;
          paintThread();
          input.focus();
        }
      }
    };

    $('#ai-form', el).addEventListener('submit', (e) => { e.preventDefault(); send(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
    });
    el.addEventListener('click', (e) => {
      const s = e.target.closest('[data-sugg]');
      if (s) {
        const q = s.dataset.sugg;
        if (q.includes('<id>')) {                     // needs an id: put it in the box with the id selected
          input.value = q.replace('<id>', ctxAlarm?.alarmId || 'ALM-');
          input.focus();
          input.setSelectionRange(input.value.length, input.value.length);
        } else send(q);
        return;
      }
      const c = e.target.closest('[data-conv]');
      if (c) {
        e.preventDefault();
        ctx.setQuery({ c: c.dataset.conv, alarm: '', projectId: '' });
        $('#ai-layout', el).classList.remove('side-open');
        openConv(c.dataset.conv);
        return;
      }
      if (e.target.closest('#ai-new')) {
        convId = null;
        thread = [];
        ctx.setQuery({ c: '' });
        $('#ai-layout', el).classList.remove('side-open');
        paintConvs();
        paintThread();
        input.focus();
        return;
      }
      if (e.target.closest('#ai-ctx-x')) { ctxAlarm = null; $('#ai-ctx', el)?.remove(); ctx.setQuery({ alarm: '', projectId: '' }); return; }
      if (e.target.closest('#ai-side-open')) { $('#ai-layout', el).classList.add('side-open'); return; }
      if (e.target.closest('#ai-side-close')) $('#ai-layout', el).classList.remove('side-open');
    });

    paintThread();
    await loadConvs();
    if (ctx.isStale()) return;
    if (convId && convs.some((c) => c.id === convId)) await openConv(convId);
    else if (convId) { convId = null; ctx.setQuery({ c: '' }); }
    input.focus();
  },
};

function setupCard(st) {
  const flagOff = session.features?.ENABLE_AI === false;
  const title = !st.configured ? 'The AI assistant is not set up yet' : 'The AI library is not installed on the server';
  const step = !st.configured
    ? `Set <span class="mono">CAMVIEW_AI_API_KEY</span> in the <span class="mono">.env</span> next to <span class="mono">docker-compose.yml</span> and restart (<span class="mono">docker compose up -d</span>). Optional: <span class="mono">CAMVIEW_AI_MODEL</span> (default <span class="mono">${esc(st.model)}</span>).`
    : 'Add <span class="mono">anthropic</span> to <span class="mono">backend/requirements.txt</span>, rebuild the image and restart.';
  return `<div class="ai-page">
    <div class="page-head"><div><h2>AI Assistant</h2><p>Ask questions about the alerts you can see, in plain English or Hinglish.</p></div></div>
    <div class="grid g-2 ai-setup">
      <section class="card"><div class="card-h"><div><h3>${icon('cpu')} ${esc(title)}</h3></div></div>
        <div class="card-b stack">
          <p style="margin:0">${step}</p>
          ${flagOff ? '<p class="muted" style="margin:0">The feature flag ENABLE_AI is off for this deployment.</p>' : ''}
          <div><div class="section-title">What is sent to the AI provider</div>
            <ul class="why">
              <li>Your question and the earlier messages of the same conversation.</li>
              <li>Only data you are allowed to see, fetched through permission-checked tools that run as you (same role, project and centre scope as the Alerts queue).</li>
              <li>Alert facts such as type, priority, decision, centre, camera, exam and times — never images, video links, internal notes or keys.</li>
            </ul></div>
          <div><div class="section-title">What is kept</div>
            <ul class="why"><li>Your conversations, visible only to you.</li><li>An audit entry per question (model, tools used, token counts) — not the text.</li></ul></div>
        </div></section>
      <section class="card"><div class="card-h"><div><h3>${icon('search')} Smart search works without AI</h3></div></div>
        <div class="card-b stack">
          <p class="muted" style="margin:0">Describe the alerts you want; the filters are shown before anything opens.</p>
          <form id="ai-ns-form" class="row"><input class="input grow" id="ai-ns" placeholder="critical pending mobile phone today" aria-label="Smart search">
            <button class="btn primary" type="submit">${icon('search', 's')} Search</button></form>
          <a href="#/search">Open Smart search ${icon('arrow', 's')}</a>
        </div></section>
    </div></div>`;
}
