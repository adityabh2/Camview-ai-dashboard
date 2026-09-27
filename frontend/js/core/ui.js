// ui.js — the Command Center component kit (plain HTML strings + binders).
// Every value that can come from data goes through esc().

export const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

// ------------------------------------------------------------------ icons
const P = {
  command: '<path d="M3 3h7v7H3zM14 3h7v4h-7zM14 11h7v10h-7zM3 14h7v7H3z"/>',
  live: '<circle cx="12" cy="12" r="2.5"/><path d="M16.2 7.8a6 6 0 0 1 0 8.4M7.8 16.2a6 6 0 0 1 0-8.4M19 5a10 10 0 0 1 0 14M5 19A10 10 0 0 1 5 5"/>',
  work: '<path d="M22 12h-6l-2 3h-4l-2-3H2"/><path d="M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.7 4H7.3a2 2 0 0 0-1.8 1.1z"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
  investigate: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m21 21-5.6-5.6M10.5 7.5v6M7.5 10.5h6"/>',
  share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="m8.6 13.5 6.8 4M15.4 6.5l-6.8 4"/>',
  camera: '<path d="M23 7 16 12l7 5z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
  tree: '<rect x="9" y="2" width="6" height="5" rx="1"/><rect x="2" y="17" width="6" height="5" rx="1"/><rect x="16" y="17" width="6" height="5" rx="1"/><path d="M12 7v5M5 17v-3h14v3"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 14l4-4 3 3 5-6"/>',
  history: '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5M12 7v5l3 2"/>',
  report: '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6M8 13h8M8 17h5"/>',
  bell: '<path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
  present: '<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>',
  users: '<path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8"/>',
  key: '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
  building: '<rect x="4" y="2" width="16" height="20" rx="1"/><path d="M9 22v-4h6v4M8 6h.01M12 6h.01M16 6h.01M8 10h.01M12 10h.01M16 10h.01M8 14h.01M12 14h.01M16 14h.01"/>',
  settings: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
  alert: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  zap: '<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  check: '<path d="M20 6 9 17l-5-5"/>',
  x: '<path d="M18 6 6 18M6 6l12 12"/>',
  bang: '<circle cx="12" cy="12" r="9"/><path d="M12 8v4M12 16h.01"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  eye: '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8S1 12 1 12z"/><circle cx="12" cy="12" r="3"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  video: '<path d="M23 7 16 12l7 5z"/><rect x="1" y="5" width="15" height="14" rx="2"/>',
  arrow: '<path d="M5 12h14M13 5l7 7-7 7"/>',
  left: '<path d="m15 18-6-6 6-6"/>', right: '<path d="m9 18 6-6-6-6"/>', down: '<path d="m6 9 6 6 6-6"/>',
  star: '<path d="m12 2 3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z"/>',
  filter: '<path d="M22 3H2l8 9.5V19l4 2v-8.5z"/>',
  download: '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M7 10l5 5 5-5M12 15V3"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4L21 8"/><path d="M21 3v5h-5"/>',
  pause: '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>',
  play: '<path d="M6 3l14 9-14 9z"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M3 6h18M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6M10 11v6M14 11v6M9 6V4h6v2"/>',
  edit: '<path d="M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  external: '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8z"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9"/>',
  menu: '<path d="M3 6h18M3 12h18M3 18h18"/>',
  layers: '<path d="m12 2 10 5-10 5L2 7z"/><path d="m2 17 10 5 10-5M2 12l10 5 10-5"/>',
  map: '<path d="m1 6 7-4 8 4 7-4v16l-7 4-8-4-7 4z"/><path d="M8 2v16M16 6v16"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"/>',
  calendar: '<rect x="3" y="4" width="18" height="18" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
  grid: '<rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 16v-4M12 8h.01"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
  shift: '<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
  rules: '<path d="M4 6h10M4 12h16M4 18h7"/><circle cx="18" cy="6" r="2"/><circle cx="15" cy="18" r="2"/>',
  audit: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>',
  compare: '<path d="M16 3h5v5M4 20 21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21v-1a7 7 0 0 1 14 0v1"/>',
  bookmark: '<path d="M19 21l-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z"/>',
};
export const icon = (name, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" aria-hidden="true">${P[name] || P.info}</svg>`;

// ------------------------------------------------------------------ format
// Display time zone preference (local or UTC) — set on <body data-tz> from Preferences.
const tz = () => (document.body.dataset.tz === 'utc' ? { timeZone: 'UTC' } : {});

export const fmt = {
  time(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit', ...tz() });
  },
  dt(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleString(undefined, { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', ...tz() });
  },
  date(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return isNaN(d) ? '—' : d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric', ...tz() });
  },
  rel(iso) {
    if (!iso) return '';
    const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
    if (isNaN(s)) return '';
    if (s < 45) return 'just now';
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} d ago`;
  },
  dur(min) {
    if (min == null) return '—';
    if (min < 60) return `${Math.round(min)} min`;
    if (min < 1440) return `${Math.floor(min / 60)}h ${Math.round(min % 60)}m`;
    return `${(min / 1440).toFixed(1)} d`;
  },
  n: (v) => (v == null ? '—' : Number(v).toLocaleString()),
  pct: (r) => (r == null ? '—' : `${(r * 100).toFixed(r * 100 >= 10 || r === 0 ? 0 : 1)}%`),
};

// ------------------------------------------------------------------ badges (icon + label, never color alone)
const PRIO_ICON = { critical: 'alert', high: 'bang', medium: 'info', low: 'info' };
export const priorityBadge = (p) => {
  const k = ['critical', 'high', 'medium', 'low'].includes(p) ? p : 'unknown';
  return `<span class="b p-${k}" title="Priority">${icon(PRIO_ICON[k] || 'info')}${esc(p || 'unknown')}</span>`;
};
const STATE_ICON = { 0: 'clock', 1: 'check', 2: 'x', 3: 'bang' };
const STATE_LABEL = { 0: 'Pending', 1: 'Valid', 2: 'Invalid', 3: 'Exception' };
export const stateBadge = (lat, label) => lat == null
  ? '<span class="b outline">Unknown</span>'
  : `<span class="b s-${lat}" title="Camview alarm state (lastActionType ${lat})">${icon(STATE_ICON[lat])}${esc(label || STATE_LABEL[lat])}</span>`;
const REVIEW = { unreviewed: ['Unreviewed', 'clock'], acknowledged: ['Acknowledged', 'eye'], marked_valid: ['Ops: valid', 'check'],
  marked_invalid: ['Ops: invalid', 'x'], marked_exception: ['Ops: exception', 'bang'] };
export const reviewBadge = (s) => {
  const [l, i] = REVIEW[s] || REVIEW.unreviewed;
  return `<span class="b outline" title="Operator decision in Command Center">${icon(i)}${l}</span>`;
};
export const workflowBadge = (state, label) => `<span class="b wf" title="Workflow state">${esc(label || state)}</span>`;
const VIS_ICON = { internal: 'lock', ready_for_review: 'clock', approved: 'check', shared: 'share', withdrawn: 'x', archived: 'x' };
const VIS_LABEL = { internal: 'Internal', ready_for_review: 'Ready for review', approved: 'Approved', shared: 'Shared', withdrawn: 'Withdrawn', archived: 'Archived' };
export const visBadge = (state, label, ack) => {
  const s = state || 'internal';
  return `<span class="b vis-${s}" title="Client visibility">${icon(VIS_ICON[s] || 'lock')}${esc(label || VIS_LABEL[s] || s)}${ack ? ' · ack' : ''}</span>`;
};
export const sevBadge = (sev) => `<span class="b sev-${esc(sev)}">${icon(sev === 'critical' ? 'alert' : sev === 'info' ? 'info' : 'bang')}${esc(sev)}</span>`;
export const evBadge = (ev) => !ev || !ev.count
  ? '<span class="ev none" title="No evidence">—</span>'
  : `<span class="ev" title="Evidence">${ev.images ? `${icon('image', 's')}${ev.images}` : ''}${ev.video ? ` ${icon('video', 's')}` : ''}</span>`;
export const prov = (kind) => `<span class="prov ${kind}" title="${{ direct: 'Direct data from Camview', derived: 'Derived / calculated by Command Center', ai: 'AI-generated', unavailable: 'Not available in the current data' }[kind] || ''}">${kind.toUpperCase()}</span>`;
export const newBadge = () => '<span class="b new">NEW</span>';
export const slaBadge = (sla) => (sla ? `<span class="b outline sla-${sla}" title="Time since raised vs configured target">${icon('clock')}${{ within: 'Within target', approaching: 'Approaching target', attention: 'Attention required' }[sla]}</span>` : '');

export function contextPath(path, { max = 8, unmappedNote = true } = {}) {
  if (!path || !path.length) return '<span class="muted">No context</span>';
  const parts = path.slice(0, max).map((n) => `<span class="${n.unmapped ? 'unmapped' : ''}" title="${esc((n.level || '').toUpperCase())}${n.name && n.name !== n.code ? ' · ' + esc(n.name) : ''}${n.unmapped ? ' · not in master data' : ''}">${esc(n.code)}</span>`);
  const unm = unmappedNote && path.some((n) => n.unmapped) ? ' <span class="prov unavailable" title="Camera not in nomenclature master data">UNMAPPED</span>' : '';
  return `<span class="ctx-path">${parts.join('<span class="sep">/</span>')}${unm}</span>`;
}

// ------------------------------------------------------------------ blocks
export function kpi({ label, value, sub, accent, href, icon: ic, prov: pv, title }) {
  const tag = href ? 'a' : 'div';
  return `<${tag} class="kpi ${accent ? 'accent-' + accent : ''}" data-key="kpi:${esc(label)}" ${href ? `href="${esc(href)}"` : ''} ${title ? `title="${esc(title)}"` : ''}>
    <div class="k-label">${ic ? icon(ic, 's') : ''}${esc(label)} ${pv ? prov(pv) : ''}</div>
    <div class="k-value" data-flash>${value == null ? '—' : esc(typeof value === 'number' ? value.toLocaleString() : value)}</div>
    ${sub ? `<div class="k-sub">${sub}</div>` : ''}
  </${tag}>`;
}

// ------------------------------------------------------------------ morph: update a live DOM tree in place
// Pages re-render as HTML strings. Replacing innerHTML on every live update would drop scroll position,
// focus, open <select> menus, playing video and every thumbnail (whose signed link changes on each
// refresh). morph() reconciles the existing tree with the new HTML instead: nodes are kept and patched,
// children with the same data-key (or id) are matched even when their order changed, and only what is
// different touches the DOM. An element whose text changed and carries data-flash gets a one-off
// highlight (KPI values).
export function morph(el, html) {
  const tpl = document.createElement('template');
  tpl.innerHTML = html;
  morphChildren(el, tpl.content);
  return el;
}

const keyOf = (n) => (n.nodeType === 1 ? n.getAttribute('data-key') || n.id || '' : '');
const same = (a, b) => a.nodeType === b.nodeType && (a.nodeType !== 1 || a.tagName === b.tagName);
const BOOL_PROPS = { checked: 1, selected: 1, disabled: 1 };

function morphChildren(from, to) {
  const old = new Map();
  for (const n of from.childNodes) { const k = keyOf(n); if (k && !old.has(k)) old.set(k, n); }
  const kept = new Set();
  let cur = from.firstChild;
  for (const tn of Array.from(to.childNodes)) {
    const k = keyOf(tn);
    let node = null;
    if (k) { node = old.get(k) || null; if (node && !same(node, tn)) node = null; }
    else if (cur && !keyOf(cur) && !kept.has(cur) && same(cur, tn)) node = cur;
    if (node) {
      if (node === cur) cur = cur.nextSibling;
      else from.insertBefore(node, cur);
      morphNode(node, tn);
      kept.add(node);
    } else {
      from.insertBefore(tn, cur);
      kept.add(tn);
    }
  }
  for (const n of Array.from(from.childNodes)) if (!kept.has(n)) n.remove();
}

function morphNode(node, tn) {
  if (node.nodeType === 3 || node.nodeType === 8) {
    if (node.data !== tn.data) {
      node.data = tn.data;
      const p = node.parentElement;
      if (p && p.hasAttribute('data-flash')) { p.classList.remove('changed'); void p.offsetWidth; p.classList.add('changed'); }
    }
    return;
  }
  if (node.nodeType !== 1) return;
  const tag = node.tagName;
  if (tag === 'CANVAS' || node.hasAttribute('data-morph-skip')) return;   // charts own their canvas
  const focused = document.activeElement === node;
  // attributes
  for (const a of Array.from(node.attributes)) {
    if (!tn.hasAttribute(a.name)) {
      if (focused && (a.name === 'value')) continue;
      node.removeAttribute(a.name);
    }
  }
  for (const a of Array.from(tn.attributes)) {
    if (node.getAttribute(a.name) === a.value) continue;
    if (tag === 'IMG' && a.name === 'src' && node.complete && node.naturalWidth > 0 && stripQuery(node.getAttribute('src')) === stripQuery(a.value)) continue;
    if (focused && a.name === 'value') continue;
    node.setAttribute(a.name, a.value);
  }
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') {
    if (!focused) {
      if (tag === 'SELECT') { morphChildren(node, tn); if (node.value !== tn.value) node.value = tn.value; }
      else if (node.type === 'checkbox' || node.type === 'radio') { if (node.checked !== tn.checked) node.checked = tn.checked; }
      else if (node.value !== tn.value) node.value = tn.value;
    }
    return;
  }
  if (tag === 'VIDEO' || tag === 'AUDIO') return;
  for (const p in BOOL_PROPS) if (p in node && p in tn && node[p] !== tn[p]) node[p] = tn[p];
  morphChildren(node, tn);
}

const stripQuery = (u) => (u || '').split(/[?#]/)[0];

export function card({ title, sub, actions = '', body = '', flush = false, id = '', cls = '' }) {
  return `<section class="card ${cls}" ${id ? `id="${id}"` : ''}>
    ${title ? `<div class="card-h"><div><h3>${title}</h3>${sub ? `<div class="sub">${sub}</div>` : ''}</div><div class="actions">${actions}</div></div>` : ''}
    <div class="card-b ${flush ? 'flush' : ''}">${body}</div></section>`;
}

export const empty = (title, text = '', ic = 'info') => `<div class="empty">${icon(ic)}<div class="e-t">${esc(title)}</div>${text ? `<div>${text}</div>` : ''}</div>`;

export function errorBox(err, { retry = true } = {}) {
  const msg = err?.message || String(err || 'Something went wrong.');
  const title = err?.status === 403 ? 'Access denied' : err?.status === 404 ? 'Not available' : err?.status === 0 ? 'Disconnected' : 'Could not load';
  return `<div class="error-state" role="alert">${icon('alert')}<div class="e-t">${esc(title)}</div><div>${esc(msg)}</div>${retry && err?.status !== 403 && err?.status !== 404 ? '<button class="btn sm" data-retry>Retry</button>' : ''}</div>`;
}

export const skeleton = (rows = 3, h = 16) => `<div class="stack" aria-busy="true">${Array.from({ length: rows }, () => `<div class="skel" style="height:${h}px"></div>`).join('')}</div>`;

export function whyList(reasons) {
  return `<ul class="why">${(reasons || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>`;
}

export function lifecycle(state) {
  const steps = [['NEW', 'Detected'], ['UNDER_REVIEW', 'Review'], ['INVESTIGATING', 'Investigate'], ['READY_FOR_CLIENT', 'Validated'],
    ['READY_FOR_APPROVAL', 'Approval'], ['APPROVED', 'Approved'], ['SHARED', 'Shared'], ['CLIENT_ACKNOWLEDGED', 'Acknowledged']];
  if (state === 'CLOSED') return '<div class="lifecycle"><div class="st done"><span class="n">✓</span>Detected</div><span class="arr">›</span><div class="st cur"><span class="n">■</span>Closed as invalid</div></div>';
  if (state === 'WITHDRAWN') return '<div class="lifecycle"><div class="st done"><span class="n">✓</span>Shared</div><span class="arr">›</span><div class="st cur"><span class="n">↩</span>Withdrawn from client</div></div>';
  const idx = Math.max(0, steps.findIndex((s) => s[0] === state));
  return `<div class="lifecycle" aria-label="Workflow lifecycle">${steps.map((s, i) => `<div class="st ${i < idx ? 'done' : i === idx ? 'cur' : ''}"><span class="n">${i < idx ? '✓' : i + 1}</span>${s[1]}</div>`).join('<span class="arr">›</span>')}</div>`;
}

export function bars(items, { color = 'var(--chart-1)', max, labelFn, onClickAttr } = {}) {
  if (!items || !items.length) return empty('No data');
  const m = max || Math.max(...items.map((i) => i.count), 1);
  return `<div class="bars">${items.map((i) => `<div class="bar-row" ${onClickAttr ? onClickAttr(i) : ''} title="${esc(i.label)}: ${i.count}">
    <span class="lab">${i.sw ? `<span class="sw" style="background:${i.sw}"></span>` : ''}${labelFn ? labelFn(i) : esc(i.label)}</span>
    <span class="bar-track"><span class="bar-fill" style="display:block;width:${Math.max(1, (i.count / m) * 100)}%;background:${i.color || color}"></span></span>
    <span class="num" style="text-align:right">${fmt.n(i.count)}</span></div>`).join('')}</div>`;
}

/** Tiny inline bar chart (e.g. alerts per hour today). Values only, no axes; the title carries the numbers. */
export function spark(values, { w = 120, h = 24, label = 'Alerts per hour today', mark } = {}) {
  const v = (values || []).map((x) => +x || 0);
  if (!v.length) return '';
  const max = Math.max(1, ...v);
  const bw = w / v.length;
  const now = mark ?? new Date().getHours();
  const bars = v.map((x, i) => {
    const bh = x ? Math.max(2, (x / max) * (h - 2)) : 1;
    return `<rect x="${(i * bw + 0.5).toFixed(1)}" y="${(h - bh).toFixed(1)}" width="${Math.max(1, bw - 1.5).toFixed(1)}" height="${bh.toFixed(1)}" rx="1" class="${x ? (i === now ? 'on now' : 'on') : 'off'}"/>`;
  }).join('');
  const peak = v.indexOf(Math.max(...v));
  const title = `${label}: ${v.reduce((s, x) => s + x, 0)} in total${Math.max(...v) ? ` · busiest ${String(peak).padStart(2, '0')}:00 (${v[peak]})` : ''}`;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>${bars}</svg>`;
}

export function legend(items) {
  return `<div class="legend">${items.map((i) => `<span><span class="sw" style="background:${i.color}"></span>${esc(i.label)}${i.value != null ? ` <b class="num">${fmt.n(i.value)}</b>` : ''}</span>`).join('')}</div>`;
}

export const PRIORITY_COLORS = { critical: 'var(--st-critical)', high: 'var(--st-serious)', medium: 'var(--st-warning)', low: 'var(--st-neutral)' };
export const VERDICT_COLORS = { Valid: 'var(--v-valid)', Pending: 'var(--v-pending)', Invalid: 'var(--v-invalid)', Exception: 'var(--v-exception)' };

export function alertItem(a, { compact = false } = {}) {
  const ic = { critical: 'alert', high: 'zap', warning: 'bang', info: 'info' }[a.severity] || 'info';
  return `<div class="alert sev-${esc(a.severity)}" data-alert="${esc(a.id)}">
    <div class="ic">${icon(ic)}</div>
    <div class="grow" style="min-width:0">
      <div class="a-title">${esc(a.title)}</div>
      <div class="a-meta"><span>${esc(a.scope.level.toUpperCase())} · ${esc(a.scope.code)}</span><span>${fmt.rel(a.timestamp)}</span><span>${a.relatedCount} related</span>${prov('derived')}</div>
      ${compact ? '' : whyList(a.reasons.slice(0, 3))}
    </div>
    <div class="a-actions">
      <button class="btn sm ghost" data-why="${esc(a.id)}" aria-label="Why am I seeing this?">Why?</button>
      ${a.action ? `<a class="btn sm" href="${esc(a.action)}">${esc(a.actionLabel || 'Open')}</a>` : ''}
    </div></div>`;
}

// ------------------------------------------------------------------ events
export function delegate(root, event, selector, fn) {
  const h = (e) => {
    const t = e.target.closest(selector);
    if (t && root.contains(t)) fn(e, t);
  };
  root.addEventListener(event, h);
  return () => root.removeEventListener(event, h);
}

export function debounce(fn, ms = 250) {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}

// ------------------------------------------------------------------ table
export function table(el, { columns, rows, sort, dir, onSort, onRow, rowClass, rowKey = (r) => r.alarmId, emptyHtml, selectable, selected, onSelect, hidden, density }) {
  if (hidden && hidden.size) columns = columns.filter((c) => !hidden.has(c.key));
  density = density || document.body.dataset.density || 'comfortable';
  if (!rows.length) {
    el.innerHTML = emptyHtml || empty('No results', 'Nothing matches the current filters.', 'search');
    return;
  }
  const head = `${selectable ? '<th style="width:28px"><input type="checkbox" data-selall aria-label="Select all"></th>' : ''}${columns.map((c) => {
    const s = c.sort && onSort;
    const arrow = s && sort === c.sort ? `<span class="arrow">${dir === 'asc' ? '▲' : '▼'}</span>` : '';
    return `<th class="${s ? 'sortable' : ''} ${c.num ? 'num' : ''}" ${s ? `data-sort="${c.sort}" tabindex="0" aria-sort="${sort === c.sort ? (dir === 'asc' ? 'ascending' : 'descending') : 'none'}"` : ''} scope="col">${esc(c.label)}${arrow}</th>`;
  }).join('')}`;
  const body = rows.map((r, i) => `<tr class="${onRow ? 'link' : ''} ${rowClass ? rowClass(r) : ''} ${selected?.has(rowKey(r)) ? 'sel' : ''}" data-i="${i}" ${onRow ? 'tabindex="0"' : ''}>
    ${selectable ? `<td><input type="checkbox" data-sel="${esc(rowKey(r))}" ${selected?.has(rowKey(r)) ? 'checked' : ''} aria-label="Select ${esc(rowKey(r))}"></td>` : ''}
    ${columns.map((c) => `<td class="${c.num ? 'num' : ''}">${c.render ? c.render(r) : esc(r[c.key])}</td>`).join('')}</tr>`).join('');
  el.innerHTML = `<div class="table-wrap"><table class="t ${density === 'compact' ? 'compact' : ''}"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  if (onSort) {
    $$('th[data-sort]', el).forEach((th) => {
      const go = () => onSort(th.dataset.sort);
      th.addEventListener('click', go);
      th.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    });
  }
  if (onRow) {
    $$('tbody tr', el).forEach((tr) => {
      const go = (e) => { if (e.target.closest('a,button,input,label')) return; onRow(rows[+tr.dataset.i], e); };
      tr.addEventListener('click', go);
      tr.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(e); });
    });
  }
  if (selectable) {
    $$('input[data-sel]', el).forEach((cb) => cb.addEventListener('change', () => onSelect(cb.dataset.sel, cb.checked)));
    $('input[data-selall]', el)?.addEventListener('change', (e) => rows.forEach((r) => onSelect(rowKey(r), e.target.checked)));
  }
}

export function pager(el, { page, totalPages, totalElements, onPage, note, size }) {
  const per = size || (totalPages > 1 ? Math.ceil(totalElements / totalPages) : totalElements) || 0;
  const from = totalElements ? (page - 1) * per + 1 : 0;
  const to = Math.min(totalElements, page * per);
  el.innerHTML = `<div class="pager">${note ? `<span class="muted grow">${esc(note)}</span>` : ''}<span>Showing <b class="num">${fmt.n(from)}–${fmt.n(to)}</b> of <b class="num">${fmt.n(totalElements)}</b></span>
    <button class="btn sm" data-p="${page - 1}" ${page <= 1 ? 'disabled' : ''} aria-label="Previous page">${icon('left', 's')}</button>
    <span class="num">${page} / ${totalPages || 1}</span>
    <button class="btn sm" data-p="${page + 1}" ${page >= totalPages ? 'disabled' : ''} aria-label="Next page">${icon('right', 's')}</button></div>`;
  $$('button[data-p]', el).forEach((b) => b.addEventListener('click', () => onPage(+b.dataset.p)));
}

/** Column chooser: returns a button; `keys` = [{key,label}], `hidden` Set, onChange(hiddenSet). */
export function columnChooser(anchor, { keys, hidden, onChange }) {
  dialog({
    title: `${icon('grid')} Columns`, side: true,
    body: `<p class="muted">Choose which columns to show. Saved for you on this device.</p>${keys.map((c) => `<label class="check" style="margin-bottom:8px"><input type="checkbox" data-col="${esc(c.key)}" ${hidden.has(c.key) ? '' : 'checked'} ${c.locked ? 'disabled' : ''}> ${esc(c.label || c.key)}</label>`).join('')}`,
    actions: [{ label: 'Show all', onClick: ({ close }) => { hidden.clear(); onChange(hidden); close(); } },
      { label: 'Done', kind: 'primary', onClick: ({ el, close }) => {
        $$('[data-col]', el).forEach((cb) => (cb.checked ? hidden.delete(cb.dataset.col) : hidden.add(cb.dataset.col)));
        onChange(hidden); close();
      } }],
  });
}

// ------------------------------------------------------------------ dialogs
let dialogStack = 0;
export function dialog({ title, body = '', actions = [], size = '', side = false, onClose, labelledBy }) {
  const prevFocus = document.activeElement;
  const ov = document.createElement('div');
  ov.className = `overlay ${side ? 'side' : ''}`;
  const id = `dlg-${Date.now()}-${dialogStack++}`;
  ov.innerHTML = `<div class="dialog ${size}" role="dialog" aria-modal="true" aria-labelledby="${labelledBy || id}">
    <div class="d-h"><h2 id="${id}">${title}</h2><span class="grow"></span><button class="btn icon ghost" data-close aria-label="Close">${icon('x')}</button></div>
    <div class="d-b">${body}</div>
    ${actions.length ? `<div class="d-f">${actions.map((a, i) => `<button class="btn ${a.kind || ''}" data-act="${i}" ${a.disabled ? 'disabled' : ''}>${a.label}</button>`).join('')}</div>` : ''}
  </div>`;
  document.body.appendChild(ov);
  const box = ov.querySelector('.dialog');
  const close = (result) => {
    ov.remove();
    document.removeEventListener('keydown', key);
    prevFocus?.focus?.();
    onClose?.(result);
  };
  const key = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); close(); }
    if (e.key === 'Tab') {  // focus trap
      const f = $$('button,a[href],input,select,textarea,[tabindex="0"]', box).filter((x) => !x.disabled && x.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  };
  document.addEventListener('keydown', key);
  ov.addEventListener('mousedown', (e) => { if (e.target === ov) close(); });
  ov.querySelector('[data-close]').addEventListener('click', () => close());
  actions.forEach((a, i) => ov.querySelector(`[data-act="${i}"]`)?.addEventListener('click', () => a.onClick?.({ close, el: box })));
  setTimeout(() => (box.querySelector('[autofocus]') || box.querySelector('input,select,textarea,button:not([data-close])') || box).focus?.(), 30);
  return { el: box, close };
}

export function confirmDialog({ title, message, confirmLabel = 'Confirm', danger = false, requireText = null }) {
  return new Promise((resolve) => {
    let done = false;
    const d = dialog({
      title,
      body: `${message}${requireText ? `<div class="field" style="margin-top:12px"><label>Type <b class="mono">${esc(requireText)}</b> to confirm</label><input class="input" data-req autocomplete="off"></div>` : ''}`,
      actions: [
        { label: 'Cancel', onClick: ({ close }) => { done = true; resolve(false); close(); } },
        { label: confirmLabel, kind: danger ? 'danger' : 'primary', disabled: !!requireText, onClick: ({ close }) => { done = true; resolve(true); close(); } },
      ],
      onClose: () => { if (!done) resolve(false); },
    });
    if (requireText) {
      const inp = d.el.querySelector('[data-req]');
      const btn = d.el.querySelector('[data-act="1"]');
      inp.addEventListener('input', () => { btn.disabled = inp.value.trim() !== requireText; });
    }
  });
}

export function toast(msg, kind = '') {
  const host = document.getElementById('toasts');
  const t = document.createElement('div');
  t.className = `toast ${kind}`;
  t.textContent = msg;
  host.appendChild(t);
  setTimeout(() => t.remove(), kind === 'error' ? 6000 : 3500);
}

export function why(alert) {
  dialog({
    title: `${icon('info')} Why am I seeing this?`,
    side: true,
    body: `<div class="stack">
      <div><div class="a-title" style="font-weight:700;font-size:14px">${esc(alert.title)}</div>
        <div class="muted" style="margin-top:4px">${esc(alert.scope.level.toUpperCase())} · ${esc(alert.scope.code)} · ${fmt.dt(alert.timestamp)} ${prov('derived')}</div></div>
      <div><div class="section-title" style="margin-top:4px">Reasons</div>${whyList(alert.reasons)}</div>
      <div><div class="section-title">Inputs used</div><dl class="kv">${Object.entries(alert.inputs || {}).map(([k, v]) => `<dt>${esc(k)}</dt><dd class="mono">${esc(Array.isArray(v) ? v.join(', ') : v)}</dd>`).join('') || '<dd class="muted">—</dd>'}</dl></div>
      <div><div class="section-title">Related records</div>${alert.related.length ? alert.related.slice(0, 20).map((id) => `<a class="b outline" style="margin:2px" href="#/investigations/${esc(id)}">${esc(id)}</a>`).join('') : '<span class="muted">None</span>'}${alert.relatedCount > 20 ? `<div class="muted">+${alert.relatedCount - 20} more</div>` : ''}</div>
      <div class="banner info">${icon('info')}<div>This alert is <b>derived</b> by Command Center from alarm data you can see. No score or AI is involved — the reasons above are the complete logic. Thresholds are configurable in Settings › Workflow.</div></div>
      ${alert.action ? `<a class="btn primary" href="${esc(alert.action)}">${esc(alert.actionLabel || 'Open')}</a>` : ''}
    </div>`,
  });
}
