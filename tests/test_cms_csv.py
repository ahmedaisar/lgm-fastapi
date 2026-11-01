"""CSV import/export: upsert matching rules, blank-cell safety, roundtrip."""

from lib.cms import csv as cms_csv
from lib.cms import store

from conftest import cms_env  # noqa: F401  (fixture)


def test_import_creates_and_matches_rows(cms_env):
    from tests.fixtures import SAMPLE_CSV

    result = cms_csv.import_csv(SAMPLE_CSV)
    assert result["created"] == 2
    assert result["updated"] == 0
    assert result["errors"] == []
    names = {r["name"] for r in store.list()}
    assert names == {"Anantara Dhigu", "Kuramathi"}
    anantara = store.get("anantara-dhigu")
    assert anantara["portal_hotel_id"] == "9001"
    assert anantara["photos"] == ["http://p1.jpg", "http://p2.jpg"]
    assert anantara["stars"] == 4.0


def test_import_upserts_by_explicit_id(cms_env):
    from tests.fixtures import SAMPLE_CSV

    cms_csv.import_csv(SAMPLE_CSV)
    again = (
        "id,name,description\n"
        "anantara-dhigu,Anantara Dhigu,Updated description\n"
    )
    result = cms_csv.import_csv(again)
    assert result["created"] == 0
    assert result["updated"] == 1
    rec = store.get("anantara-dhigu")
    assert rec["description"] == "Updated description"
    # blank cells never wipe stored values
    assert rec["portal_hotel_id"] == "9001"
    assert rec["photos"] == ["http://p1.jpg", "http://p2.jpg"]


def test_import_upserts_by_portal_hotel_id_when_id_missing(cms_env):
    from tests.fixtures import SAMPLE_CSV

    cms_csv.import_csv(SAMPLE_CSV)
    no_id = (
        "name,portal_hotel_id,website\n"
        "Anantara Dhigu,9001,https://new.example\n"
    )
    result = cms_csv.import_csv(no_id)
    assert result["updated"] == 1
    assert result["created"] == 0
    rec = store.get("anantara-dhigu")
    assert rec["website"] == "https://new.example"


def test_import_reports_line_errors_but_keeps_going(cms_env):
    bad = (
        "name,atoll\n"
        ",NoName\n"
        "Good One,North Male Atoll\n"
    )
    result = cms_csv.import_csv(bad)
    assert result["created"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["line"] == 2
    assert "name is required" in result["errors"][0]["error"]


def test_import_skips_fully_blank_lines(cms_env):
    text = "name,atoll\n\n\nOnly Hotel,\n\n"
    result = cms_csv.import_csv(text)
    assert result["created"] == 1


def test_export_roundtrip_through_csv(cms_env):
    from tests.fixtures import SAMPLE_CSV

    cms_csv.import_csv(SAMPLE_CSV)
    store.create({"name": "Lux Resort", "stars": 5, "pets_allowed": True, "photos": ["a.jpg"]})
    exported = cms_csv.to_csv(store.list())
    assert "\r\n" in exported  # PapaParse-compatible CRLF

    # import the export into an empty store
    store.remove("anantara-dhigu")
    store.remove("kuramathi")
    store.remove("lux-resort")
    result = cms_csv.import_csv(exported)
    assert result["errors"] == []
    assert result["created"] == 3
    lux = store.get("lux-resort")
    assert lux["stars"] == 5.0
    assert lux["pets_allowed"] is True
    assert lux["photos"] == ["a.jpg"]
    assert store.get("anantara-dhigu")["portal_hotel_id"] == "9001"


def test_to_csv_headers():
    header = cms_csv.to_csv([]).split("\r\n")[0]
    assert header.split(",") == cms_csv.COLUMNS
