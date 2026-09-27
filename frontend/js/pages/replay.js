// pages/replay.js — ACTIVITY REPLAY: step through recorded alarms in chronological order.
// Replays recorded alarm data only — nothing is simulated or interpolated.

import * as api from '../core/api.js';
import { currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, errorBox, skeleton, empty, toast, delegate, priorityBadge, stateBadge, $, $$ } from '../core/ui.js';
import { investigateHref } from '../components/alarms.js';

const MAX_EVENTS = 1000;
const QUICK = [['15', 'Last 15 min'], ['60', 'Last 1 h'], ['180', 'Last 3 h']];

function toLocalInput(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Activity Replay', `${esc(projectInfo(pid).code)} · recorded alarms, in order`);
    let events = [];
    let cur = -1;
    let timer = null;
    let speed = 1;
    let range = null;          // {from: Date, to: Date}
    let anchor = null;         // newest alarm time in the window (default end of the replay period)

    el.innerHTML = `
      <div class="page-head"><div><h2>Activity Replay</h2><p>Replays recorded alarm data only — events appear in the order Camview recorded them. <span class="prov direct">DIRECT</span></p></div></div>
      <section class="card" style="margin-bottom:14px"><div class="card-b">
        <div class="row">
          <div class="seg" role="group" aria-label="Quick period">${QUICK.map(([m, l]) => `<button data-quick="${m}">${l}</button>`).join('')}</div>
          <label class="row tight dim" style="font-size:12px">From <input class="input" type="datetime-local" id="rp-from"></label>
          <label class="row tight dim" style="font-size:12px">To <input class="input" type="datetime-local" id="rp-to"></label>
          <button class="btn primary" id="rp-load">${icon('refresh', 's')} Load period</button>
          <span class="grow"></span>
          <span class="muted" id="rp-info" style="font-size:12px"></span>
        </div>
      </div></section>
      <section class="card" style="margin-bottom:14px"><div class="card-b">
        <div class="row" style="margin-bottom:10px">
          <button class="btn" id="rp-prev" aria-label="Previous event">${icon('left', 's')} Previous</button>
          <button class="btn primary" id="rp-play" aria-label="Play">${icon('play', 's')} Play</button>
          <button class="btn" id="rp-next" aria-label="Next event">Next ${icon('right', 's')}</button>
          <div class="seg" role="group" aria-label="Speed">${[1, 2, 5].map((s) => `<button data-speed="${s}" class="${s === 1 ? 'on' : ''}">${s}×</button>`).join('')}</div>
          <span class="grow"></span>
          <span class="num" id="rp-pos" aria-live="polite"></span>
        </div>
        <div id="rp-bar" style="position:relative;height:28px;border-radius:6px;background:var(--panel-3);overflow:hidden" aria-label="Timeline"></div>
        <div class="row muted" style="font-size:11px;margin-top:4px"><span id="rp-start"></span><span class="grow"></span><span id="rp-end"></span></div>
      </div></section>
      <section class="card"><div class="card-h"><h3>${icon('clock')} Events</h3><div class="actions muted" style="font-size:11.5px">Click an event to open its investigation</div></div>
        <div id="rp-list" class="list" style="max-height:60vh;overflow:auto">${skeleton(6, 22)}</div></section>`;

    const stop = () => { clearTimeout(timer); timer = null; $('#rp-play', el).innerHTML = `${icon('play', 's')} Play`; };
    ctx.onCleanup(stop);

    const setRangeInputs = () => {
      $('#rp-from', el).value = toLocalInput(range.from);
      $('#rp-to', el).value = toLocalInput(range.to);
    };

    const paintBar = () => {
      const bar = $('#rp-bar', el);
      const span = range.to - range.from || 1;
      bar.innerHTML = events.map((e, i) => {
        const x = Math.max(0, Math.min(100, ((e.t - range.from) / span) * 100));
        const color = { critical: 'var(--st-critical)', high: 'var(--st-serious)', medium: 'var(--st-warning)' }[e.a.priority] || 'var(--st-neutral)';
        return `<span data-tick="${i}" title="${esc(fmt.time(e.a.firstInstance))} · ${esc(e.a.cameraCode)} · ${esc(e.a.priority)}" style="position:absolute;left:${x}%;top:4px;bottom:4px;width:3px;border-radius:2px;background:${color};opacity:${i <= cur ? 1 : 0.35};cursor:pointer"></span>`;
      }).join('') + (cur >= 0 ? `<span style="position:absolute;left:calc(${Math.max(0, Math.min(100, ((events[cur].t - range.from) / span) * 100))}% - 1px);top:0;bottom:0;width:2px;background:var(--accent)" aria-hidden="true"></span>` : '');
      $('#rp-start', el).textContent = fmt.dt(range.from.toISOString());
      $('#rp-end', el).textContent = fmt.dt(range.to.toISOString());
    };

    const paintList = () => {
      const list = $('#rp-list', el);
      if (!events.length) {
        list.innerHTML = empty('No alarms in this period', 'Choose another period — only recorded alarms can be replayed.', 'clock');
        return;
      }
      list.innerHTML = events.map((e, i) => `<a class="li ${i === cur ? 'rp-cur' : ''}" href="${investigateHref(e.a)}" data-ev="${i}"
          style="${i === cur ? 'background:var(--accent-soft);box-shadow:inset 3px 0 0 var(--accent)' : i > cur && cur >= 0 ? 'opacity:.55' : ''}">
          <span class="num nowrap" style="min-width:92px;flex-shrink:0">${esc(fmt.time(e.a.firstInstance))}</span>
          <div class="grow"><div class="t1"><span class="mono">${esc(e.a.cameraCode)}</span><span class="dim" style="font-weight:500">· ${esc(e.a.alarmTypeName)}</span></div>
            <div class="t2"><span class="mono">${esc(e.a.alarmId)}</span> · ${esc((e.a.context?.path || []).slice(1, 4).map((n) => n.code).join(' / ') || 'no context')}${e.a.totalTimesReported > 1 ? ` · ${e.a.totalTimesReported} reports` : ''}</div></div>
          <div class="row tight">${priorityBadge(e.a.priority)}${stateBadge(e.a.lastActionType, e.a.lastActionLabel)}</div></a>`).join('');
    };

    const paintPos = () => {
      $('#rp-pos', el).textContent = events.length ? `${cur < 0 ? 0 : cur + 1} / ${events.length}${cur >= 0 ? ' · ' + fmt.time(events[cur].a.firstInstance) : ''}` : '';
    };

    const goTo = (i, { scroll = true } = {}) => {
      if (!events.length) return;
      cur = Math.max(0, Math.min(events.length - 1, i));
      paintList(); paintBar(); paintPos();
      if (scroll) el.querySelector(`[data-ev="${cur}"]`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    };

    const tick = () => {
      if (cur >= events.length - 1) { stop(); return; }
      goTo(cur + 1);
      timer = setTimeout(tick, 1200 / speed);
    };

    const load = async () => {
      stop();
      const from = new Date($('#rp-from', el).value);
      const to = new Date($('#rp-to', el).value);
      if (isNaN(from) || isNaN(to) || from >= to) { toast('Choose a valid period (From before To)', 'warning'); return; }
      range = { from, to };
      $('#rp-list', el).innerHTML = skeleton(6, 22);
      try {
        const out = [];
        let page = 1, totalPages = 1, truncated = false;
        do {
          const r = await api.get('/api/alarms', { projectId: pid, from: from.toISOString(), to: to.toISOString(), sort: 'firstInstance', dir: 'asc', size: '200', page: String(page) });
          if (ctx.isStale()) return;
          out.push(...r.items);
          totalPages = r.totalPages;
          page += 1;
          if (out.length >= MAX_EVENTS) { truncated = page <= totalPages; break; }
        } while (page <= totalPages);
        events = out.slice(0, MAX_EVENTS)
          .map((a) => ({ a, t: new Date(a.firstInstance || a.lastInstance) }))
          .filter((e) => !isNaN(e.t))
          .sort((x, y) => x.t - y.t);
        cur = -1;
        $('#rp-info', el).textContent = `${events.length} recorded alarm(s) in this period${truncated ? ` — showing the first ${MAX_EVENTS}` : ''}`;
        paintList(); paintBar(); paintPos();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#rp-list', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    // Default period: the last 15 minutes before the newest alarm in the monitored window.
    try {
      const newest = await api.get('/api/alarms', { projectId: pid, sort: 'firstInstance', dir: 'desc', size: '1' });
      if (ctx.isStale()) return;
      anchor = newest.items[0] ? new Date(newest.items[0].firstInstance || newest.items[0].lastInstance) : new Date();
    } catch { anchor = new Date(); }
    const fromQ = ctx.query.from ? new Date(ctx.query.from) : null;
    const toQ = ctx.query.to ? new Date(ctx.query.to) : null;
    range = fromQ && toQ && !isNaN(fromQ) && !isNaN(toQ) ? { from: fromQ, to: toQ }
      : { from: new Date(anchor.getTime() - 15 * 60000), to: new Date(anchor.getTime() + 60000) };
    setRangeInputs();

    ctx.onCleanup(delegate(el, 'click', '[data-quick]', (e, b) => {
      const end = new Date(Math.max(anchor.getTime(), Date.now()));
      range = { from: new Date(end.getTime() - (+b.dataset.quick) * 60000), to: end };
      setRangeInputs();
      ctx.setQuery({ from: range.from.toISOString(), to: range.to.toISOString() });
      load();
    }));
    $('#rp-load', el).addEventListener('click', () => { ctx.setQuery({ from: new Date($('#rp-from', el).value).toISOString(), to: new Date($('#rp-to', el).value).toISOString() }); load(); });
    $('#rp-play', el).addEventListener('click', () => {
      if (timer) { stop(); return; }
      if (!events.length) return;
      if (cur >= events.length - 1) cur = -1;
      $('#rp-play', el).innerHTML = `${icon('pause', 's')} Pause`;
      tick();
    });
    $('#rp-next', el).addEventListener('click', () => { stop(); goTo(cur + 1); });
    $('#rp-prev', el).addEventListener('click', () => { stop(); goTo(cur - 1); });
    ctx.onCleanup(delegate(el, 'click', '[data-speed]', (e, b) => {
      speed = +b.dataset.speed;
      $$('[data-speed]', el).forEach((x) => x.classList.toggle('on', x === b));
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-tick]', (e, t) => { stop(); goTo(+t.dataset.tick); }));
    const key = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'A'].includes(document.activeElement.tagName)) return;
      if (e.key === 'ArrowRight') { stop(); goTo(cur + 1); }
      if (e.key === 'ArrowLeft') { stop(); goTo(cur - 1); }
      if (e.key === ' ') { e.preventDefault(); $('#rp-play', el).click(); }
    };
    document.addEventListener('keydown', key);
    ctx.onCleanup(() => document.removeEventListener('keydown', key));
    await load();
  },
};
