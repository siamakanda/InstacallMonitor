# InstacallMonitor v2.0

Real-time balance and margin monitoring for the Instacall Switch Portal. Alerts via desktop notifications, audible sirens, and webhooks when thresholds are breached.

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
python menu.py

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python menu.py
```

## Features

- **Balance monitoring** — polls customer edit pages, alerts when balance drops below threshold
- **Margin & Billed Min monitoring** — scrapes the Executive Summary report for per-customer margin and billed minutes
- **Per-customer thresholds** — global defaults + per-customer overrides for balance, margin, and billed min
- **Escalation** — two-tier thresholds (primary + rearm) with persistent alert state across restarts
- **Async parallel quick checks** — fetch all balances concurrently via aiohttp
- **Dual siren patterns** — rising/falling sweep (balance) vs alternating two-tone (margin)
- **Non-blocking alerts** — sirens play in background threads, monitoring continues
- **Desktop notifications** — Windows toast notifications via `plyer`
- **Webhook alerts** — Telegram and Slack webhook support with HTML formatting
- **Alert cooldown** — configurable cooldown per customer to prevent alert storms
- **SQLite history** — persistent balance, margin, and alert_state tables with auto-retention
- **CSV export** — export balance/margin history for reporting
- **Named profiles** — save/load multiple config profiles
- **Active hours scheduling** — limit monitoring to specific days and time windows
- **Health HTTP endpoint** — Uptime Kuma / Prometheus compatible (`GET /health`)
- **JSON logging** — toggle structured JSON log output
- **Crash recovery** — auto-restarts after 10s on unexpected errors
- **DB dedup** — skips duplicate inserts when values haven't changed
- **Priority-aware display** — balance lines `[B]`, margin lines `[M]`, monitored flag

## Usage

```bash
python menu.py
```

```
  InstacallMonitor  v2.0
  ------------------------------------
  Profile: default
  Monitored: 18
  Interval: 30s  |  Balance alert below -500.0
  Margin alert below 30%  |  Billed min above 70
  Summary: outbound / 5m
  Cooldown: 300s
  Audio: ON  |  Webhooks: none
  ------------------------------------
  0. Quick Check - Parallel (Async)
  1. Start Monitor
  2. Quick Check - Balances
  3. Quick Check - Summary
  4. Quick Check - Full
  5. Settings
  6. Profiles
  7. History
  8. Export
  9. Exit
```

| Option | Description |
|--------|-------------|
| 0. Quick Check - Parallel (Async) | Fetch all balances + summary concurrently with aiohttp |
| 1. Start Monitor | Continuous loop — balancess → summary → repeat |
| 2. Quick Check - Balances | One-shot balance fetch for all monitored IDs |
| 3. Quick Check - Summary | One-shot margin/billed-min for all active customers |
| 4. Quick Check - Full | Balance + summary in a single pass |
| 5. Settings | Edit thresholds, interval, IDs, webhooks, siren, scheduling |
| 6. Profiles | Create, duplicate, delete, switch named config profiles |
| 7. History | View balance and margin history from SQLite DB |
| 8. Export | Export history to CSV files |

## Console Output

```
  Monitor started at 2026-07-22 23:45:00
  IDs: 18
  Interval: 30s
  Balance alert below -500.0  |  Margin alert below 30% & Billed > 70 min
  Summary: outbound / 5m
  Cooldown: 300s
  Audio: ON (quiet mode)
  Webhooks: none
  DB retention: 30 days
  Active hours: 24/7
  Database: instacallmonitor.db
  Ctrl+C to stop.
  ------------------------------
  Running first check now...

  [B] [23:45:01] TestCo (ID: 18)  Balance -229.8235  / Credit: 600.00 (Remaining: 370.18)
  [M] [23:45:02] TestCo (ID: 18)  Margin 52.9%  |  Billed 1167.8 min [MONITORED]

  [23:45:05] Cycle complete. Next check at 23:45:35 (~30s)
```

## Settings

Settings live in `settings.toml` (TOML format). Edit directly or use the TUI menu (option 5).

### Application settings (`[settings]`)

```toml
[settings]
check_interval = 600              # seconds between monitoring cycles
request_timeout = 10              # HTTP request timeout (seconds)
cooldown = 300                    # minimum seconds between alerts per customer
audio = true                      # enable audible siren alerts
summary_direction = "outbound"    # "outbound" or "inbound"
summary_interval = "5m"           # "5m", "10m", "15m", "1h", etc.
summary_show_all = false          # show all customers in summary output
webhook_type = "none"             # "none", "telegram", or "slack"
webhook_url = ""                  # Slack/Telegram webhook URL
telegram_chat_id = ""             # Telegram chat ID
active_hours_start = ""           # HH:MM (empty = 24/7)
active_hours_end = ""
active_days = ""                  # mon,tue,wed,thu,fri (empty = all)
db_retention_days = 30            # auto-purge (0 = keep forever)
health_port = 0                   # 0 = disabled, e.g. 8081 for Uptime Kuma

# Global defaults (applied to ALL customers in summary report)
margin_below = 30.0               # alert when margin drops below this %
margin_critical = 25.0            # escalation at lower threshold
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
margin_below = 25.0               # override global margin for this customer
# billed_above = 100.0            # override global billed-min for this customer
```

### How overrides resolve

| Field | Priority |
|---|---|
| `balance_below` | **Per-customer only** — no global default, must be set in `[[watch]]` |
| `balance_critical` | Per-customer → default is same as that customer's `balance_below` (no escalation) |  
| `margin_below` | Per-customer override → `[settings]` global default |
| `margin_critical` | Per-customer override → `[settings]` global default |
| `billed_above` | Per-customer override → `[settings]` global default |

## Architecture

```
InstacallMonitor/
  menu.py              — Single CLI entry point (interactive menu)
  config.py            — Settings dataclasses, TOML load/save, validation, profiles
  auth.py              — CSRF login, session creation, re-auth helper
  scrapers.py          — Shared HTML parser + fetch_balance + fetch_summary_report
  monitor.py           — Continuous loop with scheduling, per-customer thresholds, crash recovery
  quick.py             — One-shot checks (sync + async parallel with retry)
  alerts.py            — SirenManager, AlertStateManager, notifications, trigger functions
  display.py           — Console formatting ([B]/[M] prefixes)
  retry.py             — Retry with 2s/5s backoff on transient failures
  persistence.py       — SQLite ORM (balance, margin, alert_state tables)
  notifications.py     — Telegram/Slack webhook sender
  export.py            — CSV export for balance/margin history
  health.py            — HTTP health endpoint (/health returns monitor.status)
  settings.toml        — Runtime configuration (global + per-customer overrides)
  profiles.json        — Named config profiles
  .env                 — Portal credentials (never committed)
  tests/               — 43 pytest tests
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
- Windows for `winsound` audio (sirens silently skipped on macOS/Linux)
- Portal credentials in `.env`
