"""Port of lib/pricing.js.

Sell-price markup over portal net prices. MARKUP is a percent (10 = +10%).
Applied to room net_price in API responses; the portal layer stays pure-net.
Transfers are left untouched.
"""

from typing import Optional


def apply_markup(net_price: Optional[float], markup_pct: float) -> Optional[float]:
    if net_price is None:
        return None
    return round(float(net_price) * (1 + float(markup_pct) / 100) * 100) / 100
