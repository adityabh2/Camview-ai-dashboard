// pages/clients.js — CLIENT MANAGEMENT: profiles, project mapping (many-to-many),
// users, sharing activity, visibility policy and notification preferences.

import * as api from '../core/api.js';
import { can } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, empty, toast, dialog, confirmDialog, delegate, $, $$ } from '../core/ui.js';
import { loginDialog, loginListHtml, wireLoginList } from '../components/clientlogins.js';

export default {
  async render(el, ctx) {
    setTitle('Clients', 'Client collaboration · who can receive shared alerts');
    const editable = can('client.manage');
    let data;

    el.innerHTML = `<div class="page-head"><div><h2>Clients</h2><p>A client only ever sees alerts explicitly shared with it, for projects currently assigned to it.</p></div>
      <div class="row">${editable ? `<button class="btn primary" id="c-new">${icon('plus', 's')} New client</button>` : ''}</div></div>
      <div id="c-body">${skeleton(4, 80)}</div>`;

    const projLabel = (id) => { const p = data.projects.find((x) => x.externalId === String(id)); return p ? `${p.code}${p.name && p.name !== p.code ? ' · ' + p.name : ''}` : String(id); };

    const load = async () => {
      try {
        data = await api.get('/api/clients');
        if (ctx.isStale()) return;
        paint();
        if (ctx.query.client) openClient(ctx.query.client);
      } catch (e) {
        if (ctx.isStale()) return;
        $('#c-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const paint = () => {
      $('#c-body', el).innerHTML = data.items.length ? `<div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(320px,1fr))">${data.items.map((c) => {
        const pc = c.publicationCounts || {};
        return card({
          cls: 'client-card',
          title: `${icon('building')} ${esc(c.name)}`,
          sub: esc(c.contact || ''),
          actions: c.status === 'active' ? `<span class="b vis-shared">${icon('check')}active</span>` : `<span class="b vis-withdrawn">${icon('x')}inactive</span>`,
          body: `<div class="kpis" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px">
              <div class="kpi" style="padding:8px"><div class="k-label">Shared</div><div class="k-value" style="font-size:18px">${fmt.n(c.sharedAlerts)}</div></div>
              <div class="kpi" style="padding:8px"><div class="k-label">Acknowledged</div><div class="k-value" style="font-size:18px">${fmt.n(c.acknowledged)}</div></div>
              <div class="kpi" style="padding:8px"><div class="k-label">Users</div><div class="k-value" style="font-size:18px">${c.users.length}</div></div>
              <div class="kpi" style="padding:8px"><div class="k-label">Reports</div><div class="k-value" style="font-size:18px">${fmt.n(c.reports)}</div></div></div>
            <div class="muted" style="font-size:11.5px;margin-bottom:4px">Assigned projects</div>
            <div class="row tight" style="margin-bottom:10px">${c.projects.length ? c.projects.map((p) => `<span class="b outline mono">${esc(projLabel(p))}</span>`).join('') : '<span class="muted">None — the client sees nothing</span>'}</div>
            <div class="cl-head"><span>${icon('key', 's')} Client logins <b>${c.users.length}</b></span>${data.canManageLogins ? `<button class="btn sm primary" data-cl-new="${esc(c.id)}">${icon('plus', 's')} Add login</button>` : ''}</div>
            <div data-cl-list="${esc(c.id)}">${loginListHtml(c.users, { canManage: data.canManageLogins, examsById: Object.fromEntries((c.exams || []).map((e) => [e.id, e.name])) })}</div>
            <div class="row" style="margin-top:10px"><button class="btn sm" data-open="${esc(c.id)}">${icon(editable ? 'edit' : 'eye', 's')} ${editable ? 'Manage' : 'Details'}</button>
              <a class="btn sm ghost" href="#/sharing?tab=shared&clientId=${encodeURIComponent(c.id)}">View shared alerts</a></div>`,
        });
      }).join('')}</div>` : empty('No clients yet', editable ? 'Create a client and assign projects to start sharing validated alerts.' : 'No clients have been set up.', 'building');
      // login actions (create / edit / reset password / enable-disable) — shared with the Exams page
      data.items.forEach((c) => {
        const box = $(`[data-cl-list="${CSS.escape(c.id)}"]`, el);
        if (box) wireLoginList(box, { users: c.users, client: c, exams: c.exams || [], roles: data.clientRoles || [], onDone: load });
      });
      $$('[data-cl-new]', el).forEach((b) => b.addEventListener('click', () => {
        const c = data.items.find((x) => x.id === b.dataset.clNew);
        loginDialog({ client: c, exams: c.exams || [], roles: data.clientRoles || [], onDone: load });
      }));
    };

    function openClient(id) {
      const c = data.items.find((x) => x.id === id);
      if (!c) return;
      const dis = editable ? '' : 'disabled';
      const d = dialog({
        title: `${icon('building')} ${esc(c.name)}`, side: true,
        body: `<div class="field"><label>Name</label><input class="input" id="cl-name" value="${esc(c.name)}" ${dis}></div>
          <div class="field"><label>Contact</label><input class="input" id="cl-contact" value="${esc(c.contact || '')}" ${dis}></div>
          <div class="field"><label>Status</label><select class="select" id="cl-status" ${dis}><option value="active">Active</option><option value="inactive" ${c.status === 'inactive' ? 'selected' : ''}>Inactive — all client users lose access</option></select></div>
          <div class="section-title">Assigned projects</div>
          <div class="banner warning">${icon('alert')}<div>Removing a project <b>immediately</b> removes this client's access to every alert shared from it. Access always follows the current assignment.</div></div>
          ${data.projects.map((p) => `<label class="check" style="margin-bottom:6px"><input type="checkbox" data-proj="${esc(p.externalId)}" ${c.projects.includes(p.externalId) ? 'checked' : ''} ${dis}><span class="mono">${esc(p.code)}</span><span class="muted">${esc(p.name && p.name !== p.code ? p.name : '')}</span></label>`).join('') || '<span class="muted">No projects available to you.</span>'}
          ${c.projects.filter((p) => !data.projects.some((x) => x.externalId === p)).map((p) => `<label class="check"><input type="checkbox" data-proj="${esc(p)}" checked ${dis}><span class="mono">${esc(p)}</span><span class="muted">(outside your scope)</span></label>`).join('')}
          <div class="section-title">Visibility policy</div>
          <label class="check"><input type="checkbox" id="cl-ticket" ${c.visibilityPolicy.showTicket ? 'checked' : ''} ${dis}> Show ticket numbers on shared alerts</label>
          <div class="section-title">Notification preferences</div>
          <label class="check" style="margin-bottom:6px"><input type="checkbox" id="cl-inapp" ${c.notificationPrefs.inApp !== false ? 'checked' : ''} ${dis}> In-app notifications (new/critical alerts, reports, withdrawals)</label>
          <label class="check" style="margin-bottom:6px"><input type="checkbox" disabled> Email <span class="b outline">not integrated</span></label>
          <label class="check"><input type="checkbox" disabled> SMS <span class="b outline">not integrated</span></label>
          <div class="section-title">Users (${c.users.length})</div>
          <div class="muted" style="font-size:12px;margin-bottom:6px">Manage logins (create, reset password, disable) on the client card.</div>
          ${loginListHtml(c.users, { canManage: false, examsById: Object.fromEntries((c.exams || []).map((e) => [e.id, e.name])) })}
          <div class="row" style="margin-top:8px"><a class="btn sm ghost" href="#/sharing?tab=shared&clientId=${encodeURIComponent(c.id)}">Shared alerts</a></div>`,
        actions: editable ? [{ label: 'Cancel', onClick: ({ close }) => close() }, { label: 'Save', kind: 'primary', onClick: async ({ close, el: box }) => {
          const projects = $$('[data-proj]:checked', box).map((x) => x.dataset.proj);
          const removed = c.projects.filter((p) => !projects.includes(p));
          if (removed.length && !(await confirmDialog({ title: 'Remove project access?', danger: true, confirmLabel: 'Remove access',
            message: `<p>${esc(c.name)} will immediately lose access to alerts shared from: <b class="mono">${removed.map(esc).join(', ')}</b>.</p>` }))) return;
          try {
            await api.put(`/api/clients/${encodeURIComponent(c.id)}`, {
              name: $('#cl-name', box).value.trim(), contact: $('#cl-contact', box).value.trim(), status: $('#cl-status', box).value,
              projects, visibilityPolicy: { ...c.visibilityPolicy, showTicket: $('#cl-ticket', box).checked },
              notificationPrefs: { ...c.notificationPrefs, inApp: $('#cl-inapp', box).checked },
            });
            toast('Client saved', 'success'); close(); load();
          } catch (err) { toast(err.message, 'error'); }
        } }] : [],
      });
      return d;
    }

    ctx.onCleanup(delegate(el, 'click', '[data-open]', (e, b) => openClient(b.dataset.open)));
    $('#c-new', el)?.addEventListener('click', () => {
      dialog({ title: `${icon('plus')} New client`,
        body: `<div class="field"><label>Client name</label><input class="input" id="nc-name" autofocus></div>
          <div class="field"><label>Contact</label><input class="input" id="nc-contact" placeholder="email or phone"></div>
          <div class="section-title">Projects</div>${data.projects.map((p) => `<label class="check" style="margin-bottom:6px"><input type="checkbox" data-np="${esc(p.externalId)}"><span class="mono">${esc(p.code)}</span><span class="muted">${esc(p.name || '')}</span></label>`).join('')}
          <p class="muted">A new client sees nothing until alerts are explicitly shared with it.</p>`,
        actions: [{ label: 'Cancel', onClick: ({ close }) => close() }, { label: 'Create client', kind: 'primary', onClick: async ({ close, el: box }) => {
          try { await api.post('/api/clients', { name: $('#nc-name', box).value.trim(), contact: $('#nc-contact', box).value.trim(), projects: $$('[data-np]:checked', box).map((x) => x.dataset.np) }); close(); toast('Client created', 'success'); load(); }
          catch (err) { toast(err.message, 'error'); }
        } }] });
    });
    await load();
  },
};
