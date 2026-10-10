"""Opening/closing nozzle totalizers for an operational day.

The IRAS scraper can serve a stale totalizer for a nozzle for days and then catch up in
one jump, which dumps several days of sales into one day (nozzle 16, Oct 2026: stuck 3 days,
then +1,807 L; the day's HS looked ~580 L too high). The shift-closing manual readings
are the staff's own figures, so they win when a nozzle has BOTH its opening and closing
manual reading; otherwise that nozzle falls back to the scraped pair. Both ends always
come from the same source so the scraper's 01:05 timing is never mixed with the 06:00 close.
"""
from datetime import timedelta


def totalizers_for_day(op_date):
    """Return (opening, closing, manual_nozzles): {nozzle_no: value} dicts plus the set of
    nozzle numbers that used manual readings."""
    from pumpvision.models import ManualTotalizerReading, NozzleTotalizer

    next_date = op_date + timedelta(days=1)
    opening = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=op_date)}
    closing = {r.nozzle_no: r.totalizer_end
               for r in NozzleTotalizer.query.filter_by(operational_date=next_date)}

    def manual(day):
        return {r.nozzle_no: r.totalizer_value
                for r in ManualTotalizerReading.query.filter_by(operational_date=day)
                if r.nozzle_no is not None}

    m_open, m_close = manual(op_date - timedelta(days=1)), manual(op_date)
    used = set()
    for n in set(m_open) & set(m_close):
        opening[n], closing[n] = m_open[n], m_close[n]
        used.add(n)
    return opening, closing, used
