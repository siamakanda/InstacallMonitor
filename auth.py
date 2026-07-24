from __future__ import annotations

import requests

from config import HEADERS, LOGIN_URL, get_credentials


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


def perform_login(session: requests.Session, timeout: int = 10) -> bool:
    username, password = get_credentials()
    from bs4 import BeautifulSoup
    try:
        login_init = session.get(LOGIN_URL, timeout=timeout)
        soup_login = BeautifulSoup(login_init.text, 'html.parser')
        csrf_input = soup_login.find('input', {'name': '_csrf'})
        csrf_token = csrf_input.get('value', '') if csrf_input else ""

        login_data = {"_csrf": csrf_token, "username": username, "password": password}
        login_response = session.post(LOGIN_URL, data=login_data, timeout=timeout)

        if login_response.status_code in [200, 302]:
            import logging
            logging.info("Login successful.")
            return True
        else:
            import logging
            logging.error(f"Login failed with HTTP {login_response.status_code}")
            return False
    except Exception as e:
        import logging
        logging.error(f"Login exception: {e}")
        return False


def ensure_authenticated(
    session: requests.Session,
    response: requests.Response,
    timeout: int = 10,
) -> bool:
    if "login" not in response.url.lower():
        return True
    import logging
    logging.warning("Session expired. Re-logging in...")
    return perform_login(session, timeout)


def with_session_refresh(
    session: requests.Session,
    timeout: int,
    fn,
    max_attempts: int = 2,
):
    import logging
    for attempt in range(max_attempts):
        resp = fn()
        if "login" not in resp.url.lower():
            return resp
        if attempt < max_attempts - 1:
            logging.warning(f"Session expired (attempt {attempt + 1}). Re-logging in...")
            if not perform_login(session, timeout):
                raise RuntimeError("Re-login failed")
    return resp
