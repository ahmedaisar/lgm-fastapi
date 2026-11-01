"""Rate limiter + API-key gate (constant-time compare)."""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from lib.api import keys, ratelimit
from lib.api.common import HttpError


# ---- sliding-window limiter ----

def test_limiter_allows_under_limit_and_blocks_over():
    limiter = ratelimit.SlidingWindowLimiter(2)
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("1.1.1.1") is True
    with pytest.raises(HttpError) as err:
        limiter.check("1.1.1.1")
    assert err.value.status == 429
    assert err.value.headers.get("Retry-After", "0").isdigit()
    assert int(err.value.headers["Retry-After"]) >= 1


def test_limiter_is_per_ip():
    limiter = ratelimit.SlidingWindowLimiter(1)
    assert limiter.check("1.1.1.1") is True
    assert limiter.check("2.2.2.2") is True  # different IP, fresh window
    with pytest.raises(HttpError):
        limiter.check("1.1.1.1")


def test_limiter_disabled_when_limit_zero():
    limiter = ratelimit.SlidingWindowLimiter(0)
    for _ in range(50):
        assert limiter.check("1.1.1.1") is True


def test_client_ip_prefers_forwarded_for():
    req = SimpleNamespace(headers={"x-forwarded-for": "1.2.3.4, 5.6.7.8"}, client=None)
    assert ratelimit.client_ip(req) == "1.2.3.4"
    direct = SimpleNamespace(headers={}, client=SimpleNamespace(host="9.9.9.9"))
    assert ratelimit.client_ip(direct) == "9.9.9.9"
    unknown = SimpleNamespace(headers={}, client=None)
    assert ratelimit.client_ip(unknown) == "unknown"


# ---- API key gate ----

def _request(headers=None, query=b""):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/search",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "query_string": query,
    }
    return Request(scope)


def test_api_keys_unset_means_open(monkeypatch):
    monkeypatch.setattr(keys.config, "API_KEYS", frozenset())
    assert keys.require_api_key(_request()) is None


def test_api_key_required_header(monkeypatch):
    monkeypatch.setattr(keys.config, "API_KEYS", frozenset({"k1", "k2"}))
    assert keys.require_api_key(_request({"X-API-Key": "k1"})) is None
    with pytest.raises(HttpError) as err:
        keys.require_api_key(_request({"X-API-Key": "wrong"}))
    assert err.value.status == 401


def test_api_key_query_param_fallback(monkeypatch):
    monkeypatch.setattr(keys.config, "API_KEYS", frozenset({"k1"}))
    assert keys.require_api_key(_request({}, b"api_key=k1")) is None
    with pytest.raises(HttpError):
        keys.require_api_key(_request({}, b"api_key=nope"))


def test_api_key_missing_entirely(monkeypatch):
    monkeypatch.setattr(keys.config, "API_KEYS", frozenset({"k1"}))
    with pytest.raises(HttpError) as err:
        keys.require_api_key(_request())
    assert err.value.status == 401


def test_guard_public_returns_cleanly_when_disabled(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_limiter", lambda: None)
    assert ratelimit.guard_public(SimpleNamespace(headers={}, client=None)) == (True, None)
