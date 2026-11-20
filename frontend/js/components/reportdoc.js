// components/reportdoc.js — one report, laid out as a document (screen and print).
// Used by the internal report view and the client portal, so both read the same way:
// header (title, who it is for, period, when) → key figures → sections → a short footer.

import { esc, fmt } from '../core/ui.js';

const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const cell = (v) => (v == null || v === '' ? '<span class="muted">—</span>' : ISO.test(String(v)) ? `<span class="num">${fmt.dt(v)}</span>` : esc(v));

const PRI_COLOR = { critical: 'var(--st-critical)', high: 'var(--st-serious)', medium: 'var(--st-warning)', low: 'var(--st-neutral)' };

function figures(section) {
  // the Summary section's numbers become the report's key figures; text rows stay in the details list
  const nums = section.rows.filter(([, v]) => typeof v === 'number');
  const text = section.rows.filter(([k, v]) => typeof v !== 'number' && k !== 'Client');
  return `${nums.length ? `<div class="rdoc-figs">${nums.map(([k, v]) => `<div class="rdoc-fig"><b class="num">${fmt.n(v)}</b><span>${esc(k)}</span></div>`).join('')}</div>` : ''}
    ${text.length ? `<dl class="kv rdoc-kv">${text.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v == null ? '—' : esc(v)}</dd>`).join('')}</dl>` : ''}`;
}

function barsFor(section) {
  const max = Math.max(1, ...section.rows.map((r) => Number(r[1]) || 0));
  return `<div class="rdoc-bars">${section.rows.map(([label, n]) => {
    const key = String(label || '').toLowerCase();
    return `<div class="rdoc-bar"><span class="rdoc-bar-l">${esc(label)}</span>
      <span class="rdoc-bar-t"><span style="width:${((Number(n) || 0) / max) * 100}%;background:${PRI_COLOR[key] || 'var(--accent)'}"></span></span>
      <b class="num">${fmt.n(n)}</b></div>`;
  }).join('')}</div>`;
}

function table(section) {
  if (!section.rows.length) return '<p class="muted rdoc-empty">Nothing in this period.</p>';
  return `<div class="table-wrap rdoc-table"><table class="t"><thead><tr>${section.columns.map((c) => `<th>${esc(c)}</th>`).join('')}</tr></thead>
    <tbody>${section.rows.map((row) => `<tr>${row.map((v) => `<td>${cell(v)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function sectionHtml(s) {
  let body;
  if (s.kind === 'kv' && /summary/i.test(s.title)) body = figures(s);
  else if (s.kind === 'kv') body = `<dl class="kv rdoc-kv">${s.rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${v == null ? '—' : esc(v)}</dd>`).join('')}</dl>`;
  else if (s.columns?.length === 2 && /count/i.test(s.columns[1]) && s.rows.length <= 12) body = barsFor(s);
  else body = table(s);
  return `<section class="rdoc-sec"><h3>${esc(s.title)}${s.kind === 'table' && s.rows.length > 12 ? ` <span class="muted">${fmt.n(s.rows.length)}</span>` : ''}</h3>
    ${s.note ? `<p class="muted rdoc-note">${esc(s.note)}</p>` : ''}${body}</section>`;
}

export function reportDocument(r, { showAudience = true } = {}) {
  const m = r.meta || {};
  const client = m.audience === 'client';
  const auto = m.automatic || String(m.generatedBy || '').startsWith('Automatic');
  const filters = Object.entries(m.filters || {}).filter(([k]) => !['from', 'to', 'projects', 'exam'].includes(k));
  return `<article class="rdoc">
    <header class="rdoc-head">
      <div class="grow">
        <div class="rdoc-kicker">${client ? 'Exam alert report' : 'Internal report'}${auto ? ' · sent automatically' : ''}</div>
        <h1>${esc(m.title || 'Report')}</h1>
        <div class="rdoc-meta">
          ${m.client || m.scope ? `<span><span class="muted">For</span> ${esc(m.client || m.scope)}</span>` : ''}
          <span><span class="muted">Period</span> ${esc(m.dataRange || 'All data')}</span>
          <span><span class="muted">Prepared</span> ${fmt.dt(m.generatedAt)}${m.generatedBy ? ` · ${esc(m.generatedBy)}` : ''}</span>
          ${filters.length ? `<span><span class="muted">Filters</span> ${filters.map(([k, v]) => `${esc(k)}: ${esc(v)}`).join(', ')}</span>` : ''}
        </div>
      </div>
      ${showAudience ? `<span class="b ${client ? 'vis-shared' : 'vis-internal'}">${client ? 'Client report' : 'Internal only'}</span>` : ''}
    </header>
    ${(r.sections || []).map(sectionHtml).join('')}
    <footer class="rdoc-foot muted">${esc(m.visibility || '')} · ${fmt.n(m.recordCount)} record${m.recordCount === 1 ? '' : 's'}</footer>
  </article>`;
}
