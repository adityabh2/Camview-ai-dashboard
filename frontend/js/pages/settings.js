// pages/settings.js — SETTINGS: workflow & sharing policy, intelligence thresholds,
// escalation, Camview connection, monitored projects, system status, feature flags.

import * as api from '../core/api.js';
import { can, session } from '../core/state.js';
import { setTitle, applyBranding, brandInnerHtml } from '../core/layout.js';
import { esc, icon, fmt, card, errorBox, skeleton, toast, confirmDialog, delegate, $, $$ } from '../core/ui.js';

const TABS = [['workflow', 'Workflow & sharing'], ['intelligence', 'Intelligence thresholds'], ['escalation', 'Escalation'],
  ['connection', 'Connection'], ['projects', 'Monitored projects'], ['health', 'Camera health'], ['system', 'System status'], ['features', 'Feature flags'], ['branding', 'Branding']];
const LOGO_TYPES = { png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg', webp: 'image/webp', svg: 'image/svg+xml' };
const LOGO_MAX = 512 * 1024;
const LEVELS = ['project', 'tc', 'centre', 'building', 'floor', 'room', 'camera'];
const THRESHOLDS = [
  ['repeatThreshold', 'Repeated activity — minimum reports', 'Occurrences (totalTimesReported) that make an alarm "repeated".'],
  ['repeatWindowMinutes', 'Repeated activity — window (min)', 'firstInstance → lastInstance must be within this many minutes.'],
  ['cameraActivityThreshold', 'High camera activity — alarms', 'Alarms from one camera within the window below.'],
  ['cameraActivityWindowMinutes', 'High camera activity — window (min)', ''],
  ['relatedMinCount', 'Related alarms — minimum count', 'Alarms from ≥2 cameras in the same room/centre (needs nomenclature).'],
  ['relatedWindowMinutes', 'Related alarms — window (min)', ''],
  ['spikeRatio', 'Activity spike — ratio vs baseline', 'Last hour ÷ average hour of the previous 24 h (needs ≥ 6 h of data).'],
  ['spikeMinCount', 'Activity spike — minimum alarms in last hour', ''],
  ['stormCount', 'Activity surge — alarms', 'Alarms within the window below.'],
  ['stormWindowSeconds', 'Activity surge — window (seconds)', ''],
  ['suppressionThreshold', 'Suppression activity — suppressed alarms per hour', ''],
  ['longPendingMinutes', 'Long-pending limit (min)', 'Leave blank to disable. No SLA is assumed — set only an agreed value.'],
  ['slaTargetMinutes', 'Attention timer — target (min)', 'Blank = disabled. Pending alarms past this show "Attention required".'],
  ['slaWarnMinutes', 'Attention timer — warning (min)', 'Blank = disabled. Shows "Approaching target".'],
];

export default {
  async render(el, ctx) {
    setTitle('Settings', 'Administration · policy, connection and system');
    const edit = can('settings.manage');
    let tab = ctx.query.tab || 'workflow';
    let data, roles = [];

    el.innerHTML = `<div class="page-head"><div><h2>Settings</h2><p>${edit ? 'Changes are audited.' : `${icon('lock', 's')} Read-only — changing settings needs settings.manage.`}</p></div></div>
      <div class="tabs" role="tablist">${TABS.map(([k, l]) => `<button class="tab ${k === tab ? 'on' : ''}" role="tab" data-tab="${k}">${l}</button>`).join('')}</div>
      <div id="s-body">${skeleton(6, 30)}</div>`;

    const load = async () => {
      try {
        const [s, r] = await Promise.all([api.get('/api/settings'), can('role.view') ? api.get('/api/roles') : Promise.resolve({ items: [] })]);
        if (ctx.isStale()) return;
        data = s;
        roles = r.items.filter((x) => x.audience === 'internal');
        paint();
      } catch (e) {
        if (ctx.isStale()) return;
        $('#s-body', el).innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const dis = edit ? '' : 'disabled';
    const savePolicy = async (patch) => {
      const changed = Object.fromEntries(Object.entries(patch).filter(([k, v]) => JSON.stringify(v) !== JSON.stringify(data.policy[k])));
      if (!Object.keys(changed).length) return toast('No changes');
      try { const r = await api.put('/api/settings/policy', changed); data.policy = r.policy; toast('Saved (recorded in audit trail)', 'success'); paint(); }
      catch (e) { toast(e.message, 'error'); }
    };

    const paint = () => {
      const p = data.policy;
      const c = data.connection;
      const body = $('#s-body', el);
      if (tab === 'workflow') {
        const mode = p.deliveryMode || 'controlled';
        const trig = p.deliveryTrigger === 'arrival' ? 'arrival' : 'valid';
        body.innerHTML = card({ title: `${icon('zap')} Client delivery (alert → ticket → client)`, sub: 'When and how alerts reach the exam’s client. INVALID and EXCEPTION alerts are never delivered (and are withdrawn if they had been).',
          body: `<div class="banner ${p.clientsSeeOperatorValidOnly !== false ? 'info' : 'warning'}" style="margin-bottom:12px">${icon('shield')}<div class="grow">
              <label class="check" style="margin:0"><input type="checkbox" id="p-opvalid" ${p.clientsSeeOperatorValidOnly !== false ? 'checked' : ''} ${dis}> <b>Clients see only alerts our operations team marked VALID</b></label>
              <div class="muted" style="font-size:12px;margin-top:3px">On (recommended): Camview's own status, delivery on arrival and automatic sharing never reach a client — the options below that would do so are ignored. Administrators still see every verdict. Turning it on withdraws anything delivered without an operator's VALID.</div></div></div>
            <div class="field"><label>When is an alert sent to the client?</label>
            <label class="check" style="margin-bottom:6px"><input type="radio" name="p-trigger" value="arrival" ${trig === 'arrival' ? 'checked' : ''} ${dis}> <b>On arrival — no human intervention</b>: every detection alert is delivered the moment Camview sends it (a 12:00 alert reaches the client at 12:00). Camera status events are never sent. Marking it INVALID / EXCEPTION withdraws it.</label>
            <label class="check" style="margin-bottom:12px"><input type="radio" name="p-trigger" value="valid" ${trig === 'valid' ? 'checked' : ''} ${dis}> <b>When VALID</b>: only after Camview or an operator marks the alert VALID.</label></div>
            <div class="grid g-2"><div>
            <label class="check" style="margin-bottom:8px"><input type="radio" name="p-mode" value="controlled" ${mode === 'controlled' ? 'checked' : ''} ${dis}> <b>Controlled</b> — VALID creates the ticket; a person with “publish” permission sends it with one click</label>
            <label class="check" style="margin-bottom:12px"><input type="radio" name="p-mode" value="automatic" ${mode === 'automatic' ? 'checked' : ''} ${dis}> <b>Automatic</b> — VALID creates the ticket and delivers it to the client immediately</label>
            <label class="check" style="margin-bottom:4px"><input type="checkbox" id="p-autoshare" ${p.autoShareValid ? 'checked' : ''} ${dis}> <b>Send alerts the Camview API marks VALID</b> to the client automatically (no operator action)</label>
            <div class="row tight" style="margin:0 0 10px 23px;font-size:12px"><span class="muted">age limit</span><input class="input" type="number" min="1" id="p-autohours" max="8760" value="${p.autoShareHours ?? ''}" placeholder="none" style="width:80px" ${dis}><span class="muted">hours (empty = every valid alert)</span></div>
            <label class="check" style="margin-bottom:8px"><input type="checkbox" id="p-manual" ${p.manualReview ? 'checked' : ''} ${dis}> Manual review — show VALID / INVALID / EXCEPTION buttons (off = Camview's status decides, nothing to mark)</label>
            <label class="check" style="margin-bottom:8px"><input type="checkbox" id="p-autonom" ${p.autoNomenclature !== false ? 'checked' : ''} ${dis}> Build nomenclature automatically from Camview camera data (Project › Centre › Location › Camera; imported master data wins)</label>
            <label class="check" style="margin-bottom:8px"><input type="checkbox" id="p-remarks" ${p.requireRemarks ? 'checked' : ''} ${dis}> Require a remark on every decision (off = one-click decisions)</label>
            <label class="check" style="margin-bottom:8px"><input type="checkbox" id="p-sendfour" ${p.sendFourEyes ? 'checked' : ''} ${dis}> Controlled mode: the sender cannot be the person who marked it VALID</label></div>
            <div><div class="field"><label for="p-autoev">Evidence sent to the client</label><select class="select" id="p-autoev" ${dis}>
              <option value="all" ${p.autoEvidence === 'all' ? 'selected' : ''}>All images and video</option>
              <option value="first" ${p.autoEvidence === 'first' ? 'selected' : ''}>First item only</option>
              <option value="none" ${p.autoEvidence === 'none' ? 'selected' : ''}>No evidence (details only)</option></select>
              <div class="hint">Clients never see internal notes, operator names or unmapped internal data. Context levels below apply.</div></div></div></div>
            ${edit ? '<div class="row"><span class="grow"></span><button class="btn primary" id="save-dl">Save delivery settings</button></div>' : ''}` })
          + card({ title: `${icon('share')} Workflow & client sharing policy`, sub: 'Valid never means shared. These rules decide who may request, approve and publish.',
          body: `<div class="grid g-2"><div>
            <label class="check" style="margin-bottom:10px"><input type="checkbox" id="p-approval" ${p.requireApproval ? 'checked' : ''} ${dis}> <b>Two-step approval</b> — publishing requires a supervisor approval first</label>
            <label class="check" style="margin-bottom:10px"><input type="checkbox" id="p-foureyes" ${p.fourEyes ? 'checked' : ''} ${dis}> <b>Four-eyes control</b> — the approver cannot be the person who validated or requested</label>
            <label class="check" style="margin-bottom:10px"><input type="checkbox" id="p-evidence" ${p.requireEvidence ? 'checked' : ''} ${dis}> Sharing requires at least one evidence item</label>
            <label class="check" style="margin-bottom:10px"><input type="checkbox" id="p-context" ${p.requireContext ? 'checked' : ''} ${dis}> Sharing requires a camera mapped in the nomenclature</label>
            <label class="check" style="margin-bottom:10px"><input type="checkbox" id="p-ticket" ${p.clientShowTicket ? 'checked' : ''} ${dis}> Default: include ticket numbers (clients' own policy still applies)</label>
            <div class="field" style="margin-top:12px"><label>What counts as "valid" for sharing</label><select class="select" id="p-valid" ${dis}>
              <option value="either" ${p.validSource === 'either' ? 'selected' : ''}>Camview Valid OR operator marked valid</option>
              <option value="ops" ${p.validSource === 'ops' ? 'selected' : ''}>Only an operator decision in Command Center</option>
              <option value="camview" ${p.validSource === 'camview' ? 'selected' : ''}>Only Camview's lastActionType = 1</option></select>
              <div class="hint">An operator marking invalid/exception always blocks sharing.</div></div></div>
            <div><div class="field"><label>Context levels shown to clients by default</label>
              ${LEVELS.map((l) => `<label class="check" style="margin-bottom:5px"><input type="checkbox" data-lvl="${l}" ${p.clientContextLevels.includes(l) ? 'checked' : ''} ${dis}> ${l.toUpperCase()}</label>`).join('')}
              <div class="hint">The publisher can still change this per alert in the share review.</div></div></div></div>
            ${edit ? '<div class="row"><span class="grow"></span><button class="btn primary" id="save-wf">Save policy</button></div>' : ''}` });
        $('#save-dl', body)?.addEventListener('click', () => savePolicy({
          deliveryMode: $('[name="p-mode"]:checked', body).value, deliveryTrigger: $('[name="p-trigger"]:checked', body).value, requireRemarks: $('#p-remarks', body).checked,
          autoShareValid: $('#p-autoshare', body).checked, autoShareHours: $('#p-autohours', body).value === '' ? null : Number($('#p-autohours', body).value),
          manualReview: $('#p-manual', body).checked, autoNomenclature: $('#p-autonom', body).checked,
          clientsSeeOperatorValidOnly: $('#p-opvalid', body).checked,
          sendFourEyes: $('#p-sendfour', body).checked, autoEvidence: $('#p-autoev', body).value }));
        $('#save-wf', body)?.addEventListener('click', () => savePolicy({
          requireApproval: $('#p-approval', body).checked, fourEyes: $('#p-foureyes', body).checked, requireEvidence: $('#p-evidence', body).checked,
          requireContext: $('#p-context', body).checked, clientShowTicket: $('#p-ticket', body).checked, validSource: $('#p-valid', body).value,
          clientContextLevels: $$('[data-lvl]:checked', body).map((x) => x.dataset.lvl) }));
      } else if (tab === 'intelligence') {
        body.innerHTML = card({ title: `${icon('zap')} Intelligence thresholds`, sub: 'Every intelligent alert shows the threshold it used in "Why am I seeing this?"',
          body: `<div class="grid g-2">${THRESHOLDS.map(([k, l, h]) => `<div class="field"><label for="t-${k}">${esc(l)}</label>
            <input class="input" id="t-${k}" data-th="${k}" type="number" min="0" step="any" value="${p[k] ?? ''}" placeholder="${p[k] == null ? 'disabled' : ''}" ${dis}>
            <div class="hint">${esc(h)} ${data.policyDefaults[k] != null ? `Default ${esc(data.policyDefaults[k])}.` : ''}</div></div>`).join('')}</div>
            ${edit ? '<div class="row"><span class="grow"></span><button class="btn primary" id="save-th">Save thresholds</button></div>' : ''}` });
        $('#save-th', body)?.addEventListener('click', () => {
          const patch = {};
          $$('[data-th]', body).forEach((i) => { patch[i.dataset.th] = i.value === '' ? null : Number(i.value); });
          savePolicy(patch);
        });
      } else if (tab === 'escalation') {
        const steps = p.escalation || [];
        body.innerHTML = card({ title: `${icon('alert')} Escalation policy`, sub: 'For critical alarms still pending with no operator decision. Delivered in-app. None is assumed.',
          body: `<div id="esc-steps">${steps.map((s, i) => stepRow(s, i)).join('') || '<div class="muted" id="esc-empty">No escalation steps — nothing escalates automatically.</div>'}</div>
            ${edit ? `<div class="row" style="margin-top:10px"><button class="btn" id="esc-add">${icon('plus', 's')} Add step</button><span class="grow"></span><button class="btn primary" id="esc-save">Save escalation</button></div>` : ''}
            <div class="muted" style="font-size:11.5px;margin-top:10px">Example: Critical → after 15 min Supervisor → after 45 min Manager. Each step notifies users of that role whose scope covers the alarm, once.</div>` });
        $('#esc-add', body)?.addEventListener('click', () => { $('#esc-empty', body)?.remove(); $('#esc-steps', body).insertAdjacentHTML('beforeend', stepRow({ afterMinutes: 15, roleId: roles[0]?.id }, $$('[data-step]', body).length)); });
        $('#esc-save', body)?.addEventListener('click', () => savePolicy({ escalation: $$('[data-step]', body).map((r) => ({ afterMinutes: Number($('[data-after]', r).value), roleId: $('[data-role]', r).value })).filter((s) => s.afterMinutes > 0 && s.roleId) }));
      } else if (tab === 'connection') {
        const setup = c.setupAllowed;
        body.innerHTML = `<div class="grid g-2">${card({ title: `${icon('layers')} Data mode`, sub: 'Demo and live use separate databases',
          body: `<div class="row" style="margin-bottom:10px">Current: ${data.mode === 'demo' ? '<span class="demo-flag">DEMO DATA</span>' : '<span class="b vis-shared">LIVE Camview</span>'}</div>
            <p class="dim">Demo mode uses generated data and demo accounts. Live mode uses Camview listAlarms through the server-side key. Switching signs everyone out (users differ between modes).</p>
            ${edit ? `<button class="btn" id="mode-btn" ${data.mode === 'demo' && !c.apiConfigured ? 'disabled title="Configure the API key first"' : ''}>Switch to ${data.mode === 'demo' ? 'live' : 'demo'} mode</button>` : ''}` })}
          ${card({ title: `${icon('key')} Camview connection`, sub: 'The API key is stored only on the server and is never shown again',
            body: `${!setup ? `<div class="banner warning">${icon('lock')}<div>Connection settings can only be changed from the computer running the backend (open <span class="mono">http://localhost:5000</span> there) by a user with settings.manage, or by editing <span class="mono">backend/.env</span>.</div></div>` : ''}
              <div class="field"><label>API URL</label><input class="input" id="c-url" value="${esc(c.apiUrl)}" ${setup ? '' : 'disabled'}><div class="hint">Must be https://…camviewai.com/…/alarms/listAlarms (no /api prefix).</div></div>
              <div class="field"><label>API key</label><input class="input" id="c-key" type="password" autocomplete="off" placeholder="${c.apiConfigured ? 'A key is saved — leave blank to keep it' : 'Paste the raw key (no Bearer)'}" ${setup ? '' : 'disabled'}></div>
              <div class="field"><label>Default project ID</label><input class="input" id="c-proj" value="${esc(c.defaultProjectId || '')}" ${setup ? '' : 'disabled'}></div>
              <div class="row">${setup ? '<button class="btn primary" id="c-save">Save connection</button>' : ''}${edit ? '<button class="btn" id="c-test">Test connection</button><button class="btn ghost" id="c-raw">Raw sample</button>' : ''}</div>
              <div id="c-out" style="margin-top:12px"></div>
              <dl class="kv" style="margin-top:12px"><dt>Key configured</dt><dd>${c.apiConfigured ? 'Yes' : 'No'}</dd><dt>Timeout</dt><dd>${esc(c.timeout)} s</dd><dt>Working window</dt><dd>${esc(c.windowPages)} × 100 newest alarms</dd><dt>Server cache</dt><dd>${esc(c.cacheSeconds)} s</dd></dl>` })}</div>`;
        $('#mode-btn', body)?.addEventListener('click', async () => {
          const next = data.mode === 'demo' ? 'live' : 'demo';
          if (!(await confirmDialog({ title: `Switch to ${next} mode?`, message: `<p>Everyone will be signed out. ${next === 'live' ? 'Live mode uses real Camview data and the live database — demo accounts will not exist there.' : 'Demo mode shows generated data only.'}</p><p>After switching, sign in again.</p>`, confirmLabel: `Switch to ${next}`, danger: next === 'live' }))) return;
          try { await api.post('/api/config/mode', { mode: next }); toast('Mode switched — please sign in again', 'success'); setTimeout(() => location.reload(), 900); }
          catch (e) { toast(e.message, 'error'); }
        });
        $('#c-save', body)?.addEventListener('click', async () => {
          try {
            await api.post('/api/config/setup', { apiUrl: $('#c-url', body).value.trim(), apiKey: $('#c-key', body).value.trim(), projectId: $('#c-proj', body).value.trim() || undefined });
            $('#c-key', body).value = ''; toast('Connection saved', 'success'); load();
          } catch (e) { toast(e.message, 'error'); }
        });
        $('#c-test', body)?.addEventListener('click', async () => {
          const out = $('#c-out', body);
          out.innerHTML = '<div class="muted">Testing…</div>';
          try {
            const r = await api.post('/api/config/test', { projectId: $('#c-proj', body).value.trim() || undefined });
            out.innerHTML = `<div class="banner info">${icon('check')}<div><b>Connected.</b> ${fmt.n(r.totalElements)} alarms · ${r.latencyMs} ms · alarm type IDs seen: ${esc(r.alarmTypesSeen.join(', ') || '—')} · camera fields: <span class="mono">${esc(r.cameraFields.join(', ') || '—')}</span></div></div>`;
          } catch (e) { out.innerHTML = `<div class="banner critical">${icon('alert')}<div><b>Connection failed.</b> ${esc(e.message)}</div></div>`; }
        });
        $('#c-raw', body)?.addEventListener('click', async () => {
          const out = $('#c-out', body);
          try { const r = await api.post('/api/config/raw', { projectId: $('#c-proj', body).value.trim() || undefined, size: 3 }); out.innerHTML = `<pre class="mono" style="max-height:360px;overflow:auto;background:var(--panel-2);padding:10px;border-radius:6px;font-size:11px">${esc(JSON.stringify(r, null, 2))}</pre>`; }
          catch (e) { out.innerHTML = `<div class="banner critical">${icon('alert')}<div>${esc(e.message)}</div></div>`; }
        });
      } else if (tab === 'projects') {
        const extra = data.system.extraProjects || [];
        const SRC = { default: 'CAMVIEW_PROJECT_ID', extra: 'added here', master: 'master data' };
        const feedRows = (data.system.monitoredProjects || []).map((p) => `<tr><td><div class="row tight" style="gap:6px;flex-wrap:wrap"><span class="mono muted" title="Camview project id">${esc(p.externalId)}</span>
              ${edit ? `<input class="input mono" data-pcode="${esc(p.externalId)}" value="${esc(p.code === p.externalId ? '' : p.code)}" placeholder="Project code, e.g. MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL" style="min-width:300px"><button class="btn sm" data-pcode-save="${esc(p.externalId)}">${icon('check', 's')} Save code</button>` : `<span class="mono">${esc(p.code)}</span>`}
              ${p.name && p.name !== p.code ? `<span class="muted">${esc(p.name)}</span>` : ''}</div></td>
            <td><span class="b outline">${SRC[p.source] || esc(p.source)}</span></td><td class="num">${fmt.n(p.totalElements)}</td>
            <td>${p.latestAlertAt ? `${fmt.dt(p.latestAlertAt)} <span class="${p.quietHours >= 24 ? 'sla-attention' : 'sla-within'}">${fmt.rel(p.latestAlertAt)}</span>` : '<span class="muted">—</span>'}</td>
            <td>${p.source === 'extra' && edit ? `<button class="btn sm" data-unmon="${esc(p.externalId)}">${icon('x', 's')} Remove</button>` : p.source === 'default' ? '<span class="muted">change in Connection</span>' : ''}</td></tr>`).join('');
        const stale = data.system.staleProjects || [];
        const staleRows = stale.map((p) => `<tr><td class="mono">${esc(p.code)}${p.name && p.name !== p.code ? ` <span class="muted">${esc(p.name)}</span>` : ''}</td>
            <td class="muted">no longer monitored · centres and cameras built from Camview data are still stored</td>
            <td>${edit ? `<button class="btn sm danger" data-delproj="${esc(p.externalId)}">${icon('trash', 's')} Delete its data</button>` : ''}</td></tr>`).join('');
        body.innerHTML = card({ title: `${icon('tree')} Monitored projects`, sub: 'Projects come from nomenclature master data. Add project IDs here to monitor them before master data is imported.',
          body: `<div class="field"><label>Extra project IDs (comma-separated numbers)</label><input class="input" id="pr-ids" value="${esc(extra.join(', '))}" ${dis}></div>
            <div class="muted" style="margin-bottom:10px">Available to you now: ${session.projects.map((p) => `<span class="b outline mono">${esc(p.code)}</span>`).join(' ') || 'none'}</div>
            ${edit ? '<button class="btn primary" id="pr-save">Save</button>' : ''}
            ${feedRows ? `<div class="table-wrap" style="margin-top:14px"><table class="t"><thead><tr><th>Project</th><th>Monitored because</th><th>Alerts in Camview</th><th>Newest alert</th><th></th></tr></thead><tbody>${feedRows}</tbody></table></div>` : ''}
            ${staleRows ? `<div class="muted" style="margin-top:14px;font-weight:700">Old projects (not monitored)</div><div class="table-wrap"><table class="t"><tbody>${staleRows}</tbody></table></div>` : ''}` })
          + (edit ? card({ title: `${icon('edit')} Project codes`, sub: 'Camview’s API sends only the project number. Match each project id to the exam’s own code — one line per project: <span class="mono">id, code[, name]</span>. The client and the exam follow from the code (MPESB/G2SG4-CRT-2026/… → client MPESB, exam MPESB/G2SG4-CRT; another date of the same exam joins it; another year keeps its year so names never collide). Camview’s own project record is used automatically when your key may read it.',
            body: `<textarea class="input mono" id="pc-map" rows="4" style="width:100%;font-size:12px" placeholder="2773, MPESB/G2SG4-CRT-2026/220926/LIVECCTV/IIL, MPESB Group 2 Sub Group 4&#10;2872, OTHER/CODE"></textarea>
              <div class="row" style="margin-top:8px"><span class="grow"></span><button class="btn primary" id="pc-apply">${icon('check', 's')} Apply mapping</button></div>` }) : '')
          + (edit && session.mode !== 'demo' ? card({ title: `${icon('search')} Find the running project`, sub: 'Camview has no "list projects" call: this reads project ids one by one with your key and shows which ones exist, how many alerts they hold and when the newest was raised.',
            body: `<div class="row" style="gap:8px;flex-wrap:wrap;align-items:end">
                <label class="field" style="margin:0">From <input class="input" id="pd-from" type="number" min="1" value="1500" style="width:100px"></label>
                <label class="field" style="margin:0">To <input class="input" id="pd-to" type="number" min="1" value="3500" style="width:100px"></label>
                <button class="btn primary" id="pd-run">${icon('search', 's')} Scan project ids</button>
                <label class="check" style="margin:0"><input type="checkbox" id="pd-recent" checked> Only projects with an alert in the last 7 days</label>
                <span class="muted" id="pd-note">Up to 2,000 ids per scan (about a minute) · project ids grow over time, so the running exam has the highest id — scan the highest range first</span></div>
              <div id="pd-out" style="margin-top:12px"></div>` }) : '');
        $('#pr-save', body)?.addEventListener('click', async () => {
          try { await api.put('/api/settings/projects', { projects: $('#pr-ids', body).value.split(',').map((x) => x.trim()).filter(Boolean) }); toast('Saved — sign in again to refresh your project list', 'success'); load(); }
          catch (e) { toast(e.message, 'error'); }
        });
        $('#pc-apply', body)?.addEventListener('click', async () => {
          try { const r = await api.put('/api/nomenclature/project-codes', { text: $('#pc-map', body).value }); toast(`${r.applied.length} project code(s) applied — sign in again to refresh`, 'success'); load(); }
          catch (e) { toast(e.message, 'error'); }
        });
        body.querySelectorAll('[data-pcode-save]').forEach((b) => b.addEventListener('click', async () => {
          const code = body.querySelector(`[data-pcode="${b.dataset.pcodeSave}"]`).value.trim();
          try { const r = await api.put(`/api/nomenclature/projects/${encodeURIComponent(b.dataset.pcodeSave)}`, { code }); toast(`Project ${b.dataset.pcodeSave} is now shown as ${r.code} — sign in again to refresh`, 'success'); load(); }
          catch (e) { toast(e.message, 'error'); }
        }));
        body.querySelectorAll('[data-unmon]').forEach((b) => b.addEventListener('click', async () => {
          try { await api.del(`/api/nomenclature/projects/${encodeURIComponent(b.dataset.unmon)}`); toast(`Project ${b.dataset.unmon} is no longer monitored — sign in again to refresh your project list`, 'success'); load(); }
          catch (e) { toast(e.message, 'error'); }
        }));
        body.querySelectorAll('[data-delproj]').forEach((b) => b.addEventListener('click', async () => {
          if (!await confirmDialog({ title: `Delete project ${b.dataset.delproj} data?`, message: 'Its centres, rooms and cameras built from Camview data are removed. Reviews, tickets and the audit trail stay. It is rebuilt automatically if the project is monitored again.', confirmLabel: 'Delete', danger: true })) return;
          try { const r = await api.del(`/api/nomenclature/projects/${encodeURIComponent(b.dataset.delproj)}`); toast(`Removed ${r.nodesRemoved} node(s) of project ${b.dataset.delproj}`, 'success'); load(); }
          catch (e) { toast(e.message, 'error'); }
        }));
        $('#pd-run', body)?.addEventListener('click', async () => {
          const btn = $('#pd-run', body), out = $('#pd-out', body);
          btn.disabled = true; out.innerHTML = skeleton(4, 24);
          try {
            const r = await api.post('/api/settings/projects/discover', { from: +$('#pd-from', body).value || 1, to: +$('#pd-to', body).value || 300 });
            if (ctx.isStale() || tab !== 'projects') return;
            const recent = $('#pd-recent', body)?.checked;
            const rows = r.projects.filter((p) => !p.error && (!recent || (p.quietHours != null && p.quietHours <= 24 * 7)));
            out.innerHTML = rows.length ? `<div class="muted" style="margin-bottom:6px">${rows.length} project${rows.length === 1 ? '' : 's'} readable with this key in ids ${r.from}–${r.to}, newest activity first.</div>
              <div class="table-wrap"><table class="t"><thead><tr><th>Project</th><th>Alerts</th><th>Cameras</th><th>Newest alert</th><th>Newest camera event</th><th></th></tr></thead><tbody>
              ${rows.map((p) => `<tr><td class="mono">${esc(p.projectId)}${p.monitored ? ' <span class="b outline">monitored</span>' : ''}</td><td class="num">${fmt.n(p.total)}${p.complete ? '' : ' <span class="muted" title="Large project: newest alert taken from a sample of pages">~</span>'}</td><td class="num">${fmt.n(p.cameras)}</td>
                <td>${p.latestAlertAt ? `${fmt.dt(p.latestAlertAt)} <span class="${p.quietHours >= 24 ? 'sla-attention' : 'sla-within'}">${fmt.rel(p.latestAlertAt)}</span>` : '<span class="muted">none</span>'}</td>
                <td>${p.latestAt ? `${fmt.dt(p.latestAt)} <span class="muted">${fmt.rel(p.latestAt)}</span>` : '<span class="muted">—</span>'}</td>
                <td>${p.monitored ? '' : `<button class="btn sm" data-add="${esc(p.projectId)}">${icon('plus', 's')} Monitor</button>`}</td></tr>`).join('')}</tbody></table></div>`
              : `<div class="muted">No readable project in ids ${r.from}–${r.to}. Try a wider range.</div>`;
            out.querySelectorAll('[data-add]').forEach((b) => b.addEventListener('click', () => {
              const i = $('#pr-ids', body); const ids = i.value.split(',').map((x) => x.trim()).filter(Boolean);
              if (!ids.includes(b.dataset.add)) ids.push(b.dataset.add);
              i.value = ids.join(', '); b.disabled = true; toast(`Project ${b.dataset.add} added — click Save`, 'success');
            }));
          } catch (e) { out.innerHTML = errorBox(e); }
          btn.disabled = false;
        });
      } else if (tab === 'health') {
        body.innerHTML = card({ title: `${icon('camera')} Camera & recording health source`, body: skeleton(3, 20) });
        api.get('/api/camera-health/status').then((h) => {
          if (ctx.isStale() || tab !== 'health') return;
          body.innerHTML = card({ title: `${icon('camera')} Camera & recording health source`,
            sub: 'Camera connection and recording are shown only from a real source — never from alarm names.',
            body: `<dl class="kv" style="grid-template-columns:190px 1fr">
                <dt>Source</dt><dd>${h.mode === 'push' ? '<span class="b vis-shared">Push adapter enabled</span>' : h.mode === 'camview' ? '<span class="b vis-shared">Camview</span> <span class="muted">— camera.frameSyncStatus / lastFrameSync, imported with every live refresh (recording not reported)</span>' : '<span class="b vis-internal">Not connected</span> <span class="muted">— the review screen shows CAMERA HEALTH UNAVAILABLE</span>'}</dd>
                <dt>Cameras reporting</dt><dd class="num">${fmt.n(h.camerasReporting)}</dd>
                <dt>Last report</dt><dd>${h.lastReportAt ? `${fmt.dt(h.lastReportAt)} · ${fmt.rel(h.lastReportAt)}` : '—'}</dd>
                <dt>Heartbeat considered stale after</dt><dd>${h.staleSeconds} s <span class="muted">(CAMVIEW_HEALTH_STALE_SECONDS)</span></dd></dl>
              <div class="banner info" style="margin-top:12px">${icon('info')}<div>${esc(h.note)}<br>
                Set <span class="mono">CAMVIEW_HEALTH_INGEST_TOKEN</span> in the server's <span class="mono">.env</span>, then have your NVR / VMS / heartbeat service send:<br>
                <span class="mono" style="font-size:11.5px">POST /api/camera-health/ingest · Authorization: &lt;token&gt; · {"cameras":[{"projectId":34,"cameraId":9522,"cameraState":"online","recordingState":"not_recording","streamState":"available","lastHeartbeatAt":"…","lastRecordingAt":"…"}]}</span></div></div>` });
        }).catch((e) => { body.innerHTML = errorBox(e); });
      } else if (tab === 'system') {
        const feeds = Object.entries(data.system.feeds);
        body.innerHTML = `<div class="grid g-2">${card({ title: `${icon('refresh')} Data feeds`, sub: 'Status is only "live" after a successful refresh — never just because the page loaded', flush: true,
          body: feeds.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Project</th><th>State</th><th>Last success</th><th>Window</th><th>Camview total</th><th>Last error</th></tr></thead><tbody>
            ${feeds.map(([pid, f]) => `<tr><td class="mono">${esc(pid)}</td><td><span class="dot ${f.state === 'live' ? 'live' : f.state}" style="display:inline-block;margin-right:6px"></span>${esc(f.state.toUpperCase())}</td>
              <td class="num">${f.lastSuccessAt ? fmt.dt(f.lastSuccessAt) : '—'}</td><td class="num">${fmt.n(f.windowSize)}${f.truncated ? ' (newest)' : ''}</td><td class="num">${fmt.n(f.totalElements)}</td>
              <td class="${f.lastError ? 'sla-attention' : 'muted'}">${f.lastError ? esc(f.lastError.message) + ' · ' + fmt.time(f.lastError.at) : '—'}</td></tr>`).join('')}</tbody></table></div>` : '<div class="card-b muted">No projects available.</div>' })}
          ${card({ title: `${icon('bell')} Notification channels`, body: `<dl class="kv">${Object.entries(data.system.channels).map(([k, v]) => `<dt>${esc(k.replace('_', '-'))}</dt><dd>${v ? `<span class="b vis-shared">${icon('check')}active</span>` : '<span class="b outline">not integrated</span>'}</dd>`).join('')}</dl>
            <div class="section-title">Database</div><dl class="kv"><dt>In use</dt><dd>${data.system.database === 'demo' ? '<span class="demo-flag">DEMO</span> camview-demo.db' : 'camview.db (live)'}</dd><dt>Mode</dt><dd>${esc(data.mode)}</dd></dl>` })}</div>`;
      } else if (tab === 'branding') {
        paintBranding(body);
      } else if (tab === 'features') {
        const V2 = { ENABLE_AI: 'AI Assistant (Claude). Answers once CAMVIEW_AI_API_KEY is set.', ENABLE_SMART_SEARCH: 'Plain-language search; the interpreted filters are shown first.', ENABLE_INCIDENTS: 'Incidents from correlated alerts, with owner, status and timeline.',
          ENABLE_REALTIME: 'Push updates to browsers (/api/events). Camview itself is still read every few seconds.', ENABLE_MAP: 'Operations map by city / centre; positions approximate unless set by an administrator.',
          ENABLE_ADVANCED_CORRELATION: 'Explained correlation (same centre / camera / type within a time window) for incident suggestions.' };
        body.innerHTML = card({ title: `${icon('layers')} Feature flags`, sub: 'Read-only here — set CAMVIEW feature variables in backend/.env', flush: true,
          body: `<div class="table-wrap"><table class="t"><thead><tr><th>Flag</th><th>State</th><th>Why</th></tr></thead><tbody>${Object.entries(data.features).map(([k, v]) => `<tr><td class="mono">${esc(k)}</td>
            <td>${v ? `<span class="b vis-shared">${icon('check')}on</span>` : `<span class="b outline">${icon('x')}off</span>`}</td><td class="dim">${esc(V2[k] ? (v ? 'V2 · ' + V2[k] : 'V2, switched off — ' + V2[k]) : v ? 'V1 feature' : 'Disabled by configuration')}</td></tr>`).join('')}</tbody></table></div>` });
      }
    };

    // ---------------------------------------------------------------- Branding (organisation name + logo)
    let bd = null;                                   // draft: {name, subtitle, hasLogo, logoVersion, src?, logo?, removeLogo?}
    async function paintBranding(body) {
      if (!bd) {
        body.innerHTML = skeleton(4, 30);
        try { bd = { ...(await api.get('/api/branding')) }; } catch (e) { body.innerHTML = errorBox(e); return; }
        if (ctx.isStale() || tab !== 'branding') return;
      }
      const hasImg = !!(bd.src || (bd.hasLogo && !bd.removeLogo));
      const imgUrl = bd.src || `/api/branding/logo?v=${encodeURIComponent(bd.logoVersion || '')}`;
      const previewBrand = () => ({ name: $('#br-name', body).value.trim() || 'CAMVIEW', subtitle: $('#br-sub', body).value.trim() || 'Command Center',
        src: bd.src || '', hasLogo: bd.hasLogo && !bd.removeLogo, logoVersion: bd.logoVersion });
      body.innerHTML = card({ title: `${icon('image')} Branding`, sub: 'Your organisation’s name and logo in the sidebar, sign-in page, browser tab and presentation mode.',
        body: `<div class="br-grid"><div>
            <div class="field"><label for="br-name">Product name</label><input class="input" id="br-name" maxlength="40" value="${esc(bd.name)}" placeholder="CAMVIEW" ${dis}>
              <div class="hint">Up to 40 characters. Empty = CAMVIEW.</div></div>
            <div class="field"><label for="br-sub">Subtitle</label><input class="input" id="br-sub" maxlength="60" value="${esc(bd.subtitle)}" placeholder="Command Center" ${dis}>
              <div class="hint">Up to 60 characters. Client users always see “Client Portal”.</div></div>
            <div class="field"><span class="br-label">Logo</span>
              <label class="br-drop ${edit ? '' : 'off'}" id="br-drop">
                <span class="br-drop-img">${hasImg ? `<img src="${esc(imgUrl)}" alt="">` : icon('image', 'l')}</span>
                <span class="grow"><b>${edit ? 'Drop an image here or click to choose' : hasImg ? 'Current logo' : 'No logo — the default mark is shown'}</b><br><span class="muted">PNG, JPEG, WEBP or SVG · max 512 KB · shown at most 34 px tall</span></span>
                <input type="file" id="br-file" class="sr-only" accept="image/png,image/jpeg,image/webp,image/svg+xml,.svg" aria-label="Choose a logo image" ${dis}>
              </label>
              <div id="br-err" class="banner critical hidden" role="alert" style="margin-top:8px"></div>
              ${edit && hasImg ? `<div class="row" style="margin-top:8px"><button type="button" class="btn ghost" id="br-remove">${icon('trash', 's')} Remove logo</button>${bd.src ? '<span class="muted">Not saved yet</span>' : ''}</div>` : ''}
              ${edit && bd.removeLogo ? '<div class="hint">The logo will be removed when you save.</div>' : ''}</div>
          </div>
          <div><div class="section-title" style="margin-top:0">Live preview</div>
            <div class="br-previews">
              <div class="br-preview bp-dark"><div class="br-cap">Dark theme</div><div class="brand" data-br-preview></div></div>
              <div class="br-preview bp-light"><div class="br-cap">Light theme</div><div class="brand" data-br-preview></div></div>
            </div>
            <div class="hint" style="margin-top:8px">Saved changes apply at once — nobody needs to reload.</div></div></div>
          ${edit ? '<div class="row"><span class="grow"></span><button class="btn primary" id="br-save">Save branding</button></div>' : ''}` });
      const preview = () => $$('[data-br-preview]', body).forEach((n) => { n.innerHTML = brandInnerHtml(previewBrand(), { client: false }); });
      preview();
      if (!edit) return;
      const err = $('#br-err', body);
      const fail = (m) => { err.textContent = m; err.classList.remove('hidden'); };
      const keepText = () => { bd.name = $('#br-name', body).value; bd.subtitle = $('#br-sub', body).value; };
      const takeFile = (f) => {
        err.classList.add('hidden');
        if (!f) return;
        const mime = Object.values(LOGO_TYPES).includes(f.type) ? f.type : LOGO_TYPES[(f.name.split('.').pop() || '').toLowerCase()];
        if (!mime) return fail('Choose a PNG, JPEG, WEBP or SVG image.');
        if (f.size > LOGO_MAX) return fail(`That file is ${Math.ceil(f.size / 1024)} KB — the logo can be at most 512 KB.`);
        const r = new FileReader();
        r.onload = () => {
          keepText();
          bd.logo = bd.src = `data:${mime};base64,${String(r.result).split(',')[1] || ''}`;
          bd.removeLogo = false;
          paintBranding(body);
        };
        r.onerror = () => fail('Could not read that file.');
        r.readAsDataURL(f);
      };
      $('#br-name', body).addEventListener('input', preview);
      $('#br-sub', body).addEventListener('input', preview);
      $('#br-file', body).addEventListener('change', (e) => takeFile(e.target.files[0]));
      const drop = $('#br-drop', body);
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('over'));
      drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); takeFile(e.dataTransfer.files[0]); });
      $('#br-remove', body)?.addEventListener('click', () => { keepText(); bd.logo = bd.src = null; bd.removeLogo = bd.hasLogo; paintBranding(body); });
      $('#br-save', body).addEventListener('click', async (e) => {
        const btn = e.currentTarget;
        const payload = { name: $('#br-name', body).value, subtitle: $('#br-sub', body).value };
        if (bd.logo) payload.logo = bd.logo;
        else if (bd.removeLogo) payload.removeLogo = true;
        btn.disabled = true;
        try {
          const r = await api.put('/api/branding', payload);
          bd = { ...r };
          applyBranding(r);
          toast('Branding saved (recorded in audit trail)', 'success');
          if (tab === 'branding' && !ctx.isStale()) paintBranding(body);
        } catch (x) { fail(x.message); btn.disabled = false; }
      });
    }

    function stepRow(s, i) {
      return `<div class="row" data-step style="margin-bottom:8px"><span class="b outline">Step ${i + 1}</span><span class="dim">after</span>
        <input class="input" data-after type="number" min="1" style="width:90px" value="${esc(s.afterMinutes)}" ${dis}><span class="dim">minutes notify</span>
        <select class="select" data-role ${dis}>${roles.map((r) => `<option value="${esc(r.id)}" ${r.id === s.roleId ? 'selected' : ''}>${esc(r.name)}</option>`).join('')}${roles.some((r) => r.id === s.roleId) ? '' : `<option value="${esc(s.roleId)}" selected>${esc(s.roleId)}</option>`}</select>
        ${edit ? `<button class="btn icon ghost" data-rmstep aria-label="Remove step">${icon('trash', 's')}</button>` : ''}</div>`;
    }

    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; ctx.setQuery({ tab }); $$('[data-tab]', el).forEach((x) => x.classList.toggle('on', x === b)); if (data) paint(); }));
    ctx.onCleanup(delegate(el, 'click', '[data-rmstep]', (e, b) => b.closest('[data-step]').remove()));
    await load();
  },
};
