"""Optional API-key auth for the public money endpoints (/search, /hotels/*).

API_KEYS env (comma separated) — when unset the endpoints stay open, matching
the Express service. When set, clients must send `X-API-Key: <key>` (header,
preferred) or `?api_key=<key>` (convenience for quick curl tests).
"""

from typing import FrozenSet

import hmac

from fastapi import Request

from ..api.common import HttpError
from .. import config


def require_api_key(request: Request) -> None:
    keys: FrozenSet[str] = config.API_KEYS
    if not keys:
        return
    provided = request.headers.get("x-api-key") or request.query_params.get("api_key") or ""
    # Constant-time per candidate key — don't leak match position via timing.
    provided_b = provided.encode()
    if not any(hmac.compare_digest(provided_b, key.encode()) for key in keys):
        raise HttpError(401, "invalid or missing API key")
