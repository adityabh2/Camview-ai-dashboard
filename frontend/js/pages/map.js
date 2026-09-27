// pages/map.js — OPERATIONS MAP (V2): where are the alerts, which centres need attention?
//   Positions are never invented: a centre is placed where an administrator put it, otherwise around its
//   city (looked up once on OpenStreetMap by the server, "approximate (city)"), otherwise it is listed under
//   "Not on the map". Leaflet is loaded on demand; if it cannot load, the same data is shown as a board.

import * as api from '../core/api.js';
import { on, setProject } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { css } from '../core/charts.js';
import { esc, icon, fmt, kpi, empty, errorBox, skeleton, toast, delegate, morph, $, $$ } from '../core/ui.js';

const LEAFLET_JS = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js';
const LEAFLET_CSS = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css';
const INDIA = { center: [22.6, 79.0], zoom: 5 };
const RANGES = [['today', 'Today'], ['24h', 'Last 24 h'], ['window', 'All loaded']];
const FILTERS = [['all', 'All'], ['alarm', 'Alarm'], ['warning', 'Warning'], ['ok', 'OK'], ['offline', 'Cameras offline']];
const STATUS = { alarm: ['ALARM', 'alert', '--st-critical'], warning: ['WARNING', 'bang', '--st-warning'], ok: ['OK', 'check', '--st-good'] };
const REASONS = {
  no_city: 'Camview sent no city for this centre',
  no_centre: 'Cameras without a centre code',
  not_found: 'City not found on OpenStreetMap',
  failed: 'City lookup failed — retried later',
  pending: 'City is being located…',
  disabled: 'City lookup is switched off on this server',
};

let leafletLoading = null;
function loadLeaflet() {
  if (window.L) return Promise.resolve(window.L);
  if (leafletLoading) return leafletLoading;
  leafletLoading = new Promise((resolve) => {
    if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
      const l = document.createElement('link');
      l.rel = 'stylesheet'; l.href = LEAFLET_CSS;
      document.head.appendChild(l);
    }
    const s = document.createElement('script');
    s.src = LEAFLET_JS;
    const timer = setTimeout(() => resolve(window.L || null), 10000);
    s.onload = () => { clearTimeout(timer); resolve(window.L || null); };
    s.onerror = () => { clearTimeout(timer); s.remove(); leafletLoading = null; resolve(null); };
    document.head.appendChild(s);
  });
  return leafletLoading;
}

const alertsHref = (p) => `#/alerts?status=pending&centre=${encodeURIComponent(p.code || '')}&range=all`;
const boardHref = (p) => `#/monitoring?tab=centres&centre=${encodeURIComponent(p.code || '')}`;
const precisionLabel = (p) => (p.precision === 'admin' ? 'Position set by an administrator' : p.precision === 'city' ? 'Approximate (city)' : 'Not on the map');

export default {
  async render(el, ctx) {
    setTitle('Operations map', 'where alerts happen · centre status');
    let range = RANGES.some(([k]) => k === ctx.query.range) ? ctx.query.range : 'window';
    let filter = FILTERS.some(([k]) => k === ctx.query.filter) ? ctx.query.filter : 'all';
    let search = ctx.query.search || '';
    let data = null, L = null, libTried = false, map = null, layer = null, fitted = false, loading = false, again = false;
    let placing = null;                    // centre key an administrator is placing
    const markers = new Map();             // point key -> circle marker

    el.innerHTML = `
      <div class="page-head"><div><h2>Operations map</h2><p id="mp-fresh">Loading…</p></div></div>
      <div class="kpis" id="mp-kpis">${skeleton(1, 58)}</div>
      <div class="mp-wrap">
        <section class="card mp-mapcard">
          <div class="mp-placing" id="mp-placing" hidden></div>
          <div class="mp-map" id="mp-map" role="region" aria-label="Map of centres">${skeleton(6, 40)}</div>
          <div class="mp-board" id="mp-board" hidden></div>
        </section>
        <aside class="card mp-panel" aria-label="Map filters and lists">
          <div class="mp-sec">
            <input class="input mp-search" id="mp-search" type="search" data-page-search placeholder="Search centre, code or city" value="${esc(search)}" aria-label="Search centres">
            <div class="seg mp-range" role="group" aria-label="Alert range">${RANGES.map(([k, l]) => `<button data-range="${k}" class="${k === range ? 'on' : ''}" aria-pressed="${k === range}">${l}</button>`).join('')}</div>
            <div class="mp-chips" role="group" aria-label="Filter centres">${FILTERS.map(([k, l]) => `<button class="chip ${k === filter ? 'on' : ''}" data-filter="${k}" aria-pressed="${k === filter}">${l}<span class="n" data-fn="${k}">·</span></button>`).join('')}</div>
          </div>
          <div class="mp-sec" id="mp-legend"></div>
          <div class="mp-sec" id="mp-admin" hidden></div>
          <div class="mp-sec"><h4>${icon('building', 's')} Cities</h4><div id="mp-cities">${skeleton(4, 22)}</div></div>
          <div class="mp-sec"><h4>${icon('info', 's')} Not on the map <span class="muted" id="mp-off-n"></span></h4><div id="mp-off"></div></div>
        </aside>
      </div>`;

    // ------------------------------------------------------------------ data
    const load = async () => {
      if (loading) { again = true; return; }
      loading = true;
      try {
        const d = await api.get('/api/map', { range });
        if (ctx.isStale()) return;
        data = d;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        if (!data) {
          $('#mp-map', el).innerHTML = errorBox(e);
          $('#mp-kpis', el).innerHTML = '';
          $('[data-retry]', el)?.addEventListener('click', load);
        } else toast(`Map refresh failed — showing last data. ${e.message}`, 'error');
      } finally {
        loading = false;
        if (again && !ctx.isStale()) { again = false; load(); }
      }
    };

    const matches = (p) => {
      if (filter === 'offline' ? !p.counts.camerasOffline : filter !== 'all' && p.status !== filter) return false;
      if (!search) return true;
      const s = search.toLowerCase();
      return [p.code, p.name, p.city, p.state, p.projectCode].some((v) => String(v || '').toLowerCase().includes(s));
    };

    const paint = () => {
      const t = data.totals;
      const fr = Object.values(data.freshness || {});
      const worst = fr.find((f) => f.state === 'disconnected') || fr.find((f) => f.state === 'delayed') || fr[0];
      const last = fr.map((f) => f.lastSuccessAt).filter(Boolean).sort().pop();
      const g = data.geocoding || {};
      $('#mp-fresh', el).innerHTML = `${worst ? `<span class="dot ${worst.state === 'live' ? 'live' : esc(worst.state)}" style="display:inline-block;vertical-align:middle"></span> ` : ''}updated ${last ? fmt.time(last) : '—'}
        · ${fmt.n(data.points.length)} centre${data.points.length === 1 ? '' : 's'}, ${fmt.n(data.located)} on the map
        ${g.pending ? ` · ${icon('refresh', 's')} locating cities on OpenStreetMap: ${fmt.n(g.resolved)} of ${fmt.n(g.total)} done` : ''}`;
      morph($('#mp-kpis', el), [
        kpi({ label: 'Centres in alarm', value: data.counts.alarm, accent: data.counts.alarm ? 'critical' : '', icon: 'alert', title: data.rule }),
        kpi({ label: 'Centres warning', value: data.counts.warning, accent: data.counts.warning ? 'warning' : '', icon: 'bang', title: data.rule }),
        kpi({ label: 'Centres OK', value: data.counts.ok, accent: 'good', icon: 'check' }),
        kpi({ label: 'Cameras offline', value: t.camerasOffline, accent: t.camerasOffline ? 'critical' : '', icon: 'camera', href: '#/monitoring?tab=health', sub: `${fmt.n(t.cameras)} cameras seen` }),
        kpi({ label: 'Alerts today', value: t.today, icon: 'zap', href: '#/alerts' }),
      ].join(''));
      FILTERS.forEach(([k]) => {
        const n = $(`[data-fn="${k}"]`, el);
        if (n) n.textContent = data.points.filter((p) => (k === 'all' ? true : k === 'offline' ? p.counts.camerasOffline : p.status === k)).length;
      });
      paintLegend();
      paintAdmin();
      paintCities();
      paintOff();
      if (map) paintMarkers(); else if (libTried) paintBoard();
    };

    const paintLegend = () => {
      $('#mp-legend', el).innerHTML = `
        <div class="mp-legend">
          ${Object.entries(STATUS).map(([k, [lab, ic]]) => `<span><i class="mp-dot ${k}"></i>${icon(ic, 's')} ${lab}</span>`).join('')}
          <span><i class="mp-dot ring"></i> camera offline</span>
          <span><i class="mp-dot approx"></i> approximate (city)</span>
        </div>
        <div class="muted mp-note">${icon('info', 's')} Circle size = alerts in the selected range. Positions are approximate (city level) unless set by an administrator. ${esc(data.rule)}</div>
        ${map || !libTried ? '' : '<div class="muted mp-note">Map tiles could not be loaded (offline?) — the same data is shown as a board.</div>'}`;
    };

    const placeable = () => data.points.filter((p) => !p.noCentre);
    const paintAdmin = () => {
      const box = $('#mp-admin', el);
      if (!data.canEdit || !map) { box.hidden = true; return; }
      box.hidden = false;
      const opts = placeable().map((p) => `<option value="${esc(p.key)}" ${placing === p.key ? 'selected' : ''}>${esc(p.code)} · ${esc(p.name)}${p.precision === 'admin' ? ' (set)' : ''}</option>`).join('');
      const cur = placing && data.points.find((p) => p.key === placing);
      morph(box, `<h4>${icon('edit', 's')} Set position</h4>
        <div class="row mp-admin-row"><select class="select" id="mp-place-sel" aria-label="Centre to place">${opts}</select>
          <button class="btn sm ${placing ? 'primary' : ''}" id="mp-place-go">${placing ? 'Click the map…' : 'Place on map'}</button></div>
        ${cur && cur.precision === 'admin' ? `<button class="btn sm ghost" data-clear="${esc(cur.key)}">Remove the administrator's position</button>` : ''}
        <div class="muted mp-note">Pick a centre, then click its exact location on the map. Administrator positions win over city positions.</div>`);
    };

    const paintCities = () => {
      const shown = data.points.filter(matches);
      const rows = data.cities.map((c) => {
        const ps = shown.filter((p) => p.cityKey === c.key);
        if (!ps.length) return '';
        const pend = ps.reduce((s, p) => s + p.counts.pending, 0);
        const st = ps.some((p) => p.status === 'alarm') ? 'alarm' : ps.some((p) => p.status === 'warning') ? 'warning' : 'ok';
        const located = ps.some((p) => p.lat != null);
        return `<button class="mp-city ${st}" data-city="${esc(c.key)}" ${located ? '' : 'disabled'} title="${located ? 'Zoom to this city' : esc(REASONS[c.geocode] || 'Not on the map')}" data-key="${esc(c.key)}">
          <i class="mp-dot ${st}"></i><span class="nm">${esc(c.label)}</span>
          <span class="ct">${fmt.n(ps.length)} centre${ps.length === 1 ? '' : 's'}${pend ? ` · <b>${fmt.n(pend)}</b> pending` : ''}${c.camerasOffline ? ` · <b class="bad">${fmt.n(c.camerasOffline)}</b> offline` : ''}</span></button>`;
      }).join('');
      morph($('#mp-cities', el), rows || `<div class="muted mp-note">${data.cities.length ? 'No city matches the filter.' : 'Camview sent no city for these centres.'}</div>`);
    };

    const paintOff = () => {
      const off = data.points.filter((p) => p.lat == null && matches(p));
      $('#mp-off-n', el).textContent = `(${off.length})`;
      morph($('#mp-off', el), off.length ? off.map((p) => `<div class="mp-off" data-key="${esc(p.key)}">
          <i class="mp-dot ${esc(p.status)}"></i>
          <div class="mp-off-t"><div><b class="mono">${esc(p.code || '—')}</b> ${esc(p.name && p.name !== p.code ? p.name : '')}</div>
            <div class="muted">${esc(p.projectCode)}${p.city ? ` · ${esc(p.city)}` : ''} · ${esc(REASONS[p.reason] || 'No position')} · ${fmt.n(p.counts.pending)} pending</div></div>
          ${data.canEdit && map && !p.noCentre ? `<button class="btn sm ghost" data-place="${esc(p.key)}">Place</button>` : ''}
          ${p.code ? `<a class="btn sm ghost" href="${esc(alertsHref(p))}">Alerts</a>` : ''}</div>`).join('')
        : `<div class="muted mp-note">${data.points.length ? 'Every centre in this view is on the map.' : 'No centres yet.'}</div>`);
    };

    // ------------------------------------------------------------------ popup / board tiles
    const popupHtml = (p) => {
      const [lab, ic] = STATUS[p.status];
      const c = p.counts;
      return `<div class="mp-pop">
        <div class="mp-pop-h"><span class="mp-st ${esc(p.status)}">${icon(ic, 's')} ${lab}</span><span class="mono">${esc(p.code || '')}</span></div>
        <div class="mp-pop-n">${esc(p.name || p.code)}</div>
        <div class="muted">${esc([p.city, p.state].filter(Boolean).join(', ') || 'No city')} · ${esc(p.projectCode)}</div>
        <div class="mp-pop-c">
          <span><b>${fmt.n(c.today)}</b> today</span><span><b>${fmt.n(c.pending)}</b> pending</span>
          ${c.critical ? `<span class="bad"><b>${fmt.n(c.critical)}</b> critical</span>` : ''}
          <span><b>${fmt.n(c.valid)}</b> valid</span>
          <span><b>${fmt.n(c.cameras)}</b> cam${c.camerasOffline ? ` · <b class="bad">${fmt.n(c.camerasOffline)} offline</b>` : ''}${c.syncFailed ? ` · <b class="bad">${fmt.n(c.syncFailed)} sync failed</b>` : ''}</span>
        </div>
        ${p.topTypes.length ? `<div class="mp-pop-t">${p.topTypes.map((x) => `${esc(x.name)} <b>${fmt.n(x.count)}</b>`).join(' · ')}</div>` : '<div class="muted">No alerts in this range</div>'}
        ${p.lastAlarmAt ? `<div class="muted">Last alert ${esc(fmt.rel(p.lastAlarmAt))}</div>` : ''}
        <div class="muted mp-pop-p">${icon(p.precision === 'admin' ? 'check' : 'info', 's')} ${esc(precisionLabel(p))}</div>
        <div class="mp-pop-a">
          ${p.code ? `<a class="btn sm primary" href="${esc(alertsHref(p))}">Open alerts</a><a class="btn sm" href="${esc(boardHref(p))}" data-project="${esc(p.projectId)}">Centre board</a>` : ''}
          ${data.canEdit && !p.noCentre ? `<button class="btn sm ghost" data-place="${esc(p.key)}">Move</button>` : ''}
        </div></div>`;
    };

    const paintBoard = () => {
      const box = $('#mp-board', el);
      $('#mp-map', el).hidden = true;
      box.hidden = false;
      const shown = data.points.filter(matches);
      const groups = new Map();
      shown.forEach((p) => {
        const k = p.city ? [p.city, p.state].filter(Boolean).join(', ') : 'No city from Camview';
        if (!groups.has(k)) groups.set(k, []);
        groups.get(k).push(p);
      });
      morph(box, shown.length ? [...groups.entries()].map(([city, ps]) => `<div class="mp-bgroup" data-key="g:${esc(city)}">
          <h4>${icon('building', 's')} ${esc(city)} <span class="muted">${fmt.n(ps.length)}</span></h4>
          <div class="health-grid">${ps.map((p) => {
            const [lab, ic] = STATUS[p.status];
            return `<a class="htile ${esc(p.status)}" data-key="${esc(p.key)}" href="${esc(p.code ? alertsHref(p) : '#/alerts')}">
              <div class="ht-top"><span class="st">${icon(ic, 's')} ${lab}</span><span class="ht-code mono">${esc(p.code || '')}</span></div>
              <div class="ht-name">${esc(p.name || p.code)}</div>
              <div class="ht-stats"><span><b>${fmt.n(p.counts.today)}</b> today</span><span><b>${fmt.n(p.counts.pending)}</b> pending</span>
                <span><b>${fmt.n(p.counts.cameras)}</b> cam${p.counts.camerasOffline ? ` · <b class="bad">${fmt.n(p.counts.camerasOffline)} offline</b>` : ''}</span></div>
              <div class="ht-foot">${p.topTypes.length ? esc(p.topTypes.map((x) => x.name).join(' · ')) : '<span class="muted">no alerts in this range</span>'}</div></a>`;
          }).join('')}</div></div>`).join('')
        : empty('No centres', filter !== 'all' || search ? 'No centre matches the filter.' : 'No alerts have arrived from any centre you may see.', 'map'));
    };

    // ------------------------------------------------------------------ map
    const color = (p) => css(STATUS[p.status][2]) || '#888';
    const radius = (p) => Math.min(22, 5 + 2 * Math.sqrt(p.counts.alerts || 0));
    const style = (p) => ({
      radius: radius(p),
      color: p.counts.camerasOffline ? (css('--text') || '#fff') : color(p),
      weight: p.counts.camerasOffline ? 3 : 1.5,
      dashArray: p.precision === 'city' ? '4 3' : null,
      fillColor: color(p),
      fillOpacity: p.precision === 'city' ? 0.45 : 0.7,
      opacity: 0.95,
    });

    const paintMarkers = () => {
      const keep = new Set();
      data.points.forEach((p) => {
        if (p.lat == null || !matches(p)) return;
        keep.add(p.key);
        let m = markers.get(p.key);
        if (!m) {
          m = L.circleMarker([p.lat, p.lng], style(p)).bindPopup('', { maxWidth: 300, minWidth: 220 });
          m.bindTooltip('', { direction: 'top', offset: [0, -6] });
          m.addTo(layer);
          markers.set(p.key, m);
        } else {
          m.setLatLng([p.lat, p.lng]);
          m.setStyle(style(p));
          m.setRadius(radius(p));
          if (!layer.hasLayer(m)) m.addTo(layer);
        }
        m._pt = p;
        m.setPopupContent(popupHtml(p));
        m.setTooltipContent(`${esc(p.code || '')} · ${esc(STATUS[p.status][0])}${p.counts.pending ? ` · ${fmt.n(p.counts.pending)} pending` : ''}`);
        if (p.status === 'alarm') m.bringToFront();
      });
      markers.forEach((m, k) => {
        if (!keep.has(k)) { layer.removeLayer(m); if (!data.points.some((p) => p.key === k && p.lat != null)) markers.delete(k); }
      });
      if (!fitted && data.bounds) {
        fitted = true;
        const [[s, w], [n, e]] = data.bounds;
        if (s === n && w === e) map.setView([s, w], 12); else map.fitBounds([[s, w], [n, e]], { padding: [30, 30], maxZoom: 12 });
      }
      $('#mp-empty', el)?.remove();
      if (!data.located) {
        const note = document.createElement('div');
        note.id = 'mp-empty'; note.className = 'mp-overlay';
        const g = data.geocoding || {};
        note.innerHTML = g.pending
          ? `<span class="spin" aria-hidden="true"></span> Finding the positions of ${g.pending} cit${g.pending === 1 ? 'y' : 'ies'}… the centres appear in a few seconds.`
          : `${icon('info', 's')} No centre has a position yet. ${data.canEdit ? 'Use “Set position” to place one.' : 'Positions come from the centres’ cities or from an administrator.'}`;
        $('#mp-map', el).appendChild(note);
      }
    };

    const restyle = () => { if (map && data) markers.forEach((m) => m._pt && m.setStyle(style(m._pt))); };

    const initMap = () => {
      const box = $('#mp-map', el);
      box.innerHTML = '';
      map = L.map(box, { zoomControl: true, worldCopyJump: false, preferCanvas: false }).setView(INDIA.center, INDIA.zoom);
      // Base map: OpenStreetMap; if its tiles are blocked on this network (proxy, referrer rules), switch once to
      // CARTO's OSM-based tiles so the map never stays blank.
      const osmAttr = '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
      let tiles = L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 18, subdomains: 'abc', attribution: osmAttr, referrerPolicy: 'strict-origin-when-cross-origin', crossOrigin: false,
      }).addTo(map);
      let tileErrors = 0, tileOk = 0, switched = false;
      tiles.on('tileload', () => { tileOk += 1; });
      tiles.on('tileerror', () => {
        tileErrors += 1;
        if (switched || tileOk > 0 || tileErrors < 4) return;
        switched = true;
        map.removeLayer(tiles);
        tiles = L.tileLayer('https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png', {
          maxZoom: 19, subdomains: 'abcd', referrerPolicy: 'strict-origin-when-cross-origin',
          attribution: `${osmAttr} &copy; <a href="https://carto.com/attributions" target="_blank" rel="noopener">CARTO</a>`,
        }).addTo(map);
        tiles.bringToBack();
      });
      layer = L.layerGroup().addTo(map);
      map.on('click', (e) => { if (placing) savePosition(placing, e.latlng); });
      const ro = new ResizeObserver(() => map && map.invalidateSize());
      ro.observe(box);
      ctx.onCleanup(() => { ro.disconnect(); map.remove(); map = null; });
    };

    // ------------------------------------------------------------------ admin placement
    const startPlacing = (key) => {
      placing = key;
      const p = data.points.find((x) => x.key === key);
      map?.closePopup();
      $('#mp-map', el).classList.add('placing');
      const b = $('#mp-placing', el);
      b.hidden = false;
      b.innerHTML = `${icon('edit', 's')} Click the map where <b>${esc(p?.code || '')}</b> ${esc(p?.name || '')} is. <button class="btn sm ghost" id="mp-place-cancel">Cancel</button>`;
      paintAdmin();
    };
    const stopPlacing = () => {
      placing = null;
      $('#mp-map', el).classList.remove('placing');
      $('#mp-placing', el).hidden = true;
      if (data) paintAdmin();
    };
    const savePosition = async (key, ll) => {
      const p = data.points.find((x) => x.key === key);
      try {
        await api.put('/api/map/places', { key, lat: +ll.lat.toFixed(6), lng: +ll.lng.toFixed(6), label: p ? `${p.code} · ${p.name}` : null });
        toast(`Position saved for ${p?.code || 'the centre'}`, 'success');
        stopPlacing();
        load();
      } catch (e) { toast(e.message, 'error'); }
    };
    const clearPosition = async (key) => {
      try {
        await api.put('/api/map/places', { key, clear: true });
        toast('Administrator position removed', 'success');
        load();
      } catch (e) { toast(e.message, 'error'); }
    };

    // ------------------------------------------------------------------ events
    const repaintFiltered = () => { ctx.setQuery({ filter: filter === 'all' ? '' : filter, search }); if (data) paint(); };
    ctx.onCleanup(delegate(el, 'click', '[data-filter]', (e, b) => {
      filter = b.dataset.filter;
      $$('[data-filter]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
      repaintFiltered();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-range]', (e, b) => {
      range = b.dataset.range;
      ctx.setQuery({ range: range === 'window' ? '' : range });
      $$('[data-range]', el).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
      load();
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-city]', (e, b) => {
      if (!map || !data) return;
      const ps = data.points.filter((p) => p.cityKey === b.dataset.city && p.lat != null);
      if (!ps.length) return;
      const lats = ps.map((p) => p.lat), lngs = ps.map((p) => p.lng);
      map.fitBounds([[Math.min(...lats), Math.min(...lngs)], [Math.max(...lats), Math.max(...lngs)]], { padding: [40, 40], maxZoom: 13 });
      if (window.innerWidth < 900) $('#mp-map', el).scrollIntoView({ behavior: 'smooth', block: 'center' });
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-place]', (e, b) => startPlacing(b.dataset.place)));
    ctx.onCleanup(delegate(el, 'click', '[data-clear]', (e, b) => clearPosition(b.dataset.clear)));
    ctx.onCleanup(delegate(el, 'click', '#mp-place-cancel', () => stopPlacing()));
    ctx.onCleanup(delegate(el, 'click', '#mp-place-go', () => {
      if (placing) stopPlacing(); else { const v = $('#mp-place-sel', el)?.value; if (v) startPlacing(v); }
    }));
    ctx.onCleanup(delegate(el, 'change', '#mp-place-sel', (e, s) => { if (placing) startPlacing(s.value); else paintAdmin(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-project]', (e, a) => { setProject(a.dataset.project); }));
    let st;
    $('#mp-search', el).addEventListener('input', (e) => { clearTimeout(st); st = setTimeout(() => { search = e.target.value.trim(); repaintFiltered(); }, 200); });
    const onKey = (e) => { if (e.key === 'Escape' && placing) stopPlacing(); };
    document.addEventListener('keydown', onKey);
    ctx.onCleanup(() => document.removeEventListener('keydown', onKey));
    window.addEventListener('themechange', restyle);
    ctx.onCleanup(() => window.removeEventListener('themechange', restyle));
    ctx.onCleanup(on('data', () => load()));

    // ------------------------------------------------------------------ start: data and Leaflet in parallel
    const [lib] = await Promise.all([loadLeaflet(), load()]);
    if (ctx.isStale()) return;
    L = lib;
    libTried = true;
    if (L) {
      try { initMap(); } catch (e) { console.error(e); L = null; map = null; }
    }
    if (data) paint();
  },
};
