// pages/data-quality.js — DATA QUALITY CENTER (#/data-quality)
// Surfaces data problems instead of hiding them: cameras sending alarms that
// have no master-data mapping, incomplete context, duplicate codes, unknown
// alarm types, and alarms missing timestamps / camera / evidence.
// Computed only from nomenclature master data + alarms actually seen (DERIVED).

import * as api from '../core/api.js';
import { can, currentProject, projectInfo } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt, kpi, card, empty, errorBox, skeleton, prov, delegate, $ } from '../core/ui.js';

const LEVEL_LABEL = { project: 'Project', tc: 'TC', centre: 'Centre', building: 'Building', floor: 'Floor', room: 'Room', camera: 'Camera' };

export default {
  async render(el, ctx) {
    const pid = currentProject();
    setTitle('Data Quality', `${esc(projectInfo(pid).code)} · context and data problems`);
    if (!pid) { el.innerHTML = empty('No project available', '', 'tree'); return; }
    el.innerHTML = skeleton(6, 50);
    let q = null, dict = null;

    const load = async () => {
      try {
        const [qq, dd] = await Promise.all([
          api.get('/api/context/quality', { projectId: pid }),
          api.get('/api/dictionary').catch(() => null),
        ]);
        q = qq; dict = dd;
        if (!ctx.isStale()) paint();
      } catch (e) {
        if (ctx.isStale()) return;
        el.innerHTML = errorBox(e);
        $('[data-retry]', el)?.addEventListener('click', load);
      }
    };

    const paint = () => {
      const seen = q.camerasSeen || 0;
      const mapped = q.mappedCameras || 0;
      const unmapped = q.unmappedCameras || [];
      const incomplete = q.incompleteContext || [];
      const dupes = q.duplicateCodes || [];
      const unknownTypes = q.unknownAlarmTypes || [];
      const unknownProjects = q.unknownProjects || [];
      const master = q.masterData || {};
      const issues = unmapped.length + unknownProjects.length + incomplete.length + dupes.length + unknownTypes.length
        + (q.alarmsMissingTimestamps || 0) + (q.alarmsMissingCamera || 0);
      const canManage = can('nomenclature.manage');

      el.innerHTML = `
        <div class="page-head"><div><h2>Data Quality Center</h2>
          <p>Problems are <b>surfaced, not hidden</b>: alarms with incomplete context still appear everywhere, labelled UNMAPPED. Computed from master data and the alarms in the monitored window ${prov('derived')}</p></div>
          <div class="row"><button class="btn" id="dq-refresh">${icon('refresh', 's')} Refresh</button>
            ${can('nomenclature.view') ? `<a class="btn" href="#/context?tab=import">${icon('tree', 's')} Nomenclature</a>` : ''}</div></div>
        <div class="banner ${issues ? 'warning' : 'info'}">${icon(issues ? 'alert' : 'check')}<div class="grow">${issues
          ? `<b>${fmt.n(issues)}</b> data-quality finding${issues === 1 ? '' : 's'} for ${esc(projectInfo(pid).code)}. Fixing master data improves context, intelligent alerts (location relationships need mapping) and client sharing (when context is required).`
          : 'No data-quality problems found for the alarms currently in the monitored window.'}</div></div>

        <style>.kpis.nopad .k-label{padding-right:0}</style><div class="kpis nopad" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">
          ${kpi({ label: 'Cameras seen', value: seen, sub: 'sending alarms in the window', icon: 'camera' })}
          ${kpi({ label: 'Mapped cameras', value: mapped, sub: seen ? `${fmt.pct(mapped / seen)} coverage` : '—', icon: 'check', accent: 'good' })}
          ${kpi({ label: 'Unmapped cameras', value: unmapped.length, sub: 'no master-data mapping', icon: 'alert', accent: unmapped.length ? 'warning' : undefined })}
          ${kpi({ label: 'Missing project', value: unknownProjects.length, sub: 'project id not in master data', icon: 'tree', accent: unknownProjects.length ? 'warning' : undefined })}
          ${kpi({ label: 'Missing TC', value: q.missingTc || 0, icon: 'tree' })}
          ${kpi({ label: 'Missing centre', value: q.missingCentre || 0, icon: 'building' })}
          ${kpi({ label: 'Missing room', value: q.missingRoom || 0, icon: 'grid', accent: q.missingRoom ? 'warning' : undefined })}
          ${kpi({ label: 'Incomplete context', value: incomplete.length, sub: 'mapped cameras missing levels', icon: 'layers' })}
          ${kpi({ label: 'Duplicate codes', value: dupes.length, icon: 'compare', accent: dupes.length ? 'critical' : undefined })}
          ${kpi({ label: 'Unknown alarm types', value: unknownTypes.length, sub: 'not in the dictionary', icon: 'info', accent: unknownTypes.length ? 'warning' : undefined })}
          ${kpi({ label: 'Missing timestamps', value: q.alarmsMissingTimestamps || 0, sub: 'alarms', icon: 'clock' })}
          ${kpi({ label: 'Missing camera', value: q.alarmsMissingCamera || 0, sub: 'alarms', icon: 'camera' })}
          ${kpi({ label: 'Missing evidence', value: q.alarmsMissingEvidence || 0, sub: 'alarms without images/video', icon: 'image' })}
        </div>

        <div class="grid g-2">
          ${card({ title: `${icon('camera')} Unmapped cameras`, sub: 'Cameras sending alarms that are not in the nomenclature — their alarms show without Project/TC/Centre context', flush: true, actions: prov('derived'),
            body: unmapped.length ? `<div class="table-wrap" style="max-height:340px"><table class="t"><thead><tr><th>Camera id</th><th>Resolution</th><th></th></tr></thead><tbody>
              ${unmapped.map((c) => `<tr class="link" tabindex="0" data-href="#/cameras/${encodeURIComponent(c)}?projectId=${encodeURIComponent(pid)}"><td class="mono">CAM-${esc(c)}</td><td><span class="prov unavailable">UNMAPPED</span> <span class="muted">add it to master data</span></td>
                <td><a class="btn sm ghost" href="#/live?${new URLSearchParams({ camera: c }).toString()}">Alarms</a></td></tr>`).join('')}</tbody></table></div>`
              : empty('All cameras are mapped', 'Every camera sending alarms is in the master data.', 'check') })}
          ${card({ title: `${icon('layers')} Incomplete context`, sub: 'Mapped cameras whose hierarchy is missing TC / centre / room', flush: true, actions: prov('derived'),
            body: incomplete.length ? `<div class="table-wrap" style="max-height:340px"><table class="t"><thead><tr><th>Camera</th><th>Missing levels</th></tr></thead><tbody>
              ${incomplete.map((i) => `<tr><td class="mono">${esc(i.camera)}</td><td>${(i.missing || []).map((m) => `<span class="b outline" style="margin:1px">${icon('alert')}${esc(LEVEL_LABEL[m] || m)}</span>`).join(' ')}</td></tr>`).join('')}</tbody></table></div>`
              : empty('No incomplete context', 'Every mapped camera has TC, centre and room.', 'check') })}
        </div>

        <div class="grid g-3" style="margin-top:14px">
          ${card({ title: `${icon('info')} Unknown alarm types`, sub: 'Numeric types Camview sent that have no name in the dictionary', flush: true, actions: prov('direct'),
            body: unknownTypes.length ? `<div class="list">${unknownTypes.map((t) => `<div class="li"><span class="b outline mono">type ${esc(t)}</span><div class="grow"><div class="t2">Shown as “Type ${esc(t)}” until named</div></div>
              <a class="btn sm ghost" href="#/live?${new URLSearchParams({ alarmType: t }).toString()}">Alarms</a></div>`).join('')}</div>
              ${canManage ? `<div style="padding:10px 14px"><a class="btn sm primary" href="#/context?tab=dictionary">${icon('edit', 's')} Name them in the dictionary</a></div>` : '<div class="muted" style="padding:10px 14px;font-size:12px">An administrator with nomenclature.manage can name these types.</div>'}`
              : empty('All alarm types are named', dict ? `${fmt.n((dict.alarmTypes || []).length)} types in the dictionary.` : '', 'check') })}
          ${card({ title: `${icon('compare')} Duplicate identifiers`, sub: 'Codes that should be unique appear more than once', flush: true, actions: prov('derived'),
            body: dupes.length ? `<div class="table-wrap"><table class="t"><thead><tr><th>Level</th><th>Code</th><th class="num">Occurrences</th></tr></thead><tbody>
              ${dupes.map((x) => `<tr><td>${esc(LEVEL_LABEL[x.level] || x.level)}</td><td class="mono">${esc(x.code)}</td><td class="num">${fmt.n(x.count)}</td></tr>`).join('')}</tbody></table></div>`
              : empty('No duplicates', 'Project, TC, centre and camera codes are unique.', 'check') })}
          ${card({ title: `${icon('tree')} Missing project`, sub: 'Project ids in alarm data with no master-data project', flush: true, actions: prov('derived'),
            body: unknownProjects.length ? `<div class="list">${unknownProjects.map((p) => `<div class="li"><span class="b outline mono">project ${esc(p)}</span><div class="grow t2">Import master data for this project</div></div>`).join('')}</div>`
              : empty('All projects known', '', 'check') })}
        </div>

        <div style="margin-top:14px">${card({ title: `${icon('tree')} Master data inventory`, sub: 'Nodes currently imported per level', actions: prov('direct'),
          body: `<div class="kpis nopad" style="margin-bottom:0;grid-template-columns:repeat(auto-fit,minmax(110px,1fr))">${Object.keys(LEVEL_LABEL).map((lvl) => kpi({ label: LEVEL_LABEL[lvl], value: master[lvl] || 0 })).join('')}</div>` })}</div>`;
    };

    ctx.onCleanup(delegate(el, 'click', '#dq-refresh', () => { el.innerHTML = skeleton(6, 50); load(); }));
    ctx.onCleanup(delegate(el, 'click', 'tr[data-href]', (e, tr) => { if (!e.target.closest('a,button')) location.hash = tr.dataset.href; }));
    ctx.onCleanup(delegate(el, 'keydown', 'tr[data-href]', (e, tr) => { if (e.key === 'Enter') location.hash = tr.dataset.href; }));
    await load();
  },
};
