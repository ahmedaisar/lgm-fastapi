"""Port of lib/cms/csv.js — CSV import/export for CMS Hotels.

Columns:
id,name,description,atoll,stars,photos,amenities,website,portal_hotel_id,portal_slug,
check_in_from,...,fine_print
photos/amenities are |-separated URL/tag lists. Upsert rule: match `id` when
given, else `portal_hotel_id`, else create.
"""

import csv
import io
from typing import Any, Dict, List, Optional, Tuple

from ..api.common import HttpError
from . import store

COLUMNS = [
    "id",
    "name",
    "description",
    "atoll",
    "stars",
    "photos",
    "amenities",
    "website",
    "portal_hotel_id",
    "portal_slug",
    "check_in_from",
    "check_in_notes",
    "check_out_until",
    "cancellation_prepayment",
    "child_policies",
    "cot_extra_bed_policies",
    "age_restriction",
    "pets_allowed",
    "pets_notes",
    "payment_methods",
    "fine_print",
]

_CSV_FIELDS = [
    "name",
    "description",
    "atoll",
    "stars",
    "photos",
    "amenities",
    "website",
    "portal_hotel_id",
    "portal_slug",
    "check_in_from",
    "check_in_notes",
    "check_out_until",
    "cancellation_prepayment",
    "child_policies",
    "cot_extra_bed_policies",
    "age_restriction",
    "pets_allowed",
    "pets_notes",
    "payment_methods",
    "fine_print",
]


def parse_csv(text: str) -> List[Dict[str, str]]:
    try:
        reader = csv.DictReader(io.StringIO(str(text or "")))
        rows = [dict(row) for row in reader]
    except csv.Error as err:
        raise HttpError(400, f"CSV parse error: {err}") from None
    # PapaParse skipEmptyLines: drop rows where every cell is blank
    return [r for r in rows if any((v or "").strip() for v in r.values())]


def row_to_input(row: Dict[str, str]) -> Tuple[Optional[str], Dict[str, str]]:
    def get(k: str) -> str:
        v = row.get(k)
        return "" if v is None else str(v)

    hotel_id = get("id").strip()
    hotel_id = None if hotel_id == "" else hotel_id
    input_row = {field: get(field) for field in _CSV_FIELDS}
    return hotel_id, input_row


# Blank CSV cells never wipe stored values on update (use PUT to clear a
# field explicitly); on create they fall back to defaults.
def _non_blank(input_row: Dict[str, str]) -> Dict[str, str]:
    return {k: v for k, v in input_row.items() if v != ""}


def import_csv(text: str, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Upsert rows against ONE snapshot and persist once (batch transact).

    Match rule: `id` when given, else `portal_hotel_id`, else create.
    Blank CSV cells never wipe stored values on update (use PUT to clear a
    field explicitly); on create they fall back to defaults.
    """
    rows = parse_csv(text)
    created = 0
    updated = 0
    errors: List[Dict[str, Any]] = []
    with store.transact(db_path) as records:
        by_id = {r.get("id"): r for r in records if r.get("id")}
        for i, row in enumerate(rows):
            line = i + 2  # header is line 1
            try:
                hotel_id, input_row = row_to_input(row)
                if hotel_id and hotel_id in by_id:
                    store.update_in(records, hotel_id, _non_blank(input_row))
                    updated += 1
                    continue
                portal_pid = input_row["portal_hotel_id"].strip()
                if portal_pid != "":
                    match = next(
                        (
                            r
                            for r in records
                            if str(r.get("portal_hotel_id") or "") == portal_pid
                        ),
                        None,
                    )
                    if match:
                        store.update_in(records, match["id"], _non_blank(input_row))
                        updated += 1
                        continue
                if input_row["name"].strip() == "":
                    raise HttpError(400, f"line {line}: name is required")
                rec = store.create_in(records, input_row)
                by_id[rec["id"]] = rec
                created += 1
            except Exception as err:
                message = err.message if isinstance(err, HttpError) else str(err)
                errors.append({"line": line, "error": message})
    return {"created": created, "updated": updated, "errors": errors}


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if value is False:
        return "false"
    if value is True:
        return "true"
    if isinstance(value, list):
        return "|".join(str(v) for v in value)
    return str(value)


def to_csv(records: List[Dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")  # PapaParse emits CRLF
    writer.writerow(COLUMNS)
    for r in records or []:
        writer.writerow(
            [
                r.get("id") or "",
                r.get("name") or "",
                r.get("description") or "",
                r.get("atoll") or "",
                "" if r.get("stars") is None else str(r.get("stars")),
                "|".join(r.get("photos") or []),
                "|".join(r.get("amenities") or []),
                r.get("website") or "",
                r.get("portal_hotel_id") or "",
                r.get("portal_slug") or "",
                r.get("check_in_from") or "",
                r.get("check_in_notes") or "",
                r.get("check_out_until") or "",
                r.get("cancellation_prepayment") or "",
                r.get("child_policies") or "",
                r.get("cot_extra_bed_policies") or "",
                r.get("age_restriction") or "",
                _cell(r.get("pets_allowed")),
                r.get("pets_notes") or "",
                "|".join(r.get("payment_methods") or []),
                r.get("fine_print") or "",
            ]
        )
    return buffer.getvalue()
