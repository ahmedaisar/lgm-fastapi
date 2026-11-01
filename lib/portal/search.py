"""Port of lib/portal/search.js — portal hotel search (unihotel.org).

Production changes vs the first port (output shapes unchanged):
- Parts 2..N are fetched concurrently (bounded by the session semaphore) after
  part 1 reveals partsTotal; previously every part was a serial round trip.
- `max_parts` caps the crawl; the response reports `truncated` when the portal
  had more parts than the cap. Silent truncation before.
"""

import asyncio
import datetime as _dt
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from ..errors import PortalUpstreamError
from ..log import debug, info
from .textutils import _clean, float_or_null, int_or_null, split_name_stars

SEARCH_PATH = "/profile/hotels/hotels-search-hotel/"
SEARCH_REFERER_PATH = "/hotel-search/"

DEST_COUNTRY_ID = "228"
DEST_LABEL = "Maldives"
DEFAULT_GUESTS = "ST"
ADULT_AGE = 25

SLUG_RE = re.compile(r"/hotel/([A-Za-z0-9\-_]+)/")
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}


def to_portal_date(iso: str) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    if not m:
        raise ValueError(f"Bad date {iso!r}: use YYYY-MM-DD")
    year, month, day = int(m[1]), int(m[2]), int(m[3])
    try:
        _dt.date(year, month, day)
    except ValueError:
        raise ValueError(f"Bad date {iso!r}: use YYYY-MM-DD") from None
    return f"{m[3]}.{m[2]}.{m[1]}"


def travellers_ages(adults: int, child_ages: Optional[List[int]]) -> str:
    return "-".join(str(a) for a in ([ADULT_AGE] * adults + [int(a) for a in (child_ages or [])]))


def normalize_availability(raw) -> str:
    text = str(raw or "").strip()
    if not text:
        return "unknown"
    low = text.lower().replace("_", " ").replace("-", " ")
    if low in ("rq", "r q") or "on request" in low or low.replace(" ", "").find("onrequest") != -1:
        return "on_request"
    return text


def _qq_data(row) -> Dict[str, Any]:
    el = row.select_one(".qq-btn-place")
    raw = el.get("data-value") if el is not None else None
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def parse_search_fragment(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup("<table>" + (html or "") + "</table>", "html.parser")

    def hidden_num(selector: str, default: int) -> int:
        el = soup.select_one(selector)
        if el is None:
            return default
        value = el.get("value")
        if value is None:
            value = el.get_text()
        n = int_or_null(value)
        return default if n is None else n

    part_number = hidden_num(".part-number", 1)
    parts_total = hidden_num(".parts-total", 1)

    rows: List[Dict[str, Any]] = []
    for tr in soup.select("tr.hotel-list-item-wrapper"):
        hotel_id = str(tr.get("data-hotel-id") or "")
        a = tr.select_one("a.hotel-card__title-link")
        href = str(a.get("href") or "") if a is not None else ""
        slug_m = SLUG_RE.search(href)
        hotel_slug = slug_m[1] if slug_m else ""
        title_text = _clean(a.get_text()) if a is not None else ""
        hotel_name, star_rating = split_name_stars(title_text)

        price_el = tr.select_one(".hotel-item-price-td, [data-price]")
        if price_el is None:
            continue
        data_price = price_el.get("data-price")
        price = float_or_null(data_price if data_price is not None else price_el.get_text())
        if price is None:
            continue

        currency = "USD"
        curr_el = tr.select_one(".hotel-item-price-currency-td, .hotel-card__price-currency")
        if curr_el is not None:
            sym = _clean(curr_el.get_text())
            currency = CURRENCY_SYMBOLS.get(sym) or sym or "USD"

        qq = _qq_data(tr)
        tds = tr.find_all("td")
        room_type = qq.get("roomType") or (_clean(tds[3].get_text()) if len(tds) > 3 else "")
        meal_plan = str(qq.get("boardType") or "")
        availability_text = _clean(tds[5].get_text()) if len(tds) > 5 else ""
        availability = normalize_availability(availability_text or qq.get("availability"))

        # unihotel.org moved the star rating out of the title into the location
        # text ("Мальдивы, Южный Ари Атолл 3*"); fall back to that cell.
        star_rating_final = star_rating
        if star_rating_final is None and len(tds) > 2:
            star_m = re.search(r"(\d)\s*\*", tds[2].get_text())
            if star_m:
                star_rating_final = int(star_m[1])

        rows.append(
            {
                "hotel_name": hotel_name,
                "resort": str(qq.get("region") or ""),
                "atoll": str(qq.get("region") or ""),
                "star_rating": star_rating_final,
                "room_type": room_type,
                "meal_plan": meal_plan,
                "availability": availability,
                "net_price": price,
                "currency": currency,
                "hotel_id": hotel_id or hotel_slug,
                "hotel_slug": hotel_slug,
            }
        )
    return {"rows": rows, "partNumber": part_number, "partsTotal": parts_total}


# Canonical unihotel.org query contract for /profile/hotels/hotels-search-hotel/.
# Key set AND order must match the live browser request exactly (verified against
# the portal 2026-11); every key is always present, empty string when unused.
CANONICAL_PARAM_KEYS = [
    "csrfmiddlewaretoken",
    "request_with",
    "request_value",
    "resort_slug",
    "country_slug",
    "features",
    "stars",
    "price_from",
    "price_to",
    "currency",
    "rate_code",
    "travellers_ages",
    "main_country_id",
    "hotel_ids",
    "resort_ids",
    "feature_ids",
    "stars_ids",
    "meal_plan",
    "hotel_sort",
    "hotel_limit",
    "destination",
    "date_start",
    "date_end",
    "adults",
    "childs",
    "guests",
    "nationality",
    "part_number",
]


def build_portal_params(
    *,
    csrf_token: str,
    date_start: str,
    date_end: str,
    adults: int,
    childs: int,
    ages_dash: str,
    nationality_id: str,
    rate_code: str,
    stars: Optional[int] = None,
    part_number: int,
) -> List[Tuple[str, str]]:
    values = {
        "csrfmiddlewaretoken": csrf_token,
        "request_with": "",
        "request_value": DEST_COUNTRY_ID,
        "resort_slug": "",
        "country_slug": "",
        "features": "",
        "stars": str(stars) if stars not in (None, "") else "",
        "price_from": "",
        "price_to": "",
        "currency": "USD",
        "rate_code": rate_code,
        "travellers_ages": ages_dash,
        "main_country_id": "384",
        "hotel_ids": "",
        "resort_ids": "",
        "feature_ids": "",
        "stars_ids": "",
        "meal_plan": "ALL",
        "hotel_sort": "",
        "hotel_limit": "",
        "destination": DEST_LABEL,
        "date_start": date_start,
        "date_end": date_end,
        "adults": str(adults),
        "childs": str(childs),
        "guests": DEFAULT_GUESTS,
        "nationality": nationality_id,
        "part_number": str(part_number),
    }
    return [(key, values[key]) for key in CANONICAL_PARAM_KEYS]


def assert_ok(res, what: str) -> None:
    if res.status_code >= 400:
        raise PortalUpstreamError(f"{what} failed: HTTP {res.status_code}")


def _fetch_part(session, base_headers: Dict[str, str], kwargs: Dict[str, Any], part: int):
    params = build_portal_params(**kwargs, part_number=part)
    return session.request("GET", SEARCH_PATH, params=params, headers=base_headers)


async def search_all(
    session,
    *,
    date_start: str,
    date_end: str,
    adults: int,
    childs: int,
    ages_dash: str,
    nationality_id: str,
    rate_code: str,
    stars: Optional[int] = None,
    resort: Optional[str] = None,
    max_parts: int = 50,
) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = dict(
        csrf_token=session.csrf_token or "",
        date_start=date_start,
        date_end=date_end,
        adults=adults,
        childs=childs,
        ages_dash=ages_dash,
        nationality_id=nationality_id,
        rate_code=rate_code,
        stars=stars,
    )
    base_headers = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": session.base_url + SEARCH_REFERER_PATH,
    }

    cap = max(1, int(max_parts))

    # Part 1 serially — it reveals partsTotal (and may re-login on expiry).
    res = await _fetch_part(session, base_headers, kwargs, 1)
    assert_ok(res, "Portal search")
    fragment = parse_search_fragment(res.text)
    parts_total = fragment["partsTotal"]

    fragments = [fragment]
    if parts_total > 1:
        # Parts 2..N are independent GETs; the session semaphore bounds the
        # in-flight count. Results are kept in part order.
        wanted = min(parts_total, cap)
        parts = list(range(2, wanted + 1))
        responses = await asyncio.gather(
            *(_fetch_part(session, base_headers, kwargs, p) for p in parts)
        )
        for part, res in zip(parts, responses):
            assert_ok(res, "Portal search")
            fragments.append(parse_search_fragment(res.text))
            debug(
                "search part %s/%s: %s priced rows",
                part,
                parts_total,
                len(fragments[-1]["rows"]),
            )

    all_rows: List[Dict[str, Any]] = []
    needle = str(resort or "").strip().lower()
    for fragment in fragments:
        for row in fragment["rows"]:
            if stars is not None and row["star_rating"] != stars:
                continue
            if needle and needle not in f"{row['hotel_name']} {row['resort']}".lower():
                continue
            all_rows.append(row)

    truncated = parts_total > cap
    if truncated:
        info(
            "Portal search capped at %s of %s parts (max_parts)",
            cap,
            parts_total,
        )
    if not all_rows:
        info(
            "Portal search returned zero availability (%s-%s adults=%s childs=%s nat=%s)",
            date_start,
            date_end,
            adults,
            childs,
            nationality_id,
        )
    return {"rows": all_rows, "partsTotal": parts_total, "truncated": truncated}
