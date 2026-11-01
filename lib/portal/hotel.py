"""Port of lib/portal/hotel.js — hotel booking page parsing + quote (unihotel.org)."""

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

from bs4 import BeautifulSoup

from ..log import debug, info, warn
from .search import ADULT_AGE, assert_ok
from .spo import parse_spo_template
from .textutils import clean_text, float_or_null

# unihotel.org: the hotel booking page moved under /profile/hotels/hotel/<slug>/
# (the old /hotel/<slug>/ route answers the portal's 404 page as HTTP 403).
HOTEL_SEARCH_REFERER = "/profile/hotels/hotel-search/"
DEFAULT_GUESTS = "ST"


def HOTEL_PAGE_PATH(slug: str) -> str:  # noqa: N802 (parity with JS helper name)
    return f"/profile/hotels/hotel/{slug}/"


class HotelNotFound(Exception):
    pass


def comma_ages(adults: int, child_ages: Optional[List[int]]) -> str:
    return ",".join(
        str(a) for a in ([ADULT_AGE] * adults + [int(a) for a in (child_ages or [])])
    )


def js_var(html: str, name: str) -> str:
    m = re.search(rf"\b{name}\s*=\s*'([^']*)'", html or "")
    return m[1] if m else ""


def parse_hotel_page(html: str, slug: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html or "", "html.parser")

    def sel_text(selector: str) -> str:
        el = soup.select_one(selector)
        return clean_text(el.get_text()) if el is not None else ""

    api_el = soup.select_one("#api-request-hotel-id")
    csrf_el = soup.select_one('input[name="csrfmiddlewaretoken"]')
    h1_rooms = soup.select_one("#hotel-rooms-wrapper h1")
    h1_any = soup.select_one("h1")
    return {
        # unihotel.org keeps the numeric hotel id in the booking form
        "hotel_id": (str(api_el.get("value")) if api_el is not None and api_el.get("value") else "")
        or js_var(html, "hotel_id")
        or "",
        "supplier_company_id": sel_text("span.hidden.supplier-company") or js_var(html, "supplier_company_id"),
        "rate_code_id": js_var(html, "rate_code_id"),
        "hotel_supplier_id": js_var(html, "hotel_supplier_id"),
        "room_ajax_url": js_var(html, "room_ajax_url"),
        "load_bonuses_url": js_var(html, "load_bonuses_url"),
        "csrf_token": (str(csrf_el.get("value")) if csrf_el is not None and csrf_el.get("value") else ""),
        "hotel_name": (clean_text(h1_rooms.get_text()) if h1_rooms is not None else "")
        or (clean_text(h1_any.get_text()) if h1_any is not None else "")
        or slug,
    }


def parse_cancellation(text) -> Optional[Any]:
    t = str(text or "").strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except (ValueError, TypeError):
        return t


def meal_code(title) -> str:
    s = str(title or "").split("(")[0].strip()
    return s or str(title or "").strip()


def _features_of(room) -> List[str]:
    items = [
        clean_text(li.get_text())
        for li in room.select(".hotel-room-features-wrapper li, .room-features li")
    ]
    items = [i for i in items if i]
    if items:
        return items
    wrapper = room.select_one(".hotel-room-features-wrapper, .room-features")
    text = clean_text(wrapper.get_text()) if wrapper is not None else ""
    return [text] if text else []


def parse_hotel_template(html: str) -> Dict[str, Any]:
    """Parse the rooms out of the unihotel.org hotel booking page (server-rendered).

    Structure per room: #rooms-wrapper > div.row, with one .calculation-wrapper
    per meal option (.meal-wrapper holds the meal title, .calculation-wrapper the
    hidden price/meal/rate/company fields and the visible price).
    """
    soup = BeautifulSoup(html or "", "html.parser")
    rooms: List[Dict[str, Any]] = []
    for room in soup.select("#rooms-wrapper > div.row"):
        title_el = room.select_one(".room-title")
        room_name = clean_text(title_el.get_text()) if title_el is not None else ""
        features = _features_of(room)

        priced = 0
        for calc in room.select(".calculation-wrapper"):
            price_el = calc.select_one(".hidden.price")
            price = float_or_null(price_el.get_text()) if price_el is not None else None
            if price is None:
                continue
            row_parent = calc.find_parent(class_="row")
            meal_title = ""
            if row_parent is not None:
                meal_el = row_parent.select_one(".meal-wrapper .hotel-item-price-description-title")
                meal_title = clean_text(meal_el.get_text()) if meal_el is not None else ""
            supplier_el = calc.select_one("span.hidden.supplier-company")
            currency_el = calc.select_one(".hotel-item-price-currency")
            method_el = calc.select_one("span.hidden.calculation-method")
            rooms.append(
                {
                    "room_name": room_name,
                    "hotel_supplier_id": clean_text(supplier_el.get_text()) if supplier_el is not None else None,
                    "meal_plan": meal_code(meal_title),
                    "net_price": price,
                    "currency": (clean_text(currency_el.get_text()) if currency_el is not None else "") or "USD",
                    "occupancy": "",
                    # unihotel.org renders no per-room allotment status; a priced room is
                    # bookable (search-level availability may still be "on_request")
                    "availability": "unknown",
                    "cancellation_policy": None,
                    "room_features": features,
                    "calculation_method": clean_text(method_el.get_text()) if method_el is not None else None,
                }
            )
            priced += 1
        if not priced:
            warn("Room %r has no priced meal options", room_name)
            rooms.append(
                {
                    "room_name": room_name,
                    "meal_plan": "",
                    "net_price": None,
                    "currency": "USD",
                    "occupancy": "",
                    "availability": "unknown",
                    "cancellation_policy": None,
                    "room_features": features,
                    "calculation_method": None,
                }
            )

    # unihotel.org no longer renders transfer options on the hotel page
    transfers: List[Dict[str, Any]] = []
    return {"rooms": rooms, "transfers": transfers}


async def get_hotel_quote(
    session,
    *,
    slug: str,
    date_start: str,
    date_end: str,
    adults: int,
    childs: int,
    child_ages: Optional[List[int]],
    nationality_id: str,
    rate_code: str,
    guests: str = DEFAULT_GUESTS,
) -> Dict[str, Any]:
    # The live browser link carries comma-joined ages and the rate code:
    # /profile/hotels/hotel/<slug>/?travellers_ages=25%2C25&adults=2&childs=0&
    #   date_start=08.11.2026&date_end=15.11.2026&currency=USD&rate_code=72XCP
    # NOTE: the query MUST be urlencoded like JS URLSearchParams (commas ->
    # %2C); the portal answers HTTP 500 to literal commas in travellers_ages.
    page_query = urlencode(
        [
            ("travellers_ages", comma_ages(adults, child_ages)),
            ("adults", str(adults)),
            ("childs", str(childs)),
            ("date_start", date_start),
            ("date_end", date_end),
            ("currency", "USD"),
            ("rate_code", rate_code),
        ],
        quote_via=_urlsearchparams_quote,
    )
    page_path = HOTEL_PAGE_PATH(slug) + "?" + page_query
    page_res = await session.request(
        "GET",
        page_path,
        headers={"Referer": session.base_url + HOTEL_SEARCH_REFERER},
    )
    # unihotel.org answers its 404 page with HTTP 403 for unknown slugs
    if page_res.status_code in (404, 403):
        raise HotelNotFound(
            f"Hotel {slug!r} not found — pass hotel_slug from /search (numeric ids have no portal route)"
        )
    assert_ok(page_res, "Hotel page")
    html = page_res.text
    ctx = parse_hotel_page(html, slug)
    if ctx["csrf_token"]:
        session.csrf_token = ctx["csrf_token"]

    parsed = parse_hotel_template(html)
    rooms, transfers = parsed["rooms"], parsed["transfers"]

    # SPO special offers render inside .earlybirds-info (English labels)
    spo: List[Dict[str, Any]] = []
    try:
        soup = BeautifulSoup(html, "html.parser")
        early = soup.select_one(".earlybirds-info")
        spo_html = early.decode_contents() if early is not None else html
        spo = parse_spo_template(spo_html)
        debug("hotel %s spo: %s offers", slug, len(spo))
    except Exception as err:
        warn("hotel %s spo parse failed: %s", slug, err)

    debug("quote %s: %s rows, %s transfers", slug, len(rooms), len(transfers))
    if not rooms:
        info("Hotel %s returned zero rooms (no_results)", slug)
    return {
        "hotel_name": ctx["hotel_name"],
        "hotel_id": ctx["hotel_id"],
        "hotel_slug": slug,
        "rooms": rooms,
        "transfers": transfers,
        "spo": spo,
        "availability": "ok" if rooms else "no_results",
    }


def _urlsearchparams_quote(value: str, safe: str = "", encoding=None, errors=None) -> str:
    """quote_via matching JS URLSearchParams serialization (space -> '+',
    alphanumerics plus * - . _ left raw; everything else percent-encoded)."""
    from urllib.parse import quote_plus

    return quote_plus(str(value), safe="*", encoding=encoding, errors=errors)
