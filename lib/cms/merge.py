"""Port of lib/cms/merge.js — attach CMS Hotel content to portal results.

Link internals (portal_hotel_id/portal_slug) stay out of the public view.

Improvement over the Express original: rows are matched by portal_hotel_id
OR portal_slug. The portal omits data-hotel-id on some search rows (hotel_id
falls back to the slug), so id-only matching silently missed CMS content
there — the JS original had the same gap.
"""

from typing import Any, Dict, List, Optional


def public_view(rec: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rec:
        return None
    return {
        "id": rec.get("id"),
        "name": rec.get("name"),
        "description": rec.get("description") or "",
        "atoll": rec.get("atoll") or "",
        "stars": rec.get("stars"),
        "photos": rec.get("photos") or [],
        "amenities": rec.get("amenities") or [],
        "website": rec.get("website") or "",
        "check_in_from": rec.get("check_in_from") or "",
        "check_in_notes": rec.get("check_in_notes") or "",
        "check_out_until": rec.get("check_out_until") or "",
        "cancellation_prepayment": rec.get("cancellation_prepayment") or "",
        "child_policies": rec.get("child_policies") or "",
        "cot_extra_bed_policies": rec.get("cot_extra_bed_policies") or "",
        "age_restriction": rec.get("age_restriction") or "",
        "pets_allowed": rec.get("pets_allowed"),
        "pets_notes": rec.get("pets_notes") or "",
        "payment_methods": rec.get("payment_methods") or [],
        "fine_print": rec.get("fine_print") or "",
    }


def _indexes(records: Optional[List[Dict[str, Any]]]):
    by_id: Dict[str, Dict[str, Any]] = {}
    by_slug: Dict[str, Dict[str, Any]] = {}
    for rec in records or []:
        pid = rec.get("portal_hotel_id")
        if pid is not None and pid != "":
            by_id[str(pid)] = rec
        slug = rec.get("portal_slug")
        if slug:
            by_slug.setdefault(str(slug), rec)
    return by_id, by_slug


def _lookup(key: Any, by_id: Dict, by_slug: Dict) -> Optional[Dict[str, Any]]:
    k = str(key if key is not None else "")
    if not k:
        return None
    return by_id.get(k) or by_slug.get(k)


def enrich_search_rows(rows: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_id, by_slug = _indexes(records)
    return [{**row, "cms": public_view(_lookup(row.get("hotel_id"), by_id, by_slug))} for row in (rows or [])]


def enrich_quote(quote: Dict[str, Any], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_id, by_slug = _indexes(records)
    return {
        **quote,
        "cms": public_view(_lookup(quote.get("hotel_id") or quote.get("hotel_slug"), by_id, by_slug)),
    }
