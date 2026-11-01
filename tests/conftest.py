"""Shared pytest fixtures: FakePortal test double + API TestClient harness.

The suite is fully offline — the portal is faked, the CMS store points at a
tmp_path file, and config values are monkeypatched per test.
"""

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fixtures import HOTEL_PAGE_HTML, SEARCH_FRAGMENT_HTML  # noqa: E402

ADMIN_SECRET = "unit-test-secret-0123456789abcdef0123456789abcdef"


class FakePortal:
    """Drop-in PortalSession double: records requests, serves canned pages.

    Serves SEARCH_FRAGMENT_HTML for the search endpoint and HOTEL_PAGE_HTML
    for hotel pages; slugs containing 'unknown' answer 403 like the real
    portal's 404 behaviour. Set `error` to make every request raise.
    """

    def __init__(self, search_html: str = SEARCH_FRAGMENT_HTML, hotel_html: str = HOTEL_PAGE_HTML):
        self.base_url = "https://portal.test"
        self.csrf_token = "fake-csrf-token"
        self.logged_in = True
        self.requests = []
        self.error = None
        self.search_html = search_html
        self.hotel_html = hotel_html

    async def request(self, method, path, *, params=None, data=None, json_body=None, headers=None, retry_on_expiry=True):
        self.requests.append(
            {"method": method, "path": path, "params": [tuple(p) for p in (params or [])], "headers": dict(headers or {})}
        )
        if self.error is not None:
            raise self.error
        if path.startswith("/profile/hotels/hotels-search-hotel/"):
            return httpx.Response(200, text=self.search_html)
        prefix = "/profile/hotels/hotel/"
        if path.startswith(prefix):
            slug = path[len(prefix):].split("/")[0].split("?")[0]
            if "unknown" in slug:
                # unihotel.org answers unknown slugs with 403
                return httpx.Response(403, text="forbidden")
            return httpx.Response(200, text=self.hotel_html)
        raise AssertionError(f"unexpected portal path: {path}")

    def status(self):
        return {"base_url": self.base_url, "logged_in": self.logged_in, "has_csrf": self.csrf_token is not None}

    async def ensure_login(self):
        return None

    async def aclose(self):
        return None


@pytest.fixture()
def cms_env(monkeypatch, tmp_path):
    """Point the CMS store at a fresh tmp file and clear its cache."""
    monkeypatch.setenv("CMS_JSON_PATH", str(tmp_path / "cms.json"))
    from lib.cms import store

    store._cache.clear()
    return tmp_path / "cms.json"


@pytest.fixture()
def admin_env(monkeypatch):
    monkeypatch.setenv("ADMIN_USER", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "unit-test-pass")
    monkeypatch.setenv("ADMIN_JWT_SECRET", ADMIN_SECRET)


@pytest.fixture()
def api_client(monkeypatch, cms_env, admin_env):
    """TestClient with a FakePortal + clean CMS store + known admin env."""
    from lib import config
    from api import index as api_index

    monkeypatch.setattr(config, "MARKUP", 10.0)
    monkeypatch.setattr(config, "API_KEYS", frozenset())
    client = TestClient(api_index.app)
    fake = FakePortal()
    api_index.app.state.portal = fake
    yield client, fake
    api_index.app.state.portal = fake  # keep reference; TestClient GC handles the rest


def booking_qs(**overrides):
    """Minimal valid /search query string params."""
    base = {"date_start": "2026-11-08", "date_end": "2026-11-15", "adults": "2", "childs": "0", "nationality": "MDV"}
    base.update(overrides)
    return {k: v for k, v in base.items() if v is not None}


def admin_token(client) -> str:
    res = client.post("/admin/login", json={"user": "admin", "password": "unit-test-pass"})
    assert res.status_code == 200, res.text
    return res.json()["token"]


def auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
