# LGM Proxy API — Python / FastAPI port

A 1:1 Python port of the Express service (`lgm-express`). Same routes, same
JSON shapes, same unihotel.org portal contract (ajax-login, canonical search
params, `DD.MM.YYYY` dates, `72XCP` rate code) — refactored for production
(v0.4.0). Ships with a 109-test offline suite and deploys to Vercel as-is.

## Deploy to Vercel

The repo root is Vercel-ready (`api/index.py` + `requirements.txt` +
`vercel.json`). The FastAPI app runs as a Python serverless function; static
files come from `public/`.

1. Import the repo (or just the `fastapi-port` branch) in Vercel — framework
   preset **Other**, no build command needed.
2. Set the environment variables (Project → Settings → Environment Variables):
   `PORTAL_USERNAME`, `PORTAL_PASSWORD`, `ADMIN_USER`, `ADMIN_PASSWORD`,
   `ADMIN_JWT_SECRET` (≥32 random chars), and optionally `API_KEYS`,
   `RATE_LIMIT_PER_MINUTE`, `CORS_ORIGINS`, `DOCS_ENABLED=false`, `MARKUP`.
   Never commit `.env`.
3. Deploy. `vercel.json` rewrites all routes to the function, bumps
   `maxDuration` to 60 s for multi-part searches, and includes
   `lib/`, `data/`, `public/` in the lambda bundle.

**Serverless CMS**: Vercel's filesystem is read-only, so `data/cms.json`
cannot persist writes there. The store auto-detects Vercel (`VERCEL=1`) and
admin mutations answer **409** with an explanation instead of crashing;
reads and search enrichment keep working from the bundled `data/cms.json`.
For a writable CMS on Vercel, point a database (e.g. Vercel Postgres /
Supabase) at `lib/cms/store.py` — the rest of the app is storage-agnostic.
Local/Docker deploys keep full read-write CMS behaviour.

## Test

```bash
pip install -r requirements-dev.txt
python -m pytest        # 109 offline tests — portal is mocked, no network
```

Coverage: canonical 28-key portal param contract, DD.MM.YYYY dates,
search/hotel/SPO parsers, pricing markup, nationality resolution, CMS store
+ CSV roundtrip, admin JWT auth, rate limiter, API-key gate, portal login/
expiry/retry over MockTransport, and every HTTP route incl. error mapping.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env            # fill in PORTAL_USERNAME / PORTAL_PASSWORD
python run.py                   # = uvicorn api.index:app (default port 8000)
```

> **Secrets are environment-only.** There are no fallback credentials in
> source. With `PORTAL_USERNAME`/`PORTAL_PASSWORD` unset the app still boots
> (`/health`, `/admin/*` work) and portal endpoints answer **503**.

Docker:

```bash
docker build -t lgm-fastapi .
docker run -p 8000:8000 --env-file .env -v "$(pwd)/data:/app/data" lgm-fastapi
```

## Endpoints (identical to the Express service)

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | liveness + portal session status |
| GET | `/health/portal` | forces a portal login probe |
| GET | `/search` | `date_start`, `date_end` (YYYY-MM-DD), `adults`, `childs`, `child_ages`, `nationality` (ISO3/name/numeric), `stars`, `page`, `per_page`, `resort`, `max_parts` |
| GET | `/hotels/{hotel_id}` | hotel_id = portal slug (numeric ids → 404); `?slug=` overrides |
| POST | `/admin/login` | `{user, password}` → Bearer JWT (`ADMIN_TOKEN_TTL_HOURS`, default 12h) |
| GET/POST | `/admin/hotels` | list / create (Bearer) |
| GET/PUT/DELETE | `/admin/hotels/{id}` | read / update / delete (Bearer) |
| POST | `/admin/import` | multipart `file` (.csv), Bearer, size-capped |
| GET | `/admin/export` | CSV download, Bearer |
| GET | `/admin/` | admin SPA (static, from `public/admin`) |

Every response carries an `X-Request-ID` header (echoed from the request if
present) which also appears in the access log.

## Layout (mirrors the Express repo)

```
api/index.py            # FastAPI app            ← api/index.js
lib/config.py           # env config             ← lib/config.js
lib/errors.py           # typed portal errors    (new)
lib/log.py              # leveled logging        ← lib/log.js
lib/pricing.py          # markup                 ← lib/pricing.js
lib/api/common.py       # HttpError + parsers    ← lib/api/common.js
lib/api/keys.py         # optional API-key auth  (new)
lib/api/ratelimit.py    # per-IP rate limiter    (new)
lib/data/nationality.py # ISO3 → portal id       ← lib/data/nationality.js
lib/portal/session.py   # login/retry/session    ← lib/portal/session.js
lib/portal/search.py    # canonical params+parse ← lib/portal/search.js
lib/portal/hotel.py     # hotel quote parsing    ← lib/portal/hotel.js
lib/portal/spo.py       # special offers         ← lib/portal/spo.js
lib/portal/textutils.py # shared parse helpers   (deduped)
lib/cms/store.py        # JSON store (cached)    ← lib/cms/store.js
lib/cms/csv.py          # CSV import/export      ← lib/cms/csv.js
lib/cms/merge.py        # CMS enrichment         ← lib/cms/merge.js
lib/auth/admin.py       # JWT admin auth         ← lib/auth/admin.js
public/admin/           # admin SPA (unchanged)
data/cms.json           # CMS store (unchanged)
tests/                  # offline pytest suite (109 tests)
vercel.json             # Vercel serverless config
```

## Production notes

- **Config**: `.env` is loaded by `lib/config.py` itself, so `python run.py`,
  `uvicorn`, and Docker all see identical values.
- **Security (opt-in, backwards compatible)**:
  - `API_KEYS=key1,key2` → `/search` + `/hotels/*` require `X-API-Key` (or `?api_key=`).
  - `RATE_LIMIT_PER_MINUTE=120` → per-IP sliding window (429 + `Retry-After`).
    In-memory per worker; use a shared store (e.g. Redis) if running multiple
    workers and exact budgets matter.
  - `CORS_ORIGINS=https://app.example.com` → CORS allow-list.
  - `DOCS_ENABLED=false` → hides `/docs` and `/openapi.json`.
- **CMS store**: mtime-cached in memory, atomic writes (tmp + fsync + rename),
  read-modify-write under a process lock; CSV import is a single snapshot
  transaction. Keep `WEB_CONCURRENCY=1` (locks are per-process) or move the
  store to a database first.
- **Search**: part 1 of the portal result set is fetched serially, parts 2..N
  concurrently (bounded by `MAX_CONCURRENT_PORTAL_REQUESTS`). `max_parts`
  caps the crawl (default `MAX_SEARCH_PARTS`=50) and the response reports
  `"truncated": true` when the portal had more parts than the cap.
- **Errors**: all portal failures are typed (`lib/errors.py`) and mapped
  centrally — no per-route `except Exception`.

## v0.4.0 — recommendations implemented

From the production review, applied on top of the v0.3.0 refactor:

**Added**
- **Vercel deployability** — `vercel.json` (rewrite-all → `api/index.py`,
  `maxDuration: 60`, `includeFiles` for `lib/`+`data/`+`public/`),
  `.vercelignore`, pinned Python 3.12 deps. The app imports lazily-safe
  (admin SPA mount is guarded by `isdir`), so stripped serverless bundles
  never 500 on import.
- **CMS read-only mode** — `CMS_READ_ONLY=1` (auto on Vercel) makes admin
  mutations fail with a clear **409** instead of an `OSError` → 500 on
  read-only filesystems. Reads/enrichment keep working.
- **Offline test suite** — `tests/` (109 tests, zero network): portal param
  contract, parsers, store/CSV, auth, rate limiting, session login/expiry/
  retry, and all routes with error mapping. CI-ready via `requirements-dev.txt`.
- **Testability DI hook** — `PortalSession(transport=...)` accepts an
  `httpx.MockTransport` for deterministic tests without monkeypatching.
- **CSV upload hardening** — non-UTF-8 imports answer 422 (was 500).

**Changed**
- API-key check is constant-time (`hmac.compare_digest`) instead of a set
  membership test.
- Broken nationality mapping file raises a server-side `RuntimeError` (500)
  instead of a client-looking 422.

**Dropped**
- Nothing functional. The review deliberately kept: the Dockerfile (still the
  best story for long-running hosts with a writable CMS), the admin SPA, and
  camelCase `partsTotal` (response-shape parity with the Express service).
  The only behavioural trim: `WEB_CONCURRENCY>1` remains documented as
  unsupported for CMS writes (per-process locks) — use `CMS_READ_ONLY=1` or
  a database for multi-worker deployments.

## Deliberate deviations from the Express original

| Case | Express | FastAPI port (v0.4.0) |
|---|---|---|
| Unknown/ambiguous `nationality` | 502 | **422** (client-input error) |
| Unhandled server bug | 502 `portal request failed` | **500** `internal server error` |
| Portal creds unset at request time | failed login attempt | **503** `portal not configured` |
| `/search` response | — | adds `"truncated": bool` (additive) |
| `/search` param | — | adds `max_parts` (additive) |

## Porting notes

- **HTTP client**: `httpx.AsyncClient` (manual redirects, native cookie jar
  replaces the hand-rolled JS jar; explicit connect/read/write/pool timeouts).
- **HTML parsing**: BeautifulSoup 4 (`html.parser`); selector semantics mapped 1:1.
- **CSV**: stdlib `csv` replaces PapaParse (CRLF export preserved).
- **JWT**: PyJWT (HS256) replaces `jsonwebtoken`.
- **Query strings** are encoded with `quote_plus` so spaces become `+`,
  matching JS `URLSearchParams`.
