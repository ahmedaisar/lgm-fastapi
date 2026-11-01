"""Pricing markup + nationality resolution (ISO3 / name / numeric)."""

import pytest

from lib.pricing import apply_markup
from lib.data.nationality import NationalityError, load_mapping, resolve_nationality


# ---- pricing ----

def test_apply_markup_rounds_to_cents():
    assert apply_markup(100.0, 10) == 110.0
    assert apply_markup(850, 10) == 935.0
    assert apply_markup(33.333, 10) == 36.67
    assert apply_markup(1200.50, 10) == 1320.55
    assert apply_markup(99.99, 0) == 99.99
    assert apply_markup(100.0, 12.5) == 112.5


def test_apply_markup_passes_null_through():
    assert apply_markup(None, 10) is None


# ---- nationality ----

@pytest.fixture(scope="module")
def mapping():
    return load_mapping()


def test_load_mapping_real_table(mapping):
    assert len(mapping) >= 200
    assert {"id", "iso3", "name"} <= set(mapping[0].keys())


def test_resolve_by_iso3(mapping):
    assert resolve_nationality("MDV", mapping) == "228"
    assert resolve_nationality("mdv", mapping) == "228"
    assert resolve_nationality("USA", mapping) == "413"


def test_resolve_by_exact_country_name(mapping):
    assert resolve_nationality("Maldives", mapping) == "228"
    assert resolve_nationality("maldives", mapping) == "228"


def test_resolve_numeric_passthrough(mapping):
    assert resolve_nationality("413", mapping) == "413"
    assert resolve_nationality(" 228 ", mapping) == "228"


def test_resolve_empty_raises(mapping):
    with pytest.raises(NationalityError):
        resolve_nationality("", mapping)
    with pytest.raises(NationalityError):
        resolve_nationality(None, mapping)


def test_resolve_unknown_raises(mapping):
    with pytest.raises(NationalityError, match="Unknown nationality"):
        resolve_nationality("Atlantis", mapping)


def test_resolve_ambiguous_name_raises():
    ambiguous = [
        {"id": "1", "iso3": "AAA", "name": "Dup"},
        {"id": "2", "iso3": "BBB", "name": "Dup"},
    ]
    with pytest.raises(NationalityError, match="Ambiguous"):
        resolve_nationality("Dup", ambiguous)


def test_bad_mapping_file_raises_server_error():
    # missing/corrupt mapping file = deployment bug -> RuntimeError (500),
    # NOT NationalityError (which would misreport it as bad client input)
    with pytest.raises(RuntimeError):
        load_mapping("/nonexistent/nationality.json")
