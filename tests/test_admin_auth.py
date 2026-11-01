"""Admin JWT auth: login handler, bearer guard, expiry, ephemeral secret."""

import time

import jwt
import pytest

from lib.auth import admin as admin_auth

from conftest import ADMIN_SECRET, admin_env  # noqa: F401  (fixtures)


def test_configured_and_login_success(admin_env):
    assert admin_auth.configured() is True
    status, payload = admin_auth.login_handler({"user": "admin", "password": "unit-test-pass"})
    assert status == 200
    assert payload["expires_in"] == "12h"
    sub = admin_auth.verify_token(payload["token"])
    assert sub == "admin"


def test_login_rejects_bad_credentials(admin_env):
    status, payload = admin_auth.login_handler({"user": "admin", "password": "wrong"})
    assert status == 401
    status, _ = admin_auth.login_handler({"user": "attacker", "password": "unit-test-pass"})
    assert status == 401
    status, _ = admin_auth.login_handler(None)
    assert status == 401


def test_login_503_when_not_configured(monkeypatch):
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    status, payload = admin_auth.login_handler({"user": "a", "password": "b"})
    assert status == 503
    assert "not configured" in payload["detail"]


def test_require_admin_guard(admin_env):
    _, payload = admin_auth.login_handler({"user": "admin", "password": "unit-test-pass"})
    token = payload["token"]

    sub, err = admin_auth.require_admin(f"Bearer {token}")
    assert sub == "admin" and err is None

    sub, err = admin_auth.require_admin(None)
    assert sub is None and err[0] == 401

    sub, err = admin_auth.require_admin("Token nope")
    assert sub is None and err[0] == 401

    sub, err = admin_auth.require_admin("Bearer garbage.token.here")
    assert sub is None and err[0] == 401


def test_expired_token_rejected(admin_env):
    now = int(time.time())
    expired = jwt.encode({"sub": "admin", "iat": now - 7200, "exp": now - 3600}, ADMIN_SECRET, algorithm="HS256")
    sub, err = admin_auth.require_admin(f"Bearer {expired}")
    assert sub is None and err[0] == 401


def test_wrong_secret_token_rejected(admin_env, monkeypatch):
    other = jwt.encode(
        {"sub": "admin", "exp": int(time.time()) + 600},
        "another-signing-secret-0123456789abcdef",
        algorithm="HS256",
    )
    sub, err = admin_auth.require_admin(f"Bearer {other}")
    assert sub is None and err[0] == 401


def test_ttl_configurable(admin_env, monkeypatch):
    monkeypatch.setattr(admin_auth.config, "ADMIN_TOKEN_TTL_HOURS", 5)
    _, payload = admin_auth.login_handler({"user": "admin", "password": "unit-test-pass"})
    assert payload["expires_in"] == "5h"


def test_ephemeral_secret_fallback(monkeypatch):
    monkeypatch.delenv("ADMIN_JWT_SECRET", raising=False)
    admin_auth._ephemeral_secret = None  # reset module state
    first = admin_auth.secret()
    second = admin_auth.secret()
    assert first and first == second  # stable within the process
    admin_auth._ephemeral_secret = None


def test_timing_equal():
    assert admin_auth.timing_equal("abc", "abc") is True
    assert admin_auth.timing_equal("abc", "xyz") is False
    assert admin_auth.timing_equal(None, "None") is True  # str(None) both sides
