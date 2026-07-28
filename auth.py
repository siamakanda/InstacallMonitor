from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from config import HEADERS, LOGIN_URL, get_credentials


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def perform_login(session: requests.Session, timeout: int = 10) -> bool:
    import logging

    username, password = get_credentials()
    try:
        login_init = session.get(LOGIN_URL, timeout=timeout)
        soup = BeautifulSoup(login_init.text, "html.parser")
        csrf_input = soup.find("input", {"name": "_csrf"})
        csrf_token = csrf_input.get("value", "") if csrf_input else ""

        login_data = {"_csrf": csrf_token, "username": username, "password": password}
        login_response = session.post(LOGIN_URL, data=login_data, timeout=timeout)

        if login_response.status_code in (200, 302):
            logging.info("Login successful.")
            return True
        logging.error(f"Login failed with HTTP {login_response.status_code}")
        return False
    except Exception as e:
        logging.error(f"Login exception: {e}")
        return False
