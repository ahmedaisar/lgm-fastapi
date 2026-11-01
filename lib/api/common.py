"""Port of lib/api/common.js — HttpError + request parameter parsing helpers."""

import re
from typing import Dict, List, Optional


class HttpError(Exception):
    def __init__(self, status: int, message: str, headers: Optional[Dict[str, str]] = None) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = headers or {}


def parse_child_ages(raw: Optional[str], childs: int) -> Optional[List[int]]:
    if raw is None or str(raw).strip() == "":
        if childs > 0:
            raise HttpError(422, "child_ages is required when childs>0, e.g. ?childs=2&child_ages=6,10")
        return None
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    try:
        ages = [int(p) for p in parts]
    except ValueError:
        raise HttpError(422, "child_ages must be comma-separated ints, e.g. ?child_ages=6,10") from None
    if len(ages) != childs:
        raise HttpError(422, f"child_ages has {len(ages)} entries but childs={childs}")
    return ages


def parse_date_param(value, name: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")):
        raise HttpError(422, f"Bad {name}: use YYYY-MM-DD")
    return str(value)


def parse_int_param(value, default, *, min=-float("inf"), max=float("inf"), name="param"):
    """Port of parseIntParam: None/"" -> default; ints outside [min, max] -> 422.

    Note the JS original uses parseInt (trailing garbage tolerated, e.g. "2x"
    parses as 2); int() is stricter here, which only affects malformed input.
    """
    if value is None or value == "":
        return default
    try:
        n = int(str(value).strip())
    except ValueError:
        try:  # JS parseInt tolerates leading numerics: "2x" -> 2
            import re as _re

            m = _re.match(r"^\s*[+-]?\d+", str(value))
            if not m:
                raise ValueError
            n = int(m.group(0))
        except ValueError:
            raise HttpError(422, f"Bad {name}: {value!r}") from None
    if n < min or n > max:
        raise HttpError(422, f"Bad {name}: {value!r}")
    return n
