// pages/users.js — USER MANAGEMENT. Roles/permissions are edited in Roles & Permissions, not here.

import * as api from '../core/api.js';
import { can, session } from '../core/state.js';
import { deleteLogin } from '../components/clientlogins.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, errorBox, skeleton, empty, toast, dialog, debounce, delegate, $, $$ } from '../core/ui.js';

const SCOPE_TYPES = ['global', 'project', 'tc', 'centre', 'camera'];

export default {
  async render(el, ctx) {
    setTitle('Users', 'Administration · who can sign in and what they can see');
    let q = { search: '', role: '', audience: '', status: '', ...ctx.query };
    let users = [], roles = [], clients = [];

    el.innerHTML = `
      <div class="page-head"><div><h2>User management</h2><p>Accounts, roles, clients and access scope. Server-side RBAC enforces everything shown here.</p></div>
        <div class="row">${can('user.manage') ? `<button class="btn primary" id="u-new">${icon('plus', 's')} New user</button>` : ''}</div></div>
      <div class="filters">
        <input class="input" style="min-width:240px" placeholder="Search name or email  ( / )" id="u-search" data-page-search value="${esc(q.search)}">
        <select class="select" id="u-role" aria-label="Role"><option value="">All roles</option></select>
        <select class="select" id="u-aud" aria-label="Audience"><option value="">All audiences</option><option value="internal">Internal</option><option value="client">Client</option></select>
        <select class="select" id="u-status" aria-label="Status"><option value="">Any status</option><option value="active">Active</option><option value="disabled">Disabled</option></select>
        <span class="grow"></span><span class="muted" id="u-count"></span>
      </div>
      <section class="card"><div class="card-b flush" id="u-body">${skeleton(6, 26)}</div></section>`;
    $('#u-aud', el).value = q.audience; $('#u-status', el).value = q.status;

    const load = async () => {
      try {
        const [u, r, c] = await Promise.all([api.get('/api/users'),
          can('role.view') ? api.get('/api/roles') : Promise.resolve({ items: [] }),
          can('client.view') ? api.get('/api/clients') : Promise.resolve({ items: [] })]);
        if (ctx.isStale()) return;
        users = u.items; roles = r.items.length ? r.items : [...new Map(users.map((x) => [x.roleId, { id: x.roleId, name: x.roleName, audience: x.audience }])).values()];
        clients = c.items;
        $('#u-role', el).innerHTML = '<option value="">All roles</option>' + roles.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
        $('#u-role', el).value = q.role;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#u-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const scopeText = (u) => u.audience === 'client' ? `<span class="dim">Client projects</span>`
      : (u.scopes.length ? u.scopes.map((s) => `<span class="b outline">${esc(s.type)}${s.type === 'global' ? '' : ': ' + esc(s.value)}</span>`).join(' ') : '<span class="b outline">global</span>');

    const paint = () => {
      const s = q.search.toLowerCase();
      const rows = users.filter((u) => (!s || u.name.toLowerCase().includes(s) || u.email.toLowerCase().includes(s))
        && (!q.role || u.roleId === q.role) && (!q.audience || u.audience === q.audience) && (!q.status || u.status === q.status));
      $('#u-count', el).textContent = `${rows.length} of ${users.length} users`;
      table($('#u-body', el), {
        rowKey: (u) => u.id,
        columns: [
          { label: 'Name', render: (u) => `<div class="cell-2"><b>${esc(u.name)}</b><span class="l2">${esc(u.email)}</span></div>` },
          { label: 'Status', render: (u) => u.status === 'active' ? `<span class="b vis-shared">${icon('check')}active</span>` : `<span class="b vis-withdrawn">${icon('x')}disabled</span>` },
          { label: 'Role', render: (u) => esc(u.roleName) },
          { label: 'Audience', render: (u) => `<span class="b outline">${icon(u.audience === 'client' ? 'building' : 'shield')}${esc(u.audience)}</span>` },
          { label: 'Client', render: (u) => u.clientName ? esc(u.clientName) : '<span class="muted">—</span>' },
          { label: 'Scope', render: scopeText },
          { label: 'Last login', render: (u) => `<span class="num dim">${u.lastLoginAt ? fmt.dt(u.lastLoginAt) : 'never'}</span>` },
          { label: '', render: (u) => `${u.isDemo ? '<span class="demo-flag" style="font-size:9px;padding:2px 5px">DEMO</span> ' : ''}${can('user.manage') ? `<button class="btn sm" data-edit="${esc(u.id)}">${icon('edit', 's')} Edit</button>` : ''}` },
        ],
        rows,
        emptyHtml: empty('No users match', 'Try clearing the filters.', 'users'),
      });
    };

    const setQ = (patch) => { q = { ...q, ...patch }; ctx.setQuery(q); paint(); };
    $('#u-search', el).addEventListener('input', debounce((e) => setQ({ search: e.target.value.trim() }), 200));
    $('#u-role', el).addEventListener('change', (e) => setQ({ role: e.target.value }));
    $('#u-aud', el).addEventListener('change', (e) => setQ({ audience: e.target.value }));
    $('#u-status', el).addEventListener('change', (e) => setQ({ status: e.target.value }));
    $('#u-new', el)?.addEventListener('click', () => editDialog(null));
    ctx.onCleanup(delegate(el, 'click', '[data-edit]', (e, b) => editDialog(users.find((u) => u.id === b.dataset.edit))));

    function scopeRow(s = { type: 'project', value: '' }) {
      return `<div class="row" data-scope style="margin-bottom:6px"><select class="select" data-st>${SCOPE_TYPES.map((t) => `<option ${t === s.type ? 'selected' : ''}>${t}</option>`).join('')}</select>
        <input class="input grow" data-sv placeholder="value e.g. 7, TC-0701, CAM-109" value="${esc(s.type === 'global' ? '*' : s.value)}"><button class="btn icon ghost" data-rm aria-label="Remove scope">${icon('trash', 's')}</button></div>`;
    }

    function editDialog(u) {
      const isNew = !u;
      const d = dialog({
        title: isNew ? `${icon('plus')} New user` : `${icon('edit')} Edit ${esc(u.name)}`,
        size: 'lg',
        body: `<div class="grid g-2">
            <div class="field"><label>Name</label><input class="input" id="f-name" value="${esc(u?.name || '')}" autofocus></div>
            <div class="field"><label>Email</label><input class="input" id="f-email" type="email" value="${esc(u?.email || '')}"></div>
            <div class="field"><label>Role</label><select class="select" id="f-role">${roles.map((r) => `<option value="${esc(r.id)}" data-aud="${esc(r.audience)}" ${u?.roleId === r.id ? 'selected' : ''}>${esc(r.name)} (${esc(r.audience)})</option>`).join('')}</select>
              <div class="hint">Permissions come from the role — edit them in Roles &amp; Permissions.</div></div>
            <div class="field"><label>Status</label><select class="select" id="f-status"><option value="active">Active</option><option value="disabled" ${u?.status === 'disabled' ? 'selected' : ''}>Disabled</option></select></div>
          </div>
          <div class="field" id="f-client-wrap"><label>Client (required for client roles)</label><select class="select" id="f-client"><option value="">—</option>${clients.map((c) => `<option value="${esc(c.id)}" ${u?.clientId === c.id ? 'selected' : ''}>${esc(c.name)}</option>`).join('')}</select>
            <div class="hint">Client users only ever see alerts shared with this client, for its assigned projects.</div></div>
          <div id="f-scope-wrap"><div class="section-title" style="margin-top:4px">Access scope</div>
            <div class="hint muted" style="margin-bottom:8px">Global = all projects. Otherwise the user only sees alarms inside the listed projects / TECs / TCs / centres / cameras.</div>
            <div id="f-scopes">${(u?.scopes?.length ? u.scopes : [{ type: 'global', value: '*' }]).map(scopeRow).join('')}</div>
            <button class="btn sm" id="f-addscope">${icon('plus', 's')} Add scope</button></div>
          <div class="field" style="margin-top:12px"><label>${isNew ? 'Password (leave blank to generate a temporary one)' : 'Reset password (leave blank to keep)'}</label><input class="input" type="password" id="f-pw" autocomplete="new-password"><div class="hint">Minimum ${session.passwordMin || 5} characters. A reset signs the user out everywhere.</div></div>`,
        actions: [...(!isNew && can('user.manage') && u.id !== session.user?.id ? [{ label: `${icon('trash', 's')} Delete`, kind: 'danger', onClick: ({ close }) => { close(); deleteLogin(u, load); } }] : []),
          { label: 'Cancel', onClick: ({ close }) => close() },
          { label: isNew ? 'Create user' : 'Save changes', kind: 'primary', onClick: async ({ close, el: box }) => {
            const role = $('#f-role', box);
            const aud = role.selectedOptions[0]?.dataset.aud;
            const body = { name: $('#f-name', box).value.trim(), email: $('#f-email', box).value.trim(), roleId: role.value, status: $('#f-status', box).value };
            if (aud === 'client') body.clientId = $('#f-client', box).value || null;
            else body.scopes = $$('[data-scope]', box).map((r) => ({ type: $('[data-st]', r).value, value: $('[data-st]', r).value === 'global' ? '*' : $('[data-sv]', r).value.trim() })).filter((s) => s.value);
            const pw = $('#f-pw', box).value;
            if (pw) body.password = pw;
            try {
              if (isNew) {
                delete body.status;
                const r = await api.post('/api/users', body);
                close();
                if (r.temporaryPassword) showTemp(body.email, r.temporaryPassword); else toast('User created', 'success');
              } else {
                await api.put(`/api/users/${encodeURIComponent(u.id)}`, body);
                close(); toast('User updated', 'success');
              }
              load();
            } catch (err) { toast(err.message, 'error'); }
          } }],
      });
      const box = d.el;
      const sync = () => {
        const aud = $('#f-role', box).selectedOptions[0]?.dataset.aud;
        $('#f-client-wrap', box).classList.toggle('hidden', aud !== 'client');
        $('#f-scope-wrap', box).classList.toggle('hidden', aud === 'client');
      };
      $('#f-role', box).addEventListener('change', sync); sync();
      $('#f-addscope', box).addEventListener('click', () => $('#f-scopes', box).insertAdjacentHTML('beforeend', scopeRow()));
      delegate(box, 'click', '[data-rm]', (e, b) => b.closest('[data-scope]').remove());
    }

    function showTemp(email, pw) {
      const d = dialog({
        title: `${icon('key')} Temporary password`,
        body: `<div class="banner warning">${icon('alert')}<div>This password is shown <b>once</b>. Give it to <b>${esc(email)}</b> securely and ask them to change it after signing in.</div></div>
          <div class="row"><input class="input grow mono" readonly id="tmp-pw" value="${esc(pw)}"><button class="btn" id="tmp-copy">Copy</button></div>`,
        actions: [{ label: 'Done', kind: 'primary', onClick: ({ close }) => close() }],
      });
      $('#tmp-copy', d.el).addEventListener('click', async () => { try { await navigator.clipboard.writeText(pw); toast('Copied'); } catch { $('#tmp-pw', d.el).select(); } });
    }

    await load();
  },
};
