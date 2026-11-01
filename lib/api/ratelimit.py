"""Minimal in-memory per-IP sliding-window rate limiter (stdlib only).

Scope: protects /search and /hotels/* from hammering the portal. Toggled by
RATE_LIMIT_PER_MINUTE (0 = off). Single-process accuracy — with WEB_CONCURRENCY
> 1 each worker keeps its own window (documented; use a shared store such as
Redis if per-worker budgets are unacceptable).
"""

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

from .common import HttpError


class SlidingWindowLimiter:
    def __init__(self, limit_per_minute: int) -> None:
        self.limit = max(0, int(limit_per_minute))
        self.window_s = 60.0
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._last_gc = time.monotonic()

    def check(self, key: str) -> bool:
        """Return True if allowed; raise HttpError(429) otherwise."""
        if self.limit <= 0:
            return True
        now = time.monotonic()
        self._gc(now)
        q = self._hits[key]
        while q and now - q[0] > self.window_s:
            q.popleft()
        if len(q) >= self.limit:
            retry_after = int(self.window_s - (now - q[0])) + 1
            raise HttpError(
                429,
                "rate limit exceeded, slow down",
                headers={"Retry-After": str(max(1, retry_after))},
            )
        q.append(now)
        return True

    def _gc(self, now: float) -> None:
        # Opportunistic cleanup so idle IPs don't pin memory forever.
        if now - self._last_gc < 300:
            return
        self._last_gc = now
        cutoff = now - self.window_s
        stale = [k for k, q in self._hits.items() if not q or now - q[-1] > cutoff + self.window_s]
        for k in stale:
            self._hits.pop(k, None)


_limiter: Optional[SlidingWindowLimiter] = None


def get_limiter() -> Optional[SlidingWindowLimiter]:
    global _limiter
    if _limiter is None:
        from .. import config

        if config.RATE_LIMIT_PER_MINUTE <= 0:
            return None
        _limiter = SlidingWindowLimiter(config.RATE_LIMIT_PER_MINUTE)
    return _limiter


def client_ip(request) -> str:
    # Behind a trusted proxy, use the forwarded chain's first hop.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def guard_public(request) -> Tuple[bool, Optional[SlidingWindowLimiter]]:
    limiter = get_limiter()
    if limiter is None:
        return True, None
    limiter.check(client_ip(request))
    return True, limiter
