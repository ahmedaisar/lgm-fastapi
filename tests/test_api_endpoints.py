"""End-to-end API tests via TestClient: routing, validation, contract, auth,
error mapping, CMS admin flow, X-Request-ID."""

from tests.fixtures import SAMPLE_CSV
from lib.errors import PortalLoginError, PortalNotConfigured, PortalUpstreamError
from lib.portal.search import CANONICAL_PARAM_KEYS

from conftest import auth_header, booking_qs, admin_token


# ---- health ----

def test_health_reports_portal_session_status(api_client):
    client, fake = api_client
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["logged_in"] is True
    assert body["base_url"] == "https://portal.test"
    assert body["has_csrf"] is True


def test_health_portal_forces_probe(api_client):
    client, _ = api_client
    res = client.get("/health/portal")
    assert res.status_code == 200
    assert res.json()["portal"] == "reachable"


# ---- /search ----

def test_search_happy_path_and_canonical_param_contract(api_client):
    client, fake = api_client
    res = client.get("/search", params=booking_qs())
    assert res.status_code == 200, res.text
    body = res.json()

    # the portal request carries the full verified contract, in order
    # (fixture parts-total=3 -> parts 1,2,3 fetched; part 2..N concurrently)
    assert len(fake.requests) == 3
    sent = fake.requests[0]
    assert [dict(r["params"])["part_number"] for r in fake.requests] == ["1", "2", "3"]
    assert [k for k, _ in sent["params"]] == CANONICAL_PARAM_KEYS
    values = dict(sent["params"])
    assert values["date_start"] == "08.11.2026"
    assert values["date_end"] == "15.11.2026"
    assert values["travellers_ages"] == "25-25"
    assert values["nationality"] == "228"  # MDV resolved
    assert values["rate_code"] == "72XCP"
    assert values["destination"] == "Maldives"
    assert values["meal_plan"] == "ALL"
    assert values["guests"] == "ST"
    assert values["part_number"] == "1"
    assert sent["headers"].get("X-Requested-With") == "XMLHttpRequest"

    # rows are priced (markup 10%) and shaped like the Express service
    # (fixture parts-total=3 -> 3 parts x 2 rows each)
    assert body["total"] == 6
    assert body["parts_total"] == 3
    assert body["truncated"] is False
    assert body["availability"] == "ok"
    assert body["page"] == 1 and body["per_page"] == 20
    first = body["results"][0]
    assert first["hotel_slug"] == "anantara-dhigu"
    assert first["net_price"] == 1320.55  # 1200.50 + 10%
    assert first["cms"] is None  # empty CMS store


def test_search_child_ages_flow_into_travellers_ages(api_client):
    client, fake = api_client
    res = client.get("/search", params=booking_qs(childs="2", child_ages="6,10"))
    assert res.status_code == 200
    values = dict(fake.requests[0]["params"])
    assert values["travellers_ages"] == "25-25-6-10"
    assert values["childs"] == "2"


def test_search_pagination(api_client):
    client, _ = api_client
    res = client.get("/search", params=booking_qs(per_page="1", page="2"))
    assert res.status_code == 200
    body = res.json()
    assert body["page"] == 2 and body["per_page"] == 1
    assert body["total"] == 6
    assert [r["hotel_slug"] for r in body["results"]] == ["kuramathi-maldives"]
    # markup applied on page 2 as well
    assert body["results"][0]["net_price"] == 935.0


def test_search_stars_and_resort_filters(api_client):
    client, fake = api_client
    res = client.get("/search", params=booking_qs(stars="4"))
    assert res.status_code == 200
    assert dict(fake.requests[0]["params"])["stars"] == "4"
    body = res.json()
    assert body["total"] == 3  # 1 matching row per part x 3 parts
    assert all(r["hotel_slug"] == "kuramathi-maldives" for r in body["results"])

    res2 = client.get("/search", params=booking_qs(resort="kuramathi"))
    assert res2.status_code == 200
    # filter applies per fetched part: 1 matching row x 3 parts
    assert res2.json()["total"] == 3


def test_search_max_parts_caps_crawl_and_flags_truncation(api_client):
    client, fake = api_client
    res = client.get("/search", params=booking_qs(max_parts="2"))
    assert res.status_code == 200
    body = res.json()
    assert len(fake.requests) == 2  # part 1 + part 2 (parts-total=3)
    assert body["parts_total"] == 3
    assert body["truncated"] is True
    assert body["total"] == 4  # 2 rows x 2 fetched parts


def test_search_validation_errors(api_client):
    client, fake = api_client
    cases = [
        ({k: v for k, v in booking_qs().items() if k != "nationality"}, 422),  # missing nationality
        (booking_qs(date_start="08.11.2026"), 422),  # not ISO
        (booking_qs(date_end="2026-11-01"), 422),  # before date_start
        (booking_qs(childs="2"), 422),  # childs without child_ages
        (booking_qs(childs="1", child_ages="6,10"), 422),  # ages count mismatch
        (booking_qs(adults="0"), 422),  # below min
        (booking_qs(adults="abc"), 422),  # non-numeric
        (booking_qs(per_page="0"), 422),  # below min
        (booking_qs(stars="9"), 422),  # 1..7 allowed
    ]
    for params, expected in cases:
        res = client.get("/search", params=params)
        assert res.status_code == expected, f"{params} -> {res.status_code}"
    assert fake.requests == []  # never reached the portal


def test_search_requires_api_key_when_configured(api_client, monkeypatch):
    from lib import config

    client, _ = api_client
    monkeypatch.setattr(config, "API_KEYS", frozenset({"secret-key-1"}))
    res = client.get("/search", params=booking_qs())
    assert res.status_code == 401
    res = client.get("/search", params={**booking_qs(), "api_key": "secret-key-1"})
    assert res.status_code == 200
    res = client.get("/search", params=booking_qs(), headers={"X-API-Key": "secret-key-1"})
    assert res.status_code == 200


def test_search_maps_portal_errors(api_client):
    client, fake = api_client

    fake.error = PortalLoginError("credentials rejected")
    res = client.get("/search", params=booking_qs())
    assert res.status_code == 502
    assert "portal login failed" in res.json()["detail"]

    fake.error = PortalUpstreamError("HTTP 500")
    res = client.get("/search", params=booking_qs())
    assert res.status_code == 502
    assert "portal request failed" in res.json()["detail"]

    fake.error = PortalNotConfigured("portal not configured")
    res = client.get("/search", params=booking_qs())
    assert res.status_code == 503
    assert res.json()["detail"] == "portal not configured"


# ---- /hotels/{hotel_id} ----

def test_hotel_details_happy_path_with_markup(api_client):
    client, fake = api_client
    res = client.get("/hotels/anantara-dhigu", params=booking_qs())
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hotel_name"] == "Anantara Dhigu Maldives Resort"
    assert body["hotel_id"] == "9001"
    assert body["hotel_slug"] == "anantara-dhigu"
    assert body["availability"] == "ok"
    rooms = body["rooms"]
    assert rooms[0]["net_price"] == 1320.0  # 1200 + 10%
    assert rooms[0]["meal_plan"] == "Breakfast"
    assert rooms[1]["net_price"] is None  # unpriced room passthrough
    assert body["cms"] is None
    path = fake.requests[0]["path"]
    assert path.startswith("/profile/hotels/hotel/anantara-dhigu/")
    assert "rate_code=72XCP" in path


def test_hotel_details_numeric_id_answers_404_with_hint(api_client):
    client, fake = api_client
    res = client.get("/hotels/12345", params=booking_qs())
    assert res.status_code == 404
    assert "hotel_slug" in res.json()["detail"]
    assert fake.requests == []  # rejected before the portal


def test_hotel_details_unknown_slug_maps_403_to_404(api_client):
    client, _ = api_client
    res = client.get("/hotels/unknown-villa", params=booking_qs())
    assert res.status_code == 404
    assert "unknown-villa" in res.json()["detail"]


def test_hotel_details_slug_query_override(api_client):
    client, fake = api_client
    res = client.get("/hotels/anything-goes", params={**booking_qs(), "slug": "anantara-dhigu"})
    assert res.status_code == 200
    assert fake.requests[0]["path"].startswith("/profile/hotels/hotel/anantara-dhigu/")


def test_hotel_details_cms_enrichment_hides_portal_links(api_client):
    client, _ = api_client
    token = admin_token(client)
    res = client.post(
        "/admin/hotels",
        json={
            "name": "Anantara Dhigu",
            "portal_hotel_id": "9001",
            "portal_slug": "anantara-dhigu",
            "atoll": "South Male Atoll",
        },
        headers=auth_header(token),
    )
    assert res.status_code == 201, res.text

    res = client.get("/hotels/anantara-dhigu", params=booking_qs())
    cms = res.json()["cms"]
    assert cms["name"] == "Anantara Dhigu"
    assert cms["atoll"] == "South Male Atoll"
    assert "portal_slug" not in cms and "portal_hotel_id" not in cms


# ---- admin auth over HTTP ----

def test_admin_endpoints_require_bearer(api_client):
    client, _ = api_client
    assert client.get("/admin/hotels").status_code == 401
    assert client.get("/admin/hotels", headers=auth_header("bad")).status_code == 401
    res = client.post("/admin/login", json={"user": "admin", "password": "wrong"})
    assert res.status_code == 401


def test_admin_login_503_when_unconfigured(api_client, monkeypatch):
    client, _ = api_client
    monkeypatch.delenv("ADMIN_USER", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    res = client.post("/admin/login", json={"user": "admin", "password": "x"})
    assert res.status_code == 503


def test_admin_crud_flow(api_client):
    client, _ = api_client
    token = admin_token(client)
    headers = auth_header(token)

    assert client.get("/admin/hotels", headers=headers).json() == []

    created = client.post(
        "/admin/hotels", json={"name": "Lux Resort", "stars": 5}, headers=headers
    )
    assert created.status_code == 201
    hotel_id = created.json()["id"]
    assert hotel_id == "lux-resort"

    updated = client.put(f"/admin/hotels/{hotel_id}", json={"description": "Nice"}, headers=headers)
    assert updated.status_code == 200
    assert updated.json()["description"] == "Nice"

    fetched = client.get(f"/admin/hotels/{hotel_id}", headers=headers)
    assert fetched.status_code == 200
    assert fetched.json()["stars"] == 5.0

    assert client.get("/admin/hotels/missing", headers=headers).status_code == 404
    assert client.put("/admin/hotels/missing", json={}, headers=headers).status_code == 404
    assert client.delete("/admin/hotels/missing", headers=headers).status_code == 404

    assert client.delete(f"/admin/hotels/{hotel_id}", headers=headers).json() == {"deleted": hotel_id}
    assert client.get("/admin/hotels", headers=headers).json() == []


def test_admin_import_export_and_guards(api_client, monkeypatch):
    from lib import config

    client, _ = api_client
    token = admin_token(client)
    headers = auth_header(token)

    # no file / wrong extension / bad encoding
    assert client.post("/admin/import", headers=headers).status_code == 422
    res = client.post("/admin/import", files={"file": ("x.txt", b"a,b", "text/plain")}, headers=headers)
    assert res.status_code == 422
    res = client.post("/admin/import", files={"file": ("bad.csv", b"\xff\xfe\x00", "text/csv")}, headers=headers)
    assert res.status_code == 422

    # valid import
    res = client.post("/admin/import", files={"file": ("hotels.csv", SAMPLE_CSV.encode(), "text/csv")}, headers=headers)
    assert res.status_code == 200
    assert res.json() == {"created": 2, "updated": 0, "errors": []}

    # export round-trips the rows
    res = client.get("/admin/export", headers=headers)
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert "Anantara Dhigu" in res.text

    # size cap
    monkeypatch.setattr(config, "UPLOAD_MAX_BYTES", 10)
    res = client.post("/admin/import", files={"file": ("big.csv", SAMPLE_CSV.encode(), "text/csv")}, headers=headers)
    assert res.status_code == 413


def test_admin_writes_409_when_store_read_only(api_client, monkeypatch):
    client, _ = api_client
    monkeypatch.setenv("CMS_READ_ONLY", "1")
    token = admin_token(client)
    headers = auth_header(token)

    res = client.post("/admin/hotels", json={"name": "Nope"}, headers=headers)
    assert res.status_code == 409
    assert "read-only" in res.json()["detail"]

    res = client.post("/admin/import", files={"file": ("h.csv", SAMPLE_CSV.encode(), "text/csv")}, headers=headers)
    assert res.status_code == 409


# ---- middleware ----

def test_request_id_is_echoed_and_generated(api_client):
    client, _ = api_client
    res = client.get("/health", headers={"X-Request-ID": "my-req-42"})
    assert res.headers["x-request-id"] == "my-req-42"
    res = client.get("/health")
    assert len(res.headers["x-request-id"]) == 12
