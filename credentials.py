from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ENV_LOADED = False
_ENV_PATH = Path(__file__).resolve().parent / ".env"


def _load_env_once() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    load_dotenv(dotenv_path=_ENV_PATH)
    _ENV_LOADED = True


def get_credentials() -> tuple[str, str]:
    _load_env_once()
    username = os.getenv("PORTAL_USERNAME")
    password = os.getenv("PORTAL_PASSWORD")
    if not username or not password:
        raise ValueError("PORTAL_USERNAME or PORTAL_PASSWORD not found in .env file.")
    return username, password
