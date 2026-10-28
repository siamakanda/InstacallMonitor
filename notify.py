from __future__ import annotations

import logging


def safe_notify(title: str, message: str, timeout: int = 10) -> bool:
    try:
        from plyer import notification

        notification.notify(title=title, message=message, timeout=timeout)
        return True
    except Exception as e:
        logging.warning(f"Desktop notification failed: {e}")
        return False
