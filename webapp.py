from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import persistence
from config import read_settings, save_settings
from dashboard_ui import render_page
from settings_models import Settings, is_loopback_host
from settings_validation import validate_settings_detailed


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


def _valid_csrf(handler: BaseHTTPRequestHandler) -> bool:
    expected = getattr(handler, "csrf_token", "")
    supplied = handler.headers.get("X-CSRF-Token", "")
    return bool(expected) and secrets.compare_digest(expected, supplied)


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

    field_errors = validate_settings_detailed(settings)
    if field_errors:
        return (
            400,
            {
                "ok": False,
                "errors": [e.message for e in field_errors],
                "field_errors": [
                    {"field": e.field, "customer": e.customer, "message": e.message} for e in field_errors
                ],
            },
            None,
        )

    try:
        save_settings(settings)
    except OSError as e:
        return 500, {"ok": False, "errors": [f"failed to write settings.toml: {e}"]}, None

    data = settings.to_dict()
    return 200, {"ok": True, "settings": data["settings"], "watch": data["watch"]}, settings


def _settings_warnings(settings: Settings, bound_host: str, bound_port: int) -> list[str]:
    g = settings.global_
    warnings: list[str] = []
    if (g.web_dashboard_host, g.web_dashboard_port) != (bound_host, bound_port):
        warnings.append("Dashboard host/port change takes effect after restart.")
    if not g.web_dashboard:
        warnings.append("Dashboard disabled; the current server keeps running until restart.")
    return warnings


class DashboardHandler(BaseHTTPRequestHandler):
    state: MonitorState
    refresh: int = 5
    bound_host: str = "127.0.0.1"
    bound_port: int = 0
    csrf_token: str = ""
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
            body = render_page(self.refresh, self.csrf_token).encode("utf-8")
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
                limit = int(query.get("limit", ["500"])[0])
            except (TypeError, ValueError):
                limit = 500
            limit = max(1, min(limit, 5000))
            try:
                balance = persistence.get_balance_history(customer_id=customer_id, hours=hours, limit=limit)
                margin = persistence.get_margin_history(customer_id=customer_id, hours=hours, limit=limit)
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
            data = settings.to_dict()
            _json_response(self, {"ok": True, "settings": data["settings"], "watch": data["watch"]})
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
        if not _valid_csrf(self):
            _json_response(self, {"ok": False, "errors": ["invalid or missing CSRF token"]}, 403)
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
    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread, csrf_token: str) -> None:
        self._server = server
        self._thread = thread
        self.csrf_token = csrf_token

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

    host = g.web_dashboard_host
    if not is_loopback_host(host):
        # The dashboard has no authentication: never expose it beyond this host.
        logging.warning("Dashboard host %r is not a loopback address; binding to 127.0.0.1 instead.", host)
        host = "127.0.0.1"

    csrf_token = secrets.token_urlsafe(32)
    handler = type(
        "BoundDashboardHandler",
        (DashboardHandler,),
        {
            "state": state,
            "refresh": g.web_dashboard_refresh,
            "bound_host": host,
            "bound_port": g.web_dashboard_port,
            "csrf_token": csrf_token,
        },
    )
    try:
        server = ThreadingHTTPServer((host, g.web_dashboard_port), handler)
    except OSError as e:
        logging.warning(f"Dashboard failed to bind {host}:{g.web_dashboard_port}: {e}")
        return None
    setattr(handler, "bound_port", server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, name="dashboard", daemon=True)
    thread.start()
    return DashboardHandle(server, thread, csrf_token)
