"""PortalSession login/expiry/retry behaviour over httpx.MockTransport — no network."""

import asyncio

import httpx
import pytest

from lib.errors import PortalLoginError, PortalNotConfigured, PortalUpstreamError
from lib.portal.session import PortalSession, extract_csrf, join_url

PORTAL = "https://portal.test"

LOGIN_PAGE_HTML = '<form method="post"><input name="csrfmiddlewaretoken" value="CSRF1"></form>'
PROFILE_OK = '<html><a href="/logout/">Logout</a></html>'


def make_session(handler, **kw):
    base = dict(base_url=PORTAL, username="agent@example.com", password="pw", transport=httpx.MockTransport(handler))
    base.update(kw)
    return PortalSession(**base)


def run(coro):
    return asyncio.run(coro)


def test_join_url():
    assert join_url("https://portal.test", "login-page/") == "https://portal.test/login-page/"
    assert join_url("https://portal.test/", "/x/") == "https://portal.test/x/"
    assert join_url("https://portal.test", "https://other.test/a") == "https://other.test/a"


def test_extract_csrf_from_input_and_meta():
    assert extract_csrf(LOGIN_PAGE_HTML) == "CSRF1"
    assert extract_csrf('<meta name="csrf-token" content="META1">') == "META1"
    assert extract_csrf("<html>nothing</html>") is None


def test_login_happy_path_posts_csrf_and_credentials():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login-page/":
            return httpx.Response(200, text=LOGIN_PAGE_HTML)
        if path == "/ajax-login/":
            seen["body"] = request.read().decode()
            seen["headers"] = {k.lower(): v for k, v in request.headers.items()}
            return httpx.Response(200, json={"status": "authenticated"})
        return httpx.Response(404, text="nope")

    session = make_session(handler)
    run(session.login())
    assert session.logged_in is True
    assert session.csrf_token == "CSRF1"
    assert "useremail=agent%40example.com" in seen["body"]
    assert "csrfmiddlewaretoken=CSRF1" in seen["body"]
    assert seen["headers"].get("x-requested-with") == "XMLHttpRequest"
    assert seen["headers"].get("x-csrftoken") == "CSRF1"
    run(session.aclose())


def test_login_falls_back_to_auth_probe_when_ajax_answer_is_html():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login-page/":
            return httpx.Response(200, text=LOGIN_PAGE_HTML)
        if path == "/ajax-login/":
            return httpx.Response(200, text="<html>rendered login form</html>")
        if path == "/profile/":
            return httpx.Response(200, text=PROFILE_OK)
        return httpx.Response(404)

    session = make_session(handler)
    run(session.login())
    assert session.logged_in is True
    run(session.aclose())


def test_login_fails_when_probe_says_still_logged_out():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login-page/":
            return httpx.Response(200, text=LOGIN_PAGE_HTML)
        if path == "/ajax-login/":
            return httpx.Response(200, text="<html>form</html>")
        if path == "/profile/":
            return httpx.Response(200, text='<input name="userpassword">')
        return httpx.Response(404)

    session = make_session(handler)
    with pytest.raises(PortalLoginError):
        run(session.login())
    assert session.logged_in is False
    run(session.aclose())


def test_login_rejected_status_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login-page/":
            return httpx.Response(200, text=LOGIN_PAGE_HTML)
        if request.url.path == "/ajax-login/":
            return httpx.Response(403, text="forbidden")
        return httpx.Response(404)

    with pytest.raises(PortalLoginError, match="403"):
        run(make_session(handler).login())


def test_login_without_credentials_is_not_configured():
    session = PortalSession(base_url=PORTAL, username="", password="")
    with pytest.raises(PortalNotConfigured):
        run(session.login())


def test_request_encodes_query_like_urlsearchparams():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text="data")

    session = make_session(handler)
    session.logged_in = True  # skip login round-trips
    res = run(session.request("GET", "/profile/x/", params=[("q", "a b"), ("n", "1")]))
    assert res.status_code == 200
    assert res.text == "data"
    # space must be '+' exactly like JS URLSearchParams
    assert seen["url"] == PORTAL + "/profile/x/?q=a+b&n=1"
    run(session.aclose())


def test_expired_session_triggers_relogin_then_retry():
    state = {"logins": 0, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/login-page/":
            return httpx.Response(200, text=LOGIN_PAGE_HTML)
        if path == "/ajax-login/":
            state["logins"] += 1
            return httpx.Response(200, json={"status": "authenticated"})
        if path == "/profile/hotels/hotels-search-hotel/":
            state["calls"] += 1
            if state["calls"] == 1:
                return httpx.Response(302, headers={"location": "/login-page/"})
            return httpx.Response(200, text="OK")
        return httpx.Response(404)

    session = make_session(handler)
    session.logged_in = True
    res = run(session.request("GET", "/profile/hotels/hotels-search-hotel/"))
    assert res.status_code == 200
    assert state["logins"] == 1  # exactly one re-login
    assert state["calls"] == 2
    assert session.logged_in is True
    run(session.aclose())


def test_retry_on_expiry_can_be_disabled():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/x/":
            calls["n"] += 1
            return httpx.Response(401)
        return httpx.Response(404)

    session = make_session(handler)
    session.logged_in = True
    res = run(session.request("GET", "/x/", retry_on_expiry=False))
    assert res.status_code == 401
    assert calls["n"] == 1
    run(session.aclose())


def test_transient_transport_errors_retried_then_wrapped():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    session = make_session(handler)
    session.logged_in = True
    with pytest.raises(PortalUpstreamError, match="portal unreachable"):
        run(session.request("GET", "/x/"))
    run(session.aclose())


def test_double_checked_lock_skips_login_when_already_logged_in():
    calls = {"login_page": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login-page/":
            calls["login_page"] += 1
            return httpx.Response(200, text=LOGIN_PAGE_HTML)
        if request.url.path == "/ajax-login/":
            return httpx.Response(200, json={"status": "authenticated"})
        return httpx.Response(404)

    session = make_session(handler)
    run(session.login())
    run(session.login())  # second call short-circuits
    assert calls["login_page"] == 1
    run(session.aclose())


def test_status_shape():
    session = PortalSession(base_url=PORTAL, username="u", password="p")
    status = session.status()
    assert status == {"base_url": PORTAL, "logged_in": False, "has_csrf": False}
    run(session.aclose())
