"""Owner alerts on the Credit home: drop the ones that have resolved themselves and fold the
day-by-day ones into one line each.

morning_checks.py raises one 'shift_alert' and one 'price_alert' per day; without this they
piled up (10 open on 10 Oct 2026, three of them already resolved).
  price_alert (ref D) resolves when IRAS prices for HS, MS, X2, XG effective D 06:00 are in.
  shift_alert (ref D) resolves when the D shift is closed (6 locked nozzle readings).
Review alerts already clear themselves in services/review.py.
"""
from datetime import datetime, time

ALERT_TYPES = ("credit_alert", "lube_alert", "price_alert", "shift_alert", "meter_alert", "review_alert")
LIQUID = ("HS", "MS", "X2", "XG")
GROUPED = {   # type -> one-line message for the whole group
    "shift_alert": "Shifts not closed by the attendants: {dates}. Their sales come from IRAS machine data.",
    "price_alert": "IRAS price not in yet for: {dates}. Attendant credit sales used the previous day's rate.",
}


def _price_in(day):
    from pumpvision.models import IrasPrice
    have = {p.product for p in IrasPrice.query.filter(IrasPrice.effective_from == datetime.combine(day, time(6, 0)))}
    return set(LIQUID) <= have


def _shift_closed(day):
    from pumpvision.models import ManualTotalizerReading
    return ManualTotalizerReading.query.filter(
        ManualTotalizerReading.operational_date == day, ManualTotalizerReading.is_locked.is_(True),
        ManualTotalizerReading.nozzle_no.isnot(None)).count() >= 6


RESOLVED = {"price_alert": _price_in, "shift_alert": _shift_closed}


def is_resolved(n):
    check = RESOLVED.get(n.notification_type)
    return bool(check and n.reference_date and check(n.reference_date))


def open_alerts():
    """Unread owner alerts that are still true, newest first."""
    from pumpvision.models import AppNotification
    rows = (AppNotification.query
            .filter(AppNotification.notification_type.in_(ALERT_TYPES), AppNotification.is_read.is_(False))
            .order_by(AppNotification.created_at.desc()).all())
    return [n for n in rows if not is_resolved(n)]


def alert_groups():
    """[{message, ids}] for display: one entry per grouped type, the rest one entry each."""
    out, groups = [], {}
    for n in open_alerts():
        if n.notification_type in GROUPED and n.reference_date:
            g = groups.get(n.notification_type)
            if g is None:
                g = groups[n.notification_type] = {"type": n.notification_type, "ids": [], "dates": []}
                out.append(g)
            g["ids"].append(n.id); g["dates"].append(n.reference_date)
        else:
            out.append({"type": n.notification_type, "ids": [n.id], "message": n.message})
    for g in groups.values():
        days = sorted(set(g["dates"]))
        g["message"] = GROUPED[g["type"]].format(dates=", ".join(d.strftime("%d %b") for d in days))
    return out


def clear_resolved():
    """Mark resolved alerts read. Returns how many. Caller commits."""
    from pumpvision.models import AppNotification
    n = 0
    for a in AppNotification.query.filter(AppNotification.notification_type.in_(tuple(RESOLVED)),
                                          AppNotification.is_read.is_(False)):
        if is_resolved(a):
            a.is_read = True
            n += 1
    return n
