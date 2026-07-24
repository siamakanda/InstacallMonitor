from __future__ import annotations

import logging
import time
from typing import Callable


def _is_transient(error: str | None) -> bool:
    if not error:
        return False
    e = str(error).lower()
    return "timeout" in e or "connection" in e


def retry_with_backoff(
    fn: Callable,
    error_extractor: Callable,
    *args: object,
    **kwargs: object,
):
    delays = [2, 5]
    result = fn(*args, **kwargs)
    error = error_extractor(result)
    attempt = 1

    while attempt <= len(delays) and _is_transient(error):
        logging.warning(f"Retry {attempt}/{len(delays) + 1} in {delays[attempt - 1]}s...")
        time.sleep(delays[attempt - 1])
        result = fn(*args, **kwargs)
        error = error_extractor(result)
        attempt += 1

    return result
