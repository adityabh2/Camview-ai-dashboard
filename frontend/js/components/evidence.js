// components/evidence.js — gallery + evidence focus mode (lightbox).
// Image: zoom (wheel, +/-), pan (drag), gallery strip, keyboard ←/→, fullscreen (F).
// Video: native player (play/pause/seek/volume/fullscreen).
// Internal views/downloads are audited via /api/evidence/log.

import * as api from '../core/api.js';
import { esc, icon, $, $$, toast } from '../core/ui.js';

/** items: [{kind:'image'|'video', index, url, shared?: [clientNames]}] */
export function gallery(items, { showShared = false } = {}) {
  if (!items || !items.length) return '<div class="empty">' + icon('image') + '<div class="e-t">No evidence</div><div>Camview did not attach images or video to this alarm.</div></div>';
  return `<div class="gallery">${items.map((it, i) => it.kind === 'video'
    ? `<button class="thumb" data-ev="${i}" aria-label="Play video evidence"><span class="play">${icon('play', 'l')}</span>${showShared && it.shared ? `<span class="tag b vis-shared">${icon('share')}shared</span>` : ''}</button>`
    : `<button class="thumb" data-ev="${i}" aria-label="Open image ${i + 1}"><img src="${esc(it.url)}" alt="Evidence image ${i + 1}" loading="lazy">${showShared && it.shared ? `<span class="tag b vis-shared">${icon('share')}shared</span>` : ''}</button>`).join('')}</div>`;
}

export function bindGallery(root, items, opts = {}) {
  $$('.thumb img', root).forEach((img) => img.addEventListener('error', () => {
    const b = img.closest('.thumb');
    b.classList.add('broken');
    b.innerHTML = `${icon('alert')}<span>IMAGE UNAVAILABLE</span>`;
    b.disabled = true;
  }, { once: true }));
  $$('[data-ev]', root).forEach((b) => b.addEventListener('click', () => lightbox(items, +b.dataset.ev, opts)));
}

export function lightbox(items, start = 0, { alarmId, canDownload = false, title = '', log = true } = {}) {
  let idx = start;
  let scale = 1, tx = 0, ty = 0;
  const el = document.createElement('div');
  el.className = 'lightbox';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', 'Evidence viewer');
  document.body.appendChild(el);
  const prev = document.activeElement;

  function audit(download = false) {
    if (!log || !alarmId) return;
    const it = items[idx];
    api.post('/api/evidence/log', { alarmId, kind: it.kind, index: it.index ?? idx, download }).catch(() => {});
  }

  function paint() {
    const it = items[idx];
    scale = 1; tx = 0; ty = 0;
    el.innerHTML = `
      <div class="lb-bar">
        <strong>${esc(title || 'Evidence')}</strong><span class="muted">${idx + 1} / ${items.length} · ${it.kind}</span>
        <span class="grow"></span>
        ${it.kind === 'image' ? `<button class="btn sm" data-z="out" aria-label="Zoom out">−</button><button class="btn sm" data-z="reset">100%</button><button class="btn sm" data-z="in" aria-label="Zoom in">+</button>`
          : `<label class="row tight" style="font-size:12px">Speed <select class="select" data-speed aria-label="Playback speed">${[0.25, 0.5, 1, 1.5, 2].map((s) => `<option value="${s}" ${s === 1 ? 'selected' : ''}>${s}×</option>`).join('')}</select></label>`}
        <button class="btn sm" data-fs>${icon('external', 's')} Fullscreen</button>
        ${canDownload ? `<a class="btn sm" data-dl href="${esc(it.url)}" target="_blank" rel="noopener" download>${icon('download', 's')} Download</a>` : ''}
        <button class="btn sm" data-close aria-label="Close viewer">${icon('x', 's')} Close</button>
      </div>
      <div class="lb-stage">
        ${it.kind === 'video'
          ? `<video src="${esc(it.url)}" controls autoplay playsinline></video>`
          : `<img src="${esc(it.url)}" alt="Evidence ${idx + 1}" draggable="false">`}
        ${items.length > 1 ? `<button class="btn icon lb-nav prev" data-nav="-1" aria-label="Previous">${icon('left')}</button><button class="btn icon lb-nav next" data-nav="1" aria-label="Next">${icon('right')}</button>` : ''}
      </div>
      ${items.length > 1 ? `<div class="lb-strip">${items.map((x, i) => `<button class="${i === idx ? 'on' : ''}" data-go="${i}" aria-label="Item ${i + 1}">${x.kind === 'video' ? `<span style="color:#fff">${icon('play')}</span>` : `<img src="${esc(x.url)}" alt="">`}</button>`).join('')}</div>` : ''}`;
    const media = $('img, video', el.querySelector('.lb-stage'));
    media?.addEventListener('error', () => {
      el.querySelector('.lb-stage').insertAdjacentHTML('afterbegin', '<div class="error-state" style="color:#dfe6f2">' + icon('alert') + '<div class="e-t">' + (it.kind === 'video' ? 'VIDEO UNAVAILABLE' : 'IMAGE UNAVAILABLE') + '</div><div>The file may have expired or the evidence service is unavailable.</div></div>');
      media.remove();
    });
    bind();
    audit(false);
  }

  function apply() {
    const img = $('.lb-stage img', el);
    if (img) img.style.transform = `translate(${tx}px, ${ty}px) scale(${scale})`;
  }
  function zoom(f) { scale = Math.min(8, Math.max(1, f === 0 ? 1 : scale * f)); if (scale === 1) { tx = 0; ty = 0; } apply(); }
  function go(d) { idx = (idx + d + items.length) % items.length; paint(); }

  function bind() {
    el.querySelector('[data-close]').addEventListener('click', close);
    el.querySelector('[data-fs]').addEventListener('click', () => (document.fullscreenElement ? document.exitFullscreen() : el.requestFullscreen?.()));
    $$('[data-z]', el).forEach((b) => b.addEventListener('click', () => zoom({ in: 1.4, out: 1 / 1.4, reset: 0 }[b.dataset.z])));
    $$('[data-nav]', el).forEach((b) => b.addEventListener('click', () => go(+b.dataset.nav)));
    $$('[data-go]', el).forEach((b) => b.addEventListener('click', () => { idx = +b.dataset.go; paint(); }));
    el.querySelector('[data-dl]')?.addEventListener('click', () => audit(true));
    el.querySelector('[data-speed]')?.addEventListener('change', (e) => {
      const v = $('video', el);
      if (v) v.playbackRate = +e.target.value;
    });
    const stage = el.querySelector('.lb-stage');
    stage.addEventListener('wheel', (e) => { if ($('img', stage)) { e.preventDefault(); zoom(e.deltaY < 0 ? 1.15 : 1 / 1.15); } }, { passive: false });
    let drag = null;
    stage.addEventListener('pointerdown', (e) => { if (scale > 1 && $('img', stage)) { drag = { x: e.clientX - tx, y: e.clientY - ty }; stage.classList.add('dragging'); stage.setPointerCapture(e.pointerId); } });
    stage.addEventListener('pointermove', (e) => { if (drag) { tx = e.clientX - drag.x; ty = e.clientY - drag.y; apply(); } });
    stage.addEventListener('pointerup', () => { drag = null; stage.classList.remove('dragging'); });
    el.querySelector('[data-close]').focus();
  }

  const key = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); }
    else if (e.key === 'ArrowRight') go(1);
    else if (e.key === 'ArrowLeft') go(-1);
    else if (e.key === '+' || e.key === '=') zoom(1.4);
    else if (e.key === '-') zoom(1 / 1.4);
    else if (e.key === '0') zoom(0);
    else if (e.key.toLowerCase() === 'f') el.requestFullscreen?.();
  };
  function close() {
    document.removeEventListener('keydown', key, true);
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    el.remove();
    prev?.focus?.();
  }
  document.addEventListener('keydown', key, true);
  paint();
  if (!items.length) { close(); toast('No evidence to show'); }
}
