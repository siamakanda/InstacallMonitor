"""The dashboard's single self-contained HTML page (stdlib-only, no build step).

Kept separate from webapp.py so the HTTP/API layer stays focused.
"""

from __future__ import annotations

_PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf-token" content="%CSRF%">
<title>InstacallMonitor</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --fg:#e6e8ee; --dim:#8a93a6;
          --green:#50fa7b; --red:#ff5555; --yellow:#f1fa8c; --magenta:#ff79c6; --cyan:#8be9fd; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.5 "Segoe UI", system-ui, sans-serif; }
  header { padding:14px 22px 0; border-bottom:1px solid var(--line); position:sticky; top:0;
           background:var(--bg); z-index:5; }
  .statusstrip { display:flex; flex-wrap:wrap; gap:10px 22px; align-items:baseline; padding-bottom:12px; }
  h1 { font-size:17px; margin:0; letter-spacing:.3px; }
  .stat { color:var(--dim); font-size:13px; }
  .stat b { color:var(--fg); font-weight:600; }
  .tabs { display:flex; gap:4px; }
  .tab { padding:8px 16px; border:1px solid var(--line); border-bottom:none; border-radius:8px 8px 0 0;
         background:var(--panel); color:var(--dim); cursor:pointer; font:inherit; }
  .tab.active { background:var(--bg); color:var(--fg); border-color:var(--line); }
  main { padding:18px 22px 60px; max-width:1100px; }
  section.tabpane { display:none; }
  section.tabpane.active { display:block; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.12em; color:var(--dim); margin:24px 0 10px; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim); margin:16px 0 8px; }
  .summaryline { background:var(--panel); border:1px solid var(--line); border-radius:8px;
       padding:12px 16px; color:var(--dim); font-size:13px; }
  .summaryline b { color:var(--fg); font-weight:600; }
  table { width:100%; border-collapse:collapse; background:var(--panel);
          border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  th, td { padding:8px 10px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { font-size:12px; color:var(--dim); font-weight:600; background:#12141a; }
  tr:last-child td { border-bottom:none; }
  td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }
  .muted { color:var(--dim); }
  .empty { color:var(--dim); padding:14px 4px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(280px,1fr)); gap:12px 24px; }
  .sfield { display:grid; grid-template-columns:1fr 140px; align-items:center; gap:2px 12px; font-size:13px; }
  .sfield input, .sfield select { width:140px; }
  .fielderr { grid-column:1 / -1; color:var(--red); font-size:12px; display:none; }
  .sfield.invalid .fielderr { display:block; }
  input.invalid, select.invalid { border-color:var(--red); }
  input, select { background:var(--panel); color:var(--fg); border:1px solid var(--line);
       border-radius:6px; padding:5px 8px; font:inherit; }
  #watch-rows input { width:118px; }
  #watch-rows td { vertical-align:top; }
  #watch-rows .fielderr { display:none; color:var(--red); font-size:11px; white-space:normal; max-width:130px; }
  #watch-rows td.invalid .fielderr { display:block; }
  .btn { background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px;
         padding:7px 12px; cursor:pointer; font-size:13px; }
  .btn.primary { background:#2b6cb0; border-color:#2b6cb0; color:#fff; }
  .btn.primary:disabled { opacity:.5; cursor:default; }
  .btn.small { padding:3px 9px; line-height:1.2; }
  .savebar { margin-top:20px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  .okmsg { background:rgba(80,250,123,.12); border:1px solid rgba(80,250,123,.4); padding:10px 14px;
           border-radius:8px; margin-top:12px; }
  .warnmsg { background:rgba(241,250,140,.1); border:1px solid rgba(241,250,140,.4); color:var(--yellow);
           padding:10px 14px; border-radius:8px; margin-top:8px; }
  .errmsg { background:rgba(255,85,85,.12); border:1px solid rgba(255,85,85,.4); padding:10px 14px;
           border-radius:8px; margin-top:12px; }
  .errmsg ul { margin:6px 0 0; padding-left:18px; }
  .hint { color:var(--dim); font-size:12px; margin-top:8px; }
  .dot { color:var(--yellow); }
</style>
</head>
<body>
<header>
  <div class="statusstrip">
    <h1>InstacallMonitor</h1>
    <span class="stat" id="s-alive">--</span>
    <span class="stat">Uptime <b id="s-uptime">--</b></span>
    <span class="stat">Cycle <b id="s-cycle">--</b></span>
    <span class="stat">Last check <b id="s-last">--</b></span>
    <span class="stat">Errors <b id="s-errors">--</b></span>
    <span class="stat">Settings v<b id="s-version">--</b></span>
  </div>
  <div class="tabs">
    <button class="tab active" id="tabbtn-settings" onclick="showTab('settings')">Settings</button>
    <button class="tab" id="tabbtn-live" onclick="showTab('live')">Live</button>
  </div>
</header>
<main>
  <section class="tabpane active" id="tab-settings">
    <div class="summaryline" id="config-summary">Loading&hellip;</div>

    <h2>Settings <span class="muted">(saved to settings.toml &middot; applied on save)</span></h2>
    <div id="global-fields"><div class="empty">Loading&hellip;</div></div>

    <h2>Balance-check customers</h2>
    <div id="watch-rows"><div class="empty">Loading&hellip;</div></div>
    <datalist id="customer-options"></datalist>
    <div style="margin-top:10px">
      <button class="btn small" onclick="addWatch()">+ Add customer</button>
      <span class="hint">Pick a customer from the list or type an ID. Balance below is required.
        Rates left blank fall back to the global defaults shown as placeholders.</span>
    </div>

    <div class="savebar">
      <button class="btn primary" id="save-btn" onclick="saveSettings()" disabled>Save settings</button>
      <span class="muted" id="dirty-hint" style="font-size:12px"></span>
    </div>
    <div id="settings-status"></div>
    <p class="hint">Localhost-only. Concurrent edits in another tab will be overwritten on save.</p>
  </section>

  <section class="tabpane" id="tab-live">
    <h2>Current status</h2>
    <div id="live-status"><div class="empty">Loading&hellip;</div></div>

    <h2>History</h2>
    <div class="grid" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr));align-items:center">
      <label class="sfield"><span>Customer</span><select id="hist-customer" onchange="loadHistory()"></select></label>
      <label class="sfield"><span>Window</span>
        <select id="hist-hours" onchange="loadHistory()">
          <option value="1">Last 1h</option>
          <option value="6">Last 6h</option>
          <option value="24" selected>Last 24h</option>
          <option value="168">Last 7d</option>
        </select>
      </label>
      <div><button class="btn small" onclick="loadHistory()">Refresh</button></div>
    </div>
    <h3>Balances</h3>
    <div id="hist-balance"><div class="empty">&ndash;</div></div>
    <h3>Margins</h3>
    <div id="hist-margin"><div class="empty">&ndash;</div></div>

    <h2>Alerts</h2>
    <div id="live-alerts"><div class="empty">Loading&hellip;</div></div>
  </section>
</main>
<script>
const REFRESH = %REFRESH%;
const CSRF = "%CSRF%";
const $ = id => document.getElementById(id);
const esc = v => String(v === null || v === undefined ? '' : v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const num = v => (v === null || v === undefined || v === '') ? '--' : Number(v);

const FIELDS = [
  ['check_interval','Check interval (s)','General','int'],
  ['request_timeout','Request timeout (s)','General','int'],
  ['cooldown','Alert cooldown (s)','General','int'],
  ['audio','Audio sirens','General','bool'],
  ['summary_direction','Summary direction','General','select',['outbound','inbound']],
  ['summary_interval','Summary interval','General','text'],
  ['db_retention_days','DB retention (days)','General','int'],
  ['max_workers','Max workers','General','int'],
  ['margin_below','Margin below (%)','Thresholds','float'],
  ['margin_above','Margin above (%)','Thresholds','float'],
  ['margin_deadband','Margin deadband (%)','Thresholds','float'],
  ['billed_above','Billed above (min)','Thresholds','float'],
  ['siren_loops','Siren loops','Sirens','int'],
  ['siren_min_freq','Siren min freq (Hz)','Sirens','int'],
  ['siren_max_freq','Siren max freq (Hz)','Sirens','int'],
  ['siren_step_freq','Siren step (Hz)','Sirens','int'],
  ['siren_tone_duration','Tone duration (ms)','Sirens','int'],
  ['retry_max_attempts','Retry attempts','Resilience','int'],
  ['retry_base_delay','Retry base delay (s)','Resilience','float'],
  ['retry_max_delay','Retry max delay (s)','Resilience','float'],
  ['retry_jitter_max','Retry jitter (s)','Resilience','float'],
  ['circuit_failure_threshold','Circuit threshold','Resilience','int'],
  ['circuit_recovery_timeout','Circuit recovery (s)','Resilience','float'],
  ['web_dashboard','Dashboard enabled','Dashboard','bool'],
  ['web_dashboard_host','Dashboard host','Dashboard','text'],
  ['web_dashboard_port','Dashboard port','Dashboard','int'],
  ['web_dashboard_refresh','Refresh (s)','Dashboard','int'],
];
const WATCH_KEYS = [
  ['customer','Customer'],
  ['name','Name'],
  ['balance_below','Balance below'],
  ['margin_below','Margin below %'],
  ['margin_above','Margin above %'],
  ['margin_deadband','Deadband %'],
  ['billed_above','Billed above'],
];
const OVERRIDE_KEYS = ['margin_below','margin_above','margin_deadband','billed_above'];

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
  for (const t of ['settings','live']) {
    $('tab-' + t).classList.toggle('active', t === name);
    $('tabbtn-' + t).classList.toggle('active', t === name);
  }
  if (name === 'live') { loadLive(); }
}

function inputHtml(key, type, value, options) {
  const k = esc(key);
  if (type === 'bool') return '<input type="checkbox" data-k="' + k + '"' + (value ? ' checked' : '') + '>';
  if (type === 'select') {
    return '<select data-k="' + k + '">' + (options || []).map(function (o) {
      return '<option' + (o === value ? ' selected' : '') + '>' + esc(o) + '</option>';
    }).join('') + '</select>';
  }
  const it = (type === 'text') ? 'text' : 'number';
  const step = (type === 'int') ? '1' : 'any';
  return '<input type="' + it + '" step="' + step + '" data-k="' + k + '" value="' + esc(value) + '">';
}

function readInput(el) {
  if (el.type === 'checkbox') return el.checked;
  if (el.type === 'number') return el.value === '' ? null : Number(el.value);
  return el.value;
}

function renderGlobal(g) {
  const groups = {}, order = [];
  for (const f of FIELDS) {
    if (!groups[f[2]]) { groups[f[2]] = []; order.push(f[2]); }
    groups[f[2]].push(f);
  }
  let html = '';
  for (const gname of order) {
    html += '<div><h3>' + esc(gname) + '</h3><div class="grid">';
    for (const f of groups[gname]) {
      html += '<div class="sfield" data-field="' + esc(f[0]) + '"><span>' + esc(f[1]) + '</span>'
        + inputHtml(f[0], f[3], g[f[0]], f[4]) + '<div class="fielderr"></div></div>';
    }
    html += '</div></div>';
  }
  $('global-fields').innerHTML = html;
  $('global-fields').addEventListener('input', onEdit);
  $('global-fields').addEventListener('change', onEdit);
}

function watchPlaceholder(key) {
  return (globalDefaults[key] === undefined || globalDefaults[key] === null) ? ''
    : 'global: ' + globalDefaults[key];
}

function renderWatch() {
  $('customer-options').innerHTML = knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.name || '') + '</option>';
  }).join('');

  if (!currentWatch.length) {
    $('watch-rows').innerHTML = '<div class="empty">No balance-check customers. Add one below.</div>';
    return;
  }
  let heads = '';
  for (const f of WATCH_KEYS) heads += '<th>' + esc(f[1]) + '</th>';
  heads += '<th>Overrides</th><th></th>';
  let rows = '';
  currentWatch.forEach(function (w, i) {
    rows += '<tr data-row="' + i + '">';
    rows += '<td><input list="customer-options" data-k="customer" value="' + esc(w.customer)
      + '" oninput="onCustomerInput(' + i + ', this.value)"><div class="fielderr"></div></td>';
    rows += '<td><input type="text" data-k="name" value="' + esc(w.name) + '"><div class="fielderr"></div></td>';
    rows += '<td><input type="number" step="any" data-k="balance_below" value="'
      + (w.balance_below === null || w.balance_below === undefined ? '' : esc(w.balance_below))
      + '"><div class="fielderr"></div></td>';
    for (const key of OVERRIDE_KEYS) {
      const v = w[key];
      rows += '<td><input type="number" step="any" data-k="' + key + '" value="'
        + (v === null || v === undefined ? '' : esc(v)) + '" placeholder="' + esc(watchPlaceholder(key))
        + '"><div class="fielderr"></div></td>';
    }
    rows += '<td class="muted overrides"></td>';
    rows += '<td><button class="btn small" title="Remove" onclick="removeWatch(' + i + ')">x</button></td></tr>';
  });
  $('watch-rows').innerHTML = '<table><thead><tr>' + heads + '</tr></thead><tbody>' + rows + '</tbody></table>';
  $('watch-rows').addEventListener('input', onEdit);
  $('watch-rows').addEventListener('change', onEdit);
  updateOverrideMarkers();
}

function updateOverrideMarkers() {
  document.querySelectorAll('#watch-rows tbody tr').forEach(function (tr) {
    const set = OVERRIDE_KEYS.filter(function (k) {
      const el = tr.querySelector('[data-k="' + k + '"]');
      return el && el.value !== '';
    });
    const cell = tr.querySelector('.overrides');
    if (cell) cell.innerHTML = set.length ? '<span class="dot">&#9679;</span> ' + esc(set.join(', ')) : '';
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
  document.querySelectorAll('.sfield.invalid, #watch-rows td.invalid').forEach(function (n) {
    n.classList.remove('invalid');
  });
  document.querySelectorAll('.fielderr').forEach(function (n) { n.textContent = ''; });
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
    if (fe.customer) {
      const tr = findWatchRow(fe.customer);
      const input = tr ? tr.querySelector('[data-k="' + fe.field + '"]') : null;
      if (input) { container = input.closest('td'); input.classList.add('invalid'); }
    } else {
      const field = document.querySelector('#global-fields .sfield[data-field="' + fe.field + '"]');
      if (field) {
        container = field;
        const input = field.querySelector('[data-k]');
        if (input) input.classList.add('invalid');
      }
    }
    if (container) {
      const errEl = container.querySelector('.fielderr');
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
  const on = v => v ? 'ON' : 'OFF';
  $('config-summary').innerHTML =
    '<b>' + currentWatch.length + '</b> customers monitored'
    + ' &middot; every <b>' + num(g.check_interval) + 's</b>'
    + ' &middot; margin <b>' + num(g.margin_below) + '&ndash;' + num(g.margin_above) + '%</b>'
    + ' &middot; deadband <b>' + num(g.margin_deadband) + '%</b>'
    + ' &middot; billed above <b>' + num(g.billed_above) + ' min</b>'
    + ' &middot; audio <b>' + on(g.audio) + '</b>'
    + ' &middot; cooldown <b>' + num(g.cooldown) + 's</b>'
    + ' &middot; retention <b>' + num(g.db_retention_days) + 'd</b>'
    + ' &middot; workers <b>' + num(g.max_workers) + '</b>';
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

function renderStatus(s) {
  $('s-alive').innerHTML = s.alive
    ? '<b style="color:var(--green)">&#9679; LIVE</b>' : '<b style="color:var(--red)">&#9679; STOPPED</b>';
  const up = s.uptime_seconds || 0;
  const h = Math.floor(up / 3600), m = Math.floor((up % 3600) / 60), sec = Math.floor(up % 60);
  $('s-uptime').textContent = (h ? h + 'h ' : '') + (m ? m + 'm ' : '') + sec + 's';
  $('s-cycle').textContent = s.cycle_count;
  $('s-last').textContent = s.last_check || '--';
  $('s-errors').innerHTML = s.error_count ? '<b style="color:var(--red)">' + s.error_count + '</b>' : '0';
  $('s-version').textContent = s.settings_version;
  knownCustomers = s.customers || [];
  $('customer-options').innerHTML = knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.name || '') + '</option>';
  }).join('');
  const sel = $('hist-customer');
  const keep = sel.value;
  sel.innerHTML = '<option value="">All customers</option>' + knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.customer_id + (c.name ? ' - ' + c.name : '')) + '</option>';
  }).join('');
  if ([...sel.options].some(function (o) { return o.value === keep; })) sel.value = keep;
  renderLiveStatus(s);
}

function cell(v, digits) {
  if (v === null || v === undefined || v === '') return '<span class="muted">--</span>';
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(digits === undefined ? 2 : digits) : esc(v);
}

function renderLiveStatus(s) {
  const rows = s.customers || [];
  if (!rows.length) { $('live-status').innerHTML = '<div class="empty">No data yet.</div>'; return; }
  let html = '<table><thead><tr><th>ID</th><th>Name</th><th class="num">Balance</th><th class="num">Credit</th>'
    + '<th class="num">Remaining</th><th class="num">Margin %</th><th class="num">Billed min</th>'
    + '<th>Monitored</th><th>Alert</th><th>Error</th></tr></thead><tbody>';
  for (const c of rows) {
    const state = c.margin_state || c.balance_state ? '<span style="color:var(--red)">ALERT</span>' : '<span class="muted">ok</span>';
    html += '<tr><td>' + esc(c.customer_id) + '</td><td>' + esc(c.name || '--') + '</td>'
      + '<td class="num">' + cell(c.balance, 4) + '</td>'
      + '<td class="num">' + cell(c.credit_limit) + '</td>'
      + '<td class="num">' + cell(c.remaining) + '</td>'
      + '<td class="num">' + cell(c.margin, 1) + '</td>'
      + '<td class="num">' + cell(c.billed_min, 1) + '</td>'
      + '<td>' + (c.monitored ? 'yes' : '<span class="muted">no</span>') + '</td>'
      + '<td>' + state + '</td>'
      + '<td>' + (c.error ? '<span style="color:var(--red)">' + esc(c.error) + '</span>' : '<span class="muted">--</span>') + '</td></tr>';
  }
  $('live-status').innerHTML = html + '</tbody></table>';
}

function renderAlerts(list) {
  if (!list.length) { $('live-alerts').innerHTML = '<div class="empty">No alert state recorded.</div>'; return; }
  let html = '<table><thead><tr><th>Customer</th><th>Type</th><th>State</th><th class="num">Count</th>'
    + '<th>Direction</th><th>Last alerted</th></tr></thead><tbody>';
  for (const a of list) {
    html += '<tr><td>' + esc(a.customer_id) + '</td><td>' + esc(a.alert_type) + '</td>'
      + '<td>' + (a.state ? '<span style="color:var(--red)">ALERT</span>' : '<span class="muted">ok</span>') + '</td>'
      + '<td class="num">' + esc(a.alert_count) + '</td><td>' + esc(a.direction || '--') + '</td>'
      + '<td>' + esc(a.last_alerted_at || '--') + '</td></tr>';
  }
  $('live-alerts').innerHTML = html + '</tbody></table>';
}

async function loadHistory() {
  const cid = $('hist-customer').value;
  const hours = $('hist-hours').value;
  const url = '/api/history?hours=' + encodeURIComponent(hours) + (cid ? '&customer_id=' + encodeURIComponent(cid) : '');
  try {
    const data = await (await fetch(url)).json();
    if (!data.ok) throw new Error(data.error || 'history unavailable');
    renderHistoryTable('hist-balance', data.balance,
      ['recorded_at','customer_id','balance','credit_limit','remaining'],
      ['Time','Customer','Balance','Credit','Remaining'], 3);
    renderHistoryTable('hist-margin', data.margin,
      ['recorded_at','customer_id','margin','billed_min'],
      ['Time','Customer','Margin %','Billed min'], 2);
  } catch (e) {
    $('hist-balance').innerHTML = '<div class="empty">' + esc(e.message) + '</div>';
    $('hist-margin').innerHTML = '<div class="empty">&ndash;</div>';
  }
}

function renderHistoryTable(target, rows, keys, heads, digits) {
  if (!rows || !rows.length) { $(target).innerHTML = '<div class="empty">No records.</div>'; return; }
  let html = '<table><thead><tr>' + heads.map(function (h, i) {
    return '<th' + (i >= 2 ? ' class="num"' : '') + '>' + esc(h) + '</th>';
  }).join('') + '</tr></thead><tbody>';
  for (const r of rows.slice(0, 200)) {
    html += '<tr>' + keys.map(function (k, i) {
      if (i >= 2) return '<td class="num">' + cell(r[k], digits) + '</td>';
      return '<td>' + esc(r[k]) + '</td>';
    }).join('') + '</tr>';
  }
  $(target).innerHTML = html + '</tbody></table>';
}

async function loadStatus() {
  try { renderStatus(await (await fetch('/api/status')).json()); }
  catch (e) { $('s-alive').innerHTML = '<b style="color:var(--red)">&#9679; OFFLINE</b>'; }
}

async function loadLive() {
  try { renderAlerts((await (await fetch('/api/alerts')).json()).alerts || []); }
  catch (e) { $('live-alerts').innerHTML = '<div class="empty">Alerts unavailable.</div>'; }
  loadHistory();
}

async function loadSettings() {
  try {
    const data = await (await fetch('/api/settings')).json();
    if (!data.ok) { $('global-fields').innerHTML = '<div class="empty">' + esc(data.error || 'Unavailable') + '</div>'; return; }
    renderSettings(data);
  } catch (e) {
    $('global-fields').innerHTML = '<div class="empty">Failed to load settings.</div>';
  }
}

async function saveSettings() {
  const payload = collectSettings();
  $('settings-status').innerHTML = '<span class="muted">Saving&hellip;</span>';
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
      let html = '<div class="errmsg"><b>Not saved:</b>';
      if (extra.length) html += '<ul>' + extra.map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul>';
      if (banner.length) html += '<ul>' + banner.map(function (e) { return '<li>' + esc(e) + '</li>'; }).join('') + '</ul>';
      $('settings-status').innerHTML = html + '</div>';
      return;
    }
    clearFieldErrors();
    let msg = '<div class="okmsg">Saved &mdash; applying within ~1s.</div>';
    if (data.warnings && data.warnings.length) {
      msg += '<div class="warnmsg">' + data.warnings.map(function (w) { return esc(w); }).join('<br>') + '</div>';
    }
    $('settings-status').innerHTML = msg;
    renderSettings(data);
    loadStatus();
  } catch (e) {
    $('settings-status').innerHTML = '<div class="errmsg">Save failed: ' + esc(e) + '</div>';
  }
}

window.addEventListener('beforeunload', function (e) {
  if (dirty) { e.preventDefault(); e.returnValue = ''; }
});

loadStatus();
loadSettings();
setInterval(function () { loadStatus(); if (activeTab === 'live') loadLive(); }, REFRESH * 1000);
</script>
</body>
</html>
"""


def render_page(refresh: int, csrf_token: str) -> str:
    return _PAGE_TEMPLATE.replace("%REFRESH%", str(max(int(refresh), 1))).replace("%CSRF%", csrf_token)
