// live.js — how the screens stay current.
//
//   push   /api/events (Server-Sent Events) tells the browser the moment alarm data or
//          Command Center data changed; the browser then re-reads what it shows.
//   poll   /api/status at the chosen interval, as the fallback and for the freshness pill.
//
// Two events for the pages:
//   'status'  after every poll (freshness, unread count, new-alarm count) — cheap, cosmetic
//   'data'    only when the server's data version changed (or a safety interval passed):
//             this is when a page re-reads its data. Nothing is re-rendered for nothing.
//
// Failures back off (never hammer a struggling backend); last good data is always kept.

import * as api from './api.js';
import { emit, on, pref, setPref, currentProject, isClient, session } from './state.js';

export const status = {
  state: 'connecting',          // live | delayed | disconnected | connecting
  lastSuccessAt: null,          // backend: last successful Camview refresh
  lastPollOkAt: null,           // browser: last successful call to our backend
  lastDataAt: null,             // browser: last time the pages were told to re-read
  lastError: null,
  newAlarms: 0,
  unread: 0,
  mode: null,
  version: null,                // server data version last seen
  push: false,                  // push stream connected
  polling: false,               // a poll is in flight
  failures: 0,
};

let timer = null;
let paused = false;
let stopped = true;
let es = null;
let pushDebounce = null;
let lastDataEmit = 0;
let queued = null;              // a poll asked for while one was in flight (a push during a poll is never lost)
const DATA_MAX_AGE = 60000;     // re-read at least this often even if the version did not move (relative times, SLA)

export const refreshMs = () => pref('refreshMs', 15000);
export function setRefreshMs(ms) { setPref('refreshMs', ms); restart(); }

function announceData(reason) {
  status.lastDataAt = Date.now();
  lastDataEmit = status.lastDataAt;
  emit('data', { ...status, reason });
}

async function poll({ reason = 'timer' } = {}) {
  if (!session.authenticated) return;
  if (status.polling) { if (reason !== 'timer') queued = reason; return; }
  status.polling = true;
  emit('status', status);
  const prevVersion = status.version;
  try {
    if (isClient()) {
      const n = await api.get('/api/notifications');
      status.unread = n.unread;
      status.lastPollOkAt = Date.now();
      status.state = 'live';
      status.version = n.dataVersion ?? status.version;
    } else {
      const pid = currentProject();
      if (!pid) { status.state = 'disconnected'; status.lastError = { message: 'No project available' }; status.polling = false; emit('status', status); return; }
      const s = await api.get('/api/status', { projectId: pid });
      const f = s.overall || s.freshness;                 // worst feed across every project the user sees
      Object.assign(status, {
        lastSuccessAt: f.lastSuccessAt,
        lastAttemptAt: f.lastAttemptAt,
        lastError: f.lastError,
        failing: s.overall?.failing || [],
        latestAlertAt: s.overall?.latestAlertAt || s.freshness?.latestAlertAt || null,
        quietHours: s.overall?.quietHours ?? s.freshness?.quietHours ?? null,
        newAlarms: s.newAlarms,
        newAlarmIds: s.newAlarmIds,
        unread: s.unreadNotifications,
        mode: s.freshness.mode,
        cacheSeconds: s.freshness.cacheSeconds,
        version: s.dataVersion ?? null,
        lastPollOkAt: Date.now(),
      });
      status.state = computeState();
    }
    status.failures = 0;
  } catch (e) {
    if (e.name !== 'AbortError') {
      status.failures += 1;
      status.state = 'disconnected';
      status.lastError = { message: e.message };
    }
  }
  status.polling = false;
  announce();
  emit('status', status);
  const changed = status.version !== prevVersion;
  if (changed || reason === 'force' || (status.lastPollOkAt && Date.now() - lastDataEmit > DATA_MAX_AGE)) {
    announceData(changed ? 'version' : reason);
  }
  schedule();
  if (queued) { const r = queued; queued = null; poll({ reason: r }); }
}

// ---- push channel ------------------------------------------------------------------
function connectPush() {
  if (es || stopped || !('EventSource' in window)) return;
  try {
    es = new EventSource('/api/events');
  } catch { es = null; return; }
  es.addEventListener('open', () => { status.push = true; emit('status', status); });
  es.addEventListener('version', (ev) => {
    const v = Number(ev.data);
    if (!Number.isFinite(v) || v === status.version) return;
    // coalesce a burst of changes into one re-read
    clearTimeout(pushDebounce);
    pushDebounce = setTimeout(() => poll({ reason: 'push' }), 150);
  });
  es.addEventListener('error', () => {
    // the browser reconnects by itself (retry hint from the server); polling covers the gap
    status.push = false;
    emit('status', status);
    if (es && es.readyState === EventSource.CLOSED) { es = null; setTimeout(connectPush, 5000); }
  });
}

function disconnectPush() {
  if (es) { es.close(); es = null; }
  status.push = false;
}

// ---- notification bridge: optional sound / browser notification for NEW notifications.
// Both are OFF by default (spec: never enable disruptive sounds automatically).
let lastUnread = null;
let notifPrefs = null;
export async function loadNotificationPrefs() {
  try { notifPrefs = (await api.get('/api/me/preferences')).notifications; } catch { notifPrefs = null; }
  return notifPrefs;
}
on('prefs', loadNotificationPrefs);

async function announce() {
  const prev = lastUnread;
  lastUnread = status.unread;
  if (prev === null || !(status.unread > prev)) return;
  if (!notifPrefs) await loadNotificationPrefs();
  if (!notifPrefs || (!notifPrefs.sound && !notifPrefs.browser)) return;
  let newest = null;
  try { newest = (await api.get('/api/notifications', { unread: '1' })).items[0]; } catch { /* best effort */ }
  if (!newest) return;
  if (notifPrefs.sound) beep(newest.severity === 'critical' ? 3 : 1);
  if (notifPrefs.browser && 'Notification' in window && Notification.permission === 'granted' && document.hidden) {
    const n = new Notification(`CAMVIEW · ${newest.title}`, { body: newest.body || '', tag: 'camview-' + newest.id });
    n.onclick = () => { window.focus(); if (newest.link) location.hash = newest.link.replace(/^#/, ''); n.close(); };
  }
}

let audioCtx = null;
export function beep(times = 1) {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    for (let i = 0; i < times; i++) {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      const t = audioCtx.currentTime + i * 0.2;
      o.frequency.value = times > 1 ? 880 : 660;
      o.connect(g); g.connect(audioCtx.destination);
      g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(0.18, t + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t + 0.15);
      o.start(t); o.stop(t + 0.16);
    }
  } catch { /* audio unavailable */ }
}

function computeState() {
  if (status.lastError && !status.lastSuccessAt) return 'disconnected';
  if (status.lastError) return 'delayed';
  if (!status.lastSuccessAt) return 'connecting';
  const age = (Date.now() - new Date(status.lastSuccessAt).getTime()) / 1000;
  // the server fetches Camview once per cacheSeconds, so data up to ~2 cache periods old is still on time
  return age > Math.max(60, (refreshMs() / 1000) * 4, (status.cacheSeconds || 0) * 2 + 30) ? 'delayed' : 'live';
}

// ---- scheduling: one timer, re-armed after each poll; exponential back-off while the backend fails
function nextDelay() {
  const base = refreshMs();
  if (status.failures) return Math.min(60000, base * 2 ** Math.min(status.failures, 4));
  return base;
}

function schedule() {
  clearTimeout(timer);
  if (stopped) return;
  timer = setTimeout(() => { if (!paused && !document.hidden) poll(); else schedule(); }, nextDelay());
}

export function restart() {
  stopped = false;
  clearTimeout(timer);
  poll({ reason: 'force' });
  connectPush();
}

export function pollNow() { return poll({ reason: 'force' }); }
export function stop() { stopped = true; clearTimeout(timer); timer = null; disconnectPush(); }
export function pause(v) { paused = !!v; if (!paused) poll(); }

on('project', () => { status.lastSuccessAt = null; status.lastError = null; status.newAlarms = 0; poll({ reason: 'force' }); });
document.addEventListener('visibilitychange', () => { if (!document.hidden && !stopped) { poll({ reason: 'visible' }); connectPush(); } });
window.addEventListener('online', () => { if (!stopped) { status.failures = 0; poll({ reason: 'online' }); } });
window.addEventListener('offline', () => { status.state = 'disconnected'; status.lastError = { message: 'You are offline' }; emit('status', status); });
