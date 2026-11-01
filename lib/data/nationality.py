"""Port of lib/data/nationality.js.

Nationality mapping: ISO3 / country name -> portal numeric country ID.
Source of truth is the static 249-country table extracted from the live
portal dropdown. Callers never see portal-internal IDs.

Error split: NationalityError = client-input problem (HTTP 422); a missing
or corrupt mapping FILE is a server misconfiguration and raises RuntimeError
(unhandled -> 500) so deployment bugs don't masquerade as bad client input.
"""

import json
import os
from functools import lru_cache
from typing import Dict, List


class NationalityError(Exception):
    """Client-input nationality error (surfaces as HTTP 422 in production)."""


def default_path() -> str:
    from_env = os.environ.get("NATIONALITY_MAPPING_PATH")
    if from_env:
        return os.path.abspath(from_env)
    return os.path.join(os.path.dirname(__file__), "nationality_mapping.json")


@lru_cache(maxsize=None)
def load_mapping(file_path: str = None) -> List[Dict]:
    path = file_path or default_path()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as err:
        raise RuntimeError(f"Nationality mapping file missing: {path}") from err
    except ValueError as err:
        raise RuntimeError(f"Nationality mapping at {path} is not valid JSON: {err}") from err
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Nationality mapping at {path} is empty or invalid")
    return data


def resolve_nationality(value, mapping: List[Dict]) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise NationalityError("nationality is required")
    if raw.isdigit():
        return raw
    upper = raw.upper()
    for entry in mapping:
        if str(entry.get("iso3") or "").upper() == upper:
            return str(entry["id"])
    lower = raw.lower()
    matches = [e for e in mapping if str(e.get("name") or "").lower() == lower]
    if len(matches) == 1:
        return str(matches[0]["id"])
    if matches:
        names = ", ".join(m["name"] for m in matches[:5])
        raise NationalityError(f"Ambiguous nationality {value!r}: matches {names}")
    raise NationalityError(
        f"Unknown nationality {value!r}: use ISO3 (e.g. USA) or country name"
    )
