# InstacallMonitor v3.0.0

[![CI](https://github.com/siamakanda/InstacallMonitor/actions/workflows/ci.yml/badge.svg)](https://github.com/siamakanda/InstacallMonitor/actions/workflows/ci.yml)

Real-time balance and margin monitoring for the Instacall Switch Portal. Alerts via desktop notifications and audible sirens when thresholds are breached.

## Quick Start (Any Device)

```bash
# 1. Clone the repo
git clone https://github.com/siamakanda/InstacallMonitor.git
cd InstacallMonitor

# 2. Create your credentials file
cp .env.example .env
# Edit .env and add your portal credentials:
#   PORTAL_USERNAME="your_username"
#   PORTAL_PASSWORD="your_password"

# 3. One-click setup & run (Windows)
setup_and_run.bat
```

Or manually:

```bash
# Windows
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python monitor.py

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python monitor.py
```

## Features

- **Balance monitoring** — polls customer edit pages, alerts when balance drops below threshold
- **Margin & Billed Min monitoring** — scrapes the Executive Summary report for per-customer margin and billed minutes
- **Margin range with deadband** — alerts fire when margin is outside a normal range (e.g. 30%–75%). A deadband zone prevents alert/recovery flapping near thresholds.
- **Direction-aware margin alerts** — distinct siren patterns: alternating two-tone for margin too low, steady pulse for margin too high
- **Per-customer thresholds** — global defaults + per-customer overrides for balance, margin range, deadband, and billed min
- **Escalation** — two-tier thresholds with persistent alert state across restarts, auto-recovery when values return to range
- **Triple siren patterns** — rising/falling sweep (balance), alternating two-tone (margin low), steady pulse (margin high)
- **Non-blocking alerts** — sirens play in background threads, monitoring continues
- **Desktop notifications** — Windows toast notifications via `plyer`
- **Web dashboard** — built-in local HTTP settings page (stdlib only) that mirrors the TUI config, with add/remove editing of balance-check customers
- **Cross-platform sirens** — `winsound` on Windows, generated WAV playback (`afplay`/`aplay`/`paplay`/`ffplay`) on macOS/Linux
- **Alert cooldown** — configurable cooldown per customer to prevent alert storms
- **SQLite history** — persistent balance, margin, and alert_state tables with auto-retention
- **CSV export** — export balance/margin history for reporting
- **Circuit breaker** — automatic circuit breaking for balance and summary report endpoints to avoid hammering the server on failures
- **Retry with backoff** — configurable exponential backoff with jitter for transient errors
- **Hot-reload** — settings reloaded from `settings.toml` without restart
- **DB dedup** — skips duplicate inserts when values haven't changed
- **Priority-aware display** — balance lines `[B]`, margin lines `[M]`, monitored flag, color-coded by threshold range

## Usage

```bash
python monitor.py              # start continuous monitoring
python monitor.py --run-once   # one-shot check and exit
python monitor.py --export     # export all history to CSV
python monitor.py --help       # show all options
```

Installing the project (`pip install .`) also exposes the same interface as the
`instacallmonitor` command, and `pip install -e ".[dev]"` pulls in the test/lint/type tools.

| Flag | Description |
|------|-------------|
| `--run-once` | Run a single monitoring cycle then exit |
| `--export` | Export balance and margin history to CSV files |
| `--quiet` | Suppress non-essential console output |
| `--interval N` | Override check interval (seconds) |
| `--balance-below N` | Override global balance alert threshold |
| `--margin-below N` | Override global margin lower threshold |
| `--margin-above N` | Override global margin upper threshold |
| `--margin-deadband N` | Override margin recovery deadband (%) |
| `--billed-above N` | Override billed minutes threshold |
| `--cooldown N` | Override alert cooldown (seconds) |
| `--export-customer ID` | Export history for a specific customer |
| `--no-hot-reload` | Disable settings hot-reload |
| `--no-web` | Disable the web dashboard |
| `--web-port N` | Override the dashboard port |

## Web Dashboard

A dependency-free dashboard (Python stdlib only) starts automatically alongside the monitor. It binds to
`127.0.0.1:8765` and has two tabs:

- **Settings** — a status strip (alive, uptime, cycles, errors, settings version), a config summary, an
  editor for every global setting, and the balance-check customer table.
- **Live** — current balances/margins per customer, a history view (customer + time window) backed by the
  SQLite history, and the alert-state table.

```
Dashboard  http://127.0.0.1:8765
```

Configure or disable it in `settings.toml` (`web_dashboard`, `web_dashboard_host`, `web_dashboard_port`,
`web_dashboard_refresh`) or via `--no-web` / `--web-port N`. It is **loopback-only**: a non-loopback
`web_dashboard_host` (e.g. `0.0.0.0`) is rejected at startup and falls back to `127.0.0.1`. The dashboard
has no authentication, so do not put it behind a public proxy.

### Editing settings

Everything is edited in place:

- **Settings** — every global value, grouped (General, Thresholds, Sirens, Resilience, Dashboard). Invalid
  values are underlined with the specific error shown under the field.
- **Balance-check customers** — an input + dropdown table. Pick a known customer from the list or type an
  ID, set `balance_below` (required) and any per-customer overrides, then add or remove rows. Override
  inputs show the global default as a placeholder and are marked when overridden. Removing a row asks for
  confirmation; Add rejects empty or duplicate customer IDs.
- **Unsaved-changes guard** — Save stays disabled until something changes, and the browser warns before
  leaving with unsaved edits.

Saving writes `settings.toml` (keeping a `settings.toml.bak` copy and the file's comments), validates the
values first, and wakes the monitor so hot-reload applies them within about a second — no restart needed,
except for a changed dashboard host/port (shown as a warning).

Writes go through `POST /api/settings`, which requires a JSON content type, a same-origin request, and the
page's CSRF token (`X-CSRF-Token`), so another web page in your browser cannot silently rewrite your config.

The JSON endpoints it serves can also be scraped directly: `/api/status`, `/api/customers`,
`/api/history?customer_id=ID&hours=24&limit=500`, `/api/alerts`, and `GET`/`POST /api/settings`.
`GET /api/settings` returns `{"ok": true, "settings": {…global…}, "watch": […]}`.

## Console Output

```
  InstacallMonitor  2026-08-01 23:45:00
  ──────────────────────────────────────────────────
  1 customers monitored  |  Every 30s
  Margin range 30%–75%  |  Deadband 3%  |  Billed above 70 min
    18:  balance below -500.0
  Audio  ON  |  Cooldown 300s  |  DB retention 30d
  Workers 4  |  Hot-reload ON
  Ctrl+C to stop.

  Dashboard  http://127.0.0.1:8765

  [23:45:01] [B] TestCo (ID: 18)  Balance -229.8235  / Credit: 600.00
  [23:45:02] [M] TestCo (ID: 18)  Margin 52.9%  |  Billed 1167.8 min [MONITORED]

  [23:45:05] Cycle complete. Next check at 23:45:35 (~30s)
```

## Settings

Settings live in `settings.toml` (TOML format). Edit directly.

### Application settings (`[settings]`)

```toml
[settings]
check_interval = 600              # seconds between monitoring cycles
request_timeout = 10              # HTTP request timeout (seconds)
cooldown = 300                    # minimum seconds between alerts per customer
audio = true                      # enable audible siren alerts
summary_direction = "outbound"    # "outbound" or "inbound"
summary_interval = "5m"           # "5m", "10m", "15m", "1h", etc.
db_retention_days = 30            # auto-purge (0 = keep forever)
siren_loops = 10                  # siren repetition count
siren_min_freq = 2200             # lowest siren frequency (Hz)
siren_max_freq = 3500             # highest siren frequency (Hz)
siren_step_freq = 130             # frequency step between beeps (Hz)
siren_tone_duration = 50          # duration of each beep (ms)

# Web dashboard (stdlib HTTP server, localhost by default)
web_dashboard = true              # enable the built-in dashboard
web_dashboard_host = "127.0.0.1"  # bind address
web_dashboard_port = 8765         # dashboard port
web_dashboard_refresh = 5         # browser auto-refresh interval (seconds)

# Global defaults (applied to ALL customers in summary report)
margin_below = 30.0               # alert when margin drops below this %
margin_above = 75.0               # alert when margin surges above this %
margin_deadband = 3.0             # must recover this far inside range before clearing alert
billed_above = 70.0               # only alert if billed minutes exceed this
```

### Monitored customers (`[[watch]]`)

Each block specifies a customer with its own balance threshold. All other fields are optional global overrides.

```toml
[[watch]]
customer = "18"
name = "ACME Corp"
balance_below = -500.0            # REQUIRED — balance alert for this customer
# balance_critical = -600.0       # optional escalation

[[watch]]
customer = "42"
name = "GlobalTel"
balance_below = -200.0            # different threshold per customer
margin_below = 25.0               # override global margin lower bound
margin_above = 80.0               # override global margin upper bound
margin_deadband = 5.0             # override global margin deadband
# billed_above = 100.0            # override global billed-min for this customer
```

### How overrides resolve

| Field | Priority |
|---|---|---|
| `balance_below` | **Per-customer only** — no global default, must be set in `[[watch]]` |
| `margin_below` | Per-customer override → `[settings]` global default (30%) |
| `margin_above` | Per-customer override → `[settings]` global default (75%) |
| `margin_deadband` | Per-customer override → `[settings]` global default (3%) |
| `billed_above` | Per-customer override → `[settings]` global default (70 min) |

## Architecture

```
InstacallMonitor/
  monitor.py            — Thin entrypoint (delegates to app.main)
  app.py                — Orchestration: main(), monitor loop, run-once/export
  cycles.py             — Per-cycle processing: persist results, alerts, dashboard state
  reporting.py          — Console output: banner, session summary, dashboard URL, CSV export
  cli.py                — Argument parsing, help text, CLI overrides
  runtime.py            — Shared Runtime state (shutdown event, counters, health snapshot)
  health.py             — monitor.status write/read, --status and --tail rendering
  config.py             — Settings facade: paths, TOML load/save/reload (re-exports below)
  settings_models.py    — Settings dataclasses + value coercion
  settings_validation.py— Settings validation
  credentials.py        — .env credential loading
  settings_diff.py      — Settings change detection for hot-reload
  auth.py               — CSRF login, session creation
  scrapers.py           — Scraper facade (re-exports the client and parser)
  scraper_client.py     — HTTP fetch layer with circuit breakers + retry policy
  scraper_parser.py     — Pure HTML parsers
  alerts.py             — Alert evaluation, escalation, deadband logic
  siren.py              — Siren manager (cooldown + playback)
  notify.py             — Desktop notifications
  audio.py              — Tone-sequence model + cached WAV rendering + cross-platform playback
  webapp.py             — Stdlib HTTP dashboard: live state, JSON API, settings editor
  dashboard_ui.py       — The dashboard page (HTML/CSS/JS, stdlib-only)
  display.py            — Console formatting, ANSI colors, [B]/[M] prefixes
  persistence.py        — SQLite ORM (balance, margin, alert_state tables), CSV export
  circuit_breaker.py    — Circuit breaker pattern for resilient HTTP requests
  settings.toml         — Runtime configuration (global + per-customer overrides)
  .env                  — Portal credentials (never committed)
  tests/                — 210 pytest tests
```

## Files (Runtime)

| File | Purpose |
|------|---------|
| `balance_monitor.log` | Rotating log (5 files x 1 MB) |
| `monitor.status` | JSON health status (alive, last_check, error_count) |
| `instacallmonitor.db` | SQLite database — balance + margin + alert_state history |
| `instacallmonitor.db-shm` | SQLite WAL shared memory |
| `instacallmonitor.db-wal` | SQLite WAL journal |

## Requirements

- Python 3.10+
- Audio: `winsound` on Windows; on macOS/Linux the siren is rendered to WAV and played via the first of
  `afplay`, `aplay`, `paplay` or `ffplay` found on `PATH` (falls back to the terminal bell if none exist)
- Windows toast notifications via `plyer` (notifications elsewhere depend on `plyer` backends)
- Portal credentials in `.env`
