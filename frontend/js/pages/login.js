// login.js — sign-in. In demo mode, one-click demo accounts for every role.

import * as api from '../core/api.js';
import { session } from '../core/state.js';
import { esc, icon } from '../core/ui.js';
import { brand, brandMarkHtml, brandTitle, loadBranding } from '../core/layout.js';

export default {
  render(el, { onSuccess }) {
    const demo = session.mode === 'demo';
    const demoUsers = session.demoUsers || [];
    el.innerHTML = `
    <div class="login">
      <section class="l-brand">
        <div class="row login-brand"><span data-brand-mark>${brandMarkHtml()}</span><div><div class="brand-name" data-brand-name>${esc(brand.name)}</div><div class="brand-sub" data-brand-sub>${esc(brand.subtitle)}</div></div></div>
        <div>
          <div class="hero-eyebrow" style="font-size:11px;letter-spacing:.24em;font-weight:800;color:var(--accent-2)">ALARM INTELLIGENCE • LIVE OPERATIONS • INVESTIGATION • COLLABORATION</div>
          <h1>COMMAND CENTER</h1>
          <p class="dim" style="max-width:520px;font-size:14px">Monitor alarm activity, investigate with full context and evidence, validate, approve and share only what the client is authorised to see — with every step audited.</p>
          <div class="flow">${['DETECT', 'REVIEW', 'INVESTIGATE', 'VALIDATE', 'APPROVE', 'SHARE', 'MONITOR', 'REPORT'].map((s) => `<span>${s}</span>`).join('<span style="border:0;padding:4px 0">→</span>')}</div>
        </div>
        <div class="muted" style="font-size:11.5px">${icon('lock', 's')} The Camview API key never leaves the server. Access is enforced server-side by role, scope and client.</div>
      </section>
      <section class="l-form">
        <div class="l-card">
          <div class="row login-brand l-card-brand"><span data-brand-mark>${brandMarkHtml()}</span><div><div class="brand-name" data-brand-name>${esc(brand.name)}</div><div class="brand-sub" data-brand-sub>${esc(brand.subtitle)}</div></div></div>
          ${demo ? `<div class="banner warning" style="margin-bottom:16px"><span class="demo-flag">DEMO DATA</span><div>This instance runs on generated demo data, not real alarms.${demoUsers.length ? ' Pick a demo account below (password <b class="mono">demo</b>).' : ''}</div></div>` : ''}
          <h2 style="margin:0 0 4px;font-size:20px">Sign in</h2>
          <p class="muted" style="margin:0 0 18px">Use the account your administrator created for you.</p>
          <form id="login-form" novalidate>
            <div class="field"><label for="email">Email or username</label><input class="input" id="email" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus></div>
            <div class="field"><label for="password">Password</label><input class="input" id="password" type="password" autocomplete="current-password" required></div>
            <div id="login-err" class="banner critical hidden" role="alert"></div>
            <button class="btn primary" style="width:100%;height:36px" type="submit">Sign in</button>
          </form>
          ${demo && demoUsers.length ? `<div class="section-title">Demo accounts</div>
            <div class="demo-users">${demoUsers.map((u) => `<button type="button" data-email="${esc(u.email)}"><div style="font-weight:650">${esc(u.name)}</div><div class="r">${esc(u.role)}${u.audience === 'client' ? ' · client portal' : ''}</div></button>`).join('')}</div>` : ''}
        </div>
      </section>
    </div>`;
    document.title = brandTitle();
    loadBranding(true).then(() => { if (el.querySelector('#login-form')) document.title = brandTitle(); });
    const form = el.querySelector('#login-form');
    const err = el.querySelector('#login-err');
    const submit = async (email, password) => {
      err.classList.add('hidden');
      try {
        await api.post('/api/auth/login', { email, password });
        onSuccess();
      } catch (e) {
        err.textContent = e.message;
        err.classList.remove('hidden');
      }
    };
    form.addEventListener('submit', (e) => { e.preventDefault(); submit(el.querySelector('#email').value, el.querySelector('#password').value); });
    el.querySelectorAll('[data-email]').forEach((b) => b.addEventListener('click', () => submit(b.dataset.email, session.demoPassword || 'demo')));
  },
};
