// components/clientlogins.js — create and manage CLIENT LOGINS (user name + password for the client portal).
// Used from Management › Clients and Management › Exams. A client login sees only alerts the operations team
// marked VALID, for the exams it is allowed (every exam of its client, or only the ticked ones).
// Passwords are shown once (typed or generated); the server stores only a hash.

import * as api from '../core/api.js';
import { session } from '../core/state.js';
import { esc, icon, fmt, dialog, toast, confirmDialog, $, $$ } from '../core/ui.js';

const WORDS = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
const MIN = () => session.passwordMin || 5;
export function generatePassword(n = 14) {
  const a = new Uint32Array(n);
  crypto.getRandomValues(a);
  return Array.from(a, (x) => WORDS[x % WORDS.length]).join('').replace(/(.{4})(?=.)/g, '$1-').slice(0, n + Math.floor((n - 1) / 4));
}

const pwField = (id, label, hint) => `<div class="field"><label for="${id}">${label}</label>
  <div class="pw-row"><input class="input mono" id="${id}" type="password" autocomplete="new-password" minlength="${MIN()}" placeholder="at least ${MIN()} characters">
  <button type="button" class="btn sm ghost" data-pw-show="${id}" aria-label="Show password">${icon('eye', 's')}</button>
  <button type="button" class="btn sm" data-pw-gen="${id}">${icon('refresh', 's')} Generate</button></div>
  <div class="hint">${hint}</div></div>`;

function wirePw(box) {
  $$('[data-pw-show]', box).forEach((b) => b.addEventListener('click', () => {
    const i = $(`#${b.dataset.pwShow}`, box); i.type = i.type === 'password' ? 'text' : 'password';
  }));
  $$('[data-pw-gen]', box).forEach((b) => b.addEventListener('click', () => {
    const i = $(`#${b.dataset.pwGen}`, box); i.value = generatePassword(); i.type = 'text'; i.focus(); i.select();
  }));
}

/** Shows the sign-in details once, with Copy. */
export function showCredentials({ email, password, clientName, sees, title = 'Login ready' }) {
  const text = `Portal: ${location.origin}\nSign-in name: ${email}\nPassword: ${password}`;
  dialog({
    title: `${icon('check')} ${esc(title)}`,
    body: `<div class="banner warning">${icon('key')}<div>The password is shown <b>only now</b>. Send it to ${esc(clientName || 'the client')} securely; they can change it after signing in (profile menu).</div></div>
      <dl class="kv cred"><dt>Portal</dt><dd class="mono">${esc(location.origin)}</dd><dt>Sign-in name</dt><dd class="mono">${esc(email)}</dd>
        <dt>Password</dt><dd class="mono cred-pw">${esc(password)}</dd>${sees ? `<dt>Sees</dt><dd>${esc(sees)} · VALID alerts only</dd>` : ''}</dl>`,
    actions: [
      { label: `${icon('download', 's')} Copy`, onClick: async () => {
        try { await navigator.clipboard.writeText(text); toast('Copied', 'success'); } catch { toast('Select the text and copy it', 'warning'); }
      } },
      { label: 'Done', kind: 'primary', onClick: ({ close }) => close() },
    ],
  });
}

/**
 * Create (user = null) or edit a client login.
 * client: {id, name} · exams: [{id, name}] of that client · roles: [{id, name}] · preselect: exam ids ticked for a new login
 */
export function loginDialog({ client, exams = [], roles = [], user = null, preselect = [], onDone }) {
  const isNew = !user;
  const limited = user?.exams?.length ? user.exams : preselect.length ? preselect : exams.map((e) => e.id);
  const role = user?.roleId || (roles.some((r) => r.id === 'client_user') ? 'client_user' : roles[0]?.id);
  const d = dialog({
    title: `${icon('user')} ${isNew ? 'New client login' : `Edit login · ${esc(user.email)}`}`, side: true,
    body: `<p class="muted" style="margin-top:0">For <b>${esc(client.name)}</b>. The login opens the client portal and sees <b>only alerts your team marked VALID</b>.</p>
      <div class="field"><label for="cl-lname">Person or desk name</label><input class="input" id="cl-lname" maxlength="80" value="${esc(user?.name || '')}" placeholder="e.g. MPESB control room"></div>
      <div class="field"><label for="cl-lemail">Sign-in name (username or email)</label><input class="input mono" id="cl-lemail" maxlength="80" autocomplete="off" value="${esc(user?.email || '')}" placeholder="e.g. mpesb.control"></div>
      ${pwField('cl-lpw', isNew ? 'Password' : 'New password (leave empty to keep)', isNew ? 'Type one or press Generate. Leave empty to have one generated.' : 'Only if you want to change it.')}
      <div class="field"><label for="cl-lrole">Access</label><select class="select" id="cl-lrole">${roles.map((r) => `<option value="${esc(r.id)}" ${r.id === role ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}</select>
        <div class="hint">Client Admin / Client User can acknowledge alerts; Client Viewer can only look.</div></div>
      <div class="field"><label>Exams this login can see</label>
        ${exams.length ? exams.map((e) => `<label class="check" style="margin-bottom:4px"><input type="checkbox" data-lexam="${esc(e.id)}" ${limited.includes(e.id) ? 'checked' : ''}> <b>${esc(e.name)}</b>${e.code && e.code !== e.name ? ` <span class="muted mono">${esc(e.code)}</span>` : ''}</label>`).join('')
          : `<div class="muted">No exam is mapped to ${esc(client.name)} yet — the login sees every VALID alert of the client's projects.</div>`}
        ${exams.length ? `<div class="hint">All ticked = every exam of ${esc(client.name)}, including exams added later.</div>` : ''}</div>
      ${isNew ? '' : `<div class="field"><label for="cl-lstatus">Status</label><select class="select" id="cl-lstatus"><option value="active">Active</option><option value="disabled" ${user.status === 'disabled' ? 'selected' : ''}>Disabled — cannot sign in</option></select></div>`}`,
    actions: [
      { label: 'Cancel', onClick: ({ close }) => close() },
      { label: isNew ? 'Create login' : 'Save', kind: 'primary', onClick: async ({ close, el: box }) => {
        const picked = $$('[data-lexam]:checked', box).map((x) => x.dataset.lexam);
        if (exams.length && !picked.length) { toast('Tick at least one exam', 'warning'); return; }
        const all = !exams.length || picked.length === exams.length;
        const pw = $('#cl-lpw', box).value;
        if (pw && pw.length < MIN()) { toast(`The password needs at least ${MIN()} characters`, 'warning'); return; }
        const body = { name: $('#cl-lname', box).value.trim(), email: $('#cl-lemail', box).value.trim(), roleId: $('#cl-lrole', box).value,
          clientId: client.id, scopes: all ? [] : picked.map((v) => ({ type: 'exam', value: v })) };
        if (!body.name || !body.email) { toast('Name and sign-in name are required', 'warning'); return; }
        if (pw) body.password = pw;
        if (!isNew) body.status = $('#cl-lstatus', box).value;
        try {
          const r = isNew ? await api.post('/api/users', body) : await api.put(`/api/users/${encodeURIComponent(user.id)}`, body);
          close();
          onDone?.();
          const sees = all ? `every exam of ${client.name}` : picked.map((id) => exams.find((e) => e.id === id)?.name).join(', ');
          if (isNew || pw) showCredentials({ email: body.email, password: r?.temporaryPassword || pw, clientName: client.name, sees, title: isNew ? 'Login created' : 'Password changed' });
          else toast('Login saved', 'success');
        } catch (err) { toast(err.message, 'error'); }
      } },
    ],
  });
  wirePw(d.el);
}

/** Reset a client login's password: typed or generated, shown once. */
export function resetPassword(user, clientName, onDone) {
  const d = dialog({
    title: `${icon('key')} Reset password · ${esc(user.email)}`,
    body: `<p class="muted" style="margin-top:0">The old password stops working at once and the login is signed out of every browser.</p>${pwField('cl-rpw', 'New password', 'Type one or press Generate.')}`,
    actions: [
      { label: 'Cancel', onClick: ({ close }) => close() },
      { label: 'Set password', kind: 'primary', onClick: async ({ close, el: box }) => {
        let pw = $('#cl-rpw', box).value;
        if (!pw) pw = generatePassword();
        if (pw.length < MIN()) { toast(`The password needs at least ${MIN()} characters`, 'warning'); return; }
        try {
          await api.put(`/api/users/${encodeURIComponent(user.id)}`, { password: pw });
          close();
          onDone?.();
          showCredentials({ email: user.email, password: pw, clientName, title: 'Password changed' });
        } catch (err) { toast(err.message, 'error'); }
      } },
    ],
  });
  wirePw(d.el);
}

/** Delete a login for good (the audit trail keeps its name). */
export async function deleteLogin(user, onDone) {
  if (!await confirmDialog({ title: `Delete ${esc(user.email)}?`, danger: true, confirmLabel: 'Delete login',
    message: `<p>The login <b class="mono">${esc(user.email)}</b> is removed and signed out at once. This cannot be undone; you can create a new login later.</p>`, requireText: user.email })) return;
  try {
    await api.del(`/api/users/${encodeURIComponent(user.id)}`);
    toast('Login deleted', 'success');
    onDone?.();
  } catch (err) { toast(err.message, 'error'); }
}

/** Enable / disable a login. */
export async function toggleLogin(user, onDone) {
  const disable = user.status === 'active';
  if (disable && !await confirmDialog({ title: `Disable ${esc(user.email)}?`, message: 'The login can no longer sign in. You can enable it again at any time.', confirmLabel: 'Disable', danger: true })) return;
  try {
    await api.put(`/api/users/${encodeURIComponent(user.id)}`, { status: disable ? 'disabled' : 'active' });
    toast(disable ? 'Login disabled' : 'Login enabled', 'success');
    onDone?.();
  } catch (err) { toast(err.message, 'error'); }
}

/** Compact list of a client's logins with actions (rendered as HTML; wire with wireLoginList). */
export function loginListHtml(users, { canManage, examsById = {} } = {}) {
  if (!users.length) return `<div class="cl-empty muted">${icon('user', 's')} No client login yet.</div>`;
  return `<div class="cl-list">${users.map((u) => `<div class="cl-row ${u.status !== 'active' ? 'off' : ''}">
      <span class="avatar">${esc((u.name || u.email || '?')[0].toUpperCase())}</span>
      <div class="grow" style="min-width:0"><div class="t1"><span class="mono">${esc(u.email)}</span>${u.status !== 'active' ? ' <span class="b vis-withdrawn">disabled</span>' : ''}</div>
        <div class="t2">${esc(u.name)} · ${esc(u.role || '')} · ${u.exams?.length ? esc(u.exams.map((x) => examsById[x] || x).join(', ')) : 'every exam'} · ${u.lastLoginAt ? `signed in ${fmt.rel(u.lastLoginAt)}` : 'never signed in'}</div></div>
      ${canManage ? `<div class="row tight"><button class="btn sm ghost" data-cl-edit="${esc(u.id)}" title="Edit">${icon('edit', 's')}</button>
        <button class="btn sm ghost" data-cl-reset="${esc(u.id)}" title="Reset password">${icon('key', 's')}</button>
        <button class="btn sm ghost" data-cl-toggle="${esc(u.id)}" title="${u.status === 'active' ? 'Disable' : 'Enable'}">${icon(u.status === 'active' ? 'lock' : 'check', 's')}</button>
        <button class="btn sm ghost cl-del" data-cl-delete="${esc(u.id)}" title="Delete login">${icon('trash', 's')}</button></div>` : ''}
    </div>`).join('')}</div>`;
}

export function wireLoginList(root, { users, client, exams, roles, onDone }) {
  const find = (id) => users.find((u) => u.id === id);
  $$('[data-cl-edit]', root).forEach((b) => b.addEventListener('click', () => loginDialog({ client, exams, roles, user: find(b.dataset.clEdit), onDone })));
  $$('[data-cl-reset]', root).forEach((b) => b.addEventListener('click', () => resetPassword(find(b.dataset.clReset), client.name, onDone)));
  $$('[data-cl-toggle]', root).forEach((b) => b.addEventListener('click', () => toggleLogin(find(b.dataset.clToggle), onDone)));
  $$('[data-cl-delete]', root).forEach((b) => b.addEventListener('click', () => deleteLogin(find(b.dataset.clDelete), onDone)));
}
