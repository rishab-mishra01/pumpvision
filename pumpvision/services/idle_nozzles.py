"""Nozzles that sold nothing in a shift, proven by the machine, don't need a typed reading.

A nozzle is idle for op day D when IRAS has both its 06:00 opening (NozzleTotalizer dated D)
and its 06:00 closing (dated D+1) and the two are identical, and the last manual reading
for that nozzle (if any) agrees with that value. Typing the same number again adds nothing,
so at submit time such a nozzle is closed automatically with the IRAS value. Until the IRAS
closing for the shift is in (the VPS scrape runs ~06:30), nothing is idle and a reading is
still required.
"""
from datetime import datetime, timedelta

SAME = 0.005   # totalizers carry 2 decimals


def iras_idle_nozzles(op_date):
    """{nozzle_no: totalizer value} for nozzles IRAS shows unmoved over the shift op_date."""
    from pumpvision.models import ManualTotalizerReading, NozzleTotalizer

    opening = {r.nozzle_no: r.totalizer_end for r in NozzleTotalizer.query.filter_by(operational_date=op_date)}
    closing = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=op_date + timedelta(days=1))}
    idle = {}
    for n, close in closing.items():
        if n not in opening or abs(close - opening[n]) > SAME:
            continue
        last_manual = (ManualTotalizerReading.query
                       .filter(ManualTotalizerReading.nozzle_no == n,
                               ManualTotalizerReading.operational_date < op_date)
                       .order_by(ManualTotalizerReading.operational_date.desc()).first())
        if last_manual is not None and abs(last_manual.totalizer_value - close) > SAME:
            continue                      # staff's own last reading disagrees: ask for one
        idle[n] = close
    return idle


def autoclose_idle(op_date, labels):
    """For each nozzle label (from NOZZLE_LABEL_MAP) with no reading for op_date that IRAS shows
    idle, add the reading from IRAS. Returns the labels filled. Caller commits."""
    from pumpvision.constants import NOZZLE_LABEL_MAP
    from pumpvision.models import ManualTotalizerReading, db

    have = {r.nozzle_label for r in ManualTotalizerReading.query.filter_by(operational_date=op_date)}
    idle = iras_idle_nozzles(op_date)
    filled = []
    for label in labels:
        info = NOZZLE_LABEL_MAP.get(label) or {}
        n = info.get("nozzle_no")
        if label in have or n not in idle:
            continue
        db.session.add(ManualTotalizerReading(
            operational_date=op_date, nozzle_label=label, nozzle_no=n, product=info["product"],
            totalizer_value=idle[n], recorded_at=datetime.now(), is_locked=False))
        filled.append(label)
    db.session.flush()
    return filled
