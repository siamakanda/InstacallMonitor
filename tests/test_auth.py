from __future__ import annotations

from unittest.mock import MagicMock, patch

from auth import perform_login

# A realistic login form: hidden _csrf + username/password inputs.
LOGIN_FORM_PAGE = (
    '<html><body><form method="post">'
    '<input type="hidden" name="_csrf" value="tok123"/>'
    '<input name="username"/><input name="password"/>'
    "</form></body></html>"
)

# What the Instacall portal actually renders: an EMPTY _csrf placeholder
# (state is carried by the PHPSESSID cookie).
EMPTY_CSRF_PAGE = (
    '<html><body><form method="post">'
    '<input type="hidden" name="_csrf" value=""/>'
    '<input name="username"/><input name="password"/>'
    "</form></body></html>"
)

DASHBOARD = "<html><body>Welcome</body></html>"


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
            _Resp(200, url="https://portal/login", text=LOGIN_FORM_PAGE),
            _Resp(200, url="https://portal/dashboard", text=DASHBOARD),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is True

    def test_empty_csrf_token_still_logs_in(self) -> None:
        # Regression: the portal's empty _csrf placeholder must not block login.
        session = _session(
            _Resp(200, url="https://portal/login", text=EMPTY_CSRF_PAGE),
            _Resp(200, url="https://portal/dashboard", text=DASHBOARD),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is True
        assert session.post.call_args.kwargs["data"]["_csrf"] == ""

    def test_missing_csrf_input_still_logs_in(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text="<html>no csrf field here</html>"),
            _Resp(200, url="https://portal/dashboard", text=DASHBOARD),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is True

    def test_failed_login_redirect_back_to_login_is_rejected(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_FORM_PAGE),
            _Resp(302, url="https://portal/login", text=""),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False

    def test_login_form_echoed_at_login_url_is_rejected(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_FORM_PAGE),
            _Resp(200, url="https://portal/login", text=LOGIN_FORM_PAGE),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False

    def test_dashboard_containing_password_field_is_not_rejected(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_FORM_PAGE),
            _Resp(200, url="https://portal/dashboard", text='<form><input name="password"/></form>'),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is True

    def test_post_http_error_is_failure(self) -> None:
        session = _session(
            _Resp(200, url="https://portal/login", text=LOGIN_FORM_PAGE),
            _Resp(500, url="https://portal/dashboard", text=DASHBOARD),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False

    def test_login_page_http_error_does_not_abort(self) -> None:
        # A non-200 GET is logged but the POST is still attempted.
        session = _session(
            _Resp(500, url="https://portal/login", text=""),
            _Resp(200, url="https://portal/dashboard", text=DASHBOARD),
        )
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is True
        session.post.assert_called_once()

    def test_exception_is_swallowed(self) -> None:
        session = MagicMock()
        session.get.side_effect = RuntimeError("boom")
        with patch("auth.get_credentials", return_value=("u", "p")):
            assert perform_login(session, 10) is False
