from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from config import STATUS_FILE


class HealthHandler(BaseHTTPRequestHandler):
    status_file: str = STATUS_FILE

    def log_message(self, format: str, *args: object) -> None:
        logging.debug(f"Health endpoint - {args[0]}")

    def do_GET(self) -> None:
        if self.path not in ("/", "/health"):
            self.send_response(404)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            return
        try:
            with open(self.status_file, 'r') as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            data = {"alive": False, "error": "status file not found"}
        body = json.dumps(data)
        self.send_response(200 if data.get("alive") else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())


def start_health_server(port: int) -> HTTPServer:
    server = HTTPServer(("127.0.0.1", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="health-server")
    thread.start()
    logging.info(f"Health endpoint started on port {port}")
    return server
