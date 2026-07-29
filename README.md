# InstacallMonitor v3.0.0

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
- **Per-customer thresholds** — global defaults + per-customer overrides for balance, margin, and billed min
- **Escalation** — two-tier thresholds (primary + rearm) with persistent alert state across restarts
- **Dual siren patterns** — rising/falling sweep (balance) vs alternating two-tone (margin)
- **Non-blocking alerts** — sirens play in background threads, monitoring continues
- **Desktop notifications** — Windows toast notifications via `plyer`
- **Alert cooldown** — configurable cooldown per customer to prevent alert storms
- **SQLite history** — persistent balance, margin, and alert_state tables with auto-retention
- **CSV export** — export balance/margin history for reporting
- **Crash recovery** — auto-restarts after 10s on unexpected errors
- **DB dedup** — skips duplicate inserts when values haven't changed
- **Priority-aware display** — balance lines `[B]`, margin lines `[M]`, monitored flag

## Usage

```bash
python monitor.py              # start continuous monitoring
python monitor.py --run-once   # one-shot check and exit
python monitor.py --export     # export all history to CSV
python monitor.py --help       # show all options
```

| Flag | Description |
|------|-------------|
| `--run-once` | Run a single monitoring cycle then exit |
| `--export` | Export balance and margin history to CSV files |
| `--quiet` | Suppress non-essential console output |
| `--interval N` | Override check interval (seconds) |
| `--balance-below N` | Override global balance alert threshold |
| `--margin-below N` | Override global margin alert threshold |

## Console Output

```
  InstacallMonitor  2026-07-29 23:45:00
  ──────────────────────────────────────────────────
  1 customers monitored  |  Every 30s
  Margin below 30%  |  Billed above 70 min
    18:  balance below -500.0
  Audio  ON  |  Cooldown 300s  |  DB retention 30d
  Ctrl+C to stop.

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

# Global defaults (applied to ALL customers in summary report)
margin_below = 30.0               # alert when margin drops below this %
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
| `billed_above` | Per-customer override → `[settings]` global default |

## Architecture

```
InstacallMonitor/
  monitor.py           — Main entry point: CLI parsing, monitor loop, export
  config.py            — Settings dataclasses, TOML load/save, validation, profiles
  auth.py              — CSRF login, session creation
  scrapers.py          — HTML parsers + fetch_balance + fetch_summary_report, retry logic
  alerts.py            — SirenManager, desktop notifications, alert escalation
  display.py           — Console formatting, ANSI colors, [B]/[M] prefixes
  persistence.py       — SQLite ORM (balance, margin, alert_state tables), CSV export
  settings.toml        — Runtime configuration (global + per-customer overrides)
  .env                 — Portal credentials (never committed)
  tests/               — 46 pytest tests
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
