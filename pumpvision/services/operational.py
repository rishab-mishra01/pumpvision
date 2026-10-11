"""Operational cycles and the shift-close window. The one place the date boundary lives.

A cycle is named by the calendar date it mostly covers (D) and runs closing-to-closing:
from the previous close to the close taken in the shift-close window of date D+1.

    Shift close: every day 00:00 through 00:30 IST. A close taken at 00:15 on date E
    finalizes cycle E-1 and is the opening of cycle E.

Before CYCLE_CUTOVER the old rule applies and stays untouched: cycle D ran 06:00 on D to
06:00 on D+1, so every historical record keeps its meaning. CYCLE_CUTOVER is the label of
the first cycle closed in the new window; that cycle opened at 06:00 (the old close) and
ends at the new close, every later cycle opens and ends at the close window.

All wall-clock logic here is Asia/Kolkata. Naive datetimes in the database are IST.
"""
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

CLOSE_WINDOW_START = time(0, 0)
CLOSE_WINDOW_END = time(0, 30)            # inclusive: 00:30:59 still counts as 00:30

CUTOVER_ENV = "PUMPVISION_CYCLE_CUTOVER"
DEFAULT_CYCLE_CUTOVER = date(2026, 10, 11)

LEGACY_BOUNDARY = time(6, 0)
# Time of day a cycle ends / the next one begins. A sale at exactly this time is in the next cycle.
CLOSE_BOUNDARY = time(0, 31)              # first minute after the window: nothing closes later than 00:30:59


def cycle_cutover() -> date:
    raw = os.environ.get(CUTOVER_ENV)
    if raw:
        try:
            return date.fromisoformat(raw)
        except ValueError:
            pass
    return DEFAULT_CYCLE_CUTOVER


def now_ist() -> datetime:
    """Current IST wall clock as a naive datetime (the form stored in the database)."""
    return datetime.now(IST).replace(tzinfo=None)


def to_ist_naive(now: datetime | None = None) -> datetime:
    """Naive IST wall clock for `now`. None -> the current time. An aware value is converted;
    a naive one is taken to be IST already."""
    if now is None:
        return now_ist()
    if now.tzinfo is not None:
        return now.astimezone(IST).replace(tzinfo=None)
    return now


def cycle_end(cycle: date) -> datetime:
    """When cycle `cycle` ends and the next begins, as a naive IST datetime. This is the nominal
    boundary used to file time-stamped records (sales, payments); the actual close is any time in
    the window before it."""
    nxt = cycle + timedelta(days=1)
    if cycle < cycle_cutover():
        return datetime.combine(nxt, LEGACY_BOUNDARY)
    return datetime.combine(nxt, CLOSE_BOUNDARY)


def cycle_bounds(cycle: date) -> tuple[datetime, datetime]:
    """[start, end) of a cycle, naive IST. start is the previous cycle's end."""
    return cycle_end(cycle - timedelta(days=1)), cycle_end(cycle)


def cycle_of(moment: datetime) -> date:
    """The cycle a timestamp belongs to."""
    moment = to_ist_naive(moment)
    d = moment.date()
    return d if moment >= cycle_bounds(d)[0] else d - timedelta(days=1)


def get_operational_date(now: datetime | None = None) -> date:
    """The cycle running right now (the one not yet finalized by a close)."""
    return cycle_of(to_ist_naive(now))


def shift_cycle_date(now: datetime | None = None) -> date:
    """The cycle the attendant closes, or last closed: always yesterday's date in IST.

    Inside the window it is the cycle being closed; outside it is the last finalized cycle.
    It deliberately does not follow get_operational_date: at 00:10 the running cycle is
    still yesterday's, and that is exactly the one the close finalizes."""
    return to_ist_naive(now).date() - timedelta(days=1)


def is_close_window(now: datetime | None = None) -> bool:
    """True from 00:00 through 00:30 IST (00:30:59 included, 00:31 not)."""
    t = to_ist_naive(now).time()
    return CLOSE_WINDOW_START <= t < CLOSE_BOUNDARY


def close_window_cycle(now: datetime | None = None) -> date | None:
    """The cycle a close taken at `now` would finalize, or None outside the window."""
    return shift_cycle_date(now) if is_close_window(now) else None


def close_window_for(cycle: date) -> tuple[datetime, datetime]:
    """[open, last instant] of the window in which cycle `cycle` is closed, naive IST."""
    day = cycle + timedelta(days=1)
    return (datetime.combine(day, CLOSE_WINDOW_START),
            datetime.combine(day, CLOSE_BOUNDARY) - timedelta(microseconds=1))


def next_close_window(now: datetime | None = None) -> datetime:
    """Start of the next window (today's if it has not yet opened is impossible: it opens at 00:00)."""
    n = to_ist_naive(now)
    return datetime.combine(n.date() + timedelta(days=1), CLOSE_WINDOW_START)
