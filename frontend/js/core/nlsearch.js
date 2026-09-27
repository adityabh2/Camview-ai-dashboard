// nlsearch.js — plain-language search → Alerts queue filters. Deterministic (no AI, no key needed).
//
//   "critical pending mobile phone at 9111 today"  → priority critical · Pending · Mobile Phone Detected · Centre 9111 · Today
//   "valid lab activity in indore yesterday"       → Valid · Lab Activity · City Indore · Yesterday
//   "camera offline events last 2 hours"           → Camera status events · Last 2 hours
//   "server room 1000215_21"                       → Server Room Activity · Camera 1000215_21
//
// The vocabulary (types, centres, cities, cameras, exams, clients, projects) comes from
// GET /api/search/vocabulary, built from the alerts the user may see. Words that match nothing
// become free text (`search`). The result is always shown as removable chips before searching.

import * as api from './api.js';

// ------------------------------------------------------------------ vocabulary (cached per page view)
let vocabCache = null;
let vocabAt = 0;
export async function loadVocabulary({ force = false } = {}) {
  if (!force && vocabCache && Date.now() - vocabAt < 60000) return vocabCache;
  vocabCache = await api.get('/api/search/vocabulary');
  vocabAt = Date.now();
  return vocabCache;
}

// ------------------------------------------------------------------ helpers
const pad = (n) => String(n).padStart(2, '0');
export const localDate = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const MONTHS = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];
const norm = (s) => String(s ?? '').toLowerCase().replace(/[^a-z0-9]+/g, '');
const stem = (t) => (t.length > 3 && t.endsWith('s') && !t.endsWith('ss') ? t.slice(0, -1) : t);
const words = (s) => String(s ?? '').toLowerCase().split(/[^a-z0-9]+/).filter(Boolean).map(stem);

const STOP = new Set(('show me all any list find get give display open the a an of in at on for from with and or to by which what '
  + 'where when how many much some please pls kindly alert alerts alarm alarms event events record records result results '
  + 'there are is were was be been have has had that this those these it its only just around near about latest recent new '
  + 'kya hai hain ke ka ki ko mein se wale wala wali dikhao dikhana batao bata sab sabhi saare sare wo woh yeh ye aur bhi '
  + 'centre center centres centers camera cameras cam project exam client priority status type').split(' '));
// words that alone never identify an alert type
const GENERIC = new Set(['detected', 'detection', 'activity', 'alert', 'alarm', 'camera', 'and', 'change', 'changed']);
const SYNONYMS = { phone: ['mobile', 'phone', 'cellphone', 'cell'], mobile: ['mobile', 'phone', 'cellphone', 'cell'],
  tamper: ['tamper', 'tampering'], tampering: ['tamper', 'tampering'], stand: ['stand', 'standing'], standing: ['stand', 'standing'],
  vehicle: ['vehicle', 'car'], trunk: ['trunk', 'boot'], person: ['person', 'people'], movement: ['movement', 'moving', 'motion'],
  impersonation: ['impersonation', 'impersonator', 'proxy'], lab: ['lab', 'laboratory', 'computer'], server: ['server'] };

const STATUS = [
  [/\b(?:not reviewed|unreviewed|pending|awaiting(?: review)?|to review|baaki|open)\b/, 'pending'],
  [/\b(?:false alarms?|invalid|rejected|fake|galat)\b/, 'invalid'],
  [/\b(?:valid|validated|confirmed|genuine|sahi|real)\b/, 'valid'],
  [/\bexceptions?\b/, 'exception'],
  [/\b(?:any status|all status(?:es)?|every decision|all decisions)\b/, 'all'],
];
const PRIORITY = [
  [/\b(?:critical|urgent|p1)\b(?:\s+priority)?/, 'critical'],
  [/\bhigh(?:\s+priority)?\b/, 'high'],
  [/\bmedium(?:\s+priority)?\b/, 'medium'],
  [/\blow(?:\s+priority)?\b/, 'low'],
];
export const STATUS_LABEL = { pending: 'Pending', valid: 'Valid', invalid: 'Invalid', exception: 'Exception', all: 'All decisions' };

function monthDate(day, mon, year, now) {
  const m = MONTHS.indexOf(mon.slice(0, 3));
  if (m < 0) return null;
  const y = year ? (+year < 100 ? 2000 + +year : +year) : now.getFullYear();
  const d = new Date(y, m, +day);
  return isNaN(d) || d.getMonth() !== m ? null : localDate(d);
}

// ------------------------------------------------------------------ parse
/** text + vocabulary → { filters, chips, recognised } (recognised = number of non-text filters). */
export function parse(text, vocab, now = new Date()) {
  const f = {};
  let s = ` ${String(text || '').toLowerCase().replace(/[“”"]/g, ' ')} `;
  const take = (rx, fn) => { const m = s.match(rx); if (m) { fn(m); s = s.replace(m[0], ' '); return true; } return false; };

  // ---- dates / periods
  const today = localDate(now);
  take(/\b(?:last|past|pichle|pichhle)\s+(\d{1,3})\s*(?:hours?|hrs?|h|ghante?)\b/, (m) => { f.hours = Math.max(1, +m[1]); })
    || take(/\b(?:last|past|pichle)\s+(?:hour|ghanta)\b/, () => { f.hours = 1; })
    || take(/\b(?:last|past|pichle)\s+(\d{1,3})\s*(?:days?|din)\b/, (m) => { f.from = localDate(addDays(now, -(Math.max(1, +m[1]) - 1))); f.to = today; f.period = `days:${m[1]}`; })
    || take(/\b(?:last|past|pichle)\s+(?:week|hafte?)\b/, () => { f.from = localDate(addDays(now, -6)); f.to = today; f.period = 'days:7'; })
    || take(/\bthis\s+week\b/, () => { f.from = localDate(addDays(now, -((now.getDay() + 6) % 7))); f.to = today; f.period = 'week'; })
    || take(/\b(?:today|aaj|aj|todays)\b/, () => { f.from = today; f.to = ''; f.period = 'today'; })
    || take(/\b(?:yesterday|kal|yday)\b/, () => { f.from = f.to = localDate(addDays(now, -1)); f.period = 'yesterday'; })
    || take(/\b(?:all dates|any time|anytime|ever|all time)\b/, () => { f.period = 'all'; });
  if (!f.period && !f.hours) {
    const dates = [];
    const grab = () => {
      const iso = s.match(/\b(20\d\d)-(\d{1,2})-(\d{1,2})\b/);
      if (iso) { s = s.replace(iso[0], ' '); return localDate(new Date(+iso[1], +iso[2] - 1, +iso[3])); }
      const dmy = s.match(/\b(\d{1,2})[/.](\d{1,2})[/.](\d{2,4})\b/);
      if (dmy) { s = s.replace(dmy[0], ' '); const y = +dmy[3] < 100 ? 2000 + +dmy[3] : +dmy[3]; return localDate(new Date(y, +dmy[2] - 1, +dmy[1])); }
      const dm = s.match(/\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:\s+(\d{2,4}))?\b/);
      if (dm) { s = s.replace(dm[0], ' '); return monthDate(dm[1], dm[2], dm[3], now); }
      const md = s.match(/\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b/);
      if (md) { s = s.replace(md[0], ' '); return monthDate(md[2], md[1], md[3], now); }
      return null;
    };
    for (let i = 0; i < 2; i++) { const d = grab(); if (d) dates.push(d); }
    if (dates.length) {
      dates.sort();
      f.from = dates[0]; f.to = dates[dates.length - 1]; f.period = 'custom';
      s = s.replace(/\b(?:between|since|until|till|upto|up to)\b/g, ' ');
    }
  }

  // ---- kind: camera online/offline events
  take(/\b(?:cameras?\s+(?:went\s+)?(?:offline|online|status)|(?:offline|online)\s+(?:cameras?|events?)|status\s+events?|camera\s+events?|offline|online)\b/,
    () => { f.kind = 'camera_status'; });
  if (f.kind) s = s.replace(/\b(?:cameras?|offline|online|status|events?)\b/g, ' ');

  // ---- decision + priority
  for (const [rx, v] of STATUS) if (!f.status && take(rx, () => {})) f.status = v;
  for (const [rx, v] of PRIORITY) if (!f.priority && take(rx, () => {})) f.priority = v;

  // ---- entities on the remaining tokens
  const raw = s.split(/[\s,;]+/).map((t) => t.replace(/^[^a-z0-9]+|[^a-z0-9_-]+$/gi, '')).filter(Boolean);
  const used = new Array(raw.length).fill(false);
  const v = vocab || {};
  const cams = new Map((v.cameras || []).map((c) => [norm(c), c]));
  const centres = new Map((v.centres || []).map((c) => [norm(c.code), c]));
  const projects = v.projects || [];

  raw.forEach((t, i) => {
    if (used[i]) return;
    const n = norm(t);
    const prev = raw[i - 1] || '';
    if (!n) return;
    if (!f.camera && cams.has(n)) { f.camera = cams.get(n); used[i] = true; return; }
    if (!f.camera && /^(?:cam|camera)$/.test(prev) && /^\d+$/.test(n) && cams.has(`cam${n}`)) { f.camera = cams.get(`cam${n}`); used[i] = true; used[i - 1] = true; return; }
    if (!f.centre && centres.has(n) && n.length >= 2) { f.centre = centres.get(n).code; used[i] = true; return; }
    if (!f.projectId) {
      const p = projects.find((x) => norm(x.code) === n && n.length >= 3)
        || (/^project$/.test(prev) ? projects.find((x) => x.id === String(+t) || norm(x.code) === n) : null);
      if (p) { f.projectId = p.id; used[i] = true; if (/^project$/.test(prev)) used[i - 1] = true; }
    }
  });

  // phrase matcher over unused tokens (longest n-gram first)
  const phrases = (maxN) => {
    const out = [];
    for (let n = Math.min(maxN, raw.length); n >= 1; n--) {
      for (let i = 0; i + n <= raw.length; i++) {
        if (used.slice(i, i + n).some(Boolean)) continue;
        out.push({ i, n, toks: raw.slice(i, i + n).map((x) => stem(x.toLowerCase())) });
      }
    }
    return out;
  };
  const mark = (p) => { for (let k = p.i; k < p.i + p.n; k++) used[k] = true; };

  // cities
  if (!f.centre && !f.city) {
    for (const p of phrases(3)) {
      const c = (v.cities || []).find((x) => norm(x.name) === norm(p.toks.join('')));
      if (c) {
        if (c.centres?.length === 1) f.centre = c.centres[0]; else f.city = c.name;
        mark(p);
        break;
      }
    }
  }

  // alert type (one), unless the query is about camera status events
  if (!f.type && f.kind !== 'camera_status') {
    const types = (v.types || []).map(([id, name]) => {
      const set = new Set(words(name));
      [...set].forEach((w) => (SYNONYMS[w] || []).forEach((x) => set.add(x)));
      return { id, name, set, size: words(name).length || 1 };
    });
    let best = null;
    for (const p of phrases(4)) {
      if (!p.toks.some((t) => !GENERIC.has(t) && !STOP.has(t))) continue;
      for (const t of types) {
        if (!p.toks.every((w) => t.set.has(w))) continue;
        const score = p.n * 10 + p.n / t.size;
        if (!best || score > best.score) best = { score, t, p };
      }
    }
    if (best) {
      f.type = String(best.t.id);
      mark(best.p);
      // swallow generic words of the type name next to the match ("mobile phone *detected*")
      raw.forEach((w, i) => { if (!used[i] && best.t.set.has(stem(w.toLowerCase())) && GENERIC.has(stem(w.toLowerCase()))) used[i] = true; });
    }
  }

  // exams and clients (by code or a phrase contained in exactly one name)
  const byPhrase = (list, nameOf) => {
    for (const p of phrases(4)) {
      const sig = p.toks.filter((t) => !STOP.has(t) && !/^\d+$/.test(t));
      if (!sig.length || (p.n === 1 && sig[0].length < 4)) continue;
      const hits = list.filter((x) => { const ws = new Set(words(nameOf(x))); return p.toks.every((t) => ws.has(t)); });
      if (hits.length === 1) { mark(p); return hits[0]; }
    }
    return null;
  };
  if (!f.exam) {
    raw.forEach((t, i) => { if (!f.exam && !used[i]) { const e = (v.exams || []).find((x) => x.code && norm(x.code) === norm(t)); if (e) { f.exam = e.id; used[i] = true; } } });
  }
  if (!f.exam) { const e = byPhrase(v.exams || [], (x) => x.name); if (e) f.exam = e.id; }
  if (!f.client) { const c = byPhrase(v.clients || [], (x) => x[1]); if (c) f.client = c[0]; }
  if (!f.centre && !f.city) { const c = byPhrase(v.centres || [], (x) => x.name || ''); if (c) f.centre = c.code; }

  // leftovers → free text
  const rest = raw.filter((t, i) => !used[i] && !STOP.has(t.toLowerCase()) && !STOP.has(stem(t.toLowerCase())));
  if (rest.length) {
    const coded = rest.filter((t) => /\d/.test(t));                 // an unknown code is the most useful text to search
    f.search = (coded.length ? coded[0] : rest.join(' ')).slice(0, 80);
  }
  return { filters: f, chips: chips(f, vocab), recognised: Object.keys(f).filter((k) => !['search', 'to'].includes(k) && !(k === 'period' && f.from)).length };
}

// ------------------------------------------------------------------ chips
const typeName = (vocab, id) => ((vocab?.types || []).find(([t]) => String(t) === String(id)) || [])[1] || `Alert type ${id}`;
const fmtDay = (d) => { const [y, m, dd] = d.split('-').map(Number); return new Date(y, m - 1, dd).toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' }); };

/** Removable chips [{key, label}] in reading order; `key` is what remove() drops. */
export function chips(f, vocab) {
  const out = [];
  if (f.priority) out.push({ key: 'priority', label: `Priority ${f.priority}` });
  if (f.status) out.push({ key: 'status', label: STATUS_LABEL[f.status] || f.status });
  if (f.kind === 'camera_status') out.push({ key: 'kind', label: 'Camera status events' });
  if (f.type) out.push({ key: 'type', label: typeName(vocab, f.type) });
  if (f.centre) {
    const c = (vocab?.centres || []).find((x) => x.code === f.centre);
    out.push({ key: 'centre', label: `Centre ${f.centre}${c?.name && c.name !== f.centre ? ` (${c.name.split(',')[0]})` : ''}` });
  }
  if (f.city) out.push({ key: 'city', label: `City ${f.city}` });
  if (f.camera) out.push({ key: 'camera', label: `Camera ${f.camera}` });
  if (f.exam) out.push({ key: 'exam', label: `Exam ${((vocab?.exams || []).find((e) => e.id === f.exam) || {}).name || f.exam}` });
  if (f.client) out.push({ key: 'client', label: `Client ${((vocab?.clients || []).find(([id]) => id === f.client) || [])[1] || f.client}` });
  if (f.projectId) out.push({ key: 'projectId', label: `Project ${((vocab?.projects || []).find((p) => p.id === f.projectId) || {}).code || f.projectId}` });
  if (f.hours) out.push({ key: 'hours', label: `Last ${f.hours} hour${f.hours === 1 ? '' : 's'}` });
  else if (f.period === 'today') out.push({ key: 'period', label: 'Today' });
  else if (f.period === 'yesterday') out.push({ key: 'period', label: 'Yesterday' });
  else if (f.period === 'all') out.push({ key: 'period', label: 'All dates' });
  else if (f.period === 'week') out.push({ key: 'period', label: 'This week' });
  else if (String(f.period || '').startsWith('days:')) out.push({ key: 'period', label: `Last ${f.period.slice(5)} days` });
  else if (f.from || f.to) out.push({ key: 'period', label: f.from === f.to || !f.to ? fmtDay(f.from || f.to) : `${fmtDay(f.from)} – ${fmtDay(f.to)}` });
  if (f.search) out.push({ key: 'search', label: `Text “${f.search}”` });
  return out;
}

export const summary = (list) => list.map((c) => c.label).join(' · ');

/** A copy of the filters without one chip. */
export function remove(f, key) {
  const x = { ...f };
  if (key === 'period' || key === 'hours') { delete x.period; delete x.from; delete x.to; delete x.hours; } else delete x[key];
  return x;
}

/** Filters returned by the server (AI interpretation: from/to as dates) → the same shape as parse(). */
export function fromServer(sf) {
  const f = { ...sf };
  if (f.status === 'all') delete f.status;
  if (f.kind === 'alert') delete f.kind;
  if (f.from || f.to) f.period = 'custom';
  return f;
}

// ------------------------------------------------------------------ to the Alerts queue
const SHARED = ['priority', 'type', 'centre', 'camera', 'client', 'exam', 'projectId', 'search', 'city'];

/** Parameters for GET /api/queue (live preview): exact times for "last N hours". */
export function toQueueParams(f, { size = 10, now = new Date() } = {}) {
  const p = { status: f.status || 'all', kind: f.kind || 'alert', size, page: 1 };
  SHARED.forEach((k) => { if (f[k]) p[k] = f[k]; });
  if (f.hours) p.from = new Date(now.getTime() - f.hours * 3600e3).toISOString().slice(0, 19);
  else {
    if (f.from) p.from = `${f.from}T00:00:00`;
    if (f.to) p.to = `${f.to}T23:59:59`;
  }
  return p;
}

/** '#/alerts?…' — the Alerts page filters by day, so "last N hours" opens from that day. */
export function toAlertsHref(f, now = new Date()) {
  const q = { status: f.status || 'all', kind: f.kind || 'alert' };
  SHARED.forEach((k) => { if (f[k]) q[k] = f[k]; });
  if (f.hours) q.from = localDate(new Date(now.getTime() - f.hours * 3600e3));
  else if (f.from || f.to) {
    if (f.from) q.from = f.from;
    if (f.to) q.to = f.to;
    if (f.period === 'today') q.range = 'today';
  } else q.range = 'all';
  return `#/alerts?${new URLSearchParams(q).toString()}`;
}
