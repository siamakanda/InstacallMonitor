"""The dashboard's single self-contained HTML page (stdlib-only, no build step).

Kept separate from webapp.py so the HTTP/API layer stays focused.

Visual direction: a telecom instrument console. Warm graphite surfaces, one amber
signal accent, hairline rules, tabular mono readings. Product register: dense,
quiet, legible; settings primary, live monitoring secondary.
"""

from __future__ import annotations

import json

from settings_models import FIELD_SPEC

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="%CSRF%">
<meta name="color-scheme" content="dark">
<title>InstacallMonitor</title>
<style>
  :root {
    --canvas: oklch(0.185 0.008 70);
    --surface: oklch(0.225 0.010 70);
    --surface-2: oklch(0.262 0.012 70);
    --surface-3: oklch(0.300 0.013 70);
    --line: oklch(0.330 0.012 70);
    --line-strong: oklch(0.440 0.014 70);
    --text: oklch(0.940 0.008 75);
    --muted: oklch(0.720 0.012 75);
    --faint: oklch(0.660 0.012 75);
    --accent: oklch(0.790 0.140 72);
    --accent-dim: oklch(0.620 0.110 72);
    --accent-ink: oklch(0.190 0.020 70);
    --ok: oklch(0.780 0.130 150);
    --warn: oklch(0.830 0.130 90);
    --err: oklch(0.700 0.180 28);
    --radius-lg: 12px;
    --radius: 9px;
    --radius-sm: 7px;
    --s1: 4px; --s2: 8px; --s3: 12px; --s4: 16px; --s5: 24px; --s6: 32px; --s7: 48px;
    --font-ui: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --font-mono: ui-monospace, "Cascadia Code", "SFMono-Regular", Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  html { -webkit-text-size-adjust: 100%; }
  body {
    margin: 0; background: var(--canvas); color: var(--text);
    font: 14px/1.55 var(--font-ui);
    -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
  }
  h1, h2, h3, p { margin: 0; }
  .mono { font-family: var(--font-mono); font-variant-numeric: tabular-nums; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0;
  }

  /* ---- command bar ---- */
  .topbar {
    position: sticky; top: 0; z-index: 20;
    display: flex; flex-wrap: wrap; align-items: center; gap: var(--s3) var(--s5);
    padding: var(--s3) var(--s5);
    background: var(--canvas);
    border-bottom: 1px solid var(--line);
  }
  .brand { display: flex; align-items: center; gap: var(--s3); margin-inline-end: auto; }
  .brand-mark {
    width: 26px; height: 26px; border-radius: var(--radius-sm);
    border: 1px solid var(--line-strong); position: relative; flex: none;
  }
  .brand-mark::after {
    content: ""; position: absolute; inset: 8px; border-radius: 50%;
    background: var(--accent); box-shadow: 0 0 10px oklch(0.79 0.14 72 / 0.55);
  }
  .brand-name { font-size: 14.5px; font-weight: 600; letter-spacing: 0.01em; }
  .brand-sub {
    font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.16em;
    color: var(--faint); margin-top: 1px;
  }
  .readout {
    display: flex; flex-wrap: wrap; align-items: baseline;
    gap: var(--s2) var(--s4);
  }
  .ro { display: inline-flex; align-items: baseline; gap: 6px; }
  .ro-k {
    font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.13em;
    color: var(--faint);
  }
  .ro-v { font-family: var(--font-mono); font-size: 12.5px; color: var(--text); }
  .ro-v.alert { color: var(--err); }

  .tabs {
    display: inline-flex; gap: 2px; padding: 3px;
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius-sm);
  }
  .tab {
    font: inherit; font-size: 12.5px; color: var(--muted);
    background: transparent; border: 0; border-radius: 5px;
    padding: 7px 15px; cursor: pointer; min-height: 32px;
  }
  .tab:hover { color: var(--text); }
  .tab[aria-selected="true"] { background: var(--surface-3); color: var(--text); }
  .tab:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }

  /* ---- layout ---- */
  main { max-width: 1180px; margin-inline: auto; padding: var(--s5) var(--s5) var(--s7); }
  .pane[hidden] { display: none; }
  .section { padding-block: var(--s5); border-top: 1px solid var(--line); }
  .section:first-child { border-top: 0; padding-top: var(--s3); }
  .section-head { margin-bottom: var(--s4); }
  .section-head h2 {
    font-size: 13px; font-weight: 600; letter-spacing: 0.02em;
  }
  .section-sub { color: var(--muted); font-size: 12.5px; margin-top: 3px; }
  .subhead {
    font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.13em;
    color: var(--faint); margin: var(--s4) 0 var(--s2);
  }

  .readout-strip {
    display: flex; flex-wrap: wrap; gap: var(--s2) var(--s5);
    padding: var(--s3) var(--s4);
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius);
  }
  .readout-strip .ro-v { font-size: 12px; }

  /* ---- fields ---- */
  .field-group + .field-group { margin-top: var(--s5); }
  .group-title {
    font-size: 10.5px; text-transform: uppercase; letter-spacing: 0.15em;
    color: var(--faint); padding-bottom: var(--s2);
    border-bottom: 1px solid var(--line); margin-bottom: var(--s3);
  }
  .fields {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(230px, 1fr));
    gap: var(--s3) var(--s4);
  }

  .field { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
  .field-label { font-size: 12px; color: var(--muted); }
  .field-hint { font-size: 11px; color: var(--faint); }
  .field-err, .cell-err { display: none; color: var(--err); font-size: 11.5px; line-height: 1.35; }
  .field.invalid .field-err { display: block; }
  td.invalid .cell-err { display: block; }
  .field.invalid .field-label { color: var(--err); }

  input, select {
    font: inherit; font-size: 13.5px; color: var(--text);
    background: var(--surface); border: 1px solid var(--line);
    border-radius: var(--radius-sm); padding: 8px 10px; min-height: 38px;
    width: 100%; min-width: 0;
  }
  select {
    appearance: none;
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' viewBox='0 0 10 6'><path d='M1 1l4 4 4-4' fill='none' stroke='%238a93a6' stroke-width='1.5' stroke-linecap='round'/></svg>");
    background-repeat: no-repeat;
    background-position: right 10px center;
    padding-inline-end: 30px;
  }
  input:hover, select:hover { border-color: var(--line-strong); }
  input:focus-visible, select:focus-visible, .btn:focus-visible {
    outline: 2px solid var(--accent); outline-offset: 2px;
  }
  input:focus-visible, select:focus-visible { border-color: var(--accent-dim); }
  input.invalid, select.invalid { border-color: var(--err); }
  input::placeholder { color: var(--muted); opacity: 1; }
  input[type="checkbox"] {
    width: 18px; height: 18px; min-height: 0; accent-color: var(--accent);
    justify-self: start; padding: 0; margin-block-start: 10px;
  }
  @media (max-width: 640px) {
    input, select { font-size: 16px; } /* avoid iOS focus zoom */
  }

  /* ---- buttons ---- */
  .btn {
    font: inherit; font-size: 12.5px; color: var(--text);
    background: var(--surface-2); border: 1px solid var(--line-strong);
    border-radius: var(--radius-sm); padding: 8px 14px; min-height: 36px;
    cursor: pointer; white-space: nowrap;
  }
  .btn:hover { background: var(--surface-3); border-color: var(--line-strong); }
  .btn:active { transform: scale(0.98); }
  .btn[disabled] { opacity: 0.45; cursor: not-allowed; }
  .btn.primary {
    background: var(--accent); border-color: var(--accent); color: var(--accent-ink);
    font-weight: 600;
  }
  .btn.primary:hover:not([disabled]) { background: oklch(0.830 0.135 72); }
  .btn.ghost { background: transparent; border-color: var(--line); color: var(--muted); }
  .btn.ghost:hover { background: var(--surface); color: var(--text); }
  .btn.icon {
    background: transparent; border-color: transparent; color: var(--faint);
    padding: 6px 9px; min-height: 32px;
  }
  .btn.icon:hover { color: var(--err); border-color: color-mix(in oklch, var(--err) 45%, transparent); }

  /* ---- commit bar ---- */
  .commitbar {
    position: sticky; bottom: 0; z-index: 10;
    display: flex; align-items: center; justify-content: flex-end; gap: var(--s4);
    padding: var(--s3) 0;
    background: var(--canvas);
    border-top: 1px solid var(--line-strong);
    box-shadow: 0 -10px 22px oklch(0 0 0 / 0.35);
  }
  .commit-status { margin-inline-end: auto; font-size: 12px; color: var(--warn); }

  /* ---- tables ---- */
  .table-wrap {
    border: 1px solid var(--line); border-radius: var(--radius);
    background: var(--surface); overflow-x: auto; -webkit-overflow-scrolling: touch;
  }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  thead th {
    position: sticky; top: 0; z-index: 1;
    background: var(--surface-2); color: var(--faint);
    font-size: 10px; text-transform: uppercase; letter-spacing: 0.1em; font-weight: 600;
    text-align: start; padding: 9px 12px;
    border-bottom: 1px solid var(--line); white-space: nowrap;
  }
  tbody td {
    padding: 8px 12px; border-bottom: 1px solid color-mix(in oklch, var(--line) 55%, transparent);
    vertical-align: top;
  }
  tbody tr:last-child td { border-bottom: 0; }
  tbody tr:hover td { background: color-mix(in oklch, var(--surface-2) 55%, transparent); }
  th.num, td.num { text-align: end; }
  td.num { font-family: var(--font-mono); font-variant-numeric: tabular-nums; font-size: 12.5px; }
  td.wide { white-space: normal; min-width: 160px; }
  #watch-rows table { table-layout: fixed; min-width: 880px; }
  #watch-rows th:nth-child(1) { width: 96px; }
  #watch-rows th:nth-child(2) { width: 236px; }
  #watch-rows th:nth-child(3) { width: 116px; }
  #watch-rows th:nth-child(4) { width: 116px; }
  #watch-rows th:nth-child(5) { width: 116px; }
  #watch-rows th:nth-child(6) { width: 108px; }
  #watch-rows th:nth-child(7) { width: 108px; }
  #watch-rows th:nth-child(8) { width: 132px; }
  #watch-rows th:nth-child(9) { width: 44px; }
  #watch-rows td { vertical-align: top; }
  #watch-rows td.num input { text-align: end; }
  .neg { color: var(--err); }

  /* ---- chips ---- */
  .chip {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 2px 9px; border-radius: 999px;
    font-size: 11px; line-height: 1.6; white-space: nowrap;
    color: var(--muted); background: var(--surface-2); border: 1px solid var(--line-strong);
  }
  .chip::before {
    content: ""; width: 6px; height: 6px; border-radius: 50%;
    background: currentColor; flex: none;
  }
  .chip.nodot::before { display: none; }
  .chip.ok { color: var(--ok); border-color: color-mix(in oklch, var(--ok) 40%, transparent);
             background: color-mix(in oklch, var(--ok) 10%, transparent); }
  .chip.alert { color: var(--err); border-color: color-mix(in oklch, var(--err) 45%, transparent);
                background: color-mix(in oklch, var(--err) 12%, transparent); }
  .chip.over { color: var(--accent); border-color: color-mix(in oklch, var(--accent) 40%, transparent);
               background: color-mix(in oklch, var(--accent) 10%, transparent); }
  .chip.warn { color: var(--warn); border-color: color-mix(in oklch, var(--warn) 40%, transparent);
               background: color-mix(in oklch, var(--warn) 10%, transparent); }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }

  /* ---- states ---- */
  .state {
    padding: var(--s5) var(--s4); color: var(--faint); font-size: 13px;
    text-align: center;
  }
  .state b { color: var(--muted); font-weight: 500; }
  .notice {
    padding: 10px 14px; border-radius: var(--radius-sm);
    border: 1px solid var(--line-strong); background: var(--surface);
    font-size: 12.5px; color: var(--muted); margin-top: var(--s3);
  }
  .notice.ok { border-color: color-mix(in oklch, var(--ok) 45%, transparent); color: var(--ok); }
  .notice.err { border-color: color-mix(in oklch, var(--err) 45%, transparent); color: var(--err); }
  .notice.warn { border-color: color-mix(in oklch, var(--warn) 45%, transparent); color: var(--warn); }
  .notice ul { margin: 6px 0 0; padding-inline-start: 18px; }
  .notice li + li { margin-top: 3px; }

  .toolbar {
    display: flex; flex-wrap: wrap; align-items: flex-end; gap: var(--s3);
    margin-bottom: var(--s3);
  }
  .toolbar .field { max-width: 260px; min-width: 180px; }
  .row-actions { margin-top: var(--s3); }

  @media (max-width: 700px) {
    .readout {
      display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: var(--s2) var(--s4);
    }
    .ro { flex-direction: column; align-items: flex-start; gap: 0; }
  }

  @media (prefers-reduced-motion: no-preference) {
    .btn { transition: background 150ms ease-out, border-color 150ms ease-out, transform 100ms ease-out; }
    input, select, .tab { transition: border-color 150ms ease-out, background 150ms ease-out, color 150ms ease-out; }
    .pane:not([hidden]) { animation: pane-in 180ms ease-out; }
    @keyframes pane-in {
      from { opacity: 0; transform: translateY(3px); }
      to { opacity: 1; transform: none; }
    }
  }
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="brand-mark" aria-hidden="true"></span>
    <div>
      <div class="brand-name">InstacallMonitor</div>
      <div class="brand-sub">balance &amp; margin console</div>
    </div>
  </div>

  <div class="readout" role="group" aria-label="Monitor status">
    <div class="ro"><span class="ro-k">state</span><span class="ro-v" id="s-alive">--</span></div>
    <div class="ro"><span class="ro-k">uptime</span><span class="ro-v" id="s-uptime">--</span></div>
    <div class="ro"><span class="ro-k">cycle</span><span class="ro-v" id="s-cycle">--</span></div>
    <div class="ro"><span class="ro-k">last check</span><span class="ro-v" id="s-last">--</span></div>
    <div class="ro"><span class="ro-k">errors</span><span class="ro-v" id="s-errors">--</span></div>
    <div class="ro"><span class="ro-k">settings</span><span class="ro-v" id="s-version">--</span></div>
  </div>

  <div class="tabs" role="tablist" aria-label="Views">
    <button class="tab" id="tabbtn-settings" role="tab" aria-selected="true"
            aria-controls="tab-settings" onclick="showTab('settings')">Settings</button>
    <button class="tab" id="tabbtn-live" role="tab" aria-selected="false" tabindex="-1"
            aria-controls="tab-live" onclick="showTab('live')">Live</button>
  </div>
</header>

<main>
  <section class="pane" id="tab-settings" role="tabpanel" aria-labelledby="tabbtn-settings">
    <div class="readout-strip" id="config-summary"></div>

    <div class="section">
      <div class="section-head">
        <h2>Settings</h2>
        <p class="section-sub">Saved to settings.toml. Applied automatically within about a second.</p>
      </div>
      <div id="global-fields"><div class="state">Loading settings&hellip;</div></div>
    </div>

    <div class="section">
      <div class="section-head">
        <h2>Balance-check customers</h2>
        <p class="section-sub">Blank overrides fall back to the global defaults shown in each field.</p>
      </div>
      <div id="watch-rows"><div class="state">Loading customers&hellip;</div></div>
      <datalist id="customer-options"></datalist>
      <div class="row-actions">
        <button class="btn ghost" onclick="addWatch()">Add customer</button>
      </div>
    </div>

    <div class="commitbar">
      <div class="commit-status" id="dirty-hint"></div>
      <button class="btn primary" id="save-btn" onclick="saveSettings()" disabled>Save settings</button>
    </div>
    <div id="settings-status" role="status" aria-live="polite"></div>
  </section>

  <section class="pane" id="tab-live" role="tabpanel" aria-labelledby="tabbtn-live" hidden>
    <div class="section">
      <div class="section-head">
        <h2>Current status</h2>
        <p class="section-sub">Latest readings from the running monitor.</p>
      </div>
      <div id="live-status"><div class="state">Waiting for the first cycle&hellip;</div></div>
    </div>

    <div class="section">
      <div class="section-head">
        <h2>History</h2>
        <p class="section-sub">Recorded samples from the local database.</p>
      </div>
      <div class="toolbar">
        <label class="field">
          <span class="field-label">Customer</span>
          <select id="hist-customer" onchange="loadHistory()"></select>
        </label>
        <label class="field">
          <span class="field-label">Window</span>
          <select id="hist-hours" onchange="loadHistory()">
            <option value="1">Last hour</option>
            <option value="6">Last 6 hours</option>
            <option value="24" selected>Last 24 hours</option>
            <option value="168">Last 7 days</option>
          </select>
        </label>
        <button class="btn" onclick="loadHistory()">Refresh</button>
      </div>
      <h3 class="subhead">Balances</h3>
      <div id="hist-balance"><div class="state">No samples yet.</div></div>
      <h3 class="subhead">Margins</h3>
      <div id="hist-margin"><div class="state">No samples yet.</div></div>
    </div>

    <div class="section">
      <div class="section-head">
        <h2>Alerts</h2>
        <p class="section-sub">Persisted alert state per customer and alert type.</p>
      </div>
      <div id="live-alerts"><div class="state">No alert state recorded.</div></div>
    </div>
  </section>
</main>

<script>
const REFRESH = %REFRESH%;
const CSRF = "%CSRF%";
const $ = id => document.getElementById(id);
const esc = v => String(v === null || v === undefined ? '' : v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const num = v => (v === null || v === undefined || v === '') ? '--' : Number(v);

const FIELDS = %FIELDS%;
const WATCH_KEYS = [
  ['customer','Customer'],
  ['name','Name'],
  ['balance_below','Balance below'],
  ['margin_below','Margin below %'],
  ['margin_above','Margin above %'],
  ['margin_deadband','Deadband %'],
  ['billed_above','Billed above'],
];
const WATCH_NUM = ['balance_below','margin_below','margin_above','margin_deadband','billed_above'];
const OVERRIDE_KEYS = ['margin_below','margin_above','margin_deadband','billed_above'];
const OVERRIDE_LABEL = { margin_below: 'low', margin_above: 'high', margin_deadband: 'deadband', billed_above: 'billed' };

let currentWatch = [];
let knownCustomers = [];
let globalDefaults = {};
let dirty = false;
let activeTab = 'settings';

function markDirty() {
  dirty = true;
  $('save-btn').disabled = false;
  $('dirty-hint').textContent = 'Unsaved changes';
}

function clearDirty() {
  dirty = false;
  $('save-btn').disabled = true;
  $('dirty-hint').textContent = '';
}

function showTab(name) {
  activeTab = name;
  for (const t of ['settings', 'live']) {
    const pane = $('tab-' + t);
    const btn = $('tabbtn-' + t);
    const on = t === name;
    pane.hidden = !on;
    btn.setAttribute('aria-selected', on ? 'true' : 'false');
    btn.tabIndex = on ? 0 : -1;
  }
  try { history.replaceState(null, '', '#' + name); } catch (e) {}
  if (name === 'live') loadLive();
}

function inputHtml(key, type, value, options, id) {
  const k = esc(key);
  const described = ' aria-describedby="e-' + k + '"';
  if (type === 'bool') {
    return '<input type="checkbox" id="' + esc(id) + '" data-k="' + k + '"' + described
      + (value ? ' checked' : '') + '>';
  }
  if (type === 'select') {
    return '<select id="' + esc(id) + '" data-k="' + k + '"' + described + '>'
      + (options || []).map(function (o) {
        return '<option' + (o === value ? ' selected' : '') + '>' + esc(o) + '</option>';
      }).join('') + '</select>';
  }
  const it = (type === 'text') ? 'text' : 'number';
  const step = (type === 'int') ? '1' : 'any';
  return '<input type="' + it + '" step="' + step + '" id="' + esc(id) + '" data-k="' + k + '"'
    + described + ' value="' + esc(value) + '">';
}

function readInput(el) {
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'number') return el.value === '' ? null : Number(el.value);
  return el.value;
}

function renderGlobal(g) {
  const groups = {};
  const order = [];
  for (const f of FIELDS) {
    if (!groups[f[2]]) { groups[f[2]] = []; order.push(f[2]); }
    groups[f[2]].push(f);
  }
  let html = '';
  for (const gname of order) {
    html += '<div class="field-group"><h3 class="group-title">' + esc(gname) + '</h3><div class="fields">';
    for (const f of groups[gname]) {
      html += '<div class="field" data-field="' + esc(f[0]) + '">'
        + '<label class="field-label" for="g-' + esc(f[0]) + '">' + esc(f[1]) + '</label>'
        + inputHtml(f[0], f[3], g[f[0]], f[4], 'g-' + f[0])
        + '<div class="field-err" id="e-' + esc(f[0]) + '"></div></div>';
    }
    html += '</div></div>';
  }
  $('global-fields').innerHTML = html;
  $('global-fields').oninput = onEdit;
  $('global-fields').onchange = onEdit;
}

function watchPlaceholder(key) {
  return (globalDefaults[key] === undefined || globalDefaults[key] === null)
    ? '' : 'global ' + globalDefaults[key];
}

function renderCustomerOptions() {
  $('customer-options').innerHTML = knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.name || '') + '</option>';
  }).join('');
}

function renderWatch() {
  renderCustomerOptions();

  if (!currentWatch.length) {
    $('watch-rows').innerHTML = '<div class="state">No customers yet. <b>Add customer</b> to start '
      + 'monitoring a balance.</div>';
    return;
  }
  let heads = WATCH_KEYS.map(function (f) {
    return '<th' + (WATCH_NUM.indexOf(f[0]) !== -1 ? ' class="num"' : '') + '>' + esc(f[1]) + '</th>';
  }).join('');
  heads += '<th>Overrides</th><th><span class="sr-only">Remove</span></th>';

  let rows = '';
  currentWatch.forEach(function (w, i) {
    rows += '<tr data-row="' + i + '">';
    rows += '<td><input list="customer-options" data-k="customer" aria-label="Customer ID" title="'
      + esc(w.customer) + '" value="' + esc(w.customer)
      + '" oninput="this.title=this.value; onCustomerInput(' + i + ', this.value)"><div class="cell-err"></div></td>';
    rows += '<td><input type="text" data-k="name" aria-label="Customer name" title="' + esc(w.name)
      + '" value="' + esc(w.name)
      + '" oninput="this.title=this.value"><div class="cell-err"></div></td>';
    rows += '<td class="num"><input type="number" step="any" data-k="balance_below" aria-label="Balance below" value="'
      + (w.balance_below === null || w.balance_below === undefined ? '' : esc(w.balance_below))
      + '"><div class="cell-err"></div></td>';
    for (const key of OVERRIDE_KEYS) {
      const v = w[key];
      rows += '<td class="num"><input type="number" step="any" data-k="' + key + '" aria-label="'
        + esc(key.replace(/_/g, ' ')) + '" placeholder="' + esc(watchPlaceholder(key)) + '" value="'
        + (v === null || v === undefined ? '' : esc(v)) + '"><div class="cell-err"></div></td>';
    }
    rows += '<td><div class="chips overrides-cell"></div></td>';
    rows += '<td class="num"><button class="btn icon" title="Remove customer" aria-label="Remove customer '
      + esc(w.customer || 'row') + '" onclick="removeWatch(' + i + ')">&times;</button></td></tr>';
  });

  $('watch-rows').innerHTML = '<div class="table-wrap"><table><thead><tr>' + heads
    + '</tr></thead><tbody>' + rows + '</tbody></table></div>';
  $('watch-rows').oninput = onEdit;
  $('watch-rows').onchange = onEdit;
  updateOverrideMarkers();
}

function updateOverrideMarkers() {
  document.querySelectorAll('#watch-rows tbody tr').forEach(function (tr) {
    const set = OVERRIDE_KEYS.filter(function (k) {
      const el = tr.querySelector('[data-k="' + k + '"]');
      return el && el.value !== '';
    });
    const cell = tr.querySelector('.overrides-cell');
    if (!cell) return;
    cell.innerHTML = set.length
      ? set.map(function (k) {
        return '<span class="chip nodot over">' + esc(OVERRIDE_LABEL[k] || k) + '</span>';
      }).join('')
      : '<span class="muted" style="color:var(--faint);font-size:11.5px">global</span>';
  });
}

function onCustomerInput(i, value) {
  const match = knownCustomers.find(function (c) { return String(c.customer_id) === String(value); });
  const row = document.querySelector('#watch-rows tr[data-row="' + i + '"]');
  if (!row) return;
  if (match) {
    const nameInput = row.querySelector('[data-k="name"]');
    if (nameInput && !nameInput.value) nameInput.value = match.name || '';
  }
  updateSummary();
}

function syncWatchFromDom() {
  document.querySelectorAll('#watch-rows tbody tr').forEach(function (tr) {
    const i = Number(tr.dataset.row);
    if (!currentWatch[i]) return;
    tr.querySelectorAll('[data-k]').forEach(function (inp) { currentWatch[i][inp.dataset.k] = readInput(inp); });
  });
}

function addWatch() {
  syncWatchFromDom();
  const ids = currentWatch.map(function (w) { return String(w.customer).trim(); });
  const candidate = prompt('Customer ID to add:');
  if (candidate === null) return;
  const id = candidate.trim();
  if (!id) { alert('Customer ID cannot be empty.'); return; }
  if (ids.indexOf(id) !== -1) { alert('Customer ' + id + ' is already in the list.'); return; }
  currentWatch.push({ customer: id, name: '', balance_below: null });
  renderWatch();
  markDirty();
  updateSummary();
}

function removeWatch(i) {
  const w = currentWatch[i];
  if (!w) return;
  if (!confirm('Remove customer ' + (w.customer || '(blank)') + '?')) return;
  syncWatchFromDom();
  currentWatch.splice(i, 1);
  renderWatch();
  markDirty();
  updateSummary();
}

function collectGlobal() {
  const out = {};
  document.querySelectorAll('#global-fields [data-k]').forEach(function (el) { out[el.dataset.k] = readInput(el); });
  return out;
}

function onEdit() {
  markDirty();
  updateSummary();
  updateOverrideMarkers();
  clearFieldErrors();
}

function clearFieldErrors() {
  document.querySelectorAll('.field.invalid, #watch-rows td.invalid').forEach(function (n) {
    n.classList.remove('invalid');
  });
  document.querySelectorAll('.field.invalid input, .field.invalid select, td.invalid input').forEach(function (n) {
    n.classList.remove('invalid');
    n.setAttribute('aria-invalid', 'false');
  });
  document.querySelectorAll('.field-err, .cell-err').forEach(function (n) { n.textContent = ''; });
}

function findWatchRow(customer) {
  let found = null;
  document.querySelectorAll('#watch-rows tbody tr').forEach(function (tr) {
    const c = tr.querySelector('[data-k="customer"]');
    if (c && String(c.value).trim() === String(customer)) found = tr;
  });
  return found;
}

function applyFieldErrors(fieldErrors) {
  clearFieldErrors();
  const banner = [];
  for (const fe of fieldErrors) {
    let container = null;
    let input = null;
    if (fe.customer) {
      const tr = findWatchRow(fe.customer);
      input = tr ? tr.querySelector('[data-k="' + fe.field + '"]') : null;
      container = input ? input.closest('td') : null;
    } else {
      container = document.querySelector('#global-fields .field[data-field="' + fe.field + '"]');
      input = container ? container.querySelector('[data-k]') : null;
    }
    if (container && input) {
      container.classList.add('invalid');
      input.classList.add('invalid');
      input.setAttribute('aria-invalid', 'true');
      const errEl = container.querySelector('.field-err, .cell-err');
      if (errEl) errEl.textContent = fe.message;
      else banner.push(fe.message);
    } else {
      banner.push(fe.message);
    }
  }
  return banner;
}

function updateSummary() {
  const g = collectGlobal();
  const items = [
    ['customers', num(currentWatch.length)],
    ['interval', num(g.check_interval) + 's'],
    ['margin', num(g.margin_below) + ' to ' + num(g.margin_above) + '%'],
    ['deadband', num(g.margin_deadband) + '%'],
    ['billed above', num(g.billed_above) + ' min'],
    ['audio', g.audio ? 'on' : 'off'],
    ['cooldown', num(g.cooldown) + 's'],
    ['retention', num(g.db_retention_days) + 'd'],
    ['workers', num(g.max_workers)],
  ];
  $('config-summary').innerHTML = items.map(function (it) {
    return '<div class="ro"><span class="ro-k">' + esc(it[0]) + '</span><span class="ro-v">'
      + esc(it[1]) + '</span></div>';
  }).join('');
}

function collectSettings() {
  const settings = collectGlobal();
  syncWatchFromDom();
  const watch = currentWatch.map(function (w) {
    const out = {};
    for (const k in w) { const v = w[k]; if (v !== null && v !== undefined) out[k] = v; }
    return out;
  });
  return { settings: settings, watch: watch };
}

function renderSettings(data) {
  globalDefaults = data.settings || {};
  currentWatch = (data.watch || []).map(function (w) {
    const o = Object.assign({}, w);
    for (const k of OVERRIDE_KEYS) if (o[k] === undefined) o[k] = null;
    return o;
  });
  renderGlobal(globalDefaults);
  renderWatch();
  updateSummary();
  clearDirty();
}

function chip(kind, label) {
  return '<span class="chip ' + kind + '">' + esc(label) + '</span>';
}

function renderStatus(s) {
  $('s-alive').innerHTML = s.alive ? chip('ok', 'running') : chip('alert', 'stopped');
  const up = s.uptime_seconds || 0;
  const h = Math.floor(up / 3600), m = Math.floor((up % 3600) / 60), sec = Math.floor(up % 60);
  $('s-uptime').textContent = (h ? h + 'h ' : '') + (m ? m + 'm ' : '') + sec + 's';
  $('s-cycle').textContent = s.cycle_count;
  $('s-last').textContent = s.last_check || '--';
  $('s-errors').textContent = s.error_count || 0;
  $('s-errors').className = 'ro-v' + (s.error_count ? ' alert' : '');
  $('s-version').textContent = 'v' + s.settings_version;

  knownCustomers = s.customers || [];
  renderCustomerOptions();

  const sel = $('hist-customer');
  const keep = sel.value;
  sel.innerHTML = '<option value="">All customers</option>' + knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.customer_id)
      + (c.name ? ' - ' + c.name : '') + '</option>';
  }).join('');
  if ([...sel.options].some(function (o) { return o.value === keep; })) sel.value = keep;

  renderLiveStatus(s);
}

function numCell(v, digits) {
  if (v === null || v === undefined || v === '') return '<span style="color:var(--faint)">--</span>';
  const n = Number(v);
  if (!Number.isFinite(n)) return esc(v);
  const txt = n.toFixed(digits === undefined ? 2 : digits);
  return n < 0 ? '<span class="neg">' + txt + '</span>' : txt;
}

function table(headers, rows) {
  let head = '<tr>' + headers.map(function (h) {
    return '<th' + (h.num ? ' class="num"' : '') + '>' + esc(h.label) + '</th>';
  }).join('') + '</tr>';
  if (!rows.length) return '';
  return '<div class="table-wrap"><table><thead>' + head + '</thead><tbody>'
    + rows.join('') + '</tbody></table></div>';
}

function renderLiveStatus(s) {
  const rows = (s.customers || []).map(function (c) {
    const alerting = c.margin_state || c.balance_state;
    return '<tr><td class="mono">' + esc(c.customer_id) + '</td>'
      + '<td class="wide">' + esc(c.name || '--') + '</td>'
      + '<td class="num">' + numCell(c.balance, 2) + '</td>'
      + '<td class="num">' + numCell(c.credit_limit, 2) + '</td>'
      + '<td class="num">' + numCell(c.remaining, 2) + '</td>'
      + '<td class="num">' + numCell(c.margin, 1) + '</td>'
      + '<td class="num">' + numCell(c.billed_min, 0) + '</td>'
      + '<td>' + (c.monitored ? chip('ok', 'yes') : chip('nodot', 'no')) + '</td>'
      + '<td>' + (alerting ? chip('alert', 'alert') : chip('ok', 'ok')) + '</td>'
      + '<td class="wide">' + (c.error
        ? '<span style="color:var(--err)">' + esc(c.error) + '</span>'
        : '<span style="color:var(--faint)">none</span>') + '</td></tr>';
  });
  if (!rows.length) {
    $('live-status').innerHTML = '<div class="state">Waiting for the first cycle. Readings appear here after the monitor checks a customer.</div>';
    return;
  }
  $('live-status').innerHTML = table([
    { label: 'ID' }, { label: 'Name' }, { label: 'Balance', num: true }, { label: 'Credit', num: true },
    { label: 'Remaining', num: true }, { label: 'Margin %', num: true }, { label: 'Billed min', num: true },
    { label: 'Monitored' }, { label: 'Alert' }, { label: 'Last error' },
  ], rows);
}

function renderAlerts(list) {
  const rows = (list || []).map(function (a) {
    return '<tr><td class="mono">' + esc(a.customer_id) + '</td>'
      + '<td>' + esc(a.alert_type) + '</td>'
      + '<td>' + (a.state ? chip('alert', 'alerting') : chip('nodot', 'clear')) + '</td>'
      + '<td class="num">' + esc(a.alert_count) + '</td>'
      + '<td>' + esc(a.direction || '--') + '</td>'
      + '<td class="mono">' + esc(a.last_alerted_at || '--') + '</td></tr>';
  });
  if (!rows.length) {
    $('live-alerts').innerHTML = '<div class="state">No alert state recorded. Alerts appear when a threshold is crossed.</div>';
    return;
  }
  $('live-alerts').innerHTML = table([
    { label: 'Customer' }, { label: 'Type' }, { label: 'State' },
    { label: 'Count', num: true }, { label: 'Direction' }, { label: 'Last alerted' },
  ], rows);
}

async function loadHistory() {
  const cid = $('hist-customer').value;
  const hours = $('hist-hours').value;
  const url = '/api/history?hours=' + encodeURIComponent(hours)
    + (cid ? '&customer_id=' + encodeURIComponent(cid) : '');
  try {
    const data = await (await fetch(url)).json();
    if (!data.ok) throw new Error(data.error || 'History unavailable');
    renderHistoryTable('hist-balance', data.balance,
      [{ key: 'recorded_at', label: 'Time', mono: true }, { key: 'customer_id', label: 'Customer', mono: true },
       { key: 'balance', label: 'Balance', num: true, digits: 2 },
       { key: 'credit_limit', label: 'Credit', num: true, digits: 2 },
       { key: 'remaining', label: 'Remaining', num: true, digits: 2 }]);
    renderHistoryTable('hist-margin', data.margin,
      [{ key: 'recorded_at', label: 'Time', mono: true }, { key: 'customer_id', label: 'Customer', mono: true },
       { key: 'margin', label: 'Margin %', num: true, digits: 1 },
       { key: 'billed_min', label: 'Billed min', num: true, digits: 0 }]);
  } catch (e) {
    $('hist-balance').innerHTML = '<div class="state">' + esc(e.message) + '</div>';
    $('hist-margin').innerHTML = '<div class="state">Unavailable.</div>';
  }
}

function renderHistoryTable(target, rows, cols) {
  if (!rows || !rows.length) {
    $(target).innerHTML = '<div class="state">No samples in this window.</div>';
    return;
  }
  const headers = cols.map(function (c) {
    return { label: c.label, num: !!c.num };
  });
  const body = rows.slice(0, 500).map(function (r) {
    return '<tr>' + cols.map(function (c) {
      const cls = c.num ? 'num' : (c.mono ? 'mono' : '');
      const val = c.num ? numCell(r[c.key], c.digits) : esc(r[c.key]);
      return '<td' + (cls ? ' class="' + cls + '"' : '') + '>' + val + '</td>';
    }).join('') + '</tr>';
  });
  $(target).innerHTML = table(headers, body);
}

async function loadStatus() {
  try {
    renderStatus(await (await fetch('/api/status')).json());
  } catch (e) {
    $('s-alive').innerHTML = chip('alert', 'offline');
  }
}

async function loadLive() {
  try {
    renderAlerts((await (await fetch('/api/alerts')).json()).alerts || []);
  } catch (e) {
    $('live-alerts').innerHTML = '<div class="state">Alerts unavailable.</div>';
  }
  loadHistory();
}

async function loadSettings() {
  try {
    const data = await (await fetch('/api/settings')).json();
    if (!data.ok) {
      $('global-fields').innerHTML = '<div class="state">' + esc(data.error || 'Settings unavailable') + '</div>';
      return;
    }
    renderSettings(data);
  } catch (e) {
    $('global-fields').innerHTML = '<div class="state">Failed to load settings.</div>';
  }
}

async function saveSettings() {
  const payload = collectSettings();
  $('settings-status').innerHTML = '<div class="notice">Saving&hellip;</div>';
  try {
    const resp = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      const banner = applyFieldErrors(data.field_errors || []);
      const extra = (data.errors || []).filter(function (m) { return banner.indexOf(m) === -1; });
      let html = '<div class="notice err"><b>Not saved.</b> Fix the highlighted fields.';
      if (extra.length) {
        html += '<ul>' + extra.map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul>';
      }
      $('settings-status').innerHTML = html + '</div>';
      return;
    }
    clearFieldErrors();
    let html = '<div class="notice ok"><b>Saved.</b> Applied within about a second.</div>';
    if (data.warnings && data.warnings.length) {
      html += '<div class="notice warn">'
        + data.warnings.map(function (w) { return esc(w); }).join('<br>') + '</div>';
    }
    $('settings-status').innerHTML = html;
    renderSettings(data);
    loadStatus();
  } catch (e) {
    $('settings-status').innerHTML = '<div class="notice err">Save failed: ' + esc(e) + '</div>';
  }
}

document.querySelectorAll('.tab').forEach(function (btn) {
  btn.addEventListener('keydown', function (e) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
      e.preventDefault();
      const next = activeTab === 'settings' ? 'live' : 'settings';
      showTab(next);
      $('tabbtn-' + next).focus();
    }
  });
});

window.addEventListener('beforeunload', function (e) {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

loadStatus();
loadSettings();
if (location.hash === '#live') showTab('live');
setInterval(function () { loadStatus(); if (activeTab === 'live') loadLive(); }, REFRESH * 1000);
</script>
</body>
</html>
"""


def _fields_json() -> str:
    """Serialize the editable settings schema for the page's FIELDS constant."""
    return json.dumps([[s.name, s.label, s.group, s.kind, list(s.options)] for s in FIELD_SPEC])


def render_page(refresh: int, csrf_token: str) -> str:
    return (
        _PAGE_TEMPLATE.replace("%FIELDS%", _fields_json())
        .replace("%REFRESH%", str(max(int(refresh), 1)))
        .replace("%CSRF%", csrf_token)
    )
