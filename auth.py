from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup, Tag

from config import HEADERS, LOGIN_URL, get_credentials


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def _extract_csrf_token(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    csrf_input = soup.find("input", {"name": "_csrf"})
    if isinstance(csrf_input, Tag):
        value = csrf_input.get("value")
        if isinstance(value, str):
            return value
    return ""


def _bounced_back_to_login(response: requests.Response) -> bool:
    """True when the POST landed back on the login page (credentials rejected)."""
    if "login" not in response.url.lower():
        return False
    if not response.text:
        return True
    body = BeautifulSoup(response.text, "html.parser")
    return body.find("input", {"name": "password"}) is not None


def perform_login(session: requests.Session, timeout: int = 10) -> bool:
    username, password = get_credentials()
    try:
        login_init = session.get(LOGIN_URL, timeout=timeout)
        if login_init.status_code != 200:
            logging.warning("Login page returned HTTP %s", login_init.status_code)

        # The portal renders an empty `_csrf` placeholder (state lives in the
        # PHPSESSID cookie), so a missing/empty token must not block login.
        csrf_token = _extract_csrf_token(login_init.text)
        if not csrf_token:
            logging.info("No CSRF token on the login page; proceeding without one.")

        login_data = {"_csrf": csrf_token, "username": username, "password": password}
        login_response = session.post(LOGIN_URL, data=login_data, timeout=timeout)

        if login_response.status_code >= 400:
            logging.error("Login failed with HTTP %s", login_response.status_code)
            return False
        if _bounced_back_to_login(login_response):
            logging.error("Login failed — still on the login page (HTTP %s)", login_response.status_code)
            return False

        logging.info("Login successful.")
        return True
    except Exception:
        logging.exception("Login exception")
        return False
