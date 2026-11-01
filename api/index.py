"""Port of api/index.js — Lets Go Maldives Rate Proxy API (FastAPI).

Same routes, same JSON shapes, same error semantics as the Express service:
  GET  /health                      — liveness + portal session status
  GET  /health/portal               — forces a portal login probe
  GET  /search                      — portal hotel search (paginated, CMS-enriched)
  GET  /hotels/{hotel_id}           — hotel quote by slug (numeric ids 404)
  POST /admin/login                 — admin JWT login
  GET/POST /admin/hotels, GET/PUT/DELETE /admin/hotels/{id}
  POST /admin/import (multipart), GET /admin/export (CSV)
  GET  /admin/                      — static admin SPA

Production changes vs the first port:
- lifespan() owns the PortalSession (no import-time construction, clean close).
- Central exception handlers replace per-route try/except: HttpError → its
  status, HotelNotFound → 404, NationalityError → 422, Portal* → 502/503,
  everything else → 500 with a traceback in the logs (never a leaked 502).
- Optional API-key auth + per-IP rate limit on public endpoints (API_KEYS,
  RATE_LIMIT_PER_MINUTE), optional CORS (CORS_ORIGINS), docs gate (DOCS_ENABLED).
- Every response carries X-Request-ID; access logs include it.

Run:  uvicorn api.index:app --host 0.0.0.0 --port $PORT   (or python run.py)
"""

import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from lib import config, log
from lib.api import ratelimit
from lib.api.common import HttpError, parse_child_ages, parse_date_param, parse_int_param
from lib.api.keys import require_api_key
from lib.auth import admin as admin_auth
from lib.cms import csv as cms_csv
from lib.cms import merge, store
from lib.data.nationality import NationalityError, load_mapping, resolve_nationality
from lib.errors import PortalError, PortalNotConfigured
from lib.portal.hotel import HotelNotFound, get_hotel_quote
from lib.portal.search import search_all, to_portal_date, travellers_ages
from lib.pricing import apply_markup
from lib.portal.session import PortalSession

import os

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ADMIN_STATIC = os.path.join(_BASE_DIR, "public", "admin")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.portal = PortalSession(
        base_url=config.PORTAL_BASE_URL,
        username=config.PORTAL_USERNAME,
        password=config.PORTAL_PASSWORD,
        login_path=config.PORTAL_LOGIN_PATH,
        timeout_s=config.PORTAL_TIMEOUT_SECONDS,
        max_concurrent=config.MAX_CONCURRENT_PORTAL_REQUESTS,
    )
    log.info("lgm-rate-proxy %s listening (portal=%s docs=%s)", app.version, config.PORTAL_BASE_URL, config.DOCS_ENABLED)
    yield
    await app.state.portal.aclose()


app = FastAPI(
    title="Lets Go Maldives Rate Proxy API",
    version="0.4.0",
    lifespan=lifespan,
    docs_url="/docs" if config.DOCS_ENABLED else None,
    redoc_url=None,
    openapi_url="/openapi.json" if config.DOCS_ENABLED else None,
)

if config.CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "X-API-Key", "Content-Type", "X-Requested-With"],
        max_age=600,
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    """Access log + X-Request-ID correlation on every response."""
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    start = time.monotonic()
    try:
        response = await call_next(request)
    except Exception:
        log.error(
            "%s %s failed after %sms [%s]",
            request.method,
            request.url.path,
            int((time.monotonic() - start) * 1000),
            request_id,
            exc_info=True,
        )
        raise
    response.headers["X-Request-ID"] = request_id
    log.info(
        "%s %s -> %s (%sms) [%s]",
        request.method,
        request.url.path,
        response.status_code,
        int((time.monotonic() - start) * 1000),
        request_id,
    )
    return response


# ---- central error mapping (no per-route try/except anymore) ----


@app.exception_handler(HttpError)
async def _http_error_handler(request: Request, exc: HttpError):
    return JSONResponse({"detail": exc.message}, status_code=exc.status, headers=exc.headers or {})


@app.exception_handler(HotelNotFound)
async def _hotel_not_found_handler(request: Request, exc: HotelNotFound):
    return JSONResponse({"detail": str(exc)}, status_code=404)


@app.exception_handler(NationalityError)
async def _nationality_handler(request: Request, exc: NationalityError):
    # Client-input error: the JS original surfaced this as 502; production maps
    # bad input to 422 (documented deviation).
    return JSONResponse({"detail": str(exc)}, status_code=422)


@app.exception_handler(PortalNotConfigured)
async def _portal_not_configured_handler(request: Request, exc: PortalNotConfigured):
    return JSONResponse({"detail": str(exc)}, status_code=503)


@app.exception_handler(PortalError)
async def _portal_error_handler(request: Request, exc: PortalError):
    from lib.errors import PortalLoginError

    log.warn("portal upstream error: %s", exc)
    # PortalNotConfigured has its own handler (MRO picks it first); anything
    # else reaching here is a 502 upstream/login failure.
    return JSONResponse({"detail": f"portal {'login' if isinstance(exc, PortalLoginError) else 'request'} failed: {exc}"}, status_code=502)


@app.exception_handler(Exception)
async def _unhandled_handler(request: Request, exc: Exception):
    log.error("unhandled error on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse({"detail": "internal server error"}, status_code=500)


# ---- shared request helpers ----


def booking_params(query: Dict[str, str]) -> Dict[str, Any]:
    date_start = parse_date_param(query.get("date_start"), "date_start")
    date_end = parse_date_param(query.get("date_end"), "date_end")
    if date_end < date_start:
        raise HttpError(422, "date_end must be on or after date_start")
    adults = parse_int_param(query.get("adults"), 2, min=1, max=16, name="adults")
    childs = parse_int_param(query.get("childs"), 0, min=0, max=10, name="childs")
    child_ages = parse_child_ages(query.get("child_ages"), childs)
    if not query.get("nationality"):
        raise HttpError(422, "nationality is required")
    nationality_id = resolve_nationality(query.get("nationality"), load_mapping())
    return {
        "portal_start": to_portal_date(date_start),
        "portal_end": to_portal_date(date_end),
        "adults": adults,
        "childs": childs,
        "ages_dash": travellers_ages(adults, child_ages),
        "child_ages": child_ages,
        "nationality_id": nationality_id,
    }


def _query_dict(request: Request) -> Dict[str, str]:
    # last value wins for repeated params (Express req.query semantics)
    return {k: v for k, v in request.query_params.multi_items()}


def _portal(request: Request) -> PortalSession:
    return request.app.state.portal


# ---- health ----


@app.get("/health")
async def health(request: Request):
    return {"status": "ok", **_portal(request).status()}


@app.get("/health/portal")
async def health_portal(request: Request):
    await _portal(request).ensure_login()
    return {"portal": "reachable", **_portal(request).status()}


# ---- public endpoints (optional API key + rate limit) ----


@app.get("/search", dependencies=[Depends(require_api_key)])
async def search(request: Request):
    ratelimit.guard_public(request)
    query = _query_dict(request)
    b = booking_params(query)
    stars = (
        None
        if query.get("stars") in (None, "")
        else parse_int_param(query.get("stars"), None, min=1, max=7, name="stars")
    )
    page = parse_int_param(query.get("page"), 1, min=1, name="page")
    per_page = parse_int_param(query.get("per_page"), 20, min=1, max=100, name="per_page")
    max_parts = parse_int_param(
        query.get("max_parts"), config.MAX_SEARCH_PARTS, min=1, max=config.MAX_SEARCH_PARTS, name="max_parts"
    )
    result = await search_all(
        _portal(request),
        date_start=b["portal_start"],
        date_end=b["portal_end"],
        adults=b["adults"],
        childs=b["childs"],
        ages_dash=b["ages_dash"],
        nationality_id=b["nationality_id"],
        rate_code=config.PORTAL_RATE_CODE,
        stars=stars,
        resort=query.get("resort"),
        max_parts=max_parts,
    )
    rows: List[Dict[str, Any]] = result["rows"]
    parts_total: int = result["partsTotal"]
    start = (page - 1) * per_page
    marked = [
        {**row, "net_price": apply_markup(row["net_price"], config.MARKUP)}
        for row in rows[start : start + per_page]
    ]
    enriched = merge.enrich_search_rows(marked, store.list())
    return {
        "results": enriched,
        "page": page,
        "per_page": per_page,
        "total": len(rows),
        "parts_total": parts_total,
        "truncated": bool(result.get("truncated")),
        "availability": "ok" if rows else "no_results",
    }


@app.get("/hotels/{hotel_id}", dependencies=[Depends(require_api_key)])
async def hotel_details(hotel_id: str, request: Request):
    ratelimit.guard_public(request)
    query = _query_dict(request)
    effective_slug = query.get("slug") or hotel_id
    if re.fullmatch(r"\d+", str(effective_slug or "")):
        return JSONResponse(
            {
                "detail": (
                    f"Hotel {hotel_id!r} not found — pass hotel_slug from /search "
                    "(numeric ids have no portal route)"
                )
            },
            status_code=404,
        )
    b = booking_params(query)
    quote = await get_hotel_quote(
        _portal(request),
        slug=effective_slug,
        date_start=b["portal_start"],
        date_end=b["portal_end"],
        adults=b["adults"],
        childs=b["childs"],
        child_ages=b["child_ages"],
        nationality_id=b["nationality_id"],
        rate_code=config.PORTAL_RATE_CODE,
    )
    for room in quote["rooms"]:
        room["net_price"] = apply_markup(room["net_price"], config.MARKUP)
    return merge.enrich_quote(quote, store.list())


# ---- Admin CMS (Bearer JWT, see lib/auth/admin.py) ----


@app.post("/admin/login")
async def admin_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    status, payload = admin_auth.login_handler(body)
    return JSONResponse(payload, status_code=status)


def _admin_guard(request: Request):
    admin_sub, error = admin_auth.require_admin(request.headers.get("authorization"))
    if error:
        status, payload = error
        return None, JSONResponse(payload, status_code=status)
    return admin_sub, None


@app.get("/admin/hotels")
async def admin_list(request: Request):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    return store.list()


@app.post("/admin/hotels", status_code=201)
async def admin_create(request: Request):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    try:
        body = await request.json()
    except Exception:
        body = {}
    return JSONResponse(store.create(body or {}), status_code=201)


@app.get("/admin/hotels/{hotel_row_id}")
async def admin_get(hotel_row_id: str, request: Request):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    rec = store.get(hotel_row_id)
    if not rec:
        return JSONResponse({"detail": f"CMS hotel {hotel_row_id!r} not found"}, status_code=404)
    return rec


@app.put("/admin/hotels/{hotel_row_id}")
async def admin_update(hotel_row_id: str, request: Request):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    try:
        body = await request.json()
    except Exception:
        body = {}
    rec = store.update(hotel_row_id, body or {})
    if not rec:
        return JSONResponse({"detail": f"CMS hotel {hotel_row_id!r} not found"}, status_code=404)
    return rec


@app.delete("/admin/hotels/{hotel_row_id}")
async def admin_delete(hotel_row_id: str, request: Request):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    if not store.remove(hotel_row_id):
        return JSONResponse({"detail": f"CMS hotel {hotel_row_id!r} not found"}, status_code=404)
    return {"deleted": hotel_row_id}


@app.post("/admin/import")
async def admin_import(request: Request, file: Optional[UploadFile] = None):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    if file is None:
        return JSONResponse({"detail": "multipart field 'file' (.csv) is required"}, status_code=422)
    if not re.search(r"\.csv$", file.filename or "", re.IGNORECASE):
        return JSONResponse({"detail": "only .csv uploads are accepted"}, status_code=422)
    content_bytes = await file.read(config.UPLOAD_MAX_BYTES + 1)
    if len(content_bytes) > config.UPLOAD_MAX_BYTES:
        return JSONResponse(
            {"detail": f"CSV too large (>{config.UPLOAD_MAX_BYTES} bytes)"}, status_code=413
        )
    try:
        text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        # Client-input error: don't leak a 500 for a non-UTF-8 upload.
        return JSONResponse({"detail": "CSV upload must be UTF-8 encoded"}, status_code=422)
    return cms_csv.import_csv(text)


@app.get("/admin/export")
async def admin_export(request: Request):
    admin_sub, err_response = _admin_guard(request)
    if err_response:
        return err_response
    return Response(
        content=cms_csv.to_csv(store.list()),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="cms-export.csv"'},
    )


# Admin UI (static SPA; data calls carry the Bearer token).
# Mounted after the /admin/* API routes so they match first. Guarded with
# isdir(): on stripped serverless builds a missing directory must not crash
# the import (which would 500 every route, not just /admin/*).


@app.get("/admin", include_in_schema=False)
async def admin_ui_redirect():
    return RedirectResponse("/admin/", status_code=307)


if os.path.isdir(_ADMIN_STATIC):
    app.mount("/admin", StaticFiles(directory=_ADMIN_STATIC, html=True), name="admin")
else:
    log.warn("admin SPA not found at %s — /admin/ UI disabled", _ADMIN_STATIC)
