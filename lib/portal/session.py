"""Port of lib/portal/session.js — authenticated session against unihotel.org.

Differences from the Express original, all behaviour-preserving:
- httpx.AsyncClient replaces undici fetch; the client's built-in cookie jar
  replaces the hand-rolled CookieJar (single-host portal, same semantics).
- A dedicated asyncio.Lock serialises logins (the JS code throttled with the
  request semaphore; the lock avoids a concurrent-login stampede).
- Typed errors (lib.errors) instead of bare Exceptions, and an explicit
  connect/read/write/pool timeout breakdown.
"""

import asyncio
import json
from json import JSONDecodeError
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import urlencode

import httpx

from ..errors import PortalLoginError, PortalNotConfigured, PortalUpstreamError
from ..log import debug, info, warn

Params = Union[Dict[str, Any], Sequence[Tuple[str, Any]]]


def join_url(base: str, path: str) -> str:
    return str(httpx.URL(base if base.endswith("/") else base + "/").join(path))


def extract_csrf(html: str) -> Optional[str]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html or "", "html.parser")
    for name in ("csrfmiddlewaretoken", "csrf_token", "_token"):
        el = soup.select_one(f'input[name="{name}"]')
        if el is not None and el.get("value"):
            return el["value"]
    for name in ("csrf-token", "csrf_token"):
        el = soup.select_one(f'meta[name="{name}"]')
        if el is not None and el.get("content"):
            return el["content"]
    return None


def _is_transient(err: BaseException) -> bool:
    # undici raised TypeError on connection failures / AbortError on timeouts;
    # httpx raises TransportError subclasses (ConnectError, TimeoutException, ...).
    return isinstance(err, httpx.TransportError)


class PortalSession:
    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        login_path: str = "/login/",
        timeout_s: float = 30.0,
        max_concurrent: int = 4,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.base_url = str(base_url).rstrip("/")
        self.username = username
        self.password = password
        self.login_path = login_path
        self.timeout_s = timeout_s
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                timeout_s, connect=min(10.0, timeout_s), pool=min(10.0, timeout_s)
            ),
            # manual redirects by default, like the JS fetch wrapper
            follow_redirects=False,
            headers={"User-Agent": "lgm-rate-proxy/0.4 (+fastapi)"},
            # DI hook for tests (httpx.MockTransport); production passes None.
            transport=transport,
        )
        self._semaphore = asyncio.Semaphore(max(1, max_concurrent))
        self._login_lock = asyncio.Lock()
        self.csrf_token: Optional[str] = None
        self.logged_in = False

    # -- URLs ------------------------------------------------------------

    @property
    def login_url(self) -> str:
        return join_url(self.base_url, self.login_path.lstrip("/"))

    @property
    def login_page_url(self) -> str:
        # The rendered login form lives at /login-page/; GET /login/ itself is a
        # redirect with an empty body, so it yields no CSRF token.
        return join_url(self.base_url, "login-page/")

    @property
    def ajax_login_url(self) -> str:
        # unihotel.org's sign-in form posts to /ajax-login/ (POST /login/ answers
        # 403). The endpoint replies {"status":"authenticated"} on success.
        return join_url(self.base_url, "ajax-login/")

    # -- low-level fetch ---------------------------------------------------

    async def _fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        body: Optional[Union[str, bytes]] = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        return await self.client.request(
            method,
            url,
            headers=headers or {},
            content=body,
            follow_redirects=follow_redirects,
        )

    @staticmethod
    def _is_expired_status(status: int) -> bool:
        # The portal's data endpoints always answer 200 directly, never 3xx.
        if 300 <= status < 400:
            return True
        return status in (401, 403)

    async def _verify_authenticated(self) -> bool:
        try:
            probe = await self._fetch(join_url(self.base_url, "profile/"), follow_redirects=True)
            if probe.status_code != 200:
                debug("auth probe -> HTTP %s", probe.status_code)
                return False
            text = probe.text
            ok = "userpassword" not in text and "/logout/" in text
            debug("auth probe authenticated=%s", ok)
            return ok
        except Exception:
            return False

    # -- login ---------------------------------------------------------------

    async def login(self) -> None:
        if not self.username or not self.password:
            raise PortalNotConfigured("portal not configured (PORTAL_USERNAME/PORTAL_PASSWORD)")
        async with self._login_lock:
            if self.logged_in:
                return
            # 1. Fresh CSRF + cookies from the rendered login page itself.
            token: Optional[str] = None
            for page_url in (self.login_page_url, self.login_url):
                try:
                    page = await self._fetch(page_url)
                    if page.status_code != 200:
                        continue
                    token = extract_csrf(page.text)
                    if token:
                        break
                except httpx.TransportError:
                    continue
            if token:
                self.csrf_token = token
            else:
                debug("no CSRF token found on login pages; trying without")

            # 2. Submit credentials to the AJAX login endpoint.
            form: List[Tuple[str, str]] = [
                ("useremail", self.username),
                ("userpassword", self.password),
            ]
            if self.csrf_token:
                form.append(("csrfmiddlewaretoken", self.csrf_token))
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "X-Requested-With": "XMLHttpRequest",
                "Origin": self.base_url,
                "Referer": self.login_page_url,
            }
            if self.csrf_token:
                headers["X-CSRFToken"] = self.csrf_token
            try:
                post = await self._fetch(
                    self.ajax_login_url, method="POST", headers=headers, body=urlencode(form)
                )
            except httpx.TransportError as err:
                raise PortalLoginError(f"login transport error: {type(err).__name__}") from err
            info(
                "login POST -> HTTP %s location=%s",
                post.status_code,
                post.headers.get("location") or "-",
            )
            if post.status_code >= 400:
                warn("Portal login failed: HTTP %s (check PORTAL_* creds/paths)", post.status_code)
                raise PortalLoginError(f"Portal login failed: HTTP {post.status_code}")
            post_text = post.text
            ajax_ok = False
            try:
                payload = json.loads(post_text)
                ajax_ok = payload.get("status") == "authenticated" or payload.get("success") is True
            except (JSONDecodeError, ValueError, AttributeError):
                ajax_ok = False
            # A non-JSON 200 may be a re-rendered login form — only the probe decides.
            if not ajax_ok and not await self._verify_authenticated():
                warn("Portal login failed: credentials rejected (no authenticated session established)")
                self.logged_in = False
                raise PortalLoginError(
                    "Portal login failed: credentials rejected — check PORTAL_USERNAME/PORTAL_PASSWORD"
                )
            self.logged_in = True
            info("Portal login OK as shared agency session")

    async def ensure_login(self) -> None:
        if not self.logged_in:
            await self.login()

    # -- authenticated requests ----------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[Params] = None,
        data: Optional[Params] = None,
        json_body: Optional[Any] = None,
        headers: Optional[Dict[str, str]] = None,
        retry_on_expiry: bool = True,
    ) -> httpx.Response:
        await self.ensure_login()
        url = path if path.startswith("http") else self.base_url + path
        if params is not None:
            qs = urlencode(list(params.items()) if isinstance(params, dict) else list(params), quote_via=_quote_plus)
            url += ("&" if "?" in url else "?") + qs
        body: Optional[str] = None
        out_headers = dict(headers or {})
        if data is not None:
            body = urlencode(list(data.items()) if isinstance(data, dict) else list(data), quote_via=_quote_plus)
            out_headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        elif json_body is not None:
            body = json.dumps(json_body)
            out_headers.setdefault("Content-Type", "application/json")

        async def once() -> httpx.Response:
            async with self._semaphore:
                req_headers = dict(out_headers)
                if self.csrf_token:
                    req_headers["X-CSRFToken"] = self.csrf_token
                    req_headers["Referer"] = self.base_url + "/"
                res = await self._fetch(url, method=method, headers=req_headers, body=body)
                debug("portal %s %s -> %s", method, path, res.status_code)
                return res

        async def with_retry() -> httpx.Response:
            try:
                return await once()
            except httpx.TransportError as err:
                info("transient portal network error (%s) — retrying once", type(err).__name__)
                try:
                    return await once()
                except httpx.TransportError as err2:
                    raise PortalUpstreamError(f"portal unreachable: {type(err2).__name__}") from err2

        response = await with_retry()
        if retry_on_expiry and self._is_expired_status(response.status_code):
            info("Portal session expired — re-logging in once")
            self.logged_in = False
            await self.login()
            response = await with_retry()
        return response

    def status(self) -> Dict[str, Any]:
        return {
            "base_url": self.base_url,
            "logged_in": self.logged_in,
            "has_csrf": self.csrf_token is not None,
        }

    async def aclose(self) -> None:
        await self.client.aclose()


def _quote_plus(value: str, safe: str = "", encoding: Optional[str] = None, errors: Optional[str] = None) -> str:
    # JS URLSearchParams encodes space as '+' — mirror that for the portal.
    from urllib.parse import quote_plus

    return quote_plus(str(value), safe=safe, encoding=encoding, errors=errors)
