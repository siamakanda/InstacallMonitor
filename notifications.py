from __future__ import annotations

import logging
import threading

import requests

from config import GlobalSettings


def send_webhook(global_: GlobalSettings, title: str, message: str) -> None:
    if not global_.webhook_url or global_.webhook_type == "none":
        return

    def _send() -> None:
        try:
            if global_.webhook_type == "slack":
                payload = {"text": f"*{title}*\n{message}"}
                requests.post(global_.webhook_url, json=payload, timeout=10)
            elif global_.webhook_type == "telegram":
                payload = {
                    "chat_id": global_.telegram_chat_id,
                    "text": f"<b>{title}</b>\n{message}",
                    "parse_mode": "HTML",
                }
                requests.post(global_.webhook_url, json=payload, timeout=10)
        except Exception as e:
            logging.warning(f"Webhook send failed: {e}")

    threading.Thread(target=_send, daemon=True).start()
