# InstacallMonitor — Improvement Plan

Phased plan: **fix correctness/reliability bugs first**, then structural/modularity work,
then packaging & CI/tooling. Each phase is independently shippable and ends with a green
`pytest` / `ruff` / `mypy` run. All refactors are behavior-preserving; every bug fix gets a
regression test.

Baseline to preserve: 130 tests passing, `ruff check .` clean, `mypy .` clean.

---

## Status

- **Phase 1 — Correctness & Reliability: DONE.** All 12 items implemented with regression
  tests.
- **Phase 2 — Validation & Robustness Hardening: DONE.** Validation bounds/format/host checks,
  duplicate-id detection, parser numeric/range hardening + drop logging + duplicate handling,
  web-dashboard security headers/input clamping/origin check/error envelopes, CLI warnings,
  credentials caching, notify delivery reporting.
- **Phase 3 — Structure & Modularity: DONE.** Presentation/IO split into `reporting.py`;
  per-cycle processing split into `cycles.py` (`process_balances`/`process_summary`), shrinking
  `app.py` from 589 → 428 lines; single `Args` alias; settings hot-reload locked and
  de-duplicated (diff computed once in `app._try_hot_reload`); canonical `Settings` import
  surface (`settings_models`); persistence query/export helpers; `display` isatty gating +
  explicit `init()`; `Runtime` locked accessors; dead code removed (`SettingsDiff.empty`);
  `DashboardHandle.stop()` now joins its thread.
- **Phase 4 — Packaging & DevOps: DONE.** `pyproject` build-system + `py-modules` + console
  script (`instacallmonitor`), pyproject metadata/urls, dev extras with `pytest-cov`; CI
  rewritten (editable install of `.[dev]`, Python 3.10/3.12/3.13 + Windows, `ruff format
  --check`, coverage artifact, packaging smoke test, concurrency/cache); `.gitignore` coverage
  + `.ruff_cache`; README CI badge; whole codebase formatted; shared `temp_db` fixture; new
  `ScraperClient` retry tests.

Test suite: 130 → 180 tests, `ruff check .` / `ruff format --check .` / `mypy .` clean, editable
install + `instacallmonitor` console script verified, coverage config valid (66% total).

---

## Follow-up — core coverage + settings safety (done)

- **Orchestration now tested** (`tests/test_app_loop.py`): `monitor_loop` full-cycle (persist,
  dedup, dashboard state, teardown), balance alert fire/recover, setup-failure resource cleanup,
  and `run_once` (success / summary error / login failure). Added `check_balance_alert` cases and
  a `main()` exit test. Coverage **66% → ~77%**; CI gained `--cov-fail-under=72`. Wall-clock tests
  marked `@pytest.mark.slow`.
- **No more silent settings fallback**: `config.load_settings` now raises `SettingsError` on a
  corrupt `settings.toml` instead of quietly watching a hardcoded `customer="18"`; `app.main`
  catches it and exits `1` with a clear message. `_parse_settings_file` no longer injects a default
  watch for files with no `[[watch]]` block (validation reports it instead).

Test suite: → 195 tests.

Phase 1 changes landed in: `settings_models` coercion / `fmt_val`, `config.save_settings`
escaping, `circuit_breaker` half-open self-heal, `scraper_client` breaker accounting + retry
delay + re-login lock + URL-encoded ids, `auth.perform_login` verification, `audio` bounded
playback, `alerts` stale-state clearing, `persistence` busy-timeout + schema, `health`
atomic status write, `app.monitor_loop`/`run_once` resource management.

Phase 2 changes landed in: `settings_validation` (bounds, `summary_interval` format, host
charset, nan/inf rejection, duplicate ids, de-duplicated margin-range helper),
`settings_models.is_finite_number`, `scraper_parser` (single `_parse_number`, range checks,
drop logging, duplicate-id handling, no silent `N/A`), `scraper_client` exception-type
transience, `webapp` (security headers, handler timeout, `ok` envelope, hours clamp,
DB-error JSON, bound-host origin check, no silent `""` drop, broad error wrapping),
`cli` (unknown/missing-flag warnings), `credentials` (cached dotenv + message), `notify`
(returns bool; `alerts` counts only on delivery).

---

## Phase 1 — Correctness & Reliability (highest priority) — DONE

These are real defects that can silently stop monitoring, corrupt config, or latch alerting off.

### 1.1 Circuit breaker can latch permanently in `HALF_OPEN`
- **Files:** `circuit_breaker.py` (`before_request` L45–54, `on_failure`/`on_success`), `scraper_client.py` (`fetch_balance` L64–104, `fetch_summary_report` L123–188).
- **Bug:** while `HALF_OPEN`, `before_request()` returns `False`; the only exits are `on_success`/`on_failure`. But `fetch_balance` L81–89 returns on any non-200 `< 500` (e.g. 403/404) *without* recording an outcome, so the breaker is stuck `HALF_OPEN` forever → every later request is rejected `"circuit open"`.
- **Fix:**
  - In `scraper_client`, guarantee **every** `before_request() -> True` path ends in exactly one terminal call. Treat non-2xx as `on_failure()` (client errors are failures for breaker purposes).
  - Add a self-heal in `before_request`: track when the half-open probe started; if no outcome within `recovery_timeout`, allow another probe instead of returning `False` in perpetuity.
  - Track an explicit `_half_open_in_flight` flag so a second concurrent probe is rejected (still exactly one probe).
  - `is_open` should report `True` for `HALF_OPEN` too (currently misreports health).
  - Use `time.monotonic()` instead of `time.time()` for recovery timing.
- **Tests:** new `tests/test_circuit_breaker.py` cases: half-open probe → non-200 < 500 → next call still allowed; recovery after timeout; concurrent probe rejected.

### 1.2 `settings.toml` written unescaped (corruption / injection)
- **File:** `config.py` `save_settings` L270–291.
- **Bug:** `f'{f.name} = "{val}"'` and `f'customer = "{w.customer}"'` write strings verbatim — a value with `"` or newline (settable via the web editor for `web_dashboard_host`, `summary_direction`, `summary_interval`, watch `name`/`customer`) corrupts the file or injects keys.
- **Fix:** add a `_toml_str(s)` helper that escapes `\`, `"`, `\n`, `\r`, `\t` (or emit via `json.dumps`, valid for TOML basic strings) and use it for **all** string values. Reject control chars in `settings_validation.validate_settings`.
- **Tests:** round-trip a hostile value through `save_settings` → `read_settings`; assert unchanged.

### 1.3 Global settings coercion is a no-op
- **File:** `settings_models.py` `_coerce` L9–33, `GlobalSettings.from_dict` L70–78.
- **Bug:** `from __future__ import annotations` makes `f.type` the **string** `"int"`, so `target is int` never matches → `[settings]` values are never coerced. Only `WatchTarget` (using `Optional[float]`) coerces.
- **Fix:** resolve real types with `typing.get_type_hints(GlobalSettings)` (or a name→type map) before calling `_coerce`.
- **Tests:** `GlobalSettings.from_dict({"check_interval": "30", "audio": "false"})` → `int`, `bool`.

### 1.4 Siren can latch "playing" forever → all future alerts suppressed
- **File:** `audio.py` `_play_posix` L107–131.
- **Bug:** `subprocess.run(...)` has **no `timeout=`**; a hung player blocks the alert thread, so `SirenManager.release()` never runs and `_playing` stays `True`. Also `os.fdopen` failure leaks the raw fd.
- **Fix:** add `timeout=` to `subprocess.run` (e.g. derived from sequence length), kill on timeout, wrap in `try/except` so `play()` **never raises**; close the fd safely (write via `os.fdopen` inside the try, `os.close(fd)` on failure). Move the `\a` fallback to log a warning when stdout is not a TTY.
- **Tests:** monkeypatch `subprocess.run` to raise/hang → `play()` returns without raising.

### 1.5 Login correctness & concurrent re-login race
- **Files:** `auth.py` `perform_login` L15–38; `scraper_client.py` L71–80, L149–158.
- **Bugs:** (a) bare `status_code in (200, 302)` marks a failed login redirect (`→ /login`) as success; missing CSRF token silently posts `""`. (b) Multiple worker threads share one `requests.Session` and can call `perform_login` concurrently, clobbering cookies.
- **Fix:**
  - `auth.perform_login`: check the GET status; abort if no `_csrf` token; verify success by confirming the follow-up page is **not** the login form (not by bare 302). Use `logging.exception` (no f-string).
  - Add a module-level `threading.Lock` (or a session wrapper) around the re-login block so one worker re-logs in while others wait and retry.
- **Tests:** mock responses for success, 302-to-login (must be failure), missing CSRF; concurrent re-login issues exactly one login.

### 1.6 Resource leaks & dead code in the orchestration
- **File:** `app.py`.
- **Bugs:** `session`/`executor`/`mgr`/`state`/`dashboard` are created **before** the `try` (L285–315) → leak if `SirenManager`/`create_dashboard`/banner raise; `run_once` (L154–209) has no `try/finally` to close the session; `run_export(settings, ...)` L146 has an unused param; the `except KeyboardInterrupt` (L492–495) is unreachable once the SIGINT handler is installed (L562).
- **Fix:** move resource creation inside the `try` (or wrap in `try/finally`); `run_once` uses `try/finally`; drop the unused param and the dead `KeyboardInterrupt` branch (reconcile the misleading comment); add a `SIGTERM` handler alongside SIGINT.
- **Tests:** `monitor_loop` closes the session when a pre-loop step raises (use monkeypatch to force `create_dashboard` to raise).

### 1.7 Empty summary report treated as success
- **Files:** `scraper_client.py` L180–188, `scraper_parser.py` `parse_summary_page` L137–205.
- **Bug:** `parse_summary_page` returns `None` only when the panel/tbody is missing; a present-but-empty table returns `{}`, which the client reports as **success** ("0 customers found") — indistinguishable from "parsed nothing".
- **Fix:** distinguish the two: keep `None` for "page shape not recognized", `{}` for a genuinely empty table; in the client treat `None` as a parse error (with breaker/retry accounting) and `{}` as a non-fatal warning. Make breaker accounting consistent across both fetch methods.
- **Tests:** present-but-empty vs missing-panel cases.

### 1.8 Stale alert state when a customer is removed from `[[watch]]`
- **File:** `alerts.py` `check_margin_alert` (early `return` when `not monitored`, L185–186).
- **Bug:** removing a customer mid-run leaves `state=1` in the DB forever; re-adding resumes escalation from the stale count.
- **Fix:** when `not monitored`, clear the alert state (set `state=0`) instead of an early return.
- **Tests:** unmonitored customer with prior `state=1` → state cleared.

### 1.9 Retry burst & inconsistent breaker accounting
- **File:** `scraper_client.py` `fetch_balance` L100–102, parse-exception handling L96–98/L170–178.
- **Bugs:** the loop `continue`s after `on_failure()` even when the error is **non-transient**, producing back-to-back retries with **no delay**; parse exceptions in `fetch_balance` trip the network breaker, while in `fetch_summary_report` they don't.
- **Fix:** always sleep (transient → configured backoff; non-transient → short fixed delay), and separate "fetch failure" from "parse failure" breaker accounting consistently in both methods.
- **Tests:** non-transient error still waits; parse error does not count as a network breaker failure.

### 1.10 SQLite concurrency & schema
- **File:** `persistence.py` (`_get_connection` L78–86, `init_db` L89, migrations L94).
- **Bugs:** no `PRAGMA busy_timeout` → `database is locked` when the loop writes while the dashboard reads; `alert_state` table is declared **without** `direction` and relies on a migration that swallows all `OperationalError` (a lock during migration silently leaves the column missing).
- **Fix:** set `PRAGMA busy_timeout` (e.g. 5000 ms) on every connection; add `direction` to `CREATE TABLE`; only swallow the "duplicate column" `OperationalError` in migrations.
- **Tests:** `init_db` on a fresh DB yields the `direction` column without needing the migration.

### 1.11 Atomic status write + resilient rendering
- **File:** `health.py`.
- **Bugs:** `write_status` writes non-atomically with `except OSError: pass` (invisible failures, readers can see partial JSON); `render_status` formats `uptime_seconds` unguarded (`TypeError` on bad value); `render_tail` races `exists`→`getsize`.
- **Fix:** write to a temp file + `os.replace`; log the error instead of swallowing; guard numeric formats; re-check existence inside the try; source `STATUS_FILE` from `config`.
- **Tests:** corrupt/partial status file → `render_status` does not raise.

### 1.12 `fmt_val` crashes on `nan`/`inf`
- **File:** `settings_models.py` `fmt_val` L159–164.
- **Fix:** guard non-finite floats (fall back to `str(val)`), and treat `None` correctly for the watch default (`-500.0`) context.
- **Tests:** `fmt_val(float("nan"))`, `fmt_val(float("inf"))`, `fmt_val(None)`.

---

## Phase 2 — Validation & Robustness Hardening

### 2.1 Settings validation (`settings_validation.py`)
- Add upper/lower bounds: `check_interval`, `request_timeout`, `circuit_recovery_timeout`, `web_dashboard_refresh`; validate `summary_interval` format (`^\d+[smh]$`-style); validate `web_dashboard_host` charset (reject control chars); reject `nan`/`inf` for all numeric fields; validate `retry_base_delay >= 0`; detect **duplicate `customer` IDs** in `watch`.
- De-duplicate the margin-range/deadband logic shared between global and per-watch checks.

### 2.2 Parser robustness (`scraper_parser.py`)
- One shared numeric helper (drop the `.replace("s", "")` hack; handle `,`, `$`, `\xa0`, parentheses-negatives, unicode minus) used by both `balance` and `credit_limit`.
- Validate ranges (margin 0–100, billed ≥ 0, credit ≥ 0); log rows that are dropped (short rows, missing expand button, unparseable `onclick` ID); de-duplicate customer IDs (merge/log instead of silent overwrite); stop using `"N/A"` as a silent name fallback (log it).

### 2.3 Request-layer hardening (`scraper_client.py`)
- URL-encode `customer_id` (and validate it) before building `edit_url`. **(done in Phase 1)**
- Re-check `_needs_relogin` on the post-login refetch; validate `retry_max_attempts` is a sane int. **(re-check done in Phase 1)**
- Classify transient errors by **exception type/category**, not substring matching on messages.

### 2.4 Web dashboard (`webapp.py`)
- Clamp `hours` (and cap `customer_id` length) on `/api/history`; add a request/socket timeout to `DashboardHandler`; add basic security headers; unify the JSON response envelope (`{ok,...}` everywhere); wrap `persistence` calls in handlers so DB errors return JSON, not a 500 traceback; make the CSRF/origin check compare against the **bound** host, not the client `Host` header; allow clearing text fields (stop silently dropping `""`).
- Canonicalize the `GET /api/settings` shape (drop the nested double `settings` if front-end can be updated, or keep shape but document).

### 2.5 CLI, credentials, notify
- `cli.py`: warn on unknown flags and on a value flag missing its value (don't silently drop); return an exit code.
- `credentials.py`: cache `load_dotenv` (load once, project-root-anchored), fix the error message (`PORTAL_PASSWORD`), return a named type.
- `notify.py`: return a success bool; `alerts.py` should only increment session-alert counts on actual delivery.

---

## Phase 3 — Structure & Modularity (behavior-preserving)

- **Split `app.py` (575 lines):** extract a shared `process_cycle(...)` used by both `run_once` and `monitor_loop` (removes the near-duplicated insert/alert/state block); move export/summary/banner helpers into focused modules (`reporting.py` / `statusline.py`) with thin re-exports so the current import surface still works.
- **Unify settings hot-reload:** one `settings_store` with a module-level lock guarding `_settings_mtime`/`_settings_version`; compute the diff **once** (remove the duplicate diff+log in `app._try_hot_reload` vs `config.reload_settings`).
- **Single canonical import surface:** import `Settings`/models from `settings_models`; keep `config` re-exports only for back-compat.
- **Remove dead code:** `SettingsDiff.empty`, `MonitorState.reload_requested` (or keep + use it), `DashboardHandle._thread` (join on `stop()`), the ignored `_sleep_interruptible` return, the duplicated `_ARGS` alias, `_coerce`'s dead bool branch.
- **`persistence.py`:** collapse the duplicated balance/margin query + export functions behind a small shared helper; return explicit column mappings instead of `SELECT *` + positional `zip`.
- **`display.py`:** gate ANSI on `sys.stdout.isatty()`; move the import-time `os.system("")` / stdio reconfig into an explicit init call.
- **`Runtime`:** route all counter/state reads through locked accessors; exclude `Event`/`Lock` from dataclass `eq`/`repr`.

---

## Phase 4 — Packaging & DevOps

### 4.1 `pyproject.toml`
- Add `[build-system]` (setuptools or hatchling) and package discovery for the flat modules.
- Add `[project.scripts]` → `instacallmonitor = "app:main"`.
- Add `readme`, `license`, `authors`, `classifiers`, `keywords`, `urls`.
- Keep `[project.optional-dependencies].dev` as the single source of dev deps.

### 4.2 CI (`.github/workflows/ci.yml`)
- Install via `pip install -e ".[dev]"` (remove the hand-written package list).
- Extend the matrix to **3.10 / 3.12 / 3.13** and add a **`windows-latest`** job (exercises `winsound`, `.bat`, Windows SQLite locking).
- Add `pytest --cov` + upload coverage; add `ruff format --check`; add `concurrency` cancel-in-progress; add `actions/cache`.
- Add a packaging smoke step (`pip install .` then `instacallmonitor --help`).

### 4.3 Repo hygiene
- `.gitignore`: add `.ruff_cache/`; confirm runtime artifacts (`balance_monitor.log`, `monitor.status`, `instacallmonitor.db*`, `settings.toml*`) stay ignored.
- Add README CI/coverage badges.
- Add a shared `tests/conftest.py` `temp_db` fixture to remove the two divergent copies.
- Add `pytest` `addopts`/markers (slow/integration) so timing-sensitive tests can be marked.
- Optional: `.pre-commit-config.yaml` (ruff + mypy).

### 4.4 Close test gaps
- Add tests for currently-untested modules: `auth` (done in Phase 1), `credentials`, `notify`, `settings_diff`, `ScraperClient` retry loop, and the extracted `app.process_cycle`.

---

## Files to change (summary)

| Area | Files |
|---|---|
| Bugs | `circuit_breaker.py`, `scraper_client.py`, `config.py`, `settings_models.py`, `audio.py`, `auth.py`, `alerts.py`, `persistence.py`, `health.py`, `app.py` |
| Hardening | `settings_validation.py`, `scraper_parser.py`, `webapp.py`, `cli.py`, `credentials.py`, `notify.py` |
| Structure | `app.py` (+ new `reporting.py`/`statusline.py`/`settings_store.py`), `persistence.py`, `display.py`, `runtime.py`, `settings_diff.py` |
| Tooling | `pyproject.toml`, `.github/workflows/ci.yml`, `.gitignore`, `README.md`, `tests/conftest.py`, new test files |

---

## Verification

1. **Automated (per phase):** `pytest -q`, `ruff check .`, `ruff format --check`, `mypy .` all green; new regression tests added alongside each fix.
2. **Phase 1 targeted checks:**
   - Breaker: unit test forces `HALF_OPEN` → non-200 `< 500` → assert a later call still reaches the network.
   - TOML: round-trip a value containing `"` and `\n` through save/read.
   - Coercion: string→int/bool for `[settings]`.
   - Siren: `play()` with a raising/timing-out player returns without raising and releases the siren flag.
   - Login: 302-to-login is detected as failure; concurrent re-login issues exactly one POST.
3. **Manual smoke:** `python monitor.py --run-once` and `--status`/`--tail` against a local mock (do not hit the real portal); confirm the dashboard at `127.0.0.1:8765` still serves status/settings and hot-reload applies an edit within ~1s.
4. **Phase 4:** CI green on 3.10/3.12/3.13 + Windows; `pip install -e ".[dev]"` then `instacallmonitor --help` works; coverage artifact produced.
