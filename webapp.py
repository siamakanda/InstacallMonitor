from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import persistence
from config import read_settings, save_settings, validate_settings
from settings_models import Settings


class MonitorState:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._reload_requested = threading.Event()
        self.started_at = time.time()
        self.alive = True
        self.cycle_count = 0
        self.last_check: Optional[str] = None
        self.next_check: Optional[str] = None
        self.interval = 0
        self.error_count = 0
        self.last_error: Optional[str] = None
        self.settings_version = 0
        self.customers: dict[str, dict[str, Any]] = {}

    @property
    def reload_event(self) -> threading.Event:
        return self._reload_requested

    def request_reload(self) -> None:
        self._reload_requested.set()

    def reload_requested(self) -> bool:
        return self._reload_requested.is_set()

    def clear_reload(self) -> None:
        self._reload_requested.clear()

    def update_customer(self, customer_id: str, **fields: Any) -> None:
        with self._lock:
            entry = self.customers.setdefault(customer_id, {"customer_id": customer_id})
            entry.update(fields)

    def set_cycle(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self, key, value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "alive": self.alive,
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "cycle_count": self.cycle_count,
                "last_check": self.last_check,
                "next_check": self.next_check,
                "interval": self.interval,
                "error_count": self.error_count,
                "last_error": self.last_error,
                "settings_version": self.settings_version,
                "customers": sorted(
                    (dict(c) for c in self.customers.values()),
                    key=lambda c: c.get("customer_id", ""),
                ),
            }


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _send_security_headers(handler: BaseHTTPRequestHandler) -> None:
    for name, value in _SECURITY_HEADERS.items():
        handler.send_header(name, value)


def _json_response(handler: BaseHTTPRequestHandler, payload: Any, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    _send_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def _same_origin(handler: BaseHTTPRequestHandler) -> bool:
    origin = handler.headers.get("Origin")
    if not origin:
        return True
    netloc = urlparse(origin).netloc
    port = getattr(handler, "bound_port", 0)
    host = getattr(handler, "bound_host", "127.0.0.1")
    allowed = {f"{host}:{port}", f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    return netloc in allowed


def _read_json_body(handler: BaseHTTPRequestHandler, limit: int = 1_000_000) -> Any:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError:
        raise ValueError("invalid Content-Length")
    if length <= 0:
        raise ValueError("empty request body")
    if length > limit:
        raise ValueError("request body too large")
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError(f"invalid JSON: {e}")


def _apply_settings(payload: Any) -> tuple[int, dict[str, Any], Optional[Settings]]:
    if not isinstance(payload, dict):
        return 400, {"ok": False, "errors": ["request body must be a JSON object"]}, None
    global_data = payload.get("settings", {})
    watch_data = payload.get("watch", [])
    if not isinstance(global_data, dict):
        return 400, {"ok": False, "errors": ["'settings' must be an object"]}, None
    if not isinstance(watch_data, list):
        return 400, {"ok": False, "errors": ["'watch' must be a list"]}, None

    clean_global = {k: v for k, v in global_data.items() if v is not None}
    clean_watch: list[dict[str, Any]] = []
    for entry in watch_data:
        if not isinstance(entry, dict):
            return 400, {"ok": False, "errors": ["each watch entry must be an object"]}, None
        clean_watch.append({k: v for k, v in entry.items() if v is not None})

    try:
        settings = Settings.from_dict({"settings": clean_global, "watch": clean_watch})
    except Exception as e:
        return 400, {"ok": False, "errors": [f"invalid settings: {e}"]}, None

    errors = validate_settings(settings)
    if errors:
        return 400, {"ok": False, "errors": errors}, None

    try:
        save_settings(settings)
    except OSError as e:
        return 500, {"ok": False, "errors": [f"failed to write settings.toml: {e}"]}, None

    return 200, {"ok": True, "settings": settings.to_dict()}, settings


def _settings_warnings(settings: Settings, bound_host: str, bound_port: int) -> list[str]:
    g = settings.global_
    warnings: list[str] = []
    if (g.web_dashboard_host, g.web_dashboard_port) != (bound_host, bound_port):
        warnings.append("Dashboard host/port change takes effect after restart.")
    if not g.web_dashboard:
        warnings.append("Dashboard disabled; the current server keeps running until restart.")
    return warnings


_INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>InstacallMonitor - Settings</title>
<style>
  :root { --bg:#0f1115; --panel:#171a21; --line:#262b36; --fg:#e6e8ee; --dim:#8a93a6;
          --green:#50fa7b; --red:#ff5555; --yellow:#f1fa8c; --magenta:#ff79c6; --cyan:#8be9fd; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
         font:14px/1.5 "Segoe UI", system-ui, sans-serif; }
  header { padding:16px 22px; border-bottom:1px solid var(--line); display:flex;
           flex-wrap:wrap; gap:10px 22px; align-items:baseline; position:sticky; top:0;
           background:var(--bg); z-index:5; }
  h1 { font-size:17px; margin:0; letter-spacing:.3px; }
  .stat { color:var(--dim); font-size:13px; }
  .stat b { color:var(--fg); font-weight:600; }
  main { padding:18px 22px 60px; max-width:1100px; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:.12em; color:var(--dim);
       margin:26px 0 10px; }
  h3 { font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--dim);
       margin:16px 0 8px; }
  .summaryline { background:var(--panel); border:1px solid var(--line); border-radius:8px;
       padding:12px 16px; color:var(--dim); font-size:13px; margin-top:18px; }
  .summaryline b { color:var(--fg); font-weight:600; }
  table { width:100%; border-collapse:collapse; background:var(--panel);
          border:1px solid var(--line); border-radius:8px; overflow:hidden; }
  th, td { padding:8px 10px; text-align:left; border-bottom:1px solid var(--line); white-space:nowrap; }
  th { font-size:12px; color:var(--dim); font-weight:600; background:#12141a; }
  tr:last-child td { border-bottom:none; }
  .muted { color:var(--dim); }
  .empty { color:var(--dim); padding:14px 4px; }
  .sgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:10px 20px; }
  .sfield { display:flex; justify-content:space-between; align-items:center; gap:12px; font-size:13px; }
  input, select { background:var(--panel); color:var(--fg); border:1px solid var(--line);
       border-radius:6px; padding:5px 8px; font:inherit; }
  .sfield input, .sfield select { width:130px; }
  #watch-rows input { width:120px; }
  #watch-rows input[data-k=customer] { width:120px; }
  .btn { background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:6px;
         padding:7px 12px; cursor:pointer; font-size:13px; }
  .btn.primary { background:#2b6cb0; border-color:#2b6cb0; color:#fff; }
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
</style>
</head>
<body>
<header>
  <h1>InstacallMonitor</h1>
  <span class="stat" id="s-alive">--</span>
  <span class="stat">Uptime <b id="s-uptime">--</b></span>
  <span class="stat">Cycle <b id="s-cycle">--</b></span>
  <span class="stat">Last check <b id="s-last">--</b></span>
  <span class="stat">Errors <b id="s-errors">--</b></span>
  <span class="stat">Settings v<b id="s-version">--</b></span>
</header>
<main>
  <div class="summaryline" id="config-summary">Loading...</div>

  <h2>Settings <span class="muted">(saved to settings.toml &middot; applied on save)</span></h2>
  <div id="global-fields"><div class="empty">Loading...</div></div>

  <h2>Balance-check customers</h2>
  <div id="watch-rows"><div class="empty">Loading...</div></div>
  <datalist id="customer-options"></datalist>
  <div style="margin-top:10px">
    <button class="btn small" onclick="addWatch()">+ Add customer</button>
    <span class="hint">Pick a customer from the list or type an ID. Balance below is required.</span>
  </div>

  <div class="savebar">
    <button class="btn primary" onclick="saveSettings()">Save settings</button>
    <span class="muted" style="font-size:12px">
      Writes settings.toml, keeps a .bak copy, and hot-reloads the monitor.
    </span>
  </div>
  <div id="settings-status"></div>
  <p class="hint">Localhost-only. See the README for the JSON API.</p>
</main>
<script>
const REFRESH = %REFRESH%;
const $ = id => document.getElementById(id);
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
let currentWatch = [];
let knownCustomers = [];

function esc(v) {
  return String(v === null || v === undefined ? '' : v)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
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
  const groups = {};
  const order = [];
  for (const f of FIELDS) {
    if (!groups[f[2]]) { groups[f[2]] = []; order.push(f[2]); }
    groups[f[2]].push(f);
  }
  let html = '';
  for (const gname of order) {
    html += '<div><h3>' + esc(gname) + '</h3><div class="sgrid">';
    for (const f of groups[gname]) {
      html += '<label class="sfield">' + esc(f[1]) + inputHtml(f[0], f[3], g[f[0]], f[4]) + '</label>';
    }
    html += '</div></div>';
  }
  $('global-fields').innerHTML = html;
  $('global-fields').oninput = updateSummary;
}

function renderWatch() {
  const opts = knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.name || '') + '</option>';
  }).join('');
  $('customer-options').innerHTML = opts;

  if (!currentWatch.length) {
    $('watch-rows').innerHTML = '<div class="empty">No balance-check customers. Add one below.</div>';
    return;
  }
  let heads = '';
  for (const f of WATCH_KEYS) heads += '<th>' + esc(f[1]) + '</th>';
  let rows = '';
  currentWatch.forEach(function (w, i) {
    rows += '<tr data-row="' + i + '">';
    rows += '<td><input list="customer-options" data-k="customer" value="' + esc(w.customer)
      + '" oninput="onCustomerInput(' + i + ', this.value)"></td>';
    rows += '<td><input type="text" data-k="name" value="' + esc(w.name) + '"></td>';
    for (const key of ['balance_below','margin_below','margin_above','margin_deadband','billed_above']) {
      const v = w[key];
      rows += '<td><input type="number" step="any" data-k="' + key + '" value="'
        + (v === null || v === undefined ? '' : esc(v)) + '"></td>';
    }
    rows += '<td><button class="btn small" title="Remove" onclick="removeWatch(' + i + ')">x</button></td></tr>';
  });
  $('watch-rows').innerHTML = '<table><thead><tr>' + heads + '<th></th></tr></thead><tbody>'
    + rows + '</tbody></table>';
}

function onCustomerInput(i, value) {
  const match = knownCustomers.find(function (c) { return String(c.customer_id) === String(value); });
  if (!match) return;
  const row = document.querySelector('#watch-rows tr[data-row="' + i + '"]');
  if (!row) return;
  const nameInput = row.querySelector('[data-k="name"]');
  if (nameInput && !nameInput.value) nameInput.value = match.name || '';
  updateSummary();
}

function syncWatchFromDom() {
  document.querySelectorAll('#watch-rows tbody tr').forEach(function (tr) {
    const i = Number(tr.dataset.row);
    if (!currentWatch[i]) return;
    tr.querySelectorAll('[data-k]').forEach(function (inp) {
      currentWatch[i][inp.dataset.k] = readInput(inp);
    });
  });
}

function addWatch() {
  syncWatchFromDom();
  currentWatch.push({ customer: '', name: '', balance_below: null });
  renderWatch();
  updateSummary();
}

function removeWatch(i) {
  syncWatchFromDom();
  currentWatch.splice(i, 1);
  renderWatch();
  updateSummary();
}

function collectGlobal() {
  const out = {};
  document.querySelectorAll('#global-fields [data-k]').forEach(function (el) {
    out[el.dataset.k] = readInput(el);
  });
  return out;
}

function updateSummary() {
  const g = collectGlobal();
  const on = function (v) { return v ? 'ON' : 'OFF'; };
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
  currentWatch = data.watch || [];
  renderGlobal(data.settings || {});
  renderWatch();
  updateSummary();
}

function renderStatus(s) {
  $('s-alive').innerHTML = s.alive
    ? '<b style="color:var(--green)">&#9679; LIVE</b>' : '<b style="color:var(--red)">&#9679; STOPPED</b>';
  const up = s.uptime_seconds || 0;
  const h = Math.floor(up / 3600), m = Math.floor((up % 3600) / 60), sec = Math.floor(up % 60);
  $('s-uptime').textContent = (h ? h + 'h ' : '') + (m ? m + 'm ' : '') + sec + 's';
  $('s-cycle').textContent = s.cycle_count;
  $('s-last').textContent = s.last_check || '--';
  $('s-errors').innerHTML = s.error_count
    ? '<b style="color:var(--red)">' + s.error_count + '</b>' : '0';
  $('s-version').textContent = s.settings_version;
  knownCustomers = s.customers || [];
  const opts = knownCustomers.map(function (c) {
    return '<option value="' + esc(c.customer_id) + '">' + esc(c.name || '') + '</option>';
  }).join('');
  $('customer-options').innerHTML = opts;
}

async function loadStatus() {
  try { renderStatus(await (await fetch('/api/status')).json()); }
  catch (e) { $('s-alive').innerHTML = '<b style="color:var(--red)">&#9679; OFFLINE</b>'; }
}

async function loadSettings() {
  try {
    const data = await (await fetch('/api/settings')).json();
    if (data.settings) renderSettings(data.settings);
    else $('global-fields').innerHTML = '<div class="empty">' + esc(data.error || 'Unavailable') + '</div>';
  } catch (e) {
    $('global-fields').innerHTML = '<div class="empty">Failed to load settings.</div>';
  }
}

async function saveSettings() {
  const payload = collectSettings();
  $('settings-status').innerHTML = '<span class="muted">Saving...</span>';
  try {
    const resp = await fetch('/api/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data.ok) {
      const errs = (data.errors || ['Save failed']).map(function (e) {
        return '<li>' + esc(e) + '</li>';
      }).join('');
      $('settings-status').innerHTML = '<div class="errmsg"><b>Not saved:</b><ul>' + errs + '</ul></div>';
      return;
    }
    let msg = '<div class="okmsg">Saved - applying within ~1s.</div>';
    if (data.warnings && data.warnings.length) {
      msg += '<div class="warnmsg">' + data.warnings.map(function (w) { return esc(w); }).join('<br>') + '</div>';
    }
    $('settings-status').innerHTML = msg;
    renderSettings(data.settings);
  } catch (e) {
    $('settings-status').innerHTML = '<div class="errmsg">Save failed: ' + esc(e) + '</div>';
  }
}

loadStatus();
loadSettings();
setInterval(loadStatus, REFRESH * 1000);
</script>
</body>
</html>
"""


def render_index(refresh: int) -> str:
    return _INDEX_TEMPLATE.replace("%REFRESH%", str(max(refresh, 1)))


class DashboardHandler(BaseHTTPRequestHandler):
    state: MonitorState
    refresh: int = 5
    bound_host: str = "127.0.0.1"
    bound_port: int = 0
    timeout = 30  # socket read timeout so a stuck client cannot hold a worker forever

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._handle_get()
        except Exception as e:
            logging.exception("Dashboard GET failed")
            self._safe_error(f"internal error: {e}")

    def _handle_get(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            body = render_index(self.refresh).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            _send_security_headers(self)
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/api/status":
            _json_response(self, {"ok": True, **self.state.snapshot()})
        elif parsed.path == "/api/customers":
            _json_response(self, {"ok": True, "customers": self.state.snapshot()["customers"]})
        elif parsed.path == "/api/history":
            query = parse_qs(parsed.query)
            customer_id = query.get("customer_id", [None])[0]
            if customer_id is not None:
                customer_id = customer_id[:64]
            try:
                hours = int(query.get("hours", ["24"])[0])
            except (TypeError, ValueError):
                hours = 24
            hours = max(1, min(hours, 24 * 365))
            try:
                balance = persistence.get_balance_history(customer_id=customer_id, hours=hours)
                margin = persistence.get_margin_history(customer_id=customer_id, hours=hours)
            except Exception as e:
                _json_response(self, {"ok": False, "error": f"history unavailable: {e}"}, 500)
                return
            _json_response(self, {"ok": True, "balance": balance, "margin": margin})
        elif parsed.path == "/api/alerts":
            try:
                alerts = persistence.get_alert_states()
            except Exception as e:
                _json_response(self, {"ok": False, "error": f"alerts unavailable: {e}"}, 500)
                return
            _json_response(self, {"ok": True, "alerts": alerts})
        elif parsed.path == "/api/settings":
            try:
                settings = read_settings()
            except FileNotFoundError:
                _json_response(self, {"ok": False, "error": "settings.toml not found"}, 500)
                return
            except Exception as e:
                _json_response(self, {"ok": False, "error": f"failed to read settings.toml: {e}"}, 500)
                return
            _json_response(self, {"ok": True, "settings": settings.to_dict()})
        else:
            _json_response(self, {"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._handle_post()
        except Exception as e:
            logging.exception("Dashboard POST failed")
            self._safe_error(f"internal error: {e}")

    def _handle_post(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path != "/api/settings":
            _json_response(self, {"ok": False, "error": "not found"}, 404)
            return
        content_type = self.headers.get("Content-Type", "")
        if not content_type.lower().startswith("application/json"):
            _json_response(self, {"ok": False, "errors": ["Content-Type must be application/json"]}, 415)
            return
        if not _same_origin(self):
            _json_response(self, {"ok": False, "errors": ["cross-origin request rejected"]}, 403)
            return
        try:
            payload = _read_json_body(self)
        except ValueError as e:
            _json_response(self, {"ok": False, "errors": [str(e)]}, 400)
            return

        try:
            status, body, settings = _apply_settings(payload)
        except Exception as e:
            _json_response(self, {"ok": False, "errors": [f"internal error: {e}"]}, 500)
            return
        if settings is not None:
            self.state.request_reload()
            body["warnings"] = _settings_warnings(settings, self.bound_host, self.bound_port)
        _json_response(self, body, status)

    def _safe_error(self, message: str) -> None:
        try:
            _json_response(self, {"ok": False, "error": message}, 500)
        except Exception:
            pass

    def log_message(self, format: str, *args: Any) -> None:
        return


class DashboardHandle:
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread

    @property
    def url(self) -> str:
        address = self._server.server_address
        return "http://" + str(address[0]) + ":" + str(address[1])

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def create_dashboard(state: MonitorState, settings: Settings) -> Optional[DashboardHandle]:
    g = settings.global_
    if not g.web_dashboard:
        return None
    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {
            "state": state,
            "refresh": g.web_dashboard_refresh,
            "bound_host": g.web_dashboard_host,
            "bound_port": g.web_dashboard_port,
        },
    )
    try:
        server = ThreadingHTTPServer((g.web_dashboard_host, g.web_dashboard_port), handler)
    except OSError as e:
        logging.warning(f"Dashboard failed to bind {g.web_dashboard_host}:{g.web_dashboard_port}: {e}")
        return None
    setattr(handler, "bound_port", server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, name="dashboard", daemon=True)
    thread.start()
    return DashboardHandle(server, thread)
