"""Canonical 27-key portal param contract + date/age serialization."""

import pytest

from lib.portal.search import (
    CANONICAL_PARAM_KEYS,
    build_portal_params,
    to_portal_date,
    travellers_ages,
)
from lib.portal.hotel import comma_ages

EXPECTED_KEYS = [
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


def test_canonical_key_list_is_the_verified_contract():
    assert CANONICAL_PARAM_KEYS == EXPECTED_KEYS
    # 28 keys total: the well-known "27-key" search contract + the csrf token
    # that is always part of the posted/GET payload.
    assert len(EXPECTED_KEYS) == 28


def _build(**overrides):
    kwargs = dict(
        csrf_token="T",
        date_start="08.11.2026",
        date_end="15.11.2026",
        adults=2,
        childs=0,
        ages_dash="25-25",
        nationality_id="413",
        rate_code="72XCP",
        part_number=1,
    )
    kwargs.update(overrides)
    return build_portal_params(**kwargs)


def test_param_order_matches_the_live_browser_request():
    params = _build()
    assert [k for k, _ in params] == EXPECTED_KEYS


def test_param_values_match_the_verified_contract():
    values = dict(_build(stars=5))
    assert values["request_value"] == "228"
    assert values["main_country_id"] == "384"
    assert values["currency"] == "USD"
    assert values["rate_code"] == "72XCP"
    assert values["meal_plan"] == "ALL"
    assert values["guests"] == "ST"
    assert values["destination"] == "Maldives"
    assert values["date_start"] == "08.11.2026"
    assert values["date_end"] == "15.11.2026"
    assert values["travellers_ages"] == "25-25"
    assert values["adults"] == "2"
    assert values["childs"] == "0"
    assert values["nationality"] == "413"
    assert values["part_number"] == "1"
    assert values["stars"] == "5"
    assert values["csrfmiddlewaretoken"] == "T"


def test_unused_keys_are_present_but_empty():
    values = dict(_build())
    for key in ("request_with", "resort_slug", "country_slug", "features", "price_from", "price_to",
                "hotel_ids", "resort_ids", "feature_ids", "stars_ids", "hotel_sort", "hotel_limit", "stars"):
        assert values[key] == "", key


def test_every_part_carries_the_same_contract_with_its_own_part_number():
    p1 = dict(_build())
    p7 = dict(_build(part_number=7))
    assert p1["part_number"] == "1"
    assert p7["part_number"] == "7"
    p7.pop("part_number")
    p1.pop("part_number")
    assert p1 == p7


def test_to_portal_date_converts_iso_to_dd_mm_yyyy():
    assert to_portal_date("2026-11-08") == "08.11.2026"
    assert to_portal_date("2027-01-01") == "01.01.2027"


@pytest.mark.parametrize("bad", ["08.11.2026", "2026-11-8", "", "2026/11/08", "not-a-date"])
def test_to_portal_date_rejects_non_iso_input(bad):
    with pytest.raises(ValueError):
        to_portal_date(bad)


def test_to_portal_date_rejects_impossible_calendar_dates():
    with pytest.raises(ValueError):
        to_portal_date("2026-02-30")


def test_travellers_ages_dash_format():
    assert travellers_ages(2, None) == "25-25"
    assert travellers_ages(2, [6, 10]) == "25-25-6-10"
    assert travellers_ages(1, []) == "25"
    assert travellers_ages(2, [0]) == "25-25-0"


def test_comma_ages_for_the_hotel_page_query():
    assert comma_ages(2, None) == "25,25"
    assert comma_ages(2, [6, 10]) == "25,25,6,10"
