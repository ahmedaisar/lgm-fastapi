"""Port of lib/auth/admin.js — single-admin auth with JWT bearer on /admin/*.

ADMIN_USER/ADMIN_PASSWORD env, stateless. If admin env is unset, login + guard
answer 503 instead of crashing boot. Token TTL is configurable via
ADMIN_TOKEN_TTL_HOURS (default 12).
"""

import hmac
import os
import secrets
import time
from typing import Any, Dict, Optional

import jwt

from .. import config
from ..log import warn

_ephemeral_secret: Optional[str] = None


def configured() -> bool:
    return bool(os.environ.get("ADMIN_USER") and os.environ.get("ADMIN_PASSWORD"))


def secret() -> str:
    global _ephemeral_secret
    env_secret = os.environ.get("ADMIN_JWT_SECRET")
    if env_secret:
        return env_secret
    # Ephemeral fallback: stable per process, but restarts invalidate tokens.
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_hex(32)
        warn("ADMIN_JWT_SECRET unset — ephemeral tokens issued (restart invalidates them)")
    return _ephemeral_secret


def timing_equal(a, b) -> bool:
    ba = str(a).encode()
    bb = str(b).encode()
    return hmac.compare_digest(ba, bb)


def issue_token(user: str) -> Dict[str, Any]:
    now = int(time.time())
    ttl = max(1, int(config.ADMIN_TOKEN_TTL_HOURS)) * 3600
    token = jwt.encode({"sub": str(user), "iat": now, "exp": now + ttl}, secret(), algorithm="HS256")
    return {"token": token, "expires_in": f"{config.ADMIN_TOKEN_TTL_HOURS}h"}


def verify_token(token: str) -> str:
    """Returns the admin subject or raises jwt.PyJWTError."""
    payload = jwt.decode(token, secret(), algorithms=["HS256"])
    return str(payload.get("sub") or "")


def login_handler(body: Any):
    """Returns (status, payload) mirroring the Express loginHandler responses."""
    if not configured():
        return 503, {"detail": "admin not configured (ADMIN_USER/ADMIN_PASSWORD)"}
    body = body if isinstance(body, dict) else {}
    user = body.get("user") or ""
    password = body.get("password") or ""
    if not timing_equal(user, os.environ.get("ADMIN_USER") or "") or not timing_equal(
        password, os.environ.get("ADMIN_PASSWORD") or ""
    ):
        warn("failed admin login attempt")
        return 401, {"detail": "Invalid admin credentials"}
    return 200, issue_token(user)


def require_admin(authorization: Optional[str]):
    """Dependency-style guard. Returns (admin_sub, error_response).

    Exactly one of the two is non-None, mirroring requireAdmin's early returns.
    """
    if not configured():
        return None, (503, {"detail": "admin not configured (ADMIN_USER/ADMIN_PASSWORD)"})
    header = str(authorization or "")
    token = header[7:] if header.startswith("Bearer ") else ""
    if not token:
        return None, (401, {"detail": "Bearer token required"})
    try:
        return verify_token(token), None
    except jwt.PyJWTError:
        return None, (401, {"detail": "Invalid or expired token"})
