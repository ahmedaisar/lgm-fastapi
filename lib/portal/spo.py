"""Port of lib/portal/spo.js — SPO (special offer) parsing for unihotel.org."""

import re
from typing import Any, Dict, List

from bs4 import BeautifulSoup, Comment, NavigableString

from .textutils import _clean


def parse_spo_template(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html or "", "html.parser")
    offers: List[Dict[str, Any]] = []
    for table in soup.select("table.table-striped"):
        title_cell = table.select_one("h4")
        if title_cell is None:
            continue
        # Direct text nodes only (cheerio: clone().children().remove().end().text()).
        raw_title = _clean(
            "".join(
                str(s)
                for s in title_cell.find_all(string=True, recursive=False)
                if not isinstance(s, Comment) and isinstance(s, NavigableString)
            )
        )
        # Russian portal: "(Код: X)"; unihotel.org: "( Code: X )"
        raw_title = re.sub(r"\s*\(\s*(?:Код|Code):\s*[^)]+\)\s*$", "", raw_title, flags=re.IGNORECASE).strip()
        if not raw_title:
            continue

        discount_el = title_cell.select_one(".text-success")
        discount_text = _clean(discount_el.get_text()) if discount_el is not None else ""
        # signed on both portals ("-20%" → -20)
        discount_match = re.search(r"([+-]?\d+(?:\.\d+)?)\s*%", discount_text or "")
        discount_percent = float(discount_match[1]) if discount_match else None

        rows: Dict[str, str] = {}
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            label = _clean(tds[0].get_text())
            if label.endswith(":"):
                label = label[:-1]
            value = _clean(tds[1].get_text())
            if label:
                rows[label] = value

        rooms: List[str] = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if tds and re.search(r"Комнаты|Rooms", tds[0].get_text(), re.IGNORECASE):
                if len(tds) >= 2:
                    raw = _clean(tds[1].get_text())
                    rooms = [
                        re.sub(r":\s*\d+%\s*$", "", s).strip()
                        for s in (part.strip() for part in raw.split(","))
                        if s
                    ]
                    rooms = [r for r in rooms if r]
                break

        extra_bed_rooms: List[str] = []
        for tr in table.find_all("tr"):
            tds = tr.find_all("td")
            if tds and re.search(r"Дополнительная кровать|Extra bed", tds[0].get_text(), re.IGNORECASE):
                if len(tds) >= 2:
                    extra_bed_rooms = [
                        _clean(li.get_text()) for li in tds[1].find_all("li")
                    ]
                    extra_bed_rooms = [r for r in extra_bed_rooms if r]
                break

        small_el = table.select_one("h4 small")
        type_text = _clean(small_el.get_text()) if small_el is not None else ""

        offers.append(
            {
                "name": raw_title,
                "discount_percent": discount_percent,
                "type": type_text or None,
                # Russian portal: "Размещение"/"Период бронирования"; unihotel.org: "Accommodation"/"Booking period"
                "stay_period": rows.get("Accommodation") or rows.get("Размещение") or None,
                "booking_period": rows.get("Booking period") or rows.get("Период бронирования") or None,
                "rooms": rooms or None,
                "extra_bed_rooms": extra_bed_rooms or None,
            }
        )
    return offers
