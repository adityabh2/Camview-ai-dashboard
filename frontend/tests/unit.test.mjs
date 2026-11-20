// Frontend unit tests — run with:  node --test "frontend/tests/*.test.mjs"
// Pure modules only (no browser needed): the API client's error contract, the event bus, HTML escaping and
// the badges the review flow relies on. A minimal DOM stub covers the few helpers that read <body> data.

import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.document = { body: { dataset: {} }, querySelector: () => null, createElement: () => ({ style: {} }) };
if (!globalThis.navigator) Object.defineProperty(globalThis, 'navigator', { value: { onLine: true } });
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };

const api = await import('../js/core/api.js');
const state = await import('../js/core/state.js');
const ui = await import('../js/core/ui.js');
const queue = await import('../js/components/queue.js');

const respond = (status, body) => async () => ({
  ok: status >= 200 && status < 300, status, json: async () => { if (body === undefined) throw new Error('no json'); return body; },
});

test('a 409 conflict keeps the server code, message and is never a success', async () => {
  globalThis.fetch = respond(409, { error: 'conflict', message: 'Operator A changed this alert to VALID', requestId: 'r1' });
  await assert.rejects(api.post('/api/queue/A/decide', { result: 'invalid', expectedRevision: 0 }), (e) => {
    assert.equal(e.status, 409);
    assert.equal(e.code, 'conflict');
    assert.match(e.message, /changed this alert/);
    return true;
  });
});

test('database busy (503) and upstream errors surface their own message', async () => {
  globalThis.fetch = respond(503, { error: 'database_busy', message: 'The database is busy. Nothing was saved — please try again.' });
  await assert.rejects(api.post('/x', {}), (e) => e.code === 'database_busy' && /Nothing was saved/.test(e.message));
  globalThis.fetch = respond(502, undefined);                  // non-JSON body: friendly fallback, never a stack trace
  await assert.rejects(api.get('/y'), (e) => e.status === 502 && /Camview/.test(e.message));
});

test('network failure is reported as such, not as a server error', async () => {
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await assert.rejects(api.get('/z'), (e) => e.status === 0 && e.code === 'network');
});

test('a 401 on a data call announces an expired session', async () => {
  let expired = 0;
  const off = state.on('auth:expired', () => { expired += 1; });
  globalThis.fetch = respond(401, { error: 'unauthenticated', message: 'Please sign in.' });
  await assert.rejects(api.get('/api/queue'));
  off();
  assert.equal(expired, 1);
});

test('identical GETs in flight are de-duplicated', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; await new Promise((r) => setTimeout(r, 5)); return { ok: true, status: 200, json: async () => ({ n: calls }) }; };
  const [a, b] = await Promise.all([api.get('/api/queue', { status: 'all' }), api.get('/api/queue', { status: 'all' })]);
  assert.equal(calls, 1);
  assert.deepEqual(a, b);
});

test('the event bus isolates a failing listener', () => {
  let got = null;
  const off1 = state.on('t', () => { throw new Error('boom'); });
  const off2 = state.on('t', (p) => { got = p; });
  const err = console.error; console.error = () => {};
  state.emit('t', 42);
  console.error = err;
  off1(); off2();
  assert.equal(got, 42);
});

test('esc() neutralises markup from alert data', () => {
  assert.equal(ui.esc('<img src=x onerror=alert(1)>"\''), '&lt;img src=x onerror=alert(1)&gt;&quot;&#39;');
  assert.equal(ui.esc(null), '');
});

test('the acknowledgement badge follows the review state', () => {
  const base = { alarmId: 'A', decision: 'pending', eventKind: 'alert', review: {} };
  assert.match(queue.ackBadge(base), /NOT ACKNOWLEDGED/);
  assert.match(queue.ackBadge({ ...base, review: { acknowledgedBy: 'Asha', acknowledgedAt: '2026-09-28T10:00:00Z' } }), /ACK · Asha/);
  assert.equal(queue.ackBadge({ ...base, decision: 'valid' }), '');                   // decided: the badge steps aside
  assert.equal(queue.ackBadge({ ...base, eventKind: 'camera_status' }), '');         // status events are never acknowledged
  assert.match(queue.alertRow(base), /NOT ACKNOWLEDGED/);
});

test('delivery badges cover every server delivery state, including queued', () => {
  for (const s of ['ready', 'needs_client', 'queued', 'delivered', 'withdrawn', 'failed', 'not_deliverable']) {
    assert.ok(queue.DELIVERY[s], `missing badge for ${s}`);
  }
  assert.match(queue.deliveryBadge('queued'), /retried automatically/);
  assert.match(queue.deliveryBadge('unknown-state'), /Not delivered/);
});

test('decision badges separate Camview status from operator decisions', () => {
  assert.match(queue.decisionBadge('valid', 'camview'), /Camview/);
  assert.doesNotMatch(queue.decisionBadge('valid', 'operator'), /· Camview/);
  assert.match(queue.decisionBadge('nonsense'), /Pending review/);
});
