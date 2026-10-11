"""CNG selling price = CGD eLedger cost price for the operational date + a fixed margin.

The cost price is not stored as its own column. It is the eLedger debit on the day's
CGD supply rows divided by the kg on those rows, which is what `sdms_pad_exporter`
saves in `sdms_summaries.cng_revenue` (ledger amount) and `cng_kg_total`. Despite the
column name, `cng_revenue` is therefore what the outlet *paid* CGD, not what it sold for.
"""
import math
from decimal import ROUND_HALF_UP, Decimal

CNG_MARGIN_PER_KG = Decimal("2.47")
# A ledger rate outside this band is a parse error, not a price.
COST_MIN_PER_KG = 20.0
COST_MAX_PER_KG = 400.0

BASIS_ELEDGER = "eledger_cost_plus_margin"


def _money(x) -> float:
    return float(Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def cost_per_kg(kg, ledger_amount):
    """eLedger cost price per kg, or None when it cannot be trusted."""
    try:
        kg = float(kg or 0.0)
        amount = float(ledger_amount or 0.0)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(kg) and math.isfinite(amount)) or kg <= 0 or amount <= 0:
        return None
    cost = amount / kg
    if not COST_MIN_PER_KG <= cost <= COST_MAX_PER_KG:
        return None
    return _money(cost)


def selling_price(cost):
    """Cost + fixed margin, to the paisa."""
    return _money(Decimal(str(cost)) + CNG_MARGIN_PER_KG)


def price_from_ledger(kg, ledger_amount):
    """(cost, selling_price, revenue) for a day's kg and eLedger amount; all None if cost invalid."""
    cost = cost_per_kg(kg, ledger_amount)
    if cost is None:
        return None, None, None
    price = selling_price(cost)
    return cost, price, _money(Decimal(str(float(kg))) * Decimal(str(price)))
