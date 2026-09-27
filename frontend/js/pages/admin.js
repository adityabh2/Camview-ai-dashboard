// pages/admin.js — ADMINISTRATION: the settings that make the flow automatic, and the checks that keep it honest.
// Only entries the user may open are shown. Advanced tools live in the sidebar under Advanced.

import { setTitle } from '../core/layout.js';
import { allowed, match } from '../core/router.js';
import { esc, icon } from '../core/ui.js';

const SECTIONS = [
  ['Setup', [
    ['#/settings?tab=projects', 'Projects & codes', 'tree', 'Which Camview projects are monitored, their project codes — the client and exam follow from the code.'],
    ['#/management?tab=exams', 'Exams & clients', 'report', 'Exam ↔ client ↔ project mapping (created automatically from project codes; adjust here).'],
    ['#/settings?tab=workflow', 'Delivery', 'zap', 'When alerts reach the client (on arrival / when VALID) and what evidence is sent.'],
    ['#/management?tab=dictionary', 'Alert types', 'layers', 'Names and severity of the numeric Camview alert types.'],
    ['#/users', 'Users & roles', 'users', 'Accounts, roles, permissions and access scopes.'],
  ]],
  ['Checks', [
    ['#/monitoring?tab=health', 'Camera health', 'camera', 'Which cameras are online, offline or not syncing frames.'],
    ['#/management?tab=quality', 'Data quality', 'shield', 'Unmapped cameras, unknown alert types, missing context.'],
    ['#/settings?tab=system', 'Connection & feeds', 'settings', 'Camview connection, refresh state and the newest alert per project.'],
    ['#/audit', 'Audit trail', 'audit', 'Every decision, delivery and change, append-only.'],
  ]],
];

export default {
  async render(el) {
    setTitle('Administration', 'Setup and checks');
    const ok = (href) => { const m = match(href.slice(1).split('?')[0]); return m && allowed(m.r); };
    el.innerHTML = `<div class="page-head"><div><h2>Administration</h2><p>The few settings that make the flow automatic, and the checks that keep it honest.</p></div></div>
      ${SECTIONS.map(([title, items]) => {
        const vis = items.filter(([href]) => ok(href));
        return vis.length ? `<div class="section-title">${esc(title)}</div><div class="admin-grid">${vis.map(([href, label, ic, text]) =>
          `<a class="atile" href="${href}">${icon(ic)}<div><div class="t1">${esc(label)}</div><div class="t2">${esc(text)}</div></div></a>`).join('')}</div>` : '';
      }).join('')}`;
  },
};
