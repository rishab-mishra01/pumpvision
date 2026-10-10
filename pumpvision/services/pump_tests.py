"""Pump tests: a test is a record, not an assumption.

The nozzle totalizer is the ground truth for what went through the nozzle. When staff did a
pump test they enter it (nozzle, litres, returned to tank or not); only then is that volume
taken out of sales. It is never a sale, so it never counts towards money. If it was poured
back into the tank the tank gauge never lost it, so
    sold litres = totalizer litres - tested litres = tank drawdown,
and volume and money reconcile over time. If it left the tank (returned_to_tank False) the
tank lost it too, and the reconciliation has to expect that extra loss.
"""
import math

from pumpvision.constants import NOZZLE_SALES_PRODUCT, PUMP_TEST_NOZZLES

MAX_TEST_LITRES = 100


def record_test(op_date, nozzle_no, litres_raw, returned_to_tank, note, user_id):
    """Validate and save one pump test. Returns (FuelTest | None, error_code | None).

    error codes: bad_nozzle, bad_litres.
    """
    from pumpvision.models import FuelTest, db

    try:
        nozzle_no = int(nozzle_no)
    except (TypeError, ValueError):
        return None, "bad_nozzle"
    if nozzle_no not in NOZZLE_SALES_PRODUCT:
        return None, "bad_nozzle"
    try:
        litres = float(litres_raw)
    except (TypeError, ValueError):
        return None, "bad_litres"
    if not math.isfinite(litres) or not 0 < litres <= MAX_TEST_LITRES:
        return None, "bad_litres"

    t = FuelTest(op_date=op_date, product=NOZZLE_SALES_PRODUCT[nozzle_no], nozzle_no=nozzle_no,
                 litres=round(litres, 2), returned_to_tank=bool(returned_to_tank),
                 note=(note or "").strip()[:200] or None, logged_by=user_id)
    db.session.add(t)
    db.session.commit()
    return t, None


def nozzle_label(nozzle_no):
    return PUMP_TEST_NOZZLES.get(nozzle_no, "")


def tests_for_day(op_date):
    """All pump tests recorded for the shift, oldest first."""
    from pumpvision.models import FuelTest
    return FuelTest.query.filter_by(op_date=op_date).order_by(FuelTest.id).all()


def tested_by_nozzle(op_date):
    """{nozzle_no: litres tested} for the shift; entries without a nozzle are left out."""
    out = {}
    for t in tests_for_day(op_date):
        if t.nozzle_no is not None:
            out[t.nozzle_no] = out.get(t.nozzle_no, 0.0) + t.litres
    return out
