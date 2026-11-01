"""Environment-driven configuration with production-safe defaults.

All knobs live here. `.env` is loaded HERE (not in api/index.py) so that every
entrypoint — `python run.py`, `uvicorn api.index:app`, docker CMD — sees the
same values. The previous version loaded dotenv only inside api/index.py, which
meant `python run.py` silently ignored .env (config was imported first).

Secrets (PORTAL_USERNAME / PORTAL_PASSWORD / ADMIN_*) are read from the
environment only — no fallback credentials in source.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _num(name: str, default: float, *, min_v: float = None, max_v: float = None) -> float:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        n = float(v)
    except ValueError:
        return default
    if n != n:  # NaN guard
        return default
    if min_v is not None and n < min_v:
        return default
    if max_v is not None and n > max_v:
        return default
    return n


def _int(name: str, default: int, *, min_v: int = None, max_v: int = None) -> int:
    return int(_num(name, default, min_v=min_v, max_v=max_v))


def _bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    return raw not in ("0", "false", "no", "off")


def _csv_list(name: str) -> list:
    raw = os.environ.get(name) or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


# -- Portal (unihotel.org) ----------------------------------------------------
PORTAL_BASE_URL = (os.environ.get("PORTAL_BASE_URL") or "https://unihotel.org").rstrip("/")
PORTAL_LOGIN_PATH = os.environ.get("PORTAL_LOGIN_PATH") or "/ajax-login/"
# Shared agency credentials — MUST come from the environment (.env / deploy
# secrets). Unset credentials keep the app bootable (/health, /admin/*) and
# portal endpoints answer 503 until they are provided.
PORTAL_USERNAME = (os.environ.get("PORTAL_USERNAME") or "").strip()
PORTAL_PASSWORD = os.environ.get("PORTAL_PASSWORD") or ""
# Verified live on unihotel.org 2026-11: 72XCP renders the hotel page with
# full room rates; the previous code (7ZV5P) returned an empty rooms list.
PORTAL_RATE_CODE = (os.environ.get("PORTAL_RATE_CODE") or "72XCP").strip()
MAX_CONCURRENT_PORTAL_REQUESTS = _int("MAX_CONCURRENT_PORTAL_REQUESTS", 4, min_v=1, max_v=64)
PORTAL_TIMEOUT_SECONDS = _num("PORTAL_TIMEOUT_SECONDS", 30, min_v=1, max_v=300)
MAX_SEARCH_PARTS = _int("MAX_SEARCH_PARTS", 50, min_v=1, max_v=200)


def portal_configured() -> bool:
    return bool(PORTAL_USERNAME and PORTAL_PASSWORD)


# -- Pricing / API surface ------------------------------------------------------
MARKUP = _num("MARKUP", 10, min_v=0, max_v=1000)
# Optional API keys for the public endpoints (/search, /hotels/*). Comma
# separated. Unset => endpoints stay open (backwards compatible).
API_KEYS = frozenset(_csv_list("API_KEYS"))
# Simple per-IP sliding-window rate limit for public endpoints (requests per
# minute). 0 disables. In-memory: applies per worker process.
RATE_LIMIT_PER_MINUTE = _int("RATE_LIMIT_PER_MINUTE", 0, min_v=0, max_v=100000)
# Allowed browser origins for CORS, comma separated. Empty => no CORS headers.
CORS_ORIGINS = _csv_list("CORS_ORIGINS")
# Multipart upload cap for POST /admin/import (bytes).
UPLOAD_MAX_BYTES = _int("UPLOAD_MAX_BYTES", 2_000_000, min_v=10_000)
# OpenAPI docs at /docs — turn off on public deployments.
DOCS_ENABLED = _bool("DOCS_ENABLED", True)

# -- Admin CMS ------------------------------------------------------------------
ADMIN_TOKEN_TTL_HOURS = _int("ADMIN_TOKEN_TTL_HOURS", 12, min_v=1, max_v=720)

# -- Misc ------------------------------------------------------------------------
LOG_LEVEL = (os.environ.get("LOG_LEVEL") or "info").lower()
NATIONALITY_MAPPING_PATH = (
    os.environ.get("NATIONALITY_MAPPING_PATH") or "lib/data/nationality_mapping.json"
)
PORT = _int("PORT", 8000, min_v=1, max_v=65535)
WEB_CONCURRENCY = _int("WEB_CONCURRENCY", 1, min_v=1, max_v=32)
