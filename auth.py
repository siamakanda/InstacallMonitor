from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup, Tag

from config import HEADERS, LOGIN_URL, get_credentials


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def perform_login(session: requests.Session, timeout: int = 10) -> bool:
    username, password = get_credentials()
    try:
        login_init = session.get(LOGIN_URL, timeout=timeout)
        if login_init.status_code != 200:
            logging.error("Login page returned HTTP %s", login_init.status_code)
            return False

        soup = BeautifulSoup(login_init.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf"})
        csrf_token = ""
        if isinstance(csrf_input, Tag):
            value = csrf_input.get("value")
            csrf_token = value if isinstance(value, str) else ""
        if not csrf_token:
            logging.error("Login page is missing the CSRF token; aborting login.")
            return False

        login_data = {"_csrf": csrf_token, "username": username, "password": password}
        login_response = session.post(LOGIN_URL, data=login_data, timeout=timeout)

        # A failed login redirects back to the login page (HTTP 302), so a bare
        # status check is not enough — verify we actually left the login page.
        failed = "login" in login_response.url.lower()
        if not failed and login_response.status_code == 200 and login_response.text:
            body = BeautifulSoup(login_response.text, "html.parser")
            if body.find("input", {"name": "password"}) is not None:
                failed = True
        if failed:
            logging.error("Login failed — still on the login page (HTTP %s)", login_response.status_code)
            return False
        if login_response.status_code >= 400:
            logging.error("Login failed with HTTP %s", login_response.status_code)
            return False

        logging.info("Login successful.")
        return True
    except Exception:
        logging.exception("Login exception")
        return False
