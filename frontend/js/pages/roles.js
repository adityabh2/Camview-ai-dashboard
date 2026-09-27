// pages/roles.js — ROLES & PERMISSIONS. The matrix is configurable; the server
// enforces it (and refuses client permissions on internal roles and vice versa).

import * as api from '../core/api.js';
import { can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, errorBox, skeleton, toast, dialog, confirmDialog, delegate, $, $$ } from '../core/ui.js';

const GROUPS = [
  ['Dashboard & live', ['dashboard.', 'live.', 'work.']], ['Alarms', ['alarm.']], ['Evidence', ['evidence.']],
  ['Cameras & context', ['camera.', 'nomenclature.', 'project.']], ['Analytics, history & reports', ['analytics.', 'history.', 'report.']],
  ['Alerts & shift', ['alert.', 'shift.']], ['Presentation & notifications', ['presentation.', 'notification.']],
  ['Audit', ['audit.']], ['Users & roles', ['user.', 'role.']], ['Clients (management)', ['client.view', 'client.manage']],
  ['Settings', ['settings.']], ['Client portal', ['client.portal', 'client.acknowledge', 'client.evidence', 'client.report.view', 'client.analytics']],
];
const SUMMARY = [
  ['View', (p) => p.includes('alarm.view') || p.includes('client.portal')],
  ['Investigate', (p) => p.includes('alarm.investigate')],
  ['Validate', (p) => p.includes('alarm.validate')],
  ['Publish', (p) => p.includes('alarm.publish')],
  ['Admin', (p) => p.includes('user.manage') || p.includes('role.manage')],
];

function groupOf(perm) {
  for (const [g, prefixes] of GROUPS) {
    if (prefixes.some((pre) => (pre.endsWith('.') ? perm.startsWith(pre) : perm === pre))) return g;
  }
  return 'Other';
}

export default {
  async render(el, ctx) {
    setTitle('Roles & Permissions', 'Administration · configurable permission matrix');
    const editable = can('role.manage');
    let data;
    const dirty = new Map();   // roleId -> Set(perms)

    el.innerHTML = `<div class="page-head"><div><h2>Roles &amp; permissions</h2><p>What each role may do. The UI hides what a role can't do; the server independently refuses it.</p></div>
      <div class="row">${editable ? `<button class="btn" id="r-new">${icon('plus', 's')} New role</button>` : '<span class="b outline">' + icon('lock') + 'read-only (needs role.manage)</span>'}</div></div>
      <div id="r-body">${skeleton(6, 30)}</div>`;

    const load = async () => {
      try {
        data = await api.get('/api/roles');
        if (ctx.isStale()) return;
        dirty.clear();
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#r-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const permsOf = (r) => (dirty.has(r.id) ? [...dirty.get(r.id)] : r.permissions);

    const paint = () => {
      const roles = data.items;
      const summary = `<section class="card" style="margin-bottom:14px"><div class="card-h"><h3>Summary</h3><div class="sub">Derived from each role's permissions</div></div>
        <div class="card-b flush table-wrap"><table class="t"><thead><tr><th>Role</th><th>Audience</th><th class="num">Users</th>${SUMMARY.map(([l]) => `<th style="text-align:center">${l}</th>`).join('')}</tr></thead><tbody>
        ${roles.map((r) => `<tr><td><b>${esc(r.name)}</b>${r.is_system ? ' <span class="b outline">built-in</span>' : ''}<div class="muted" style="font-size:11px">${esc(r.description || '')}</div></td>
          <td><span class="b outline">${icon(r.audience === 'client' ? 'building' : 'shield')}${esc(r.audience)}</span></td><td class="num">${r.users}</td>
          ${SUMMARY.map(([l, fn]) => { const ok = fn(permsOf(r)); return `<td style="text-align:center"><span class="${ok ? 'sla-within' : ''}" style="${ok ? '' : 'color:var(--text-3)'}" title="${l}: ${ok ? 'yes' : 'no'}">${ok ? '✓' : '✕'}<span class="sr-only">${ok ? 'yes' : 'no'}</span></span></td>`; }).join('')}</tr>`).join('')}
        </tbody></table></div></section>`;

      const catalog = data.catalog;
      const grouped = {};
      Object.keys(catalog).sort().forEach((p) => { (grouped[groupOf(p)] = grouped[groupOf(p)] || []).push(p); });
      const order = [...GROUPS.map((g) => g[0]), 'Other'].filter((g) => grouped[g]);
      const clientSet = new Set(data.clientPermissions);
      const internalSet = new Set(data.internalPermissions);

      const matrix = `<section class="card"><div class="card-h"><h3>Permission matrix</h3><div class="sub">${editable ? 'Tick to grant, then save the role. Client roles can only hold client permissions; internal roles only internal ones.' : 'Read-only'}</div></div>
        <div class="card-b flush" style="overflow:auto;max-height:70vh"><table class="matrix"><thead><tr><th>Permission</th>${roles.map((r) => `<th title="${esc(r.description || '')}">${esc(r.name)}<div class="muted" style="font-weight:500">${esc(r.audience)}</div></th>`).join('')}</tr></thead><tbody>
        ${order.map((g) => `<tr class="grp"><td colspan="${roles.length + 1}">${esc(g)}</td></tr>${grouped[g].map((p) => `<tr><td><div class="mono" style="font-size:11.5px">${esc(p)}</div><div class="muted" style="font-size:11px">${esc(catalog[p])}</div></td>
          ${roles.map((r) => {
            const allowedForAud = r.audience === 'client' ? clientSet.has(p) : internalSet.has(p);
            const on = permsOf(r).includes(p);
            const dis = !editable || !allowedForAud;
            return `<td><input type="checkbox" data-role="${esc(r.id)}" data-perm="${esc(p)}" ${on ? 'checked' : ''} ${dis ? 'disabled' : ''} aria-label="${esc(r.name)}: ${esc(p)}" title="${allowedForAud ? '' : 'Not available for ' + esc(r.audience) + ' roles'}"></td>`;
          }).join('')}</tr>`).join('')}`).join('')}
        </tbody>${editable ? `<tfoot><tr><td></td>${roles.map((r) => `<td style="text-align:center;padding:8px 4px"><button class="btn sm ${dirty.has(r.id) ? 'primary' : ''}" data-save="${esc(r.id)}" ${dirty.has(r.id) ? '' : 'disabled'}>Save</button>${!r.is_system ? `<button class="btn sm ghost" data-del="${esc(r.id)}" aria-label="Delete ${esc(r.name)}">${icon('trash', 's')}</button>` : ''}</td>`).join('')}</tr></tfoot>` : ''}
        </table></div></section>`;
      $('#r-body', el).innerHTML = summary + matrix;
    };

    ctx.onCleanup(delegate(el, 'change', 'input[data-perm]', (e, cb) => {
      const r = data.items.find((x) => x.id === cb.dataset.role);
      const set = dirty.get(r.id) || new Set(r.permissions);
      cb.checked ? set.add(cb.dataset.perm) : set.delete(cb.dataset.perm);
      dirty.set(r.id, set);
      const btn = el.querySelector(`[data-save="${CSS.escape(r.id)}"]`);
      if (btn) { btn.disabled = false; btn.classList.add('primary'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-save]', async (e, b) => {
      const id = b.dataset.save;
      const r = data.items.find((x) => x.id === id);
      const next = [...(dirty.get(id) || [])];
      const removed = r.permissions.filter((p) => !next.includes(p));
      const added = next.filter((p) => !r.permissions.includes(p));
      const ok = await confirmDialog({ title: `Save ${esc(r.name)}?`, message: `<p>${r.users} user(s) have this role. Changes apply on their next request.</p>
        ${added.length ? `<p><b>Grant:</b> <span class="mono">${added.map(esc).join(', ')}</span></p>` : ''}${removed.length ? `<p><b>Revoke:</b> <span class="mono">${removed.map(esc).join(', ')}</span></p>` : ''}
        <p class="muted">Recorded in the audit trail.</p>`, confirmLabel: 'Save role' });
      if (!ok) return;
      try { await api.put(`/api/roles/${encodeURIComponent(id)}`, { permissions: next }); toast('Role saved', 'success'); load(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-del]', async (e, b) => {
      const r = data.items.find((x) => x.id === b.dataset.del);
      if (!(await confirmDialog({ title: `Delete role ${esc(r.name)}?`, message: 'Only roles without users can be deleted.', danger: true, confirmLabel: 'Delete' }))) return;
      try { await api.del(`/api/roles/${encodeURIComponent(r.id)}`, {}); toast('Role deleted', 'success'); load(); }
      catch (err) { toast(err.message, 'error'); }
    }));
    $('#r-new', el)?.addEventListener('click', () => {
      dialog({ title: `${icon('plus')} New role`,
        body: `<div class="field"><label>Name</label><input class="input" id="nr-name" autofocus></div>
          <div class="field"><label>Description</label><input class="input" id="nr-desc"></div>
          <div class="field"><label>Audience</label><select class="select" id="nr-aud"><option value="internal">Internal (operations staff)</option><option value="client">Client (portal only)</option></select>
          <div class="hint">Cannot be changed later. Client roles can never access internal data.</div></div>
          <p class="muted">The role starts with no permissions — grant them in the matrix.</p>`,
        actions: [{ label: 'Cancel', onClick: ({ close }) => close() }, { label: 'Create', kind: 'primary', onClick: async ({ close, el: box }) => {
          try { await api.post('/api/roles', { name: $('#nr-name', box).value.trim(), description: $('#nr-desc', box).value.trim(), audience: $('#nr-aud', box).value, permissions: [] }); close(); toast('Role created', 'success'); load(); }
          catch (err) { toast(err.message, 'error'); }
        } }] });
    });
    await load();
  },
};
