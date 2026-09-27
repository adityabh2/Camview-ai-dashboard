// pages/v2.js — V2 FEATURES: what each one does, whether it is on, and a link to it.
// Every feature listed here is implemented; a feature flag can still switch one off (backend/.env).
// Old links (#/v2/<feature>) open the real page.

import * as api from '../core/api.js';
import { session } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, icon, fmt } from '../core/ui.js';
import { status as live } from '../core/live.js';

const FEATURES = {
  incidents: {
    title: 'Incident Management', icon: 'layers', flag: 'ENABLE_INCIDENTS', href: '#/incidents',
    text: 'Related alerts (same centre or camera, close in time) are suggested as one incident with the reason spelled out. Incidents have an owner, a status, comments and a timeline.',
  },
  map: {
    title: 'Operations Map', icon: 'map', flag: 'ENABLE_MAP', href: '#/map',
    text: 'Every centre on a map, coloured by alert status and sized by activity. Positions are by city unless an administrator sets the exact place.',
  },
  ai: {
    title: 'AI Assistant', icon: 'cpu', flag: 'ENABLE_AI', href: '#/assistant', needsKey: true,
    text: 'Ask questions in plain words ("which centres have the most mobile phone alerts today?"). Answers use only data you are allowed to see and cite the alerts they come from.',
  },
  'nl-search': {
    title: 'Smart Search', icon: 'search', flag: 'ENABLE_SMART_SEARCH', href: '#/search',
    text: 'Type "critical pending mobile phone at 9111 today" and the filters are shown before searching. Also in Ctrl K.',
  },
  realtime: {
    title: 'Real-time Updates', icon: 'zap', flag: 'ENABLE_REALTIME', href: null,
    text: 'Screens update the moment data changes (push from Command Center to the browser). Camview itself has no push, so the server reads it every few seconds.',
  },
};
const ALIASES = { correlation: 'incidents', search: 'nl-search' };

export default {
  async render(el, ctx) {
    const key = ALIASES[ctx.params.feature] || ctx.params.feature;
    const f = FEATURES[key];
    if (f && f.href && session.features?.[f.flag]) { location.replace(f.href); return; }
    setTitle('V2 features', 'What is new and where to find it');
    let ai = null;
    try { ai = await api.get('/api/ai/status'); } catch { /* not available */ }
    if (ctx.isStale()) return;
    const stateOf = (x) => {
      if (!session.features?.[x.flag]) return ['off', 'OFF'];
      if (x.needsKey && !ai?.configured) return ['setup', 'NEEDS API KEY'];
      return ['on', 'ON'];
    };
    const liveNote = `${live.push ? 'Push connected' : 'Checking every few seconds'} · last update ${live.lastDataAt ? fmt.time(new Date(live.lastDataAt).toISOString()) : '—'}`;
    el.innerHTML = `<div class="page-head"><div><h2>V2 features</h2><p>All V2 features are built and switched on. A feature flag in the server configuration can turn one off.</p></div></div>
      <div class="v2-grid">${Object.entries(FEATURES).map(([k, x]) => {
        const [cls, label] = stateOf(x);
        const tag = x.href && cls !== 'off' ? 'a' : 'div';
        return `<${tag} class="v2-card" ${tag === 'a' ? `href="${x.href}"` : ''} data-feature="${esc(k)}">
          <div class="v2-top">${icon(x.icon)}<h3>${esc(x.title)}</h3><span class="grow"></span><span class="v2-state ${cls}">${label}</span></div>
          <p>${esc(x.text)}</p>
          <div class="v2-foot"><span class="mono">${esc(x.flag)}</span><span class="grow"></span>${k === 'realtime' ? esc(liveNote) : x.href && cls !== 'off' ? `Open ${icon('right', 's')}` : ''}</div>
        </${tag}>`;
      }).join('')}</div>
      ${ai && !ai.configured ? `<div class="banner warning" style="margin-top:14px">${icon('cpu')}<div class="grow"><b>AI Assistant needs an API key.</b> Set <span class="mono">CAMVIEW_AI_API_KEY</span> in the .env next to docker-compose.yml and restart. Smart Search works without it.</div></div>` : ''}`;
  },
};
