"""Opening/closing nozzle totalizers for an operational day.

The IRAS scraper can serve a stale totalizer for a nozzle for days and then catch up in
one jump, which dumps several days of sales into one day (nozzle 16, Oct 2026: stuck 3 days,
then +1,807 L; the day's HS looked ~580 L too high). The shift-closing manual readings
are the staff's own figures, so they win when a nozzle has BOTH its opening and closing
manual reading; otherwise that nozzle falls back to the scraped pair. Both ends always
come from the same source so the scraper's 01:05 timing is never mixed with the 06:00 close.

A manual reading only counts as the close of a day if it was recorded at that day's close. From
CYCLE_CUTOVER that is the 00:00-00:30 window on the next date (services/operational.py); earlier
days keep the old rule, within a few hours of 06:00 (MANUAL_EARLY / MANUAL_LATE). A reading entered
a day or more late (a missed shift closed afterwards) is the meter at another time, so it is
ignored for that day.
"""
from datetime import datetime, time, timedelta

from pumpvision.services.operational import close_window_for, cycle_cutover

MANUAL_EARLY = timedelta(hours=1)     # a reading just before 06:00 still counts
MANUAL_LATE = timedelta(hours=6)      # ...and one up to 6 h after; later is a different time


def valid_manual_closes(day):
    """{nozzle_no: value} of the staff's shift-close readings for `day` that really are its
    06:00 close: taken within MANUAL_EARLY/MANUAL_LATE of the boundary. One taken a day or more
    later (a missed shift closed late) is the meter at another time, so it is left out."""
    from pumpvision.models import ManualTotalizerReading

    if day >= cycle_cutover():
        lo, hi = close_window_for(day)
        ok = lambda at: lo <= at <= hi                                    # noqa: E731
    else:
        boundary = datetime.combine(day + timedelta(days=1), time(6, 0))
        ok = lambda at: -MANUAL_EARLY <= at - boundary <= MANUAL_LATE     # noqa: E731
    return {r.nozzle_no: r.totalizer_value
            for r in ManualTotalizerReading.query.filter_by(operational_date=day)
            if r.nozzle_no is not None and r.recorded_at is not None and ok(r.recorded_at)}


def opening_reading(nozzle_no, op_date):
    """Opening totalizer of a nozzle for shift op_date, by the same rule as the dashboard:
    1. the staff's close of the previous shift (shift-close data is the source of truth);
    2. if there is no valid one, IRAS's 06:00 reading (NozzleTotalizer dated op_date);
    3. if IRAS has none either, the most recent earlier shift-close reading.
    """
    from pumpvision.models import ManualTotalizerReading, NozzleTotalizer

    prev = valid_manual_closes(op_date - timedelta(days=1))
    if nozzle_no in prev:
        return prev[nozzle_no]
    row = NozzleTotalizer.query.filter_by(nozzle_no=nozzle_no, operational_date=op_date).first()
    if row:
        return row.totalizer_end
    row = (ManualTotalizerReading.query
           .filter(ManualTotalizerReading.nozzle_no == nozzle_no,
                   ManualTotalizerReading.operational_date < op_date)
           .order_by(ManualTotalizerReading.operational_date.desc())
           .first())
    return row.totalizer_value if row else None


def totalizers_for_day(op_date):
    """Return (opening, closing, manual_nozzles): {nozzle_no: value} dicts plus the set of
    nozzle numbers that used manual readings."""
    from pumpvision.models import NozzleTotalizer

    next_date = op_date + timedelta(days=1)
    opening = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=op_date)}
    closing = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=next_date)}

    m_open, m_close = valid_manual_closes(op_date - timedelta(days=1)), valid_manual_closes(op_date)
    used = set()
    for n in set(m_open) & set(m_close):
        opening[n], closing[n] = m_open[n], m_close[n]
        used.add(n)
    return opening, closing, used
