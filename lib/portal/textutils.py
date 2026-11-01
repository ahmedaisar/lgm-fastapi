"""Shared parsing helpers for portal HTML/price text.

Deduplicated from search.py / hotel.py / spo.py (each previously carried its
own copy of _clean / float parsing).
"""

import re
from typing import Optional, Tuple

_clean_ws = re.compile(r"\s+")


def clean_text(text) -> str:
    return _clean_ws.sub(" ", str(text or "")).strip()


# Aliases kept short at call sites
def _clean(s: str) -> str:  # noqa: N802  (parity with the JS helpers)
    return clean_text(s)


def int_or_null(v) -> Optional[int]:
    try:
        return int(str(v if v is not None else "").strip())
    except (ValueError, TypeError):
        return None


def float_or_null(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


STAR_RE = re.compile(r"^(.*?)\s+(\d)\*\s*.*$")


def split_name_stars(full) -> Tuple[str, Optional[int]]:
    """'Sun Siyam Olhuveli 5*' -> ('Sun Siyam Olhuveli', 5)."""
    m = STAR_RE.search(str(full or "").strip())
    if m:
        return m[1].strip(), int(m[2])
    return str(full or "").strip(), None
