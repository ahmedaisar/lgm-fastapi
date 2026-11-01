"""CMS JSON store: CRUD, normalization/validation, read-only guard."""

import pytest

from lib.api.common import HttpError
from lib.cms import store

from conftest import admin_env, cms_env  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _cms(cms_env):  # every test in this module gets an isolated store
    return cms_env


def test_create_applies_slug_and_defaults():
    rec = store.create({"name": "Anantara Dhigu"})
    assert rec["id"] == "anantara-dhigu"
    assert rec["description"] == ""
    assert rec["photos"] == []
    assert rec["stars"] is None
    assert rec["portal_hotel_id"] is None
    assert rec["updated_at"].endswith("Z")


def test_create_dedupes_slugs():
    first = store.create({"name": "Anantara Dhigu"})
    second = store.create({"name": "Anantara Dhigu"})
    assert first["id"] == "anantara-dhigu"
    assert second["id"] == "anantara-dhigu-2"


def test_get_update_remove_roundtrip():
    rec = store.create({"name": "Kuramathi", "description": "old"})
    got = store.get("kuramathi")
    assert got["name"] == "Kuramathi"

    updated = store.update("kuramathi", {"description": "new", "stars": "4", "id": "HACK"})
    assert updated["description"] == "new"
    assert updated["stars"] == 4.0
    assert updated["id"] == "kuramathi"  # id cannot be reassigned

    assert store.update("missing-hotel", {"name": "x"}) is None
    assert store.remove("kuramathi") is True
    assert store.remove("kuramathi") is False
    assert store.get("kuramathi") is None
    assert store.list() == []


def test_slugify_strips_accents_and_symbols():
    rec = store.create({"name": "Café del Mar — Malé & Resort!"})
    assert rec["id"] == "cafe-del-mar-male-resort"


def test_normalize_validation_errors():
    with pytest.raises(HttpError):
        store.create({})  # name required
    with pytest.raises(HttpError):
        store.create({"name": "x", "stars": 9})
    with pytest.raises(HttpError):
        store.create({"name": "x", "stars": "abc"})
    with pytest.raises(HttpError):
        store.create({"name": "x", "portal_hotel_id": "not-numeric"})
    with pytest.raises(HttpError):
        store.create({"name": "x", "pets_allowed": "maybe"})
    with pytest.raises(HttpError):
        store.create(["not", "a", "dict"])


def test_boolean_and_null_coercion():
    rec = store.create({"name": "B", "pets_allowed": "yes"})
    assert rec["pets_allowed"] is True
    rec = store.update(rec["id"], {"pets_allowed": "not allowed"})
    assert rec["pets_allowed"] is False
    rec = store.update(rec["id"], {"pets_allowed": ""})
    assert rec["pets_allowed"] is None


def test_updates_persist_to_disk_between_cache_reads(cms_env):
    store.create({"name": "Persisted"})
    store._cache.clear()  # simulate another process editing -> mtime cache miss
    assert [r["name"] for r in store.load_all()] == ["Persisted"]


# ---- read-only guard (Vercel / ephemeral filesystems) ----

def test_read_only_env_blocks_writes_with_409(monkeypatch, _cms):
    monkeypatch.setenv("CMS_READ_ONLY", "1")
    assert store.read_only() is True
    with pytest.raises(HttpError) as err:
        store.create({"name": "Nope"})
    assert err.value.status == 409


def test_vercel_env_auto_enables_read_only(monkeypatch, _cms):
    monkeypatch.delenv("CMS_READ_ONLY", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    assert store.read_only() is True
    with pytest.raises(HttpError):
        store.remove("x")


def test_cms_read_only_zero_allows_writes(monkeypatch, _cms):
    monkeypatch.setenv("CMS_READ_ONLY", "0")
    monkeypatch.delenv("VERCEL", raising=False)
    assert store.read_only() is False
    assert store.create({"name": "Writable"})["id"] == "writable"


def test_reads_still_work_in_read_only_mode(monkeypatch, _cms):
    store.create({"name": "Visible"})
    monkeypatch.setenv("CMS_READ_ONLY", "1")
    assert [r["name"] for r in store.list()] == ["Visible"]
