"""Port of lib/cms/store.js — JSON file store for CMS Hotels.

data/cms.json is the committed source of truth; writes are atomic
(tmp file + os.replace + fsync).

Production changes vs the first port (API/behaviour preserved):
- mtime-keyed in-memory cache: reads don't hit the disk on every request, and
  external edits (git pull, manual fix) invalidate automatically.
- a module-level RLock around read-modify-write cycles, so concurrent admin
  requests can't lose updates (previously two writers could clobber each other).
- transact(): batch mutations (CSV import) apply to one snapshot and persist
  once — the previous import re-read the file for every row (O(n²) I/O).
- READ-ONLY mode: on ephemeral filesystems (Vercel serverless serves a
  read-only /var/task) any write attempt previously surfaced as an opaque
  OSError → 500. read_only() detects that (CMS_READ_ONLY env, auto-on when
  VERCEL=1) and mutations fail fast with a clean 409 instead.
"""

import json
import os
import re
import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from ..api.common import HttpError

# The module exports its own list() (port of store.list), which shadows the
# builtin below — capture the real type up front for isinstance checks.
_builtin_list = list

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_lock = threading.RLock()
# path -> (st_mtime_ns, records)
_cache: Dict[str, Any] = {}


def default_path() -> str:
    env = os.environ.get("CMS_JSON_PATH")
    if env:
        return os.path.abspath(env)
    # JS resolves against the process CWD; the port anchors to the project root
    # so the server behaves the same regardless of where uvicorn is launched.
    return os.path.join(_BASE_DIR, "data", "cms.json")


_READONLY_TRUTHY = ("1", "true", "yes", "on")


def read_only() -> bool:
    """True when CMS writes must be refused (409) instead of crashing (500).

    - CMS_READ_ONLY=1 forces it on (e.g. multi-worker/immutable deploys).
    - Auto-enabled when VERCEL=1: serverless lambdas run on a read-only
      filesystem, so data/cms.json cannot be persisted there.
    """
    raw = (os.environ.get("CMS_READ_ONLY") or "").strip().lower()
    if raw:
        return raw in _READONLY_TRUTHY
    return os.environ.get("VERCEL") == "1"


def _assert_writable() -> None:
    if read_only():
        raise HttpError(
            409,
            "CMS store is read-only in this environment "
            "(ephemeral filesystem / CMS_READ_ONLY=1). Serve CMS data from a "
            "database or commit data/cms.json instead.",
        )


def load_all(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    path = db_path or default_path()
    with _lock:
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            _cache.pop(path, None)
            return []
        hit = _cache.get(path)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (ValueError, json.JSONDecodeError) as err:
            raise HttpError(500, f"CMS store unreadable: {err}") from None
        if not isinstance(data, _builtin_list):
            raise HttpError(500, "CMS store unreadable: not a list")
        _cache[path] = (mtime, data)
        return data


def save_all(records: List[Dict[str, Any]], db_path: Optional[str] = None) -> None:
    _assert_writable()
    path = db_path or default_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(records, indent=2, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    with _lock:
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:  # pragma: no cover
            mtime = 0
        _cache[path] = (mtime, records)


@contextmanager
def transact(db_path: Optional[str] = None) -> Iterator[List[Dict[str, Any]]]:
    """Load one snapshot, mutate it in place, persist exactly once on exit."""
    _assert_writable()
    with _lock:
        records = load_all(db_path)
        yield records
        save_all(records, db_path)


def slugify(name, existing: set) -> str:
    base_str = str(name or "").lower()
    base_str = unicodedata.normalize("NFKD", base_str)
    base_str = "".join(c for c in base_str if not unicodedata.combining(c))
    base = re.sub(r"[^a-z0-9]+", "-", base_str).strip("-") or "hotel"
    slug = base
    n = 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    return slug


def split_list(value) -> List[str]:
    if isinstance(value, _builtin_list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [v.strip() for v in str(value if value is not None else "").split("|") if v.strip()]


def null_if_empty(value) -> Optional[str]:
    s = str(value if value is not None else "").strip()
    return None if s == "" else s


def to_boolean_or_null(value) -> Optional[bool]:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s == "":
        return None
    if s in ("true", "yes", "y", "1", "allowed"):
        return True
    if s in ("false", "no", "n", "0", "not allowed"):
        return False
    raise HttpError(400, f"expected true/false, got {value!r}")


POLICY_FIELDS = [
    "check_in_from",
    "check_in_notes",
    "check_out_until",
    "cancellation_prepayment",
    "child_policies",
    "cot_extra_bed_policies",
    "age_restriction",
    "pets_notes",
    "fine_print",
]

DEFAULTS: Dict[str, Any] = {
    "description": "",
    "atoll": "",
    "stars": None,
    "photos": [],
    "amenities": [],
    "website": "",
    "portal_hotel_id": None,
    "portal_slug": None,
    **{f: "" for f in POLICY_FIELDS},
    "pets_allowed": None,
    "payment_methods": [],
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# Coerce + validate a hotel body. Raises HttpError(400) on bad input.
# In strict mode (create) name is required; update merges over the stored record first.
def normalize_record(body: Any, *, partial: bool = False) -> Dict[str, Any]:
    if not isinstance(body, dict) or isinstance(body, _builtin_list):
        raise HttpError(400, "hotel body must be a JSON object")
    out: Dict[str, Any] = {}
    if "name" in body or not partial:
        name = str(body.get("name") if body.get("name") is not None else "").strip()
        if not name:
            raise HttpError(400, "name is required")
        out["name"] = name
    if "description" in body:
        out["description"] = str(body.get("description") if body.get("description") is not None else "")
    if "atoll" in body:
        out["atoll"] = str(body.get("atoll") if body.get("atoll") is not None else "")
    if "stars" in body:
        raw = body.get("stars")
        if raw is None or raw == "":
            out["stars"] = None
        else:
            try:
                n = float(raw)
            except (ValueError, TypeError):
                raise HttpError(400, f"stars must be 0-7 or null, got {raw!r}") from None
            if n != n or n < 0 or n > 7:
                raise HttpError(400, f"stars must be 0-7 or null, got {raw!r}")
            out["stars"] = n
    if "photos" in body:
        out["photos"] = split_list(body.get("photos"))
    if "amenities" in body:
        out["amenities"] = split_list(body.get("amenities"))
    if "website" in body:
        out["website"] = str(body.get("website") if body.get("website") is not None else "").strip()
    if "portal_hotel_id" in body:
        v = null_if_empty(body.get("portal_hotel_id"))
        if v is not None and not re.fullmatch(r"\d+", v):
            raise HttpError(400, f"portal_hotel_id must be numeric, got {body.get('portal_hotel_id')!r}")
        out["portal_hotel_id"] = v
    if "portal_slug" in body:
        out["portal_slug"] = null_if_empty(body.get("portal_slug"))
    # House rules & policies (all optional free text, kept as-is for display).
    for field in POLICY_FIELDS:
        if field in body:
            out[field] = str(body.get(field) if body.get(field) is not None else "")
    if "pets_allowed" in body:
        out["pets_allowed"] = to_boolean_or_null(body.get("pets_allowed"))
    if "payment_methods" in body:
        out["payment_methods"] = split_list(body.get("payment_methods"))
    return out


# ---- snapshot-level helpers (used by transact / csv import) ----


def create_in(records: List[Dict[str, Any]], body: Any) -> Dict[str, Any]:
    data = normalize_record(body)
    rec: Dict[str, Any] = {
        "id": slugify(data["name"], {r.get("id") for r in records}),
        **DEFAULTS,
        **data,
        "updated_at": _utc_now_iso(),
    }
    records.append(rec)
    return rec


def update_in(records: List[Dict[str, Any]], id: str, patch: Any) -> Optional[Dict[str, Any]]:
    idx = next((i for i, r in enumerate(records) if r.get("id") == id), -1)
    if idx == -1:
        return None
    rest = dict(patch or {})
    rest.pop("id", None)
    data = normalize_record(rest, partial=True)
    records[idx] = {**records[idx], **data, "id": id, "updated_at": _utc_now_iso()}
    return records[idx]


def remove_in(records: List[Dict[str, Any]], id: str) -> bool:
    idx = next((i for i, r in enumerate(records) if r.get("id") == id), -1)
    if idx == -1:
        return False
    records.pop(idx)
    return True


# ---- public API (single-record convenience wrappers) ----


def list(db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    return load_all(db_path)


def get(id: str, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    return next((r for r in load_all(db_path) if r.get("id") == id), None)


def create(body: Any, db_path: Optional[str] = None) -> Dict[str, Any]:
    with transact(db_path) as records:
        rec = create_in(records, body)
    return rec


def update(id: str, patch: Any, db_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    with transact(db_path) as records:
        rec = update_in(records, id, patch)
    return rec


def remove(id: str, db_path: Optional[str] = None) -> bool:
    with transact(db_path) as records:
        return remove_in(records, id)
