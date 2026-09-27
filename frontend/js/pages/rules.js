// pages/rules.js — ALERT RULES (visual builder), SCHEDULED / EVENT-PHASE ALERTS, TEMPLATE LIBRARY.
// Delivery is in-app only; other channels are shown as not integrated (never faked).

import * as api from '../core/api.js';
import { can, currentProject } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, empty, toast, dialog, confirmDialog, delegate, sevBadge, table, $, $$ } from '../core/ui.js';
import { columns, investigateHref } from '../components/alarms.js';

const TABS = [['rules', 'Alert rules'], ['groups', 'Recipient groups'], ['schedules', 'Scheduled & event alerts'], ['templates', 'Template library']];
const STATUS_BADGE = { active: '<span class="b vis-shared">✓ Active</span>', draft: '<span class="b vis-ready_for_review">✎ Draft</span>', disabled: '<span class="b outline">✕ Disabled</span>' };
const PERIODS = [[1, 'Last 1 hour'], [6, 'Last 6 hours'], [24, 'Last 24 hours'], [168, 'Last 7 days'], [336, 'Last 14 days']];

export default {
  async render(el, ctx) {
    setTitle('Alert Rules', 'Administration · rules, schedules and templates');
    const edit = can('alert.manage');
    let tab = ctx.query.tab || 'rules';
    let R, S;

    el.innerHTML = `<div class="page-head"><div><h2>Alert rules &amp; schedules</h2><p>Rules create explainable alerts from alarm data. Delivery channel: in-app. ${edit ? '' : icon('lock', 's') + ' Read-only (needs alert.manage).'}</p></div>
      <div class="row">${edit ? `<button class="btn primary" id="new-rule">${icon('plus', 's')} New rule</button><button class="btn" id="new-group">${icon('users', 's')} New group</button><button class="btn" id="new-sched">${icon('calendar', 's')} New schedule</button>` : ''}</div></div>
      <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab ${k === tab ? 'on' : ''}" data-tab="${k}" role="tab">${l}</button>`).join('')}</div>
      <div id="ru-body">${skeleton(5, 40)}</div>`;

    const load = async () => {
      try {
        [R, S] = await Promise.all([api.get('/api/alert-rules'), api.get('/api/schedules')]);
        if (users === null && can('user.view')) {
          users = await api.get('/api/users').then((x) => x.items.filter((u) => u.audience === 'internal' && u.status === 'active')).catch(() => []);
        }
        if (ctx.isStale()) return;
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#ru-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const roleName = (id) => {
      if (String(id).startsWith('group:')) { const g = (R.groups || []).find((x) => `group:${x.id}` === id); return g ? `👥 ${g.name}` : id; }
      return R.roles.find((r) => r.id === id)?.name || id;
    };
    const condText = (c) => `${esc(R.fields[c.field] || c.field)} ${esc(R.ops[c.op] || c.op)} <b>${esc(c.value)}</b>`;

    const paint = () => {
      const body = $('#ru-body', el);
      if (tab === 'rules') {
        body.innerHTML = R.items.length ? `<div class="stack">${R.items.map((r) => card({
          title: `${icon('rules')} ${esc(r.name)}`,
          sub: `v${esc(r.version || 1)} · created by ${esc(r.created_by || '—')} ${fmt.dt(r.created_at)}${r.updated_by ? ` · last changed by ${esc(r.updated_by)} ${fmt.rel(r.updated_at)}` : ''}`,
          actions: `${sevBadge(r.severity)}${STATUS_BADGE[r.status] || STATUS_BADGE.disabled}<span class="b outline mono">v${esc(r.version || 1)}</span>
            <button class="btn sm ghost" data-hist="${r.id}">${icon('history', 's')} History</button>
            ${edit ? `${['active', 'draft', 'disabled'].filter((st) => st !== r.status).map((st) => `<button class="btn sm" data-status="${r.id}" data-to="${st}">${{ active: 'Activate', draft: 'Move to draft', disabled: 'Disable' }[st]}</button>`).join('')}
              <button class="btn sm" data-edit="${r.id}">${icon('edit', 's')} Edit</button><button class="btn sm ghost" data-del="${r.id}" aria-label="Delete rule">${icon('trash', 's')}</button>` : ''}`,
          body: `<div class="row" style="gap:6px;align-items:flex-start">
            ${flow('Event', 'Alarm in working set')}${flow('Conditions', r.conditions.map(condText).join('<br>AND '))}${flow('Scope', esc(r.scope))}
            ${flow('Recipients', (r.recipients || []).map((x) => esc(roleName(x))).join(', ') || '—')}${flow('Channel', 'In-app')}
            ${flow('Escalation', r.escalation ? `after ${esc(r.escalation.afterMinutes)} min → ${esc(roleName(r.escalation.roleId))}` : 'none')}${flow('Action', esc(r.action === 'queue' ? 'Add to work queue' : 'Notify') + (r.template ? ` · ${esc(r.template)}` : ''))}</div>`,
        })).join('')}</div>` : empty('No alert rules', edit ? 'Create a rule to raise explainable alerts from alarm conditions.' : 'No rules configured.', 'rules');
      } else if (tab === 'groups') {
        const gs = R.groups || [];
        body.innerHTML = card({ title: `${icon('users')} Recipient groups`, sub: 'Named sets of internal roles and users (Control Room, Supervisor Team, Management…). Use them as rule or schedule recipients; delivery still respects each member\'s permissions and scope.', flush: true,
          body: gs.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Group</th><th>Description</th><th>Roles</th><th>Users</th><th>Used by rules</th><th></th></tr></thead><tbody>
            ${gs.map((g) => `<tr><td><b>${esc(g.name)}</b></td><td class="dim">${esc(g.description || '—')}</td>
              <td>${(g.roles || []).map((x) => `<span class="b outline">${esc(roleName(x))}</span>`).join(' ') || '<span class="muted">—</span>'}</td>
              <td>${(g.userIds || []).map((u) => `<span class="b outline">${esc(userName(u))}</span>`).join(' ') || '<span class="muted">—</span>'}</td>
              <td class="num">${R.items.filter((r) => (r.recipients || []).includes(`group:${g.id}`)).length}</td>
              <td>${edit ? `<button class="btn sm" data-gedit="${g.id}">${icon('edit', 's')} Edit</button><button class="btn sm ghost" data-gdel="${g.id}" aria-label="Delete group">${icon('trash', 's')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
            : empty('No recipient groups', edit ? 'Create a group such as "Control Room" and use it as a rule recipient.' : 'No groups configured.', 'users') });
      } else if (tab === 'schedules') {
        body.innerHTML = card({ title: `${icon('calendar')} Scheduled & event-phase alerts`, sub: 'PRE-EVENT → START → DURING → END → POST. Fired once, in-app, to the chosen roles.', flush: true,
          body: S.items.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>When</th><th>Phase</th><th>Name</th><th>Message</th><th>Recipients</th><th>Status</th><th></th></tr></thead><tbody>
            ${S.items.map((s) => `<tr><td class="num">${fmt.dt(s.at)}</td><td><span class="b outline">${esc(s.phase.replace(/_/g, ' '))}</span></td><td><b>${esc(s.name)}</b></td>
              <td class="dim">${esc(s.message || S.templates[s.template] || '—')}</td><td>${(s.recipients || []).map((x) => esc(roleName(x))).join(', ') || '—'}</td>
              <td>${s.fired_at ? `<span class="b vis-shared">${icon('check')}fired ${fmt.rel(s.fired_at)}</span>` : s.enabled ? `<span class="b outline">${icon('clock')}scheduled</span>` : '<span class="b outline">disabled</span>'}</td>
              <td>${edit ? `${s.fired_at ? '' : `<button class="btn sm" data-stoggle="${s.id}" data-on="${s.enabled ? 0 : 1}">${s.enabled ? 'Disable' : 'Enable'}</button>`}<button class="btn sm ghost" data-sdel="${s.id}" aria-label="Delete schedule">${icon('trash', 's')}</button>` : ''}</td></tr>`).join('')}</tbody></table></div>`
            : empty('No schedules', 'Add event phases (e.g. "Paper 1 ends") to notify teams at the right time.', 'calendar') });
      } else {
        body.innerHTML = card({ title: `${icon('report')} Template library`, sub: 'Used by rules and schedules', flush: true,
          body: `<div class="list">${Object.entries(R.templates).map(([k, v]) => `<div class="li"><span class="b outline mono">${esc(k)}</span><div class="grow">${esc(v)}</div></div>`).join('')}</div>` });
      }
    };
    const flow = (label, html) => `<div style="flex:1;min-width:140px;border:1px solid var(--border);border-radius:6px;padding:8px 10px;background:var(--panel-2)"><div class="muted" style="font-size:10px;font-weight:800;letter-spacing:.1em;text-transform:uppercase">${label}</div><div style="font-size:12px;margin-top:3px">${html}</div></div>`;

    function condRow(c = { field: 'priority', op: 'eq', value: 'critical' }) {
      return `<div class="row" data-cond style="margin-bottom:6px"><select class="select" data-cf>${Object.entries(R.fields).map(([k, l]) => `<option value="${esc(k)}" ${k === c.field ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
        <select class="select" data-co>${Object.entries(R.ops).map(([k, l]) => `<option value="${esc(k)}" ${k === c.op ? 'selected' : ''}>${esc(l)}</option>`).join('')}</select>
        <input class="input grow" data-cv value="${esc(c.value)}" placeholder="value (comma-separated for 'is one of')"><button class="btn icon ghost" data-crm aria-label="Remove condition">${icon('trash', 's')}</button></div>`;
    }

    function ruleDialog(r) {
      const esc_ = r?.escalation || null;
      const d = dialog({
        title: r ? `${icon('edit')} Edit rule` : `${icon('plus')} New alert rule`, size: 'lg',
        body: `<div class="field"><label>Rule name</label><input class="input" id="r-name" value="${esc(r?.name || '')}" autofocus></div>
          <div class="section-title">1 · Event</div><div class="dim">An alarm in the monitored working set (evaluated on every refresh).</div>
          <div class="section-title">2 · Conditions (all must match)</div><div id="r-conds">${(r?.conditions?.length ? r.conditions : [undefined]).map(condRow).join('')}</div>
          <button class="btn sm" id="r-addc">${icon('plus', 's')} Add condition</button>
          <div class="muted" style="font-size:11px;margin-top:6px">Priority values: critical/high/medium/low · lastActionType: 0 pending, 1 valid, 2 invalid, 3 exception · workflow: NEW, UNDER_REVIEW, INVESTIGATING, READY_FOR_CLIENT, READY_FOR_APPROVAL, APPROVED, SHARED…</div>
          <div class="grid g-2" style="margin-top:12px">
            <div class="field"><label>3 · Scope</label><select class="select" id="r-scope">${R.scopes.map((s) => `<option ${s === (r?.scope || 'camera') ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></div>
            <div class="field"><label>4 · Severity</label><select class="select" id="r-sev">${R.severities.map((s) => `<option ${s === (r?.severity || 'warning') ? 'selected' : ''}>${esc(s)}</option>`).join('')}</select></div></div>
          <div class="section-title">5 · Recipients</div><div class="muted" style="font-size:11.5px;margin-bottom:6px">Roles</div><div class="row">${R.roles.map((ro) => `<label class="check"><input type="checkbox" data-rcp="${esc(ro.id)}" ${(r?.recipients || []).includes(ro.id) ? 'checked' : ''}> ${esc(ro.name)}</label>`).join('')}</div>
          <div class="muted" style="font-size:11.5px;margin:8px 0 6px">Recipient groups</div><div class="row">${(R.groups || []).length ? R.groups.map((g) => `<label class="check"><input type="checkbox" data-rcp="group:${esc(g.id)}" ${(r?.recipients || []).includes(`group:${g.id}`) ? 'checked' : ''}> ${icon('users', 's')} ${esc(g.name)}</label>`).join('') : '<span class="muted">No groups yet — create them in the Recipient groups tab.</span>'}</div>
          <div class="section-title">6 · Channel</div><div class="row">${Object.entries(R.channels).map(([k, on]) => `<label class="check"><input type="radio" name="r-ch" value="${esc(k)}" ${on ? '' : 'disabled'} ${k === (r?.channel || 'in_app') ? 'checked' : ''}> ${esc(k.replace('_', '-'))}${on ? '' : ' <span class="b outline">not integrated</span>'}</label>`).join('')}</div>
          <div class="section-title">7 · Escalation (optional)</div><div class="row"><label class="check"><input type="checkbox" id="r-esc" ${esc_ ? 'checked' : ''}> after</label>
            <input class="input" id="r-escmin" type="number" min="1" style="width:90px" value="${esc(esc_?.afterMinutes ?? 20)}"><span class="dim">minutes notify</span>
            <select class="select" id="r-escrole">${R.roles.map((ro) => `<option value="${esc(ro.id)}" ${esc_?.roleId === ro.id ? 'selected' : ''}>${esc(ro.name)}</option>`).join('')}</select></div>
          <div class="grid g-2" style="margin-top:12px"><div class="field"><label>8 · Action</label><select class="select" id="r-act"><option value="notify" ${r?.action !== 'queue' ? 'selected' : ''}>Create alert + notify</option><option value="queue" ${r?.action === 'queue' ? 'selected' : ''}>Create alert + add to work queue</option></select></div>
            <div class="field"><label>Template</label><select class="select" id="r-tpl"><option value="">—</option>${Object.keys(R.templates).map((t) => `<option ${t === r?.template ? 'selected' : ''}>${esc(t)}</option>`).join('')}</select></div></div>
          <div class="grid g-2"><div class="field"><label>Status</label><select class="select" id="r-status">${(R.statuses || ['active', 'draft', 'disabled']).map((st) => `<option value="${st}" ${st === (r?.status || 'draft') ? 'selected' : ''}>${{ active: 'Active — evaluated now', draft: 'Draft — saved, not evaluated', disabled: 'Disabled' }[st] || st}</option>`).join('')}</select>
            <div class="hint">Every save creates a new version (v${r ? (r.version || 1) + 1 : 1}).</div></div>
            <div class="field"><label>Test period</label><div class="row"><select class="select" id="r-period">${PERIODS.map(([h, l]) => `<option value="${h}" ${h === 24 ? 'selected' : ''}>${l}</option>`).join('')}</select><button class="btn" type="button" id="r-testbtn">${icon('play', 's')} Test rule</button></div>
            <div class="hint">Testing never changes production behaviour.</div></div></div>
          <div id="r-test" style="margin-top:8px"></div>`,
        actions: [
          { label: 'Cancel', onClick: ({ close }) => close() },
          { label: r ? 'Save rule' : 'Create rule', kind: 'primary', onClick: async ({ close, el: box }) => {
            const payload = { name: $('#r-name', box).value.trim(), conditions: conds(box), scope: $('#r-scope', box).value, severity: $('#r-sev', box).value,
              recipients: $$('[data-rcp]:checked', box).map((x) => x.dataset.rcp), channel: box.querySelector('[name=r-ch]:checked')?.value || 'in_app',
              escalation: $('#r-esc', box).checked ? { afterMinutes: Number($('#r-escmin', box).value), roleId: $('#r-escrole', box).value } : null,
              action: $('#r-act', box).value, template: $('#r-tpl', box).value || null, status: $('#r-status', box).value };
            try {
              const res = r ? await api.put(`/api/alert-rules/${r.id}`, payload) : await api.post('/api/alert-rules', payload);
              close(); toast(r && res.changed && !res.changed.length ? 'No changes to save' : `Rule saved as v${res.version}`, 'success'); setTab('rules'); load();
            }
            catch (e) { toast(e.message, 'error'); }
          } }],
      });
      $('#r-addc', d.el).addEventListener('click', () => $('#r-conds', d.el).insertAdjacentHTML('beforeend', condRow()));
      $('#r-testbtn', d.el).addEventListener('click', () => testRule(d.el));
      delegate(d.el, 'click', '[data-crm]', (e, b) => b.closest('[data-cond]').remove());
    }
    const conds = (box) => $$('[data-cond]', box).map((r) => ({ field: $('[data-cf]', r).value, op: $('[data-co]', r).value, value: $('[data-cv]', r).value.trim() })).filter((c) => c.value !== '');

    const setTab = (t) => { tab = t; ctx.setQuery({ tab }); $$('[data-tab]', el).forEach((x) => x.classList.toggle('on', x.dataset.tab === tab)); };
    const userName = (id) => (users || []).find((u) => u.id === id)?.name || id;
    let users = null;

    async function testRule(box) {
      const out = $('#r-test', box);
      const hours = +$('#r-period', box).value;
      out.innerHTML = skeleton(3, 20);
      try {
        const t = await api.post('/api/alert-rules/test', { conditions: conds(box), projectId: currentProject(), hours });
        const label = PERIODS.find((p) => p[0] === hours)?.[1] || `${hours} h`;
        out.innerHTML = `<div class="banner info">${icon('info')}<div><b>${t.matches}</b> matching alarm(s) of <b>${t.scanned}</b> scanned · ${esc(label.toLowerCase())}${t.truncated ? ' (newest alarms only)' : ''}<br><span class="dim">${t.explanation.map(esc).join(' AND ') || 'No conditions'}</span></div></div>
          <div id="r-matches" style="max-height:280px;overflow:auto;border:1px solid var(--border);border-radius:6px"></div>
          ${t.matches > t.items.length ? `<div class="muted" style="font-size:11.5px;margin-top:4px">Showing the newest ${t.items.length} of ${t.matches} matches.</div>` : ''}`;
        table($('#r-matches', out), { columns: columns(['priority', 'alarm', 'camera', 'state', 'workflow', 'last']), rows: t.items,
          onRow: (a) => { location.hash = investigateHref(a); },
          emptyHtml: '<div class="empty"><div class="e-t">No matching alarms in this period</div></div>' });
        out.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } catch (e) { out.innerHTML = `<div class="banner critical">${icon('alert')}<div>${esc(e.message)}</div></div>`; }
    }

    async function historyDialog(r) {
      const d = dialog({ title: `${icon('history')} Version history — ${esc(r.name)}`, size: 'lg', body: skeleton(4, 30) });
      try {
        const v = await api.get(`/api/alert-rules/${r.id}/versions`);
        d.el.querySelector('.d-b').innerHTML = v.items.length ? `<div class="list">${v.items.map((x) => `<div class="li" style="flex-direction:column;align-items:stretch">
          <div class="row"><span class="b outline mono">v${esc(x.version)}</span>${STATUS_BADGE[x.status] || ''}${x.version === r.version ? '<span class="b vis-approved">current</span>' : ''}<span class="grow"></span><span class="dim" style="font-size:12px">${esc(x.changedBy || '—')} · ${fmt.dt(x.changedAt)}</span></div>
          <div style="margin-top:4px">${esc(x.summary || '')}</div>
          <details style="margin-top:4px"><summary class="muted" style="cursor:pointer;font-size:12px">Show conditions & settings</summary>
            <div style="font-size:12px;margin-top:6px">${(x.snapshot.conditions || []).map(condText).join('<br>AND ')}<br><span class="dim">Scope ${esc(x.snapshot.scope)} · severity ${esc(x.snapshot.severity)} · action ${esc(x.snapshot.action)} · recipients ${(x.snapshot.recipients || []).map((y) => esc(roleName(y))).join(', ') || '—'}</span></div></details></div>`).join('')}</div>`
          : '<div class="empty"><div class="e-t">No versions recorded</div></div>';
      } catch (e) { d.el.querySelector('.d-b').innerHTML = errorBox(e, { retry: false }); }
    }

    async function groupDialog(g) {
      if (users === null) {
        users = can('user.view') ? await api.get('/api/users').then((x) => x.items.filter((u) => u.audience === 'internal' && u.status === 'active')).catch(() => []) : [];
      }
      dialog({ title: g ? `${icon('edit')} Edit recipient group` : `${icon('users')} New recipient group`,
        body: `<div class="field"><label>Name</label><input class="input" id="g-name" value="${esc(g?.name || '')}" placeholder="e.g. Control Room" autofocus></div>
          <div class="field"><label>Description</label><input class="input" id="g-desc" value="${esc(g?.description || '')}"></div>
          <div class="section-title">Internal roles</div><div class="row">${R.roles.map((ro) => `<label class="check"><input type="checkbox" data-grole="${esc(ro.id)}" ${(g?.roles || []).includes(ro.id) ? 'checked' : ''}> ${esc(ro.name)}</label>`).join('')}</div>
          <div class="section-title">Internal users</div>${can('user.view') ? `<div class="row" style="max-height:200px;overflow:auto">${users.map((u) => `<label class="check"><input type="checkbox" data-guser="${esc(u.id)}" ${(g?.userIds || []).includes(u.id) ? 'checked' : ''}> ${esc(u.name)} <span class="muted">${esc(u.roleName)}</span></label>`).join('') || '<span class="muted">No internal users</span>'}</div>` : '<div class="muted">You can pick roles only (viewing users needs user.view).</div>'}
          <p class="muted" style="margin-top:10px">Client users can never be members. Delivery still checks each member's permissions and scope.</p>`,
        actions: [{ label: 'Cancel', onClick: ({ close }) => close() }, { label: g ? 'Save group' : 'Create group', kind: 'primary', onClick: async ({ close, el: box }) => {
          const payload = { name: $('#g-name', box).value.trim(), description: $('#g-desc', box).value.trim(),
            roles: $$('[data-grole]:checked', box).map((x) => x.dataset.grole),
            userIds: can('user.view') ? $$('[data-guser]:checked', box).map((x) => x.dataset.guser) : (g?.userIds || []) };
          if (!payload.name) return toast('Give the group a name', 'warning');
          try { g ? await api.put(`/api/recipient-groups/${g.id}`, payload) : await api.post('/api/recipient-groups', payload); close(); toast('Group saved', 'success'); setTab('groups'); load(); }
          catch (e) { toast(e.message, 'error'); }
        } }] });
    }

    function schedDialog() {
      dialog({ title: `${icon('calendar')} New scheduled / event alert`,
        body: `<div class="field"><label>Name</label><input class="input" id="s-name" placeholder="e.g. Paper 1 — last 30 minutes" autofocus></div>
          <div class="grid g-2"><div class="field"><label>Phase</label><select class="select" id="s-phase">${S.phases.map((p) => `<option value="${esc(p)}">${esc(p.replace(/_/g, ' '))}</option>`).join('')}</select></div>
          <div class="field"><label>When (your local time)</label><input class="input" type="datetime-local" id="s-at"></div></div>
          <div class="field"><label>Template</label><select class="select" id="s-tpl"><option value="">— custom message —</option>${Object.entries(S.templates).map(([k, v]) => `<option value="${esc(k)}">${esc(k)} — ${esc(v)}</option>`).join('')}</select></div>
          <div class="field"><label>Message (overrides the template)</label><textarea class="input" id="s-msg" rows="2"></textarea></div>
          <div class="section-title">Recipients</div><div class="row">${R.roles.map((ro) => `<label class="check"><input type="checkbox" data-srcp="${esc(ro.id)}"> ${esc(ro.name)}</label>`).join('')}${(R.groups || []).map((g) => `<label class="check"><input type="checkbox" data-srcp="group:${esc(g.id)}"> ${icon('users', 's')} ${esc(g.name)}</label>`).join('')}</div>
          <p class="muted">Delivered once, in-app, when the time passes (checked while Command Center is in use). Email/SMS are not integrated.</p>`,
        actions: [{ label: 'Cancel', onClick: ({ close }) => close() }, { label: 'Create', kind: 'primary', onClick: async ({ close, el: box }) => {
          const at = $('#s-at', box).value;
          if (!at) return toast('Choose a date and time', 'warning');
          try {
            await api.post('/api/schedules', { name: $('#s-name', box).value.trim(), phase: $('#s-phase', box).value, at: new Date(at).toISOString(),
              template: $('#s-tpl', box).value || null, message: $('#s-msg', box).value.trim() || null, recipients: $$('[data-srcp]:checked', box).map((x) => x.dataset.srcp) });
            close(); toast('Schedule created', 'success'); setTab('schedules'); load();
          } catch (e) { toast(e.message, 'error'); }
        } }] });
    }

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; ctx.setQuery({ tab }); $$('[data-tab]', el).forEach((x) => x.classList.toggle('on', x === b)); if (R) paint(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-edit]', (e, b) => ruleDialog(R.items.find((x) => String(x.id) === b.dataset.edit))));
    ctx.onCleanup(delegate(el, 'click', '[data-status]', async (e, b) => {
      try { const res = await api.put(`/api/alert-rules/${b.dataset.status}`, { status: b.dataset.to }); toast(`Rule is now ${b.dataset.to} (v${res.version})`, 'success'); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-hist]', (e, b) => historyDialog(R.items.find((x) => String(x.id) === b.dataset.hist))));
    ctx.onCleanup(delegate(el, 'click', '[data-gedit]', (e, b) => groupDialog((R.groups || []).find((x) => String(x.id) === b.dataset.gedit))));
    ctx.onCleanup(delegate(el, 'click', '[data-gdel]', async (e, b) => {
      const used = R.items.filter((r) => (r.recipients || []).includes(`group:${b.dataset.gdel}`)).length;
      if (!(await confirmDialog({ title: 'Delete recipient group?', message: used ? `${used} rule(s) use this group; they will no longer notify its members.` : 'The group is not used by any rule.', danger: true, confirmLabel: 'Delete' }))) return;
      try { await api.del(`/api/recipient-groups/${b.dataset.gdel}`, {}); toast('Group deleted', 'success'); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-del]', async (e, b) => {
      if (!(await confirmDialog({ title: 'Delete rule?', message: 'The rule stops creating alerts. Past notifications remain.', danger: true, confirmLabel: 'Delete' }))) return;
      try { await api.del(`/api/alert-rules/${b.dataset.del}`, {}); toast('Rule deleted', 'success'); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-stoggle]', async (e, b) => {
      try { await api.put(`/api/schedules/${b.dataset.stoggle}`, { enabled: b.dataset.on === '1' }); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    ctx.onCleanup(delegate(el, 'click', '[data-sdel]', async (e, b) => {
      if (!(await confirmDialog({ title: 'Delete schedule?', message: 'It will not fire.', danger: true, confirmLabel: 'Delete' }))) return;
      try { await api.del(`/api/schedules/${b.dataset.sdel}`, {}); load(); } catch (err) { toast(err.message, 'error'); }
    }));
    $('#new-rule', el)?.addEventListener('click', () => ruleDialog(null));
    $('#new-sched', el)?.addEventListener('click', schedDialog);
    $('#new-group', el)?.addEventListener('click', () => groupDialog(null));
    await load();
  },
};
