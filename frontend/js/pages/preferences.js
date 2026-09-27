// pages/preferences.js — PREFERENCES (all users).
// Display preferences live in this browser (safe values only); notification
// preferences are stored on the server because they change what gets delivered.

import * as api from '../core/api.js';
import { isClient, pref, setPref, emit } from '../core/state.js';
import { setTitle, toggleTheme } from '../core/layout.js';
import { refreshMs, setRefreshMs, beep } from '../core/live.js';
import { esc, icon, card, errorBox, skeleton, toast, delegate, $ } from '../core/ui.js';

const seg = (name, options, current) => `<div class="seg" role="group" aria-label="${esc(name)}" style="align-self:flex-start">${options.map(([v, l]) =>
  `<button type="button" data-seg="${esc(name)}" data-v="${esc(v)}" class="${String(v) === String(current) ? 'on' : ''}" aria-pressed="${String(v) === String(current)}">${esc(l)}</button>`).join('')}</div>`;

export default {
  async render(el, ctx) {
    setTitle('Preferences', 'Display and notification settings for you');
    el.innerHTML = skeleton(5, 40);
    let n;
    try {
      n = (await api.get('/api/me/preferences')).notifications;
    } catch (e) {
      if (ctx.isStale()) return;
      el.innerHTML = errorBox(e);
      $('[data-retry]', el)?.addEventListener('click', () => this.render(el, ctx));
      return;
    }
    if (ctx.isStale()) return;

    const theme = document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
    const density = pref('density', 'comfortable');
    const tz = pref('tz', 'local');
    const cycle = pref('presentationCycle', 15);
    const hasBrowserApi = 'Notification' in window;

    el.innerHTML = `<div class="page-head"><div><h2>Preferences</h2><p>${icon('lock', 's')} Only safe preferences are stored in this browser — never credentials, keys or evidence. Notification preferences are saved to your account.</p></div></div>
      <div class="grid g-2">
        ${card({ title: `${icon('sun')} Display`, body: `
          <div class="field"><label>Theme</label>${seg('theme', [['dark', 'Dark'], ['light', 'Light']], theme)}</div>
          <div class="field"><label>Table density</label>${seg('density', [['comfortable', 'Comfortable'], ['compact', 'Compact']], density)}
            <div class="hint">Compact tables show more rows; secondary lines are hidden.</div></div>
          <div class="field"><label>Display time zone</label>${seg('tz', [['local', 'Local time'], ['utc', 'UTC']], tz)}
            <div class="hint">Times re-render in the chosen zone on the next view you open.</div></div>
          <div class="field"><label for="pf-refresh">Refresh interval</label><select class="select" id="pf-refresh" style="max-width:240px">
            ${[5000, 10000, 15000, 30000, 60000, 120000, 180000].map((ms) => `<option value="${ms}" ${refreshMs() === ms ? 'selected' : ''}>Every ${ms < 60000 ? ms / 1000 + ' seconds' : ms / 60000 + ' min'}</option>`).join('')}</select>
            <div class="hint">${isClient() ? 'How often your notification count is refreshed.' : 'How often live data and the freshness indicator are refreshed (polling).'}</div></div>
          <div class="field"><label for="pf-cycle">Presentation auto-cycle</label><select class="select" id="pf-cycle" style="max-width:240px">
            ${[10, 15, 30, 60].map((s) => `<option value="${s}" ${Number(cycle) === s ? 'selected' : ''}>Every ${s} seconds</option>`).join('')}</select>
            <div class="hint">Time each slide stays on screen in Presentation Mode.</div></div>` })}
        ${card({ title: `${icon('bell')} Notifications`, body: `
          <label class="check" style="margin-bottom:10px"><input type="checkbox" id="pf-enabled" ${n.enabled ? 'checked' : ''}> Receive notifications</label>
          <label class="check" style="margin-bottom:10px"><input type="checkbox" id="pf-critical" ${n.criticalOnly ? 'checked' : ''}> Critical only</label>
          <div class="banner info" style="margin:4px 0 14px">${icon('info')}<div><b>Critical notifications are always delivered</b>, even when notifications are off or set to critical only, and they can't be snoozed.</div></div>
          <label class="check" style="margin-bottom:6px"><input type="checkbox" id="pf-sound" ${n.sound ? 'checked' : ''}> Play a sound for new notifications</label>
          <div class="row" style="margin:0 0 12px 23px"><button class="btn sm" id="pf-test">${icon('play', 's')} Test sound</button><span class="muted" style="font-size:11.5px">Off by default — never enabled automatically.</span></div>
          <label class="check" style="margin-bottom:6px"><input type="checkbox" id="pf-browser" ${n.browser ? 'checked' : ''} ${hasBrowserApi ? '' : 'disabled'}> Browser notifications when this tab is in the background</label>
          <div class="muted" style="font-size:11.5px;margin-left:23px">${hasBrowserApi ? `Browser permission: <b id="pf-perm">${esc(Notification.permission)}</b>` : 'This browser does not support notifications.'}</div>
          <div class="muted" style="font-size:11.5px;margin-top:12px">Email and SMS delivery are not integrated yet.</div>` })}
      </div>
      <div class="muted" style="margin-top:14px;font-size:12px">${icon('key', 's')} To change your password, open the user menu (your name, top right) › Change password.</div>`;

    const saveNotif = async (patch) => {
      try {
        n = (await api.put('/api/me/preferences', { notifications: patch })).notifications;
        emit('prefs');
        toast('Notification preferences saved', 'success');
      } catch (e) { toast(e.message, 'error'); }
    };

    ctx.onCleanup(delegate(el, 'click', '[data-seg]', (e, b) => {
      const name = b.dataset.seg, v = b.dataset.v;
      el.querySelectorAll(`[data-seg="${name}"]`).forEach((x) => { x.classList.toggle('on', x === b); x.setAttribute('aria-pressed', x === b); });
      if (name === 'theme') {
        if ((document.documentElement.dataset.theme === 'light' ? 'light' : 'dark') !== v) toggleTheme();
      } else if (name === 'density') {
        setPref('density', v); document.body.dataset.density = v; toast(`Table density: ${v}`);
      } else if (name === 'tz') {
        setPref('tz', v); document.body.dataset.tz = v; toast(`Times will be shown in ${v === 'utc' ? 'UTC' : 'local time'}`);
      }
    }));
    $('#pf-refresh', el).addEventListener('change', (e) => { setRefreshMs(+e.target.value); toast(`Refresh every ${+e.target.value / 1000}s`); });
    $('#pf-cycle', el).addEventListener('change', (e) => { setPref('presentationCycle', +e.target.value); toast('Presentation cycle saved'); });
    $('#pf-enabled', el).addEventListener('change', (e) => saveNotif({ enabled: e.target.checked }));
    $('#pf-critical', el).addEventListener('change', (e) => saveNotif({ criticalOnly: e.target.checked }));
    $('#pf-sound', el).addEventListener('change', (e) => { if (e.target.checked) beep(1); saveNotif({ sound: e.target.checked }); });
    $('#pf-test', el).addEventListener('click', () => beep(1));
    $('#pf-browser', el).addEventListener('change', async (e) => {
      if (e.target.checked && hasBrowserApi) {
        const perm = Notification.permission === 'granted' ? 'granted' : await Notification.requestPermission();
        const p = $('#pf-perm', el);
        if (p) p.textContent = perm;
        if (perm !== 'granted') {
          e.target.checked = false;
          toast('Browser notifications are blocked — allow them in the site settings', 'warning');
          return saveNotif({ browser: false });
        }
      }
      saveNotif({ browser: e.target.checked });
    });
  },
};
