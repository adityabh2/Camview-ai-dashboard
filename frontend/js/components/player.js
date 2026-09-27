// components/player.js — the evidence workspace of the review screen (and the client alert page).
//   Video panel : the primary viewer. Native controls (play / pause / seek / timeline / volume /
//                 fullscreen / current time / duration) + speed, a loading state and a clip strip
//                 when several clips exist. Missing or broken video → VIDEO UNAVAILABLE.
//   Image panel : main image + thumbnails; click to enlarge (zoom / pan in the viewer). Two or more
//                 images can be played as a SNAPSHOT SEQUENCE (clearly labelled, not a video).
//                 Missing or broken image → IMAGE UNAVAILABLE.
// Mounted once per alert: live refreshes never re-render it, so playback position is never lost.
// Internal evidence views are audited through /api/evidence/log (log: false on the client portal).

import * as api from '../core/api.js';
import { esc, icon, $, $$ } from '../core/ui.js';
import { lightbox } from './evidence.js';

const SPEEDS = [0.25, 0.5, 1, 1.5, 2];
const t2 = (s) => (Number.isFinite(s) ? `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}` : '–:––');

const unavailable = (kind, why) => `<div class="pl-unavail">${icon(kind === 'video' ? 'video' : 'image', 'l')}
  <b>${kind === 'video' ? 'VIDEO UNAVAILABLE' : 'IMAGE UNAVAILABLE'}</b><span>${esc(why)}</span></div>`;

export function mountEvidence(videoRoot, imageRoot, items, { alarmId, canDownload = false, title = '', log = true, boxes = [] } = {}) {
  const videos = items.filter((x) => x.kind === 'video');
  const images = items.filter((x) => x.kind === 'image');
  const logged = new Set();
  const audit = (it) => {
    const key = `${it.kind}:${it.index}`;
    if (!log || !alarmId || logged.has(key)) return;
    logged.add(key);
    api.post('/api/evidence/log', { alarmId, kind: it.kind, index: it.index ?? 0, download: false }).catch(() => {});
  };

  // ------------------------------------------------------------------ video
  let vi = 0, speed = 1;
  const brokenV = new Set();
  function paintVideo() {
    if (!videos.length) {
      videoRoot.innerHTML = `<div class="pl-stage pl-empty">${unavailable('video', 'No video is attached to this alert.')}</div>`;
      return;
    }
    const v = videos[vi];
    videoRoot.innerHTML = `
      <div class="pl-stage" id="pl-vstage">
        ${brokenV.has(vi) ? unavailable('video', 'The video could not be loaded (expired link or evidence service unavailable).')
          : `<video src="${esc(v.url)}" controls playsinline preload="metadata"></video><div class="pl-loading" id="pl-vload"><span class="spin"></span>Loading video…</div>`}
      </div>
      <div class="pl-bar">
        <span class="pl-time num" id="pl-time">0:00 / –:––</span>
        <label class="row tight">Speed <select class="select sm" data-vspeed aria-label="Playback speed">${SPEEDS.map((s) => `<option value="${s}" ${s === speed ? 'selected' : ''}>${s}×</option>`).join('')}</select></label>
        <span class="grow"></span>
        <button class="btn sm" data-vfs>${icon('external', 's')} Fullscreen</button>
        ${canDownload ? `<a class="btn sm" href="${esc(v.url)}" target="_blank" rel="noopener" download>${icon('download', 's')}</a>` : ''}
      </div>
      ${videos.length > 1 ? `<div class="pl-clips" role="tablist" aria-label="Clips">${videos.map((_, i) => `<button class="btn sm ${i === vi ? 'primary' : ''}" data-clip-i="${i}" role="tab" aria-selected="${i === vi}">${icon('play', 's')} Clip ${i + 1}</button>`).join('')}</div>` : ''}`;
    const el = $('video', videoRoot);
    if (el) {
      el.playbackRate = speed;
      const load = $('#pl-vload', videoRoot);
      const time = () => { const t = $('#pl-time', videoRoot); if (t) t.textContent = `${t2(el.currentTime)} / ${t2(el.duration)}`; };
      el.addEventListener('loadedmetadata', time);
      el.addEventListener('timeupdate', time);
      el.addEventListener('canplay', () => load?.remove(), { once: true });
      el.addEventListener('waiting', () => load && (load.style.display = ''));
      el.addEventListener('playing', () => { load?.remove(); audit(v); });
      el.addEventListener('error', () => { brokenV.add(vi); paintVideo(); }, { once: true });
    }
    $('[data-vspeed]', videoRoot)?.addEventListener('change', (e) => { speed = +e.target.value; if (el) el.playbackRate = speed; });
    $('[data-vfs]', videoRoot)?.addEventListener('click', () => (document.fullscreenElement ? document.exitFullscreen() : (el || $('#pl-vstage', videoRoot)).requestFullscreen?.()));
    $$('[data-clip-i]', videoRoot).forEach((b) => b.addEventListener('click', () => { vi = +b.dataset.clipI; paintVideo(); }));
  }

  // ------------------------------------------------------------------ images
  let ii = 0, seq = null;
  const brokenI = new Set();
  function paintImages() {
    if (!images.length) {
      imageRoot.innerHTML = `<div class="pl-img pl-empty">${unavailable('image', 'No image is attached to this alert.')}</div>`;
      return;
    }
    const img = images[ii];
    imageRoot.innerHTML = `
      <button class="pl-img" data-open aria-label="Enlarge image ${ii + 1}" ${brokenI.has(ii) ? 'disabled' : ''}>
        ${brokenI.has(ii) ? unavailable('image', 'The image could not be loaded.') : `<span class="pl-frame"><img src="${esc(img.url)}" alt="Evidence image ${ii + 1}">${boxes.length && ii === 0 ? '<svg class="pl-boxes" aria-hidden="true"></svg>' : ''}</span>`}
        ${seq ? `<span class="pl-tag">${icon('play', 's')} SNAPSHOT SEQUENCE · ${ii + 1}/${images.length} · not a video</span>` : images.length > 1 ? `<span class="pl-tag">${ii + 1} / ${images.length}</span>` : ''}
        ${brokenI.has(ii) ? '' : `<span class="pl-zoom">${icon('search', 's')} Enlarge</span>`}
      </button>
      ${images.length > 1 ? `<div class="pl-strip">${images.map((x, i) => `<button class="pl-thumb ${i === ii ? 'on' : ''} ${brokenI.has(i) ? 'broken' : ''}" data-img="${i}" aria-label="Image ${i + 1}">
          ${brokenI.has(i) ? '<span class="pl-v"><small>IMAGE UNAVAILABLE</small></span>' : `<img src="${esc(x.url)}" alt="" loading="lazy">`}</button>`).join('')}</div>
        <div class="pl-bar"><button class="btn sm" data-seq>${icon(seq ? 'pause' : 'play', 's')} ${seq ? 'Stop sequence' : 'Play as sequence'}</button></div>` : ''}`;
    $('.pl-img img', imageRoot)?.addEventListener('error', () => { brokenI.add(ii); paintImages(); }, { once: true });
    $$('.pl-thumb img', imageRoot).forEach((t) => t.addEventListener('error', () => { const i = +t.closest('[data-img]').dataset.img; if (!brokenI.has(i)) { brokenI.add(i); paintImages(); } }, { once: true }));
    $('[data-open]', imageRoot)?.addEventListener('click', () => { stopSeq(); lightbox(images, ii, { alarmId, canDownload, title, log }); });
    $$('[data-img]', imageRoot).forEach((b) => b.addEventListener('click', () => { stopSeq(); ii = +b.dataset.img; paintImages(); }));
    $('[data-seq]', imageRoot)?.addEventListener('click', () => (seq ? (stopSeq(), paintImages()) : startSeq()));
    if (!brokenI.has(ii)) audit(img);
    drawBoxes();
  }
  // What the AI detected, drawn on the alert frame in the frame's own pixel coordinates (Camview metadata).
  function drawBoxes() {
    const frame = $('.pl-frame', imageRoot);
    const im = frame && $('img', frame), svg = frame && $('.pl-boxes', frame);
    if (!im || !svg) return;
    const sync = () => {
      const w = im.naturalWidth, h = im.naturalHeight;
      if (!w || !h || !im.clientWidth) return;
      svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
      svg.setAttribute('preserveAspectRatio', 'none');
      Object.assign(svg.style, { width: `${im.clientWidth}px`, height: `${im.clientHeight}px`, left: `${im.offsetLeft}px`, top: `${im.offsetTop}px` });
      if (svg.childElementCount) return;
      const sw = Math.max(2, Math.round(w / 320)), fs = Math.max(12, Math.round(w / 42));
      svg.innerHTML = boxes.map((b) => {
        const x = Math.min(b.x1, b.x2), y = Math.min(b.y1, b.y2), bw = Math.abs(b.x2 - b.x1), bh = Math.abs(b.y2 - b.y1);
        const label = `${b.label}${b.confidence != null ? ` ${Math.round(b.confidence * 100)}%` : ''}`;
        return `<rect x="${x}" y="${y}" width="${bw}" height="${bh}" stroke-width="${sw}"/><text x="${x + sw}" y="${y > fs + sw ? y - sw : y + fs}" font-size="${fs}">${esc(label)}</text>`;
      }).join('');
    };
    if (im.complete && im.naturalWidth) sync(); else im.addEventListener('load', sync, { once: true });
    if (window.ResizeObserver) new ResizeObserver(sync).observe(im);
  }
  function stopSeq() { clearInterval(seq); seq = null; }
  function startSeq() {
    seq = setInterval(() => { if (!imageRoot.isConnected) return stopSeq(); ii = (ii + 1) % images.length; paintImages(); }, 900);
    paintImages();
  }

  paintVideo();
  paintImages();
  return {
    next() { if (images.length > 1) { stopSeq(); ii = (ii + 1) % images.length; paintImages(); } },
    prev() { if (images.length > 1) { stopSeq(); ii = (ii - 1 + images.length) % images.length; paintImages(); } },
    togglePlay() { const v = $('video', videoRoot); if (v) { v.paused ? v.play().catch(() => {}) : v.pause(); } },
    destroy() { stopSeq(); $('video', videoRoot)?.pause(); },
  };
}
