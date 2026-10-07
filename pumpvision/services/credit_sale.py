"""Recording a credit sale -- one place for the rules, used by the attendant and
manager screens so they cannot drift apart.

A sale is entered either as an amount (₹) or a quantity (litres, or kg for CNG).
The rate is today's IRAS price for liquid fuel (latest known if today's has not
landed yet -- see services/prices.get_rsp) and the owner-set pump price for CNG.
"""
import math
from datetime import date, datetime

LIQUID = ("HS", "MS", "X2", "XG")


def rate_map(day: date | None = None) -> dict:
    """{'HS': 101.16, ..., 'CNG': 99.8}; a product with no known rate maps to None."""
    from pumpvision.models import AppSetting, db
    from pumpvision.services.prices import get_rsp

    day = day or date.today()
    rates = {p: get_rsp(p, day) for p in LIQUID}
    cng = db.session.get(AppSetting, "cng_rsp_per_kg")
    try:
        rates["CNG"] = float(cng.value) if cng and float(cng.value) > 0 else None
    except ValueError:
        rates["CNG"] = None
    return rates


def record_sale(customer, vehicle_number, product, input_mode, quantity_raw, entered_by, rates=None, when=None):
    """Validate and save one credit sale and add it to the customer's balance.

    `when` backdates the sale (owner correction of a forgotten entry); default is now.

    Returns (transaction, errors). errors are short codes -- no_vehicle, bad_product,
    no_rate, qty_not_number, qty_not_positive -- for each screen to word in its own
    language. Nothing is written when there are errors.
    """
    from pumpvision.models import CreditTransaction, db

    rates = rates if rates is not None else rate_map()
    vehicle_number = (vehicle_number or "").strip().upper()
    product = (product or "").strip().upper()
    errors = []

    if not vehicle_number:
        errors.append("no_vehicle")
    if product not in rates:
        errors.append("bad_product")
    rate = rates.get(product)
    if product in rates and rate is None:
        errors.append("no_rate")

    quantity = None
    try:
        quantity = float(quantity_raw)
        if not math.isfinite(quantity):       # "nan"/"inf" would poison the balance
            raise ValueError
        if quantity <= 0:
            errors.append("qty_not_positive")
    except (ValueError, TypeError):
        errors.append("qty_not_number")
    if errors:
        return None, errors

    if input_mode == "litres":
        litres, amount = quantity, round(quantity * rate, 2)
    else:
        litres, amount = round(quantity / rate, 3), round(quantity, 2)

    now = when or datetime.now()
    txn = CreditTransaction(
        customer_id=customer.customer_id,
        vehicle_number=vehicle_number,
        transaction_date=now.date(),
        transaction_time=now.time(),
        product=product,
        litres=litres,
        rate_per_litre=rate,
        amount=amount,
        attendant_name=entered_by,
        is_legacy_entry=False,
    )
    db.session.add(txn)
    customer.add_to_balance(amount)
    db.session.commit()
    return txn, []
