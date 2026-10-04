"""Attendant meter readings vs IRAS, allowing for when the reading was taken.

IRAS gives the exact meter at 06:00, the shift boundary. Attendants read the
meter whenever they get to it -- 06:20, 07:45 -- so their closing reading
includes the fuel sold since 06:00. Comparing it straight against IRAS turns
every late reading into a false "excess". Here the expected gap is estimated
from the time the reading was taken and that nozzle's own sales rate, and only
a gap the timing cannot explain is flagged.

    gap      = attendant closing - IRAS 06:00 reading
    expected = fuel that nozzle dispensed between 06:00 and the reading, from
               1. the tank gauge (ATG, every 30 min): the product's draw-down over
                  that window, split by the nozzle's share of the day's sales; or
               2. if the gauge can't be used (no readings around the window, a
                  delivery in it, or XG's unreliable probe): the nozzle's average
                  litres/hour that day x open hours in the window
               plus the 5 L pump test if read after it (~08:20) -- the meter counts
               it but the fuel goes back into the tank.
    flag     when |gap - expected| > TOLERANCE_L + pct x expected, where pct is
             tighter for the gauge (measured) than for the day average (guessed)
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

OPEN_HOURS_PER_DAY = 19          # outlet open ~06:00 -> 01:00
CLOSED_FROM, CLOSED_TO = 1, 6    # 01:00-06:00 nothing is sold
PUMP_TEST_AT = time(8, 20)
PUMP_TEST_L = 5.0
TOLERANCE_L = 10.0               # reading error / rounding
TOLERANCE_PCT = 0.6              # day-average estimate: sales are bursty
TOLERANCE_PCT_ATG = 0.2          # tank-gauge estimate: measured, but split by share
ATG_MAX_STEP_MIN = 120           # a gauge reading must be within 2 h of each end
ATG_DELIVERY_L = 50              # a rise this big inside the window = a delivery
TANK = {"HS": 1, "MS": 2, "X2": 3}   # XG (tank 4) probe is unreliable

NOZZLES = [  # (label shown, ManualTotalizerReading label, IRAS nozzle no, product)
    ("HS1", "HSD 1", 7, "HS"), ("HS2", "HSD 2", 16, "HS"), ("MS1", "MS 1", 18, "MS"),
    ("MS2", "MS 2", 15, "MS"), ("X2", "XP", 17, "X2"), ("XG", "XG", 11, "XG"),
]


def _atg_volume_at(series, t):
    """Tank volume at t, interpolated between the gauge readings around it, or None."""
    before = [(ts, v) for ts, v in series if ts <= t]
    after = [(ts, v) for ts, v in series if ts >= t]
    if not before or not after:
        return None
    (t0, v0), (t1, v1) = before[-1], after[0]
    if (t - t0).total_seconds() > ATG_MAX_STEP_MIN * 60 or (t1 - t).total_seconds() > ATG_MAX_STEP_MIN * 60:
        return None
    if t1 == t0:
        return v0
    return v0 + (v1 - v0) * (t - t0).total_seconds() / (t1 - t0).total_seconds()


def _atg_drawdown(series, a, b):
    """Litres drawn from the tank between a and b, or None if unusable (gap, delivery)."""
    va, vb = _atg_volume_at(series, a), _atg_volume_at(series, b)
    if va is None or vb is None:
        return None
    inside = [v for ts, v in series if a <= ts <= b]
    for x, y in zip(inside, inside[1:]):
        if y - x > ATG_DELIVERY_L:
            return None
    return va - vb


def _open_hours(a: datetime, b: datetime) -> float:
    """Hours between a and b (a <= b) when the outlet is open (outside 01:00-06:00)."""
    total, t = 0.0, a
    while t < b:
        step = min(b, (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0))
        if not (CLOSED_FROM <= t.hour < CLOSED_TO):
            total += (step - t).total_seconds() / 3600
        t = step
    return total


@dataclass
class NozzleCheck:
    name: str
    attendant: float | None
    taken_at: datetime | None
    iras: float | None
    minutes_from_six: int | None   # + after 06:00, - before
    gap: float | None
    expected: float | None
    status: str                    # ok | check | missing_entry | missing_iras
    note: str
    method: str = ""               # "tank gauge" | "day average" | ""


def check_day(op_date: date):
    """Checks for the shift op_date 06:00 -> op_date+1 06:00."""
    from pumpvision.models import ManualTotalizerReading, NozzleTotalizer, TankReading

    boundary = datetime.combine(op_date + timedelta(days=1), time(6, 0))
    readings = {r.nozzle_label: r for r in
                ManualTotalizerReading.query.filter_by(operational_date=op_date).all()}
    opening = {r.nozzle_no: r.totalizer_end for r in
               NozzleTotalizer.query.filter_by(operational_date=op_date).all()}
    closing = {r.nozzle_no: r.totalizer_end for r in
               NozzleTotalizer.query.filter_by(operational_date=op_date + timedelta(days=1)).all()}

    day_litres = {n: closing[n] - opening[n] for n in closing if n in opening}
    product_litres = {}
    for _, _, n, prod in NOZZLES:
        product_litres[prod] = product_litres.get(prod, 0.0) + max(0.0, day_litres.get(n, 0.0))
    atg = {}
    for prod, tank in TANK.items():
        rows = (TankReading.query
                .filter(TankReading.tank_id == tank,
                        TankReading.scraped_at >= boundary - timedelta(hours=3),
                        TankReading.scraped_at <= boundary + timedelta(hours=30))
                .order_by(TankReading.scraped_at).all())
        atg[prod] = [(r.scraped_at, r.volume_litres) for r in rows if r.volume_litres is not None]

    out = []
    for name, label, n, prod in NOZZLES:
        r = readings.get(label)
        iras = closing.get(n)
        att = r.totalizer_value if r else None
        taken = r.recorded_at if r else None
        mins = round((taken - boundary).total_seconds() / 60) if taken else None
        if r is None:
            out.append(NozzleCheck(name, None, None, iras, None, None, None, "missing_entry", "not entered"))
            continue
        if iras is None:
            out.append(NozzleCheck(name, att, taken, None, mins, None, None, "missing_iras",
                                   "IRAS 06:00 reading not in yet"))
            continue

        litres = day_litres.get(n)
        a, b, sign = (boundary, taken, 1) if taken >= boundary else (taken, boundary, -1)
        drawn = _atg_drawdown(atg[prod], a, b) if prod in atg else None
        share = (max(0.0, litres) / product_litres[prod]) if litres and product_litres.get(prod) else None
        if drawn is not None and share is not None:
            expected, pct, method = sign * max(0.0, drawn) * share, TOLERANCE_PCT_ATG, "tank gauge"
        else:
            rate = litres / OPEN_HOURS_PER_DAY if litres and litres > 0 else 0.0
            expected, pct, method = sign * rate * _open_hours(a, b), TOLERANCE_PCT, "day average"
        if taken >= boundary and taken.time() >= PUMP_TEST_AT and taken.date() == boundary.date():
            expected += PUMP_TEST_L
        gap = att - iras
        allowed = TOLERANCE_L + pct * abs(expected)
        off = gap - expected

        if taken >= boundary and gap < -TOLERANCE_L:
            status, note = "check", "below the 06:00 reading although taken later -- likely a wrong entry"
        elif abs(off) <= allowed:
            status = "ok"
            note = "matches" if abs(expected) < 1 else "gap explained by reading time"
        elif off > 0:
            status, note = "check", f"{off:,.0f} L more than the reading time explains"
        else:
            status, note = "check", f"{-off:,.0f} L less than the reading time explains"
        out.append(NozzleCheck(name, att, taken, iras, mins, round(gap, 2), round(expected, 1), status, note, method))
    return out
