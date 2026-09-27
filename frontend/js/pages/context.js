// pages/context.js — NOMENCLATURE: "Where exactly did it happen?"
// Tabs: Explorer (Project › TC › Centre › Building › Floor › Room › Camera)
//       Data quality · Dictionary (alarm types, priorities) · Import / Export.
// Mappings come only from imported master data — nothing is invented here.

import * as api from '../core/api.js';
import { can, currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { bar } from '../core/charts.js';
import { esc, icon, fmt, kpi, card, errorBox, skeleton, empty, bars, toast, confirmDialog, delegate, prov,
  PRIORITY_COLORS, $, $$ } from '../core/ui.js';
import { miniRow } from '../components/alarms.js';
import { watchButton } from './camera.js';

const TABS = [['explorer', 'Explorer', 'tree'], ['quality', 'Data quality', 'check'], ['dictionary', 'Dictionary', 'report'], ['import', 'Import / Export', 'download']];
const LEVEL_LABEL = { project: 'Project', tc: 'TC', centre: 'Centre', building: 'Building', floor: 'Floor', room: 'Room', camera: 'Camera' };
const CSV_COLS = ['project_id', 'project_code', 'project_name', 'tc_code', 'tc_name', 'centre_code', 'centre_name',
  'building', 'floor', 'room', 'camera_id', 'camera_code', 'camera_name'];

export default {
  async render(el, ctx) {
    const pid = ctx.query.projectId || currentProject();
    setTitle('Nomenclature', `${esc(projectInfo(pid).code)} · context & master data`);
    let tab = TABS.some((t) => t[0] === ctx.query.tab) ? ctx.query.tab : 'explorer';

    el.innerHTML = `
      <div class="page-head"><div><h2>Nomenclature & context</h2><p>Project › TC › Centre › Sub-location › Camera exactly as Camview sends it (project id, tcCode, centerCode / center / city / state, subLocation, cameraNumber); imported master data may add building and floor — from imported master data, or built automatically from Camview camera data (centre code, centre, location, camera) where nothing is imported. Imported data always wins.</p></div></div>
      <div class="tabs" role="tablist">${TABS.map(([k, l, ic]) => `<button class="tab ${k === tab ? 'on' : ''}" role="tab" aria-selected="${k === tab}" data-tab="${k}">${icon(ic, 's')} ${l}</button>`).join('')}</div>
      <div id="ctx-body"></div>`;
    const body = $('#ctx-body', el);

    const show = () => {
      $$('[data-tab]', el).forEach((b) => { b.classList.toggle('on', b.dataset.tab === tab); b.setAttribute('aria-selected', b.dataset.tab === tab); });
      ({ explorer, quality, dictionary, importer })[tab === 'import' ? 'importer' : tab](body, ctx, pid);
    };
    ctx.onCleanup(delegate(el, 'click', '[data-tab]', (e, b) => { tab = b.dataset.tab; ctx.setQuery({ tab, node: tab === 'explorer' ? ctx.query.node : '' }); show(); }));
    show();
  },
};

// ------------------------------------------------------------------ Explorer
async function explorer(body, ctx, pid) {
  body.innerHTML = `<div class="grid" style="grid-template-columns:minmax(260px,360px) minmax(0,1fr)">
    <section class="card"><div class="card-h"><h3>${icon('tree')} Hierarchy</h3><div class="actions"><button class="btn sm ghost" data-expand="all">Expand</button><button class="btn sm ghost" data-expand="none">Collapse</button></div></div>
      <div class="card-b" style="max-height:72vh;overflow:auto"><div id="tree" class="tree" role="tree" aria-label="Nomenclature hierarchy">${skeleton(8, 18)}</div></div></section>
    <div id="node-panel">${skeleton(6, 50)}</div></div>`;
  let tree;
  try {
    tree = await api.get('/api/context/tree', { projectId: pid });
  } catch (e) {
    if (ctx.isStale()) return;
    body.innerHTML = errorBox(e);
    $('[data-retry]', body)?.addEventListener('click', () => explorer(body, ctx, pid));
    return;
  }
  if (ctx.isStale()) return;
  if (!tree.tree.length) {
    body.innerHTML = empty('No master data for this project', `Import the nomenclature (Import / Export tab) to resolve alarms into Project › TC › Centre › Room › Camera. ${tree.unmappedAlarms ? `${tree.unmappedAlarms} alarms currently have no context.` : ''}`, 'tree');
    return;
  }
  const byId = new Map();
  const walk = (n, parent) => { byId.set(n.id, { n, parent }); n.children.forEach((c) => walk(c, n)); };
  tree.tree.forEach((n) => walk(n, null));
  const expanded = new Set();
  let selected = ctx.query.node && byId.has(ctx.query.node) ? ctx.query.node : tree.tree[0].id;
  // expand root + path to selection
  tree.tree.forEach((n) => { expanded.add(n.id); n.children.forEach((c) => expanded.add(c.id)); });
  for (let p = byId.get(selected); p; p = p.parent ? byId.get(p.parent.id) : null) expanded.add(p.n.id);

  const nodeHtml = (n) => {
    const hasKids = n.children.length > 0;
    const open = expanded.has(n.id);
    return `<li role="treeitem" aria-expanded="${hasKids ? open : 'false'}" aria-selected="${n.id === selected}">
      <div class="node ${n.id === selected ? 'on' : ''}" data-node="${esc(n.id)}" tabindex="0">
        <span class="tog" ${hasKids ? `data-toggle="${esc(n.id)}" aria-label="${open ? 'Collapse' : 'Expand'}"` : ''}>${hasKids ? (open ? '▾' : '▸') : ''}</span>
        <span class="lvl">${esc(LEVEL_LABEL[n.level] || n.level)}</span>
        <span class="mono" title="${esc(n.name || '')}">${esc(n.code)}</span>${n.name && n.name !== n.code && n.level !== 'camera' ? ` <span class="muted" style="font-size:11px">${esc(n.name)}</span>` : ''}${n.source === 'camview' && n.level === 'project' ? ' <span class="prov direct" title="Built automatically from Camview camera data">CAMVIEW</span>' : ''}
        <span class="cnt" title="${n.count} alarms · ${n.critical} critical · ${n.pending} pending">${n.critical ? `<span style="color:var(--st-critical)" title="critical">▲${n.critical}</span> ` : ''}${fmt.n(n.count)}</span>
      </div>
      ${hasKids && open ? `<ul role="group">${n.children.map(nodeHtml).join('')}</ul>` : ''}</li>`;
  };
  const paintTree = () => { $('#tree', body).innerHTML = `<ul>${tree.tree.map(nodeHtml).join('')}</ul>`; };

  const select = async (id) => {
    selected = id;
    ctx.setQuery({ node: id });
    for (let p = byId.get(id); p; p = p.parent ? byId.get(p.parent.id) : null) expanded.add(p.n.id);
    paintTree();
    await nodePanel($('#node-panel', body), ctx, pid, id, select);
  };

  paintTree();
  const tr = $('#tree', body);
  ctx.onCleanup(delegate(tr, 'click', '[data-toggle]', (e, t) => {
    e.stopPropagation();
    const id = t.dataset.toggle;
    expanded.has(id) ? expanded.delete(id) : expanded.add(id);
    paintTree();
  }));
  ctx.onCleanup(delegate(tr, 'click', '[data-node]', (e, t) => { if (!e.target.closest('[data-toggle]')) select(t.dataset.node); }));
  ctx.onCleanup(delegate(tr, 'keydown', '[data-node]', (e, t) => {
    const id = t.dataset.node;
    if (e.key === 'Enter') { e.preventDefault(); select(id); }
    if (e.key === 'ArrowRight' && byId.get(id).n.children.length) { expanded.add(id); paintTree(); $(`[data-node="${CSS.escape(id)}"]`, tr)?.focus(); }
    if (e.key === 'ArrowLeft') { expanded.delete(id); paintTree(); $(`[data-node="${CSS.escape(id)}"]`, tr)?.focus(); }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      const all = $$('[data-node]', tr);
      const i = all.indexOf(t);
      all[Math.max(0, Math.min(all.length - 1, i + (e.key === 'ArrowDown' ? 1 : -1)))]?.focus();
    }
  }));
  ctx.onCleanup(delegate(body, 'click', '[data-expand]', (e, b) => {
    expanded.clear();
    if (b.dataset.expand === 'all') byId.forEach((v, k) => expanded.add(k));
    else tree.tree.forEach((n) => expanded.add(n.id));
    paintTree();
  }));
  await nodePanel($('#node-panel', body), ctx, pid, selected, select);
}

async function nodePanel(host, ctx, pid, id, select) {
  host.innerHTML = skeleton(6, 50);
  let d;
  try {
    d = await api.get(`/api/context/node/${encodeURI(id)}`, { projectId: pid, tzOffset: -new Date().getTimezoneOffset() });
  } catch (e) {
    host.innerHTML = errorBox(e);
    $('[data-retry]', host)?.addEventListener('click', () => nodePanel(host, ctx, pid, id, select));
    return;
  }
  if (ctx.isStale()) return;
  const n = d.node;
  const m = d.metrics;
  const crumbs = n.path.map((p, i) => (i === n.path.length - 1 ? `<b class="mono">${esc(p.code)}</b>` : `<a href="#" class="mono" data-go="${esc(p.id)}">${esc(p.code)}</a>`)).join('<span class="sep"> / </span>');
  const childLevel = n.children[0]?.level;
  host.innerHTML = `
    <div class="card" style="padding:14px 16px;margin-bottom:14px">
      <div class="ctx-path" style="font-size:12px">${crumbs}</div>
      <div class="row" style="margin-top:8px"><span class="b outline">${esc(LEVEL_LABEL[n.level] || n.level)}</span><h2 style="margin:0;font-size:18px" class="mono">${esc(n.code)}</h2>
        ${n.name && n.name !== n.code ? `<span class="dim">${esc(n.name)}</span>` : ''}${n.source === 'camview' ? '<span class="prov direct" title="Built automatically from Camview camera data (camera.centerCode / center / subLocation / cameraNumber). Import master data to replace it.">FROM CAMVIEW</span>' : ''}<span class="grow"></span>
        ${can('nomenclature.manage') ? `<button class="btn sm" id="node-rename">${icon('edit', 's')} ${n.name && n.name !== n.code ? 'Rename' : 'Add name'}</button>` : ''}
        <span id="node-watch"></span>
        ${n.level === 'camera' ? `<a class="btn sm" href="#/cameras/${encodeURIComponent(n.externalId)}?projectId=${encodeURIComponent(pid)}">${icon('camera', 's')} Camera page</a>` : ''}
        <a class="btn sm" href="#/live?${n.level === 'camera' ? `camera=${encodeURIComponent(n.externalId)}` : ['tc', 'centre'].includes(n.level) ? `${n.level}=${encodeURIComponent(n.code)}` : ''}">${icon('live', 's')} Live alarms</a></div>
    </div>
    <div class="kpis">
      ${kpi({ label: 'Alarms', value: m.total, icon: 'layers' })}
      ${kpi({ label: 'Critical', value: m.critical, icon: 'alert', accent: 'critical' })}
      ${kpi({ label: 'Pending', value: m.pending, icon: 'clock', accent: 'warning' })}
      ${kpi({ label: 'Valid', value: m.valid, icon: 'check', accent: 'good' })}
      ${kpi({ label: 'Invalid', value: m.invalid, sub: `false-alarm rate ${fmt.pct(m.falseAlarmRate)}`, icon: 'x', accent: 'violet' })}
      ${kpi({ label: 'Shared with client', value: m.sharedWithClient, icon: 'share' })}
    </div>
    ${n.children.length ? card({ title: `${icon('tree')} ${esc(LEVEL_LABEL[childLevel] || 'Children')} (${n.children.length})`, sub: 'Click to drill down', flush: true,
      body: `<div class="table-wrap"><table class="t"><thead><tr><th>${esc(LEVEL_LABEL[childLevel] || 'Node')}</th><th>Name</th><th class="num">Alarms</th><th class="num">Critical</th><th class="num">Pending</th><th class="num">Valid</th><th class="num">Invalid</th><th class="num">Shared</th></tr></thead>
        <tbody>${d.children.map((c) => `<tr class="link" tabindex="0" data-go="${esc(c.id)}"><td class="mono">${esc(c.code)}</td><td class="dim">${esc(c.name && c.name !== c.code ? c.name : '')}</td>
          <td class="num">${fmt.n(c.total)}</td><td class="num">${fmt.n(c.critical)}</td><td class="num">${fmt.n(c.pending)}</td><td class="num">${fmt.n(c.valid)}</td><td class="num">${fmt.n(c.invalid)}</td><td class="num">${fmt.n(c.shared)}</td></tr>`).join('')}</tbody></table></div>` }) : ''}
    <div class="grid g-main" style="margin-top:14px">
      ${card({ title: `${icon('chart')} Activity — last 24 hours`, sub: 'By priority', body: `<div class="legend">${['critical', 'high', 'medium', 'low'].map((k) => `<span><span class="sw" style="background:${PRIORITY_COLORS[k]}"></span>${k}</span>`).join('')}</div><div class="chart sm"><canvas id="node-hourly"></canvas></div>` })}
      ${card({ title: 'Priority', actions: prov('direct'), body: bars(d.priorityDistribution.map((x) => ({ ...x, color: PRIORITY_COLORS[x.key], sw: PRIORITY_COLORS[x.key] }))) })}
    </div>
    <div style="margin-top:14px">${card({ title: `${icon('clock')} Recent alarms`, flush: true, body: d.recent.length ? `<div class="list">${d.recent.slice(0, 10).map((a) => miniRow(a)).join('')}</div>` : empty('No alarms under this node') })}</div>`;
  host.querySelector('#node-rename')?.addEventListener('click', async () => {
    const name = window.prompt(`Name for ${n.code}`, n.name && n.name !== n.code ? n.name : '');
    if (name == null) return;
    try {
      await api.put(`/api/nomenclature/nodes/${encodeURIComponent(n.id)}`, { name });
      toast('Saved — shown everywhere this location appears', 'success');
      window.dispatchEvent(new HashChangeEvent('hashchange'));
    } catch (e) { toast(e.message, 'error'); }
  });
  // Watch this node (project / TC / centre / room / camera). Rooms are watched by their
  // unique node id (room numbers repeat across centres); projects/cameras by their Camview id.
  const watchable = { project: 'project', tc: 'tc', centre: 'centre', room: 'room', camera: 'camera' }[n.level];
  if (watchable) {
    const entityId = ['project', 'camera'].includes(n.level) ? n.externalId : n.level === 'room' ? n.id : n.code;
    const label = n.level === 'room' ? `Room ${n.code} · ${(n.path.find((p) => p.level === 'centre') || {}).code || ''}` : n.code;
    watchButton($('#node-watch', host), { entityType: watchable, entityId: String(entityId), label, projectId: pid });
  }
  $$('[data-go]', host).forEach((x) => {
    const go = (e) => { e.preventDefault(); select(x.dataset.go); };
    x.addEventListener('click', go);
    x.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(e); });
  });
  bar($('#node-hourly', host), {
    labels: d.hourly.map((h) => new Date(h.start).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })),
    series: ['critical', 'high', 'medium', 'low'].map((k) => ({ label: k, data: d.hourly.map((h) => h[k]), color: PRIORITY_COLORS[k] })),
    stacked: true,
  });
}

// ------------------------------------------------------------------ Data quality
async function quality(body, ctx, pid) {
  body.innerHTML = skeleton(6, 40);
  let q;
  try { q = await api.get('/api/context/quality', { projectId: pid }); }
  catch (e) { if (!ctx.isStale()) { body.innerHTML = errorBox(e); $('[data-retry]', body)?.addEventListener('click', () => quality(body, ctx, pid)); } return; }
  if (ctx.isStale()) return;
  const md = q.masterData || {};
  const list = (items, fmtFn = (x) => `<span class="b outline mono">${esc(x)}</span>`) => (items && items.length ? `<div class="row tight">${items.map(fmtFn).join('')}</div>` : '<span class="muted">None</span>');
  body.innerHTML = `
    <div class="banner info">${icon('info')}<div class="grow">Computed only from imported master data and the alarms actually received in the monitored window. ${prov('derived')}</div></div>
    <div class="kpis">
      ${kpi({ label: 'Cameras seen', value: q.camerasSeen, icon: 'camera' })}
      ${kpi({ label: 'Mapped cameras', value: q.mappedCameras, sub: q.camerasSeen ? `${fmt.pct(q.mappedCameras / q.camerasSeen)} of seen` : '', icon: 'check', accent: 'good' })}
      ${kpi({ label: 'Unmapped cameras', value: q.unmappedCameras.length, icon: 'alert', accent: q.unmappedCameras.length ? 'warning' : '' })}
      ${kpi({ label: 'Unknown projects', value: q.unknownProjects.length, icon: 'tree' })}
      ${kpi({ label: 'Missing TC', value: q.missingTc })}
      ${kpi({ label: 'Missing centre', value: q.missingCentre })}
      ${kpi({ label: 'Missing room', value: q.missingRoom })}
      ${kpi({ label: 'Duplicate codes', value: q.duplicateCodes.length, accent: q.duplicateCodes.length ? 'critical' : '' })}
      ${kpi({ label: 'Unknown alarm types', value: q.unknownAlarmTypes.length, href: '#/context?tab=dictionary' })}
    </div>
    <div class="grid g-2">
      ${card({ title: `${icon('camera')} Unmapped cameras`, sub: 'Sending alarms but not in master data', body: list(q.unmappedCameras, (x) => `<a class="b outline mono" href="#/cameras/${encodeURIComponent(x)}?projectId=${encodeURIComponent(pid)}">CAM ${esc(x)}</a>`) })}
      ${card({ title: `${icon('tree')} Unknown projects`, sub: 'Project ids in alarms without a project node', body: list(q.unknownProjects) })}
      ${card({ title: `${icon('report')} Unknown alarm types`, sub: 'Type ids not in the Alarm Type Dictionary', body: list(q.unknownAlarmTypes) })}
      ${card({ title: `${icon('alert')} Duplicate codes`, sub: 'Codes that should be unique (project, TC, centre, camera)',
        body: q.duplicateCodes.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Level</th><th>Code</th><th class="num">Count</th></tr></thead><tbody>${q.duplicateCodes.map((x) => `<tr><td>${esc(LEVEL_LABEL[x.level] || x.level)}</td><td class="mono">${esc(x.code)}</td><td class="num">${x.count}</td></tr>`).join('')}</tbody></table></div>` : '<span class="muted">None</span>' })}
    </div>
    <div class="grid g-2" style="margin-top:14px">
      ${card({ title: `${icon('layers')} Master data`, body: `<dl class="kv">${Object.entries(md).map(([k, v]) => `<dt>${esc(LEVEL_LABEL[k] || k)}</dt><dd class="num">${fmt.n(v)}</dd>`).join('')}</dl>` })}
      ${card({ title: `${icon('info')} Alarm data completeness`, body: `<dl class="kv"><dt>Missing timestamps</dt><dd class="num">${fmt.n(q.alarmsMissingTimestamps)}</dd><dt>Missing camera</dt><dd class="num">${fmt.n(q.alarmsMissingCamera)}</dd><dt>No evidence</dt><dd class="num">${fmt.n(q.alarmsMissingEvidence)}</dd></dl>` })}
    </div>
    <div style="margin-top:14px">${card({ title: `${icon('tree')} Incomplete context`, sub: 'Cameras whose path is missing a level', flush: true,
      body: q.incompleteContext.length ? `<div class="table-wrap" style="max-height:340px"><table class="t"><thead><tr><th>Camera</th><th>Missing levels</th></tr></thead><tbody>${q.incompleteContext.map((x) => `<tr><td class="mono">${esc(x.camera)}</td><td>${x.missing.map((l) => `<span class="b outline">${esc(LEVEL_LABEL[l] || l)}</span>`).join(' ')}</td></tr>`).join('')}</tbody></table></div>` : empty('Every mapped camera has a complete path', '', 'check') })}</div>`;
}

// ------------------------------------------------------------------ Dictionary
async function dictionary(body, ctx) {
  body.innerHTML = skeleton(6, 30);
  let d;
  try { d = await api.get('/api/dictionary'); }
  catch (e) { if (!ctx.isStale()) { body.innerHTML = errorBox(e); $('[data-retry]', body)?.addEventListener('click', () => dictionary(body, ctx)); } return; }
  if (ctx.isStale()) return;
  const edit = can('nomenclature.manage');
  let types = d.alarmTypes.map((t) => ({ ...t }));
  let prios = d.priorities.map((p) => ({ ...p }));
  const sevOpts = (v) => ['', 'critical', 'high', 'medium', 'low'].map((s) => `<option value="${s}" ${s === (v || '') ? 'selected' : ''}>${s || '—'}</option>`).join('');
  const polOpts = (v) => [['allowed', 'Allowed'], ['review', 'Needs review'], ['never', 'Never share']].map(([k, l]) => `<option value="${k}" ${k === (v || 'allowed') ? 'selected' : ''}>${l}</option>`).join('');
  const unconfirmed = prios.some((p) => !p.confirmed);

  const paint = () => {
    body.innerHTML = `
      ${card({ title: `${icon('report')} Alarm type dictionary`, sub: 'Camview sends numeric alarm types. Names come from here — never invented.',
        actions: edit ? `<button class="btn sm" data-addtype>${icon('plus', 's')} Add type</button><button class="btn sm primary" data-savetypes>Save types</button>` : '', flush: true,
        body: types.length ? `<div class="table-wrap"><table class="t"><thead><tr><th style="width:80px">ID</th><th>Name</th><th>Description</th><th>Display severity</th><th>Client share policy</th>${edit ? '<th></th>' : ''}</tr></thead><tbody>
          ${types.map((t, i) => edit ? `<tr><td><input class="input" style="width:70px" data-t="${i}" data-k="id" value="${esc(t.id)}" inputmode="numeric" aria-label="Type id"></td>
            <td><input class="input" style="width:100%" data-t="${i}" data-k="name" value="${esc(t.name)}" aria-label="Name"></td>
            <td><input class="input" style="width:100%" data-t="${i}" data-k="description" value="${esc(t.description || '')}" aria-label="Description"></td>
            <td><select class="select" data-t="${i}" data-k="severity" aria-label="Severity">${sevOpts(t.severity)}</select></td>
            <td><select class="select" data-t="${i}" data-k="client_share_policy" aria-label="Client share policy">${polOpts(t.client_share_policy)}</select></td>
            <td><button class="btn sm ghost" data-deltype="${i}" aria-label="Remove">${icon('trash', 's')}</button></td></tr>`
            : `<tr><td class="num">${esc(t.id)}</td><td>${esc(t.name)}</td><td class="dim">${esc(t.description || '')}</td><td>${esc(t.severity || '—')}</td><td>${esc({ allowed: 'Allowed', review: 'Needs review', never: 'Never share' }[t.client_share_policy] || t.client_share_policy)}</td></tr>`).join('')}
          </tbody></table></div>` : empty('No alarm types defined', edit ? 'Add the type ids you see in alarms (Data quality lists unknown ids).' : 'An administrator can define alarm type names.') })}
      <div style="margin-top:14px">${card({ title: `${icon('alert')} Priority configuration`, sub: 'Maps Camview numeric priority to labels and sort order',
        actions: edit ? `<button class="btn sm" data-addprio>${icon('plus', 's')} Add</button><button class="btn sm primary" data-saveprios>Save priorities</button>` : '',
        body: `${unconfirmed ? `<div class="banner warning">${icon('alert')}<div class="grow">Camview does not document what the numeric priority values mean. Labels marked <b>unconfirmed</b> are assumptions — confirm them with the Camview team, then tick “Confirmed”.</div></div>` : ''}
          <div class="table-wrap"><table class="t"><thead><tr><th>Value</th><th>Label</th><th>Rank (0 = most severe)</th><th>Confirmed</th>${edit ? '<th></th>' : ''}</tr></thead><tbody>
          ${prios.map((p, i) => edit ? `<tr><td><input class="input" style="width:70px" data-p="${i}" data-k="value" value="${esc(p.value)}" inputmode="numeric" aria-label="Value"></td>
            <td><select class="select" data-p="${i}" data-k="label" aria-label="Label">${['critical', 'high', 'medium', 'low'].map((l) => `<option ${l === p.label ? 'selected' : ''}>${l}</option>`).join('')}${['critical', 'high', 'medium', 'low'].includes(p.label) ? '' : `<option selected>${esc(p.label)}</option>`}</select></td>
            <td><input class="input" style="width:70px" data-p="${i}" data-k="rank" value="${esc(p.rank)}" inputmode="numeric" aria-label="Rank"></td>
            <td><label class="check"><input type="checkbox" data-p="${i}" data-k="confirmed" ${p.confirmed ? 'checked' : ''}> Confirmed</label></td>
            <td><button class="btn sm ghost" data-delprio="${i}" aria-label="Remove">${icon('trash', 's')}</button></td></tr>`
            : `<tr><td class="num">${esc(p.value)}</td><td>${esc(p.label)}</td><td class="num">${esc(p.rank)}</td><td>${p.confirmed ? `<span class="b outline">${icon('check')}confirmed</span>` : '<span class="b p-medium">unconfirmed</span>'}</td></tr>`).join('')}
          </tbody></table></div>` })}</div>`;
  };
  const read = () => {
    $$('[data-t]', body).forEach((i) => { types[+i.dataset.t][i.dataset.k] = i.value; });
    $$('[data-p]', body).forEach((i) => { prios[+i.dataset.p][i.dataset.k] = i.type === 'checkbox' ? i.checked : i.value; });
  };
  paint();
  if (!edit) return;
  const deleted = [];
  ctx.onCleanup(delegate(body, 'click', '[data-addtype]', () => { read(); types.push({ id: '', name: '', description: '', severity: '', client_share_policy: 'allowed' }); paint(); }));
  ctx.onCleanup(delegate(body, 'click', '[data-deltype]', (e, b) => { read(); const t = types.splice(+b.dataset.deltype, 1)[0]; if (t && d.alarmTypes.some((x) => String(x.id) === String(t.id))) deleted.push(t.id); paint(); }));
  ctx.onCleanup(delegate(body, 'click', '[data-addprio]', () => { read(); prios.push({ value: '', label: 'low', rank: prios.length, confirmed: false }); paint(); }));
  ctx.onCleanup(delegate(body, 'click', '[data-delprio]', (e, b) => { read(); prios.splice(+b.dataset.delprio, 1); paint(); }));
  ctx.onCleanup(delegate(body, 'click', '[data-savetypes]', async () => {
    read();
    try {
      const r = await api.put('/api/dictionary/alarm-types', { items: types.map((t) => ({ id: t.id, name: t.name, description: t.description || null, severity: t.severity || null, clientSharePolicy: t.client_share_policy })), delete: deleted });
      types = r.alarmTypes.map((t) => ({ ...t })); deleted.length = 0; paint(); toast('Alarm types saved', 'success');
    } catch (e) { toast(e.message, 'error'); }
  }));
  ctx.onCleanup(delegate(body, 'click', '[data-saveprios]', async () => {
    read();
    try {
      const r = await api.put('/api/dictionary/priorities', { items: prios.map((p) => ({ value: p.value, label: p.label, rank: p.rank, confirmed: !!p.confirmed })) });
      prios = r.priorities.map((p) => ({ ...p })); paint(); toast('Priorities saved', 'success');
    } catch (e) { toast(e.message, 'error'); }
  }));
}

// ------------------------------------------------------------------ Import / Export
function importer(body, ctx) {
  const manage = can('nomenclature.manage');
  body.innerHTML = `<div class="grid g-main">
    ${card({ title: `${icon('download')} Import master data`, sub: manage ? 'CSV or JSON · replaces the current nomenclature' : 'Requires nomenclature management permission',
      body: manage ? `
        <div class="row" style="margin-bottom:10px"><div class="seg" role="group" aria-label="Format"><button data-fmt="csv" class="on">CSV</button><button data-fmt="json">JSON</button></div>
          <label class="btn">${icon('plus', 's')} Choose file<input type="file" id="imp-file" accept=".csv,.json,text/csv,application/json" hidden></label><span class="muted" id="imp-name"></span></div>
        <textarea class="input" id="imp-data" rows="12" style="width:100%;font-family:var(--mono);font-size:12px" placeholder="Paste CSV (with header row) or JSON here…"></textarea>
        <div class="row" style="margin-top:10px"><span class="muted grow" style="font-size:11.5px">Import replaces all nomenclature nodes. Alarms are re-resolved immediately.</span><button class="btn primary" id="imp-go">${icon('download', 's')} Import</button></div>
        <div id="imp-result" style="margin-top:12px"></div>`
        : empty('Read-only', 'Ask an administrator to import or update the nomenclature.', 'lock') })}
    <div class="stack">
      ${card({ title: `${icon('report')} Export`, body: `<p class="dim" style="margin-top:0">Download the current nomenclature as CSV (same format as import).</p><button class="btn" id="exp-go">${icon('download', 's')} Export CSV</button>` })}
      ${card({ title: `${icon('info')} CSV columns`, body: `<div class="row tight">${CSV_COLS.map((c) => `<span class="b outline mono">${c}</span>`).join('')}</div>
        <p class="muted" style="font-size:11.5px">One row per camera. <b>project_id</b> is required (Camview's numeric projectId); <b>camera_id</b> must match Camview's cameraId. Levels may be left blank.</p>
        <pre class="mono" style="font-size:11px;white-space:pre-wrap;background:var(--panel-2);padding:8px;border-radius:6px;border:1px solid var(--border)">project_id,project_code,tc_code,centre_code,building,floor,room,camera_id,camera_code
7,PROJECT-07,TC-023,CTR-018,B,2,204,109,CAM-109</pre>` })}
    </div></div>`;
  let fmtSel = 'csv';
  $('#exp-go', body).addEventListener('click', async () => {
    try { await api.download('/api/nomenclature/export.csv', null, 'nomenclature.csv'); } catch (e) { toast(e.message, 'error'); }
  });
  if (!manage) return;
  ctx.onCleanup(delegate(body, 'click', '[data-fmt]', (e, b) => { fmtSel = b.dataset.fmt; $$('[data-fmt]', body).forEach((x) => x.classList.toggle('on', x === b)); }));
  $('#imp-file', body).addEventListener('change', (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => {
      $('#imp-data', body).value = r.result;
      $('#imp-name', body).textContent = f.name;
      fmtSel = /\.json$/i.test(f.name) ? 'json' : 'csv';
      $$('[data-fmt]', body).forEach((x) => x.classList.toggle('on', x.dataset.fmt === fmtSel));
    };
    r.readAsText(f);
  });
  $('#imp-go', body).addEventListener('click', async () => {
    const raw = $('#imp-data', body).value.trim();
    if (!raw) return toast('Paste or choose a file first', 'warning');
    let data = raw;
    if (fmtSel === 'json') {
      try { data = JSON.parse(raw); } catch { return toast('That is not valid JSON', 'error'); }
    }
    const ok = await confirmDialog({ title: 'Replace nomenclature master data?', message: '<p>The current Project › TC › Centre › Room › Camera hierarchy will be <b>replaced</b> by this import. The change is recorded in the audit trail.</p>', confirmLabel: 'Import and replace', danger: true });
    if (!ok) return;
    const out = $('#imp-result', body);
    try {
      const r = await api.post('/api/nomenclature/import', { format: fmtSel, data, replace: true });
      out.innerHTML = `<div class="banner ${r.errors.length ? 'warning' : 'info'}">${icon(r.errors.length ? 'alert' : 'check')}<div class="grow"><b>Imported ${fmt.n(r.rows)} rows.</b> ${Object.entries(r.counts).map(([k, v]) => `${esc(LEVEL_LABEL[k] || k)}: <b>${v}</b>`).join(' · ')}</div></div>
        ${r.errors.length ? `<ul class="check-list">${r.errors.map((x) => `<li class="no"><span class="m">✕</span>${esc(x)}</li>`).join('')}</ul>` : ''}`;
      toast('Nomenclature imported', 'success');
    } catch (e) {
      out.innerHTML = `<div class="banner critical">${icon('alert')}<div>${esc(e.message)}</div></div>`;
    }
  });
}
