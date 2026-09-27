// pages/exams.js — EXAMS: exam ↔ client ↔ project mapping. This is what lets the
// system resolve the exam and the client of every alert automatically.

import * as api from '../core/api.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, table, errorBox, skeleton, empty, toast, dialog, $, $$ } from '../core/ui.js';
import { loginDialog, resetPassword } from '../components/clientlogins.js';

export default {
  async render(el, ctx) {
    setTitle('Exams', 'Which client and projects each exam belongs to');
    let data = null;
    el.innerHTML = `<div class="page-head"><div><h2>Exams</h2><p>Every alert is matched to its exam through its project (and the exam dates when several exams share a project). The exam's client receives its VALID alerts.</p></div>
      <button class="btn primary hidden" id="x-new">${icon('plus', 's')} New exam</button></div>
      <div id="x-code"></div>
      <section class="card"><div class="card-b flush" id="x-body">${skeleton(5, 30)}</div></section>`;

    const load = async () => {
      try {
        data = await api.get('/api/exams');
        if (ctx.isStale()) return;
        paint();
      } catch (e) {
        if (!ctx.isStale()) $('#x-body', el).innerHTML = errorBox(e);
      }
    };

    const paint = () => {
      $('#x-new', el).classList.toggle('hidden', !data.canManage);
      // names the project code gives (Camview sends only the number; the code is entered once, the names follow)
      const differ = data.items.filter((e) => e.fromCode && (e.name !== e.fromCode.exam || (e.clientName && e.clientName !== e.fromCode.client)));
      $('#x-code', el).innerHTML = differ.map((e) => `<div class="banner info x-codebar">${icon('tree')}<div class="grow">
          <b>Names from the project code</b> <span class="mono muted">${esc(e.fromCode.projectCode)}</span><br>
          Client <b>${esc(e.fromCode.client)}</b> · exam <b>${esc(e.fromCode.exam)}</b>${e.fromCode.date ? ` · date <b>${fmt.date(e.fromCode.date)}</b>` : ''}
          <span class="muted">— now “${esc(e.clientName || '—')}” / “${esc(e.name)}”, typed by hand.</span></div>
          ${data.canManage ? `<button class="btn sm primary" data-fromcode="${esc(e.id)}">Use these names</button>` : ''}</div>`).join('');
      table($('#x-body', el), {
        rowKey: (e) => e.id,
        columns: [
          { label: 'Exam', render: (e) => `<div class="cell-2"><b>${esc(e.name)}</b><span class="l2 mono">${esc(e.code)}</span></div>` },
          { label: 'Client', render: (e) => e.clientName ? esc(e.clientName) : '<span class="muted">Any client mapped to the project</span>' },
          { label: 'Projects', render: (e) => e.projects.map((p) => `<span class="b outline" title="${esc(p.name || '')}">${esc(p.code)}</span>`).join(' ') },
          { label: 'Dates', render: (e) => e.startDate || e.endDate ? `<span class="num">${e.startDate ? fmt.date(e.startDate) : '…'} → ${e.endDate ? fmt.date(e.endDate) : '…'}</span>` : '<span class="muted">Always</span>' },
          { label: 'Status', render: (e) => `<span class="b ${e.status === 'active' ? 'vis-shared' : 'vis-internal'}">${esc(e.status)}</span>` },
          { label: 'Open tickets', render: (e) => `<a href="#/tickets?exam=${encodeURIComponent(e.id)}" class="num">${e.tickets}</a>` },
          { label: 'Client logins', render: (e) => (!e.clientId ? '<span class="muted">Choose a client first</span>'
            : `<div class="x-logins">${(e.logins || []).map((u) => `<button type="button" class="b outline x-login" data-xlogin="${esc(e.id)}|${esc(u.id)}" title="${esc(u.role)} · ${u.exams.length ? 'limited exams' : 'every exam of the client'}${u.lastLoginAt ? ' · last sign-in ' + fmt.rel(u.lastLoginAt) : ' · never signed in'}">${icon('user', 's')}${esc(u.email)}${u.status !== 'active' ? ' (disabled)' : ''}</button>`).join('') || '<span class="muted">None yet</span>'}
              ${data.canManageLogins ? `<button class="btn sm" data-login="${esc(e.id)}">${icon('plus', 's')} Create login</button>` : ''}</div>`) },
          { label: '', render: (e) => `<div class="row tight" style="justify-content:flex-end"><a class="btn sm ghost" href="#/alerts?exam=${encodeURIComponent(e.id)}&status=all">Alerts</a>${data.canManage ? `<button class="btn sm" data-edit="${esc(e.id)}">${icon('edit', 's')} Edit</button>` : ''}</div>` },
        ],
        rows: data.items,
        emptyHtml: empty('No exams yet', data.canManage ? 'Create an exam and map it to a client and its projects.' : 'An administrator maps exams to clients and projects.', 'report'),
      });
      $$('[data-edit]', el).forEach((b) => b.addEventListener('click', () => edit(data.items.find((x) => x.id === b.dataset.edit))));
      $$('[data-login]', el).forEach((b) => b.addEventListener('click', () => createLogin(data.items.find((x) => x.id === b.dataset.login))));
      $$('[data-xlogin]', el).forEach((b) => b.addEventListener('click', () => { const [eid, uid] = b.dataset.xlogin.split('|'); manageLogin(data.items.find((x) => x.id === eid), uid); }));
      $$('[data-fromcode]', el).forEach((b) => b.addEventListener('click', async () => {
        b.disabled = true;
        try {
          const r = await api.post(`/api/exams/${encodeURIComponent(b.dataset.fromcode)}/names-from-code`, { renameClient: true });
          toast(`Now: client ${r.client} · exam ${r.exam.name}`, 'success');
          load();
        } catch (err) { b.disabled = false; toast(err.message, 'error'); }
      }));
    };

    const edit = (ex) => {
      const e = ex || { name: '', code: '', clientId: '', projectIds: [], startDate: '', endDate: '', status: 'active' };
      const d = dialog({
        title: ex ? `Edit exam · ${esc(ex.name)}` : 'New exam',
        side: true,
        body: `<div class="field"><label for="x-name">Name</label><input class="input" id="x-name" maxlength="120" value="${esc(e.name)}" placeholder="e.g. SRE 2026 — Prelims"></div>
          <div class="field"><label for="x-code">Code</label><input class="input mono" id="x-code" maxlength="24" value="${esc(e.code)}" placeholder="auto from the name"></div>
          <div class="field"><label for="x-client">Client</label><select class="select" id="x-client"><option value="">— any client mapped to the projects —</option>
            ${data.clients.map((c) => `<option value="${esc(c.id)}" ${c.id === e.clientId ? 'selected' : ''}>${esc(c.name)}${c.status !== 'active' ? ' (inactive)' : ''}</option>`).join('')}</select>
            <div class="hint">Choosing a client also gives that client access to the projects below.</div></div>
          <div class="field"><label>Projects</label>${data.projects.map((p) => `<label class="check" style="margin-bottom:4px"><input type="checkbox" data-proj="${esc(p.externalId)}" ${e.projectIds.includes(String(p.externalId)) ? 'checked' : ''}> <b>${esc(p.code)}</b> <span class="muted">${esc(p.name && p.name !== p.code ? p.name : '')}</span></label>`).join('') || '<div class="muted">No projects available.</div>'}</div>
          <div class="grid g-2"><div class="field"><label for="x-start">Start date</label><input class="input" type="date" id="x-start" value="${esc(e.startDate || '')}"></div>
            <div class="field"><label for="x-end">End date</label><input class="input" type="date" id="x-end" value="${esc(e.endDate || '')}"></div></div>
          <div class="hint" style="margin-top:-6px">Dates are only needed when several exams share one project.</div>
          <div class="field"><label for="x-status">Status</label><select class="select" id="x-status"><option value="active">Active</option><option value="inactive" ${e.status === 'inactive' ? 'selected' : ''}>Inactive</option></select></div>`,
        actions: [
          { label: 'Cancel', onClick: ({ close }) => close() },
          { label: ex ? 'Save' : 'Create exam', kind: 'primary', onClick: async ({ close }) => {
            const root = d.el;
            const body = { name: $('#x-name', root).value.trim(), code: $('#x-code', root).value.trim(), clientId: $('#x-client', root).value || null,
              projectIds: $$('[data-proj]:checked', root).map((x) => x.dataset.proj), startDate: $('#x-start', root).value || null,
              endDate: $('#x-end', root).value || null, status: $('#x-status', root).value };
            if (!body.name) return toast('Name is required', 'warning');
            if (!body.projectIds.length) return toast('Choose at least one project', 'warning');
            try {
              await (ex ? api.put(`/api/exams/${encodeURIComponent(ex.id)}`, body) : api.post('/api/exams', body));
              toast(ex ? 'Exam saved' : 'Exam created', 'success');
              close();
              load();
            } catch (err) { toast(err.message, 'error'); }
          } },
        ],
      });
    };

    // Client logins: the same dialog as Management › Clients (create / edit / reset password)
    const clientOf = (ex) => ({ id: ex.clientId, name: ex.clientName || '' });
    const examsOf = (ex) => data.items.filter((x) => x.clientId === ex.clientId).map((x) => ({ id: x.id, name: x.name, code: x.code }));
    const createLogin = (ex) => loginDialog({ client: clientOf(ex), exams: examsOf(ex), roles: data.clientRoles || [], preselect: [ex.id], onDone: load });
    const manageLogin = (ex, uid) => {
      const u = (ex.logins || []).find((x) => x.id === uid);
      if (!u) return;
      dialog({ title: `${icon('user')} ${esc(u.email)}`, body: `<p>${esc(u.name)} · ${esc(u.role)} · ${u.lastLoginAt ? `signed in ${fmt.rel(u.lastLoginAt)}` : 'never signed in'}</p>`,
        actions: [
          { label: `${icon('key', 's')} Reset password`, onClick: ({ close }) => { close(); resetPassword(u, ex.clientName, load); } },
          { label: `${icon('edit', 's')} Edit`, kind: 'primary', onClick: ({ close }) => { close(); loginDialog({ client: clientOf(ex), exams: examsOf(ex), roles: data.clientRoles || [], user: { ...u, roleId: (data.clientRoles || []).find((r) => r.name === u.role)?.id }, onDone: load }); } },
        ] });
    };

    $('#x-new', el).addEventListener('click', () => edit(null));
    await load();
  },
};
