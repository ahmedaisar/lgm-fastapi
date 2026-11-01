"""Portal HTML parsers: search fragment, hotel page, SPO offers, text utils."""

import asyncio

from tests.fixtures import HOTEL_PAGE_HTML, SPO_HTML, SEARCH_FRAGMENT_HTML
from lib.portal.search import normalize_availability, parse_search_fragment, search_all
from lib.portal.hotel import get_hotel_quote, js_var, parse_hotel_page, parse_hotel_template
from lib.portal.spo import parse_spo_template
from lib.portal.textutils import float_or_null, int_or_null, split_name_stars
from conftest import FakePortal


# ---- search fragment ----

def test_parse_search_fragment_extracts_part_counters():
    parsed = parse_search_fragment(SEARCH_FRAGMENT_HTML)
    assert parsed["partNumber"] == 1
    assert parsed["partsTotal"] == 3


def test_parse_search_fragment_row_with_stars_in_location_text():
    rows = parse_search_fragment(SEARCH_FRAGMENT_HTML)["rows"]
    row = rows[0]
    assert row["hotel_id"] == "9001"
    assert row["hotel_slug"] == "anantara-dhigu"
    assert row["hotel_name"] == "Anantara Dhigu"
    # unihotel.org moved stars into the location cell — parser falls back to it
    assert row["star_rating"] == 5
    assert row["net_price"] == 1200.50
    assert row["currency"] == "USD"
    assert row["room_type"] == "Deluxe Beach Villa"
    assert row["meal_plan"] == "BB"
    assert row["resort"] == "South Male Atoll"
    assert row["availability"] == "on_request"


def test_parse_search_fragment_row_with_stars_in_title_and_slug_id_fallback():
    rows = parse_search_fragment(SEARCH_FRAGMENT_HTML)["rows"]
    row = rows[1]
    assert row["hotel_id"] == "kuramathi-maldives"  # no data-hotel-id -> slug
    assert row["hotel_name"] == "Kuramathi Maldives"
    assert row["star_rating"] == 4
    assert row["net_price"] == 850.0
    assert row["meal_plan"] == "HB"
    assert row["availability"] == "available"


def test_search_all_fetches_all_parts_and_reports_truncation():
    fake = FakePortal()
    result = asyncio.run(
        search_all(
            fake,
            date_start="08.11.2026",
            date_end="15.11.2026",
            adults=2,
            childs=0,
            ages_dash="25-25",
            nationality_id="228",
            rate_code="72XCP",
        )
    )
    # parts-total=3 in the fixture -> parts 1..3 fetched
    assert len(fake.requests) == 3
    assert result["partsTotal"] == 3
    assert result["truncated"] is False
    assert result["rows"] and result["rows"][0]["hotel_slug"] == "anantara-dhigu"


def test_search_all_caps_parts_and_flags_truncated():
    fake = FakePortal()
    result = asyncio.run(
        search_all(
            fake,
            date_start="08.11.2026",
            date_end="15.11.2026",
            adults=2,
            childs=0,
            ages_dash="25-25",
            nationality_id="228",
            rate_code="72XCP",
            max_parts=2,
        )
    )
    assert len(fake.requests) == 2
    assert result["truncated"] is True


def test_search_all_filters_by_stars_and_resort():
    fake = FakePortal()
    result = asyncio.run(
        search_all(
            fake,
            date_start="08.11.2026",
            date_end="15.11.2026",
            adults=2,
            childs=0,
            ages_dash="25-25",
            nationality_id="228",
            rate_code="72XCP",
            stars=4,
        )
    )
    assert [r["hotel_slug"] for r in result["rows"]] == ["kuramathi-maldives"] * 3

    fake2 = FakePortal()
    result2 = asyncio.run(
        search_all(
            fake2,
            date_start="08.11.2026",
            date_end="15.11.2026",
            adults=2,
            childs=0,
            ages_dash="25-25",
            nationality_id="228",
            rate_code="72XCP",
            resort="kuramathi",
        )
    )
    assert all("kuramathi" in r["hotel_slug"] for r in result2["rows"])
    assert len(result2["rows"]) == 3


# ---- availability normalizer ----

def test_normalize_availability():
    assert normalize_availability("") == "unknown"
    assert normalize_availability(None) == "unknown"
    assert normalize_availability("RQ") == "on_request"
    assert normalize_availability("On request") == "on_request"
    assert normalize_availability("on_request") == "on_request"
    assert normalize_availability("available") == "available"


# ---- hotel page ----

def test_parse_hotel_page_context():
    ctx = parse_hotel_page(HOTEL_PAGE_HTML, "anantara-dhigu")
    assert ctx["hotel_id"] == "9001"
    assert ctx["csrf_token"] == "page-csrf-123"
    assert ctx["hotel_name"] == "Anantara Dhigu Maldives Resort"
    assert ctx["supplier_company_id"] == "S77"


def test_parse_hotel_template_rooms_meals_prices():
    parsed = parse_hotel_template(HOTEL_PAGE_HTML)
    rooms = parsed["rooms"]
    assert len(rooms) == 2
    priced = rooms[0]
    assert priced["room_name"] == "Beach Villa"
    assert priced["meal_plan"] == "Breakfast"
    assert priced["net_price"] == 1200.00
    assert priced["currency"] == "$"
    assert priced["room_features"] == ["Wi-Fi", "Outdoor pool"]
    assert priced["hotel_supplier_id"] == "S77"
    assert priced["calculation_method"] == "Per night"
    # unpriced room keeps its row with a null price
    unpriced = rooms[1]
    assert unpriced["room_name"] == "Water Villa"
    assert unpriced["net_price"] is None
    assert unpriced["meal_plan"] == ""
    assert unpriced["room_features"] == []
    assert parsed["transfers"] == []


def test_get_hotel_quote_builds_urlencoded_page_query_and_marks_availability():
    fake = FakePortal()
    quote = asyncio.run(
        get_hotel_quote(
            fake,
            slug="anantara-dhigu",
            date_start="08.11.2026",
            date_end="15.11.2026",
            adults=2,
            childs=1,
            child_ages=[7],
            nationality_id="228",
            rate_code="72XCP",
        )
    )
    path = fake.requests[0]["path"]
    # literal commas break the portal -> must be %2C like JS URLSearchParams
    assert "travellers_ages=25%2C25%2C7" in path
    assert "rate_code=72XCP" in path
    assert "date_start=08.11.2026" in path
    assert quote["hotel_name"] == "Anantara Dhigu Maldives Resort"
    assert quote["hotel_id"] == "9001"
    assert quote["hotel_slug"] == "anantara-dhigu"
    assert quote["availability"] == "ok"
    assert quote["rooms"][0]["net_price"] == 1200.00
    assert quote["spo"] == []
    # the page's CSRF token is adopted by the session for later requests
    assert fake.csrf_token == "page-csrf-123"


def test_spo_parsing():
    offers = parse_spo_template(SPO_HTML)
    assert len(offers) == 1
    offer = offers[0]
    assert offer["name"] == "Early booking"
    assert offer["discount_percent"] == -20.0
    assert offer["type"] == "SPO"
    assert offer["stay_period"] == "01.11.2026 - 20.12.2026"
    assert offer["booking_period"] == "01.06.2026 - 31.08.2026"
    assert offer["rooms"] == ["Beach Villa", "Water Villa"]
    assert offer["extra_bed_rooms"] == ["Deluxe Villa"]


def test_spo_parsing_empty_input():
    assert parse_spo_template("") == []
    assert parse_spo_template(None) == []


# ---- text utils ----

def test_split_name_stars():
    assert split_name_stars("Sun Siyam Olhuveli 5*") == ("Sun Siyam Olhuveli", 5)
    assert split_name_stars("Kuramathi") == ("Kuramathi", None)
    assert split_name_stars("") == ("", None)


def test_number_parsers():
    assert int_or_null(" 42 ") == 42
    assert int_or_null("x") is None
    assert int_or_null(None) is None
    assert float_or_null("1,200.50") == 1200.50
    assert float_or_null("850") == 850.0
    assert float_or_null("") is None
    assert float_or_null(None) is None


def test_js_var_extraction():
    html = "<script>var rate_code_id = '17'; var empty = '';</script>"
    assert js_var(html, "rate_code_id") == "17"
    assert js_var(html, "missing") == ""
    assert js_var("", "x") == ""
