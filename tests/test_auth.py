from __future__ import annotations

from unittest.mock import MagicMock, patch

from auth import perform_login

LOGIN_PAGE = '<html><body><form><input name="_csrf" value="tok123"/></form></body></html>'


class _Resp:
    def __init__(self, status_code: int, url: str = "", text: str = "") -> None:
        self.status_code = status_code
        self.url = url
        self.text = text


def _session(login_init: _Resp, post_resp: _Resp | None = None) -> MagicMock:
    session = MagicMock()
    session.get.return_value = login_init
    if post_resp is not None:
        session.post.return_value = post_resp
    return session


class TestPerformLogin:
    def test_success_redirects_away_from_login(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_PAGE),
            _Resp(200, url="https://portal/dashboard", text="<html>ok</html>"),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is True

    def test_failed_login_redirect_back_to_login_is_rejected(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_PAGE),
            _Resp(302, url="https://portal/login", text=""),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False

    def test_200_page_still_showing_password_is_rejected(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_PAGE),
            _Resp(200, url="https://portal/dashboard", text='<form><input name="password"/></form>'),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False

    def test_missing_csrf_token_aborts_before_post(self) -> None:
        session = _session(_Resp(200, url="https://portal/login", text="<html>no token</html>"))
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False
        session.post.assert_not_called()

    def test_login_page_http_error(self) -> None:
        session = _session(_Resp(500, url="https://portal/login"))
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False

    def test_exception_is_swallowed(self) -> None:
        session = MagicMock()
        session.get.side_effect = RuntimeError("boom")
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False
