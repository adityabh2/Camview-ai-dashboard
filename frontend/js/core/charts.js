// charts.js — thin Chart.js wrapper.
// * loads Chart.js on demand (cdnjs, jsDelivr fallback); if both fail the
//   caller's table/number view still works (charts are never the only view)
// * colours come from CSS tokens at draw time and are re-read on the `themechange`
//   window event, so every live chart re-themes in place without a reload
// * dataviz mark specs: bars <= 24px, 4px rounded data-end square at the baseline (only the top
//   segment of a stack is rounded), 2px surface gap between stacked segments, hairline recessive
//   grid, one tooltip listing every series (value first, line keys) with a stack total
// * a chart already on a canvas is updated in place on live refresh (never rebuilt → no flicker)
// * prefers-reduced-motion → no animation

const SOURCES = [
  'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js',
  'https://cdn.jsdelivr.net/npm/chart.js@4.4.4/dist/chart.umd.min.js',
];
let loading = null;

export function loadCharts() {
  if (window.Chart) return Promise.resolve(window.Chart);
  if (loading) return loading;
  loading = new Promise((resolve) => {
    const tryNext = (i) => {
      if (i >= SOURCES.length) return resolve(null);
      const s = document.createElement('script');
      s.src = SOURCES[i];
      s.onload = () => resolve(window.Chart || null);
      s.onerror = () => { s.remove(); tryNext(i + 1); };
      document.head.appendChild(s);
    };
    tryNext(0);
  });
  return loading;
}

export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif";
const reduced = () => !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const anim = () => (reduced() ? false : { duration: 280, easing: 'easeOutCubic' });
// a colour given as 'var(--token)' is resolved now (and again on every theme change)
const resolve = (c) => (c && c.startsWith('var(') ? css(c.slice(4, -1)) : c) || css('--chart-1');
const fmtN = (v) => (Number(v) || 0).toLocaleString();

// Hover "lift": mix the mark colour a little towards the text colour (lighter on dark, darker on light).
function lift(hex, towards, amt = 0.18) {
  const p = (h) => { const m = /^#?([0-9a-f]{6})$/i.exec(h || ''); return m ? [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16)) : null; };
  const a = p(hex); const b = p(towards);
  if (!a || !b) return hex;
  return '#' + a.map((v, i) => Math.round(v + (b[i] - v) * amt).toString(16).padStart(2, '0')).join('');
}
function withAlpha(hex, alpha) {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(m[1].slice(i, i + 2), 16));
  return `rgba(${r},${g},${b},${alpha})`;
}

const live = new Set();
function prune() { live.forEach((c) => { if (!c.canvas || !c.canvas.isConnected) { c.destroy(); live.delete(c); } }); }
export function destroyAll() {
  live.forEach((c) => c.destroy());
  live.clear();
}

function unavailable(canvas) {
  const box = canvas.parentElement;
  if (box) box.innerHTML = '<div class="empty"><div class="e-t">Chart unavailable</div><div>The chart library could not be loaded (offline?). The figures are shown in the tables on this page.</div></div>';
}

// ---- theme ----------------------------------------------------------------------------------------
function theme() {
  return { text: css('--chart-text'), strong: css('--text'), text2: css('--text-2'), text3: css('--text-3'), grid: css('--chart-grid'),
    surface: css('--panel'), tipBg: css('--panel-3'), tipBorder: css('--border-strong') };
}
function base(Chart) {
  Chart.defaults.font.family = FONT;
  Chart.defaults.font.size = 11;
  Chart.defaults.color = css('--chart-text');
}
// (Re)apply every theme-dependent colour of a chart from the current CSS tokens.
function paintTheme(chart) {
  const t = theme();
  const o = chart.options;
  Object.values(o.scales || {}).forEach((s) => {
    if (s.ticks) s.ticks.color = t.text;
    if (s.grid) s.grid.color = t.grid;
    if (s.border) s.border.color = t.grid;
  });
  const tip = o.plugins.tooltip;
  Object.assign(tip, { backgroundColor: t.tipBg, titleColor: t.text2, bodyColor: t.strong, footerColor: t.strong, borderColor: t.tipBorder });
  chart.$theme = t;
  chart.data.datasets.forEach((d) => {
    const c = resolve(d.$token);
    d.$color = c;
    if (chart.config.type === 'bar') {
      d.backgroundColor = c;
      d.hoverBackgroundColor = lift(c, t.strong);
      d.borderColor = t.surface;
    } else {
      d.borderColor = c;
      d.backgroundColor = d.$area ? withAlpha(c, 0.1) : c;
      d.pointHoverBackgroundColor = c;
      d.pointHoverBorderColor = t.surface;
    }
  });
}
let themeHooked = false;
function hookTheme() {
  if (themeHooked) return;
  themeHooked = true;
  window.addEventListener('themechange', () => {
    prune();
    // computed style needs the new data-theme applied first; it already is when the event fires
    live.forEach((c) => { paintTheme(c); c.update('none'); });
  });
}

// ---- tiny inline plugins -------------------------------------------------------------------------
// "Now" marker: a faint band behind the current bucket plus a small label above the plot.
const nowPlugin = {
  id: 'cvNow',
  beforeDatasetsDraw(chart, _a, opts) {
    const i = opts?.index;
    if (i == null || i < 0 || chart.options.indexAxis === 'y') return;
    const x = chart.scales.x; const area = chart.chartArea;
    if (!x || i >= (chart.data.labels || []).length) return;
    const step = x.width / Math.max(1, chart.data.labels.length);
    const cx = x.getPixelForValue(i);
    const t = chart.$theme || theme();
    const { ctx } = chart;
    ctx.save();
    ctx.fillStyle = withAlpha(/^#/.test(t.text3) ? t.text3 : '#7a879c', 0.12);
    ctx.fillRect(cx - step / 2, area.top, step, area.bottom - area.top);
    ctx.fillStyle = t.text2;
    ctx.font = `600 10px ${FONT}`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText(opts.label || 'Now', Math.min(Math.max(cx, area.left + 12), area.right - 12), area.top - 3);
    ctx.restore();
  },
};
// Value labels at the end of each bar (every bar is a whole bar, so the tip is free space).
const valuePlugin = {
  id: 'cvValues',
  afterDatasetsDraw(chart, _a, opts) {
    if (!opts?.display) return;
    const t = chart.$theme || theme();
    const horizontal = chart.options.indexAxis === 'y';
    const { ctx } = chart;
    ctx.save();
    ctx.font = `600 11px ${FONT}`;
    ctx.fillStyle = t.text2;
    chart.data.datasets.forEach((d, di) => {
      if (!chart.isDatasetVisible(di)) return;
      chart.getDatasetMeta(di).data.forEach((el, i) => {
        const v = d.data[i];
        if (v == null) return;
        const txt = fmtN(v) + (opts.suffix || '');
        if (horizontal) { ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(txt, el.x + 6, el.y); }
        else { ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(txt, el.x, el.y - 4); }
      });
    });
    ctx.restore();
  },
};
// Crosshair for line charts: a hairline that snaps to the hovered x.
const crosshairPlugin = {
  id: 'cvCrosshair',
  afterDatasetsDraw(chart) {
    if (chart.config.type !== 'line') return;
    const act = chart.tooltip?.getActiveElements?.() || [];
    if (!act.length) return;
    const x = act[0].element.x; const a = chart.chartArea;
    const t = chart.$theme || theme();
    const { ctx } = chart;
    ctx.save();
    ctx.strokeStyle = t.text3; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(x) + 0.5, a.top); ctx.lineTo(Math.round(x) + 0.5, a.bottom); ctx.stroke();
    ctx.restore();
  },
};

// Which dataset is the visible, non-zero top of the stack at an index (only that one gets rounded ends).
function topOfStack(chart, index) {
  for (let i = chart.data.datasets.length - 1; i >= 0; i--) {
    if (chart.isDatasetVisible(i) && Number(chart.data.datasets[i].data[index]) > 0) return i;
  }
  return -1;
}

// Shorten a category label to fit maxPx (measured, not guessed); the tooltip title keeps the full name.
function fitText(ctx, s, maxPx) {
  s = String(s ?? '');
  ctx.save(); ctx.font = `11px ${FONT}`;
  let out = s;
  if (ctx.measureText(s).width > maxPx) {
    let lo = 1; let hi = s.length;
    while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (ctx.measureText(s.slice(0, mid).trimEnd() + '…').width <= maxPx) lo = mid; else hi = mid - 1; }
    out = s.slice(0, lo).trimEnd() + '…';
  }
  ctx.restore();
  return out;
}
const truncate = (s, n) => { s = String(s ?? ''); return n && s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s; };

function tooltipBase(t) {
  return {
    backgroundColor: t.tipBg, titleColor: t.text2, bodyColor: t.strong, footerColor: t.strong, borderColor: t.tipBorder, borderWidth: 1,
    padding: { x: 10, y: 8 }, cornerRadius: 6, caretSize: 5, displayColors: true, boxWidth: 10, boxHeight: 4, boxPadding: 6, multiKeyBackground: 'transparent',
    titleFont: { weight: '500', size: 11 }, bodyFont: { weight: '600', size: 12 }, footerFont: { weight: '700', size: 12 }, footerMarginTop: 6,
    // line keys, not boxes
    callbacks: { labelColor: (c) => { const k = c.dataset.$color || c.dataset.borderColor; return { borderColor: k, backgroundColor: k, borderWidth: 1, borderRadius: 0 }; } },
  };
}

/**
 * bar(canvas, {labels, series:[{label, data, color}], stacked, horizontal, onClick, max, suffix,
 *              hidden:[datasetIndex], nowIndex, nowLabel, tickEvery, valueLabels, truncate, titles:[tooltip title per label]})
 */
export async function bar(canvas, opts) {
  if (!canvas) return null;
  const Chart = await loadCharts();
  if (!canvas.isConnected) return null;
  if (!Chart) { unavailable(canvas); return null; }
  base(Chart); hookTheme(); prune();
  const t = theme();
  const stacked = !!opts.stacked;
  const horizontal = !!opts.horizontal;
  const hidden = new Set(opts.hidden || []);
  const valueLabels = opts.valueLabels ?? horizontal;     // horizontal bars: value at the tip by default
  const vAxis = horizontal ? 'x' : 'y';
  const makeDataset = (s, i) => ({
    label: s.label, data: s.data, $token: s.color, $color: resolve(s.color), hidden: hidden.has(i),
    backgroundColor: resolve(s.color), hoverBackgroundColor: lift(resolve(s.color), t.strong), borderColor: t.surface,
    // stacked: only the top visible segment carries the 4px data-end; the rest get a 2px surface gap on top
    borderRadius: stacked ? (c) => (topOfStack(c.chart, c.dataIndex) === c.datasetIndex ? 4 : 0) : 4,
    borderWidth: stacked ? (c) => (topOfStack(c.chart, c.dataIndex) === c.datasetIndex ? 0 : (horizontal ? { right: 2 } : { top: 2 })) : 0,
    borderSkipped: 'start', inflateAmount: 0,
    maxBarThickness: horizontal ? 16 : 24, categoryPercentage: 1, barPercentage: horizontal ? 0.62 : 0.66,
  });
  const datasets = opts.series.map(makeDataset);
  const pluginOpts = {
    cvNow: { index: opts.nowIndex ?? null, label: opts.nowLabel },
    cvValues: { display: valueLabels, suffix: opts.suffix || '' },
  };

  // Live update: a chart already on this canvas gets the new numbers in place (bars glide to their new
  // height) instead of being destroyed and rebuilt, so the page never flickers on a refresh.
  const existing = Chart.getChart(canvas);
  if (existing && existing.config.type === 'bar' && existing.data.datasets.length === datasets.length
      && existing.options.indexAxis === (horizontal ? 'y' : 'x')) {
    existing.data.labels = opts.labels;
    existing.data.datasets.forEach((d, i) => {
      Object.assign(d, { data: datasets[i].data, label: datasets[i].label, $token: datasets[i].$token, $color: datasets[i].$color });
      existing.setDatasetVisibility(i, !hidden.has(i));
    });
    existing.$onClick = opts.onClick;
    existing.$opts = opts;
    Object.assign(existing.options.plugins.cvNow, pluginOpts.cvNow);
    Object.assign(existing.options.plugins.cvValues, pluginOpts.cvValues);
    existing.options.animation = anim();
    paintTheme(existing);
    existing.update();
    return existing;
  }
  if (existing) { existing.destroy(); live.delete(existing); }

  const tip = tooltipBase(t);
  tip.callbacks = {
    ...tip.callbacks,
    title: (items) => {
      if (!items.length) return '';
      const c = items[0].chart; const i = items[0].dataIndex;
      return String(c.$opts?.titles?.[i] ?? c.data.labels[i] ?? '');
    },
    label: (c) => ` ${fmtN(c.parsed[vAxis])}${opts.suffix || ''}  ${c.dataset.label}`,
    footer: stacked ? (items) => (items.length > 1 ? `${fmtN(items.reduce((s, i) => s + (Number(i.parsed[vAxis]) || 0), 0))}${opts.suffix || ''}  Total` : '') : undefined,
  };
  if (datasets.length === 1) tip.displayColors = false;   // one series: the title names it, no key needed
  if (stacked && !horizontal) tip.itemSort = (a, b) => b.datasetIndex - a.datasetIndex;   // same order as the stack reads

  const catTicks = {
    color: t.text, maxRotation: 0, autoSkip: !opts.tickEvery, maxTicksLimit: horizontal ? undefined : 12, padding: 6,
    callback(value, index) {
      const label = this.getLabelForValue(value);
      if (horizontal) {
        if (opts.truncate) return truncate(label, opts.truncate);
        return fitText(this.chart.ctx, label, this.chart.width * 0.4);
      }
      if (opts.tickEvery) {
        const every = this.chart.width < 480 ? opts.tickEvery * 2 : opts.tickEvery;
        return index % every === 0 ? label : '';
      }
      return label;
    },
  };
  const valTicks = { color: t.text, precision: 0, maxTicksLimit: 5, padding: 6, callback: (v) => fmtN(v) + (opts.suffix || '') };
  const cat = { stacked, grid: { display: false }, border: { display: !horizontal, color: t.grid }, ticks: catTicks };
  const val = {
    stacked, beginAtZero: true, max: opts.max, grace: horizontal ? 0 : '8%',
    // horizontal bars carry a value label at every tip, so the value axis is dropped entirely
    display: !(horizontal && valueLabels), grid: { color: t.grid, lineWidth: 1, drawTicks: false }, border: { display: false }, ticks: valTicks,
  };

  const chart = new Chart(canvas, {
    type: 'bar',
    data: { labels: opts.labels, datasets },
    plugins: [nowPlugin, valuePlugin],
    options: {
      responsive: true, maintainAspectRatio: false, animation: anim(),
      indexAxis: horizontal ? 'y' : 'x',
      interaction: { mode: 'index', intersect: false, axis: horizontal ? 'y' : 'x' },
      layout: { padding: { top: opts.nowIndex != null && !horizontal ? 16 : (valueLabels && !horizontal ? 16 : 4), right: horizontal && valueLabels ? 40 : 4 } },
      plugins: {
        legend: { display: false },
        tooltip: tip,
        ...pluginOpts,
      },
      scales: horizontal ? { x: val, y: cat } : { x: cat, y: val },
      onClick: (evt, els) => { const fn = chart.$onClick; if (fn && els.length) fn(els[0].index, els[0].datasetIndex); },
      onHover: (evt, els) => { if (evt.native?.target) evt.native.target.style.cursor = chart.$onClick && els.length ? 'pointer' : 'default'; },
    },
  });
  chart.$onClick = opts.onClick;
  chart.$opts = opts;
  chart.$theme = t;
  live.add(chart);
  return chart;
}

/**
 * line(canvas, {labels, series:[{label, data, color}], area, suffix})
 */
export async function line(canvas, opts) {
  if (!canvas) return null;
  const Chart = await loadCharts();
  if (!canvas.isConnected) return null;
  if (!Chart) { unavailable(canvas); return null; }
  base(Chart); hookTheme(); prune();
  const t = theme();
  const area = !!opts.area && opts.series.length === 1;
  const existing = Chart.getChart(canvas);
  if (existing && existing.config.type === 'line' && existing.data.datasets.length === opts.series.length) {
    existing.data.labels = opts.labels;
    existing.data.datasets.forEach((d, i) => { Object.assign(d, { data: opts.series[i].data, label: opts.series[i].label, $token: opts.series[i].color, $color: resolve(opts.series[i].color) }); });
    existing.options.animation = anim();
    paintTheme(existing);
    existing.update();
    return existing;
  }
  if (existing) { existing.destroy(); live.delete(existing); }
  const tip = tooltipBase(t);
  tip.callbacks = { ...tip.callbacks, label: (c) => ` ${fmtN(c.parsed.y)}${opts.suffix || ''}  ${c.dataset.label}` };
  const chart = new Chart(canvas, {
    type: 'line',
    data: { labels: opts.labels, datasets: opts.series.map((s) => ({ label: s.label, data: s.data, $token: s.color, $color: resolve(s.color), $area: area,
      borderColor: resolve(s.color), backgroundColor: area ? withAlpha(resolve(s.color), 0.1) : resolve(s.color), fill: area ? 'origin' : false,
      borderWidth: 2, borderJoinStyle: 'round', borderCapStyle: 'round', tension: 0.25,
      pointRadius: 0, pointHoverRadius: 5, pointHitRadius: 12, pointHoverBorderWidth: 2, pointHoverBackgroundColor: resolve(s.color), pointHoverBorderColor: t.surface })) },
    plugins: [crosshairPlugin],
    options: {
      responsive: true, maintainAspectRatio: false, animation: anim(),
      interaction: { mode: 'index', intersect: false },
      plugins: { legend: { display: false }, tooltip: tip },
      scales: {
        x: { grid: { display: false }, border: { display: true, color: t.grid }, ticks: { color: t.text, maxRotation: 0, autoSkip: true, maxTicksLimit: 10, padding: 6 } },
        y: { grid: { color: t.grid, drawTicks: false }, border: { display: false }, beginAtZero: true, grace: '8%', ticks: { color: t.text, precision: 0, maxTicksLimit: 5, padding: 6, callback: (v) => fmtN(v) } },
      },
    },
  });
  chart.$theme = t;
  live.add(chart);
  return chart;
}

/** Show or hide one series of the chart on a canvas (legend toggles). Returns the new visibility. */
export function setSeriesVisible(canvas, index, visible) {
  const chart = window.Chart?.getChart?.(canvas);
  if (!chart || !chart.data.datasets[index]) return null;
  chart.setDatasetVisibility(index, visible);
  chart.options.animation = anim();
  chart.update();
  return visible;
}
