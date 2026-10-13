"""Opening/closing nozzle totalizers for an operational day.

Historical cycles use the legacy 06:00 ISS boundaries. From the cycle cutover onward,
each closing boundary is selected independently: a valid attendant close wins and the
IRAS Shift Totalizer's midnight close fills a missing manual reading. The legacy ISS
table is never relabelled or silently mixed into a post-cutover midnight boundary.

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
    """Staff readings captured inside the close window applicable to ``day``."""
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


def iras_midnight_closes(day):
    """{nozzle_no: value} from IRAS's midnight Shift Totalizer for ``day``."""
    from pumpvision.models import IrasMidnightTotalizer

    return {
        row.nozzle_no: row.totalizer_close
        for row in IrasMidnightTotalizer.query.filter_by(operational_date=day)
    }


def closes_for_day(day):
    """Return the chosen close and its source for each nozzle.

    After the midnight cutover, a valid attendant close wins nozzle-by-nozzle and
    the matching IRAS midnight boundary fills only the missing manual readings.
    Historical 06:00 cycles retain the original all-manual-or-ISS behavior.
    """
    if day < cycle_cutover():
        from pumpvision.models import NozzleTotalizer

        manual = valid_manual_closes(day)
        iras = {
            row.nozzle_no: row.totalizer_end
            for row in NozzleTotalizer.query.filter_by(
                operational_date=day + timedelta(days=1)
            )
        }
    else:
        manual = valid_manual_closes(day)
        iras = iras_midnight_closes(day)

    values = dict(iras)
    sources = {nozzle_no: "iras" for nozzle_no in iras}
    values.update(manual)
    sources.update({nozzle_no: "manual" for nozzle_no in manual})
    return values, sources


def opening_reading(nozzle_no, op_date):
    """Opening totalizer for the cycle, using its actual closing-boundary model."""
    from pumpvision.models import ManualTotalizerReading, NozzleTotalizer

    if op_date > cycle_cutover():
        previous, _sources = closes_for_day(op_date - timedelta(days=1))
        return previous.get(nozzle_no)

    # The cutover cycle opened at the final legacy 06:00 boundary. Historical
    # cycles also continue through this path unchanged.

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

    if op_date >= cycle_cutover():
        if op_date == cycle_cutover():
            cutover_iras_opening = {
                row.nozzle_no: row.totalizer_end
                for row in NozzleTotalizer.query.filter_by(operational_date=op_date)
            }
            opening = dict(cutover_iras_opening)
            # The cutover cycle started at the final legacy close, where a valid
            # attendant reading was still the preferred boundary.
            opening.update(valid_manual_closes(op_date - timedelta(days=1)))
        else:
            opening, _opening_sources = closes_for_day(op_date - timedelta(days=1))
        closing, closing_sources = closes_for_day(op_date)

        # When today's manual close is missing, use an IRAS-to-IRAS pair. This
        # keeps the fallback internally consistent and makes an unchanged pair
        # an explicit zero-sale result, even if yesterday had a manual close.
        if op_date == cycle_cutover():
            for nozzle_no, source in closing_sources.items():
                if source == "iras" and nozzle_no in cutover_iras_opening:
                    opening[nozzle_no] = cutover_iras_opening[nozzle_no]
        else:
            previous_iras = iras_midnight_closes(op_date - timedelta(days=1))
            for nozzle_no, source in closing_sources.items():
                if source == "iras" and nozzle_no in previous_iras:
                    opening[nozzle_no] = previous_iras[nozzle_no]
        used = {
            nozzle_no for nozzle_no, source in closing_sources.items()
            if source == "manual"
        }
        return opening, closing, used

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


def has_liquid_close_pair(op_date, nozzle_numbers=(7, 11, 15, 16, 17, 18)):
    """Whether every liquid nozzle has both boundaries, including valid zero-sale days."""
    opening, closing, _manual = totalizers_for_day(op_date)
    return all(nozzle_no in opening and nozzle_no in closing for nozzle_no in nozzle_numbers)
