// pages/hub.js — one destination, several existing screens as tabs (Monitoring, Management).
// The tab's page module is reused as-is; nothing is duplicated.

import { can, canAny, session } from '../core/state.js';
import { setTitle } from '../core/layout.js';
import { esc, errorBox, icon, $ } from '../core/ui.js';

export function hub(title, sub, tabs) {
  return {
    async render(el, ctx) {
      const visible = tabs.filter((t) => (!t.flag || session.features?.[t.flag]) && (!t.perms || (t.any ? canAny(...t.perms) : t.perms.every(can))));
      const key = visible.some((t) => t.key === ctx.query.tab) ? ctx.query.tab : visible[0]?.key;
      el.innerHTML = `<div class="tabs hub-tabs" role="tablist">${visible.map((t) => `<a class="tab ${t.key === key ? 'on' : ''}" role="tab" aria-selected="${t.key === key}"
          href="#${esc(ctx.path)}?tab=${esc(t.key)}">${t.icon ? icon(t.icon, 's') : ''}${esc(t.label)}</a>`).join('')}</div><div id="hub-body"></div>`;
      const tab = visible.find((t) => t.key === key);
      if (!tab) { $('#hub-body', el).innerHTML = errorBox({ status: 403, message: 'You have no access to this area.' }, { retry: false }); return; }
      const mod = await import(`./${tab.module}.js`);
      if (ctx.isStale()) return;
      await mod.default.render($('#hub-body', el), ctx);
      setTitle(title, `${esc(sub)} · ${esc(tab.label)}`);
    },
  };
}
