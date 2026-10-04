"""Morning checks that raise an owner alert (Credit home) when something is missing.

    PUMPVISION_SKIP_BOOTSTRAP=1 .venv/bin/python scripts/morning_checks.py price   # cron 07:30 IST
    PUMPVISION_SKIP_BOOTSTRAP=1 .venv/bin/python scripts/morning_checks.py shift   # cron 09:00 IST

price -- today's IRAS price (effective 06:00 today) is not in the DB. Attendant credit
         sales then fall back to the latest earlier price, which is wrong on a day
         the price changed.
shift -- the last completed shift (yesterday 06:00 -> today 06:00) is not closed; or,
         if it is, a nozzle's reading is off by more than its reading time explains
         (pumpvision/services/meter_check.py).

Read-only apart from the alert, and never raises the same alert twice.
"""
import os
import sys
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def alert(db, AppNotification, kind, message, ref):
    if AppNotification.query.filter_by(notification_type=kind, reference_date=ref).first():
        print("already alerted:", message)
        return
    db.session.add(AppNotification(notification_type=kind, message=message, reference_date=ref))
    db.session.commit()
    print("ALERT:", message)


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("price", "shift"):
        sys.exit(__doc__)
    from pumpvision import create_app
    from pumpvision.models import AppNotification, IrasPrice, ManualTotalizerReading, db
    from pumpvision.services.operational import get_operational_date

    app = create_app()
    with app.app_context():
        today = get_operational_date()
        if sys.argv[1] == "price":
            start = datetime.combine(today, time(6, 0))
            have = {p.product for p in IrasPrice.query.filter(IrasPrice.effective_from == start)}
            missing = sorted({"HS", "MS", "X2", "XG"} - have)
            if missing:
                alert(db, AppNotification, "price_alert",
                      f"Today's IRAS price ({today:%d %b}) is not in yet for {', '.join(missing)}. "
                      f"Attendant credit sales are using the previous day's rate until it arrives.", today)
            else:
                print("price ok for", today)
        else:
            last = today - timedelta(days=1)
            closed = ManualTotalizerReading.query.filter(
                ManualTotalizerReading.operational_date == last,
                ManualTotalizerReading.is_locked == True,  # noqa: E712
                ManualTotalizerReading.nozzle_no != None,  # noqa: E711
            ).count() >= 6
            if not closed:
                alert(db, AppNotification, "shift_alert",
                      f"The {last:%d %b} shift (ended 06:00 today) has not been closed by the attendants.", last)
            else:
                from pumpvision.services.meter_check import check_day
                flagged = [c for c in check_day(last) if c.status == "check"]
                if flagged:
                    detail = "; ".join(f"{c.name} {c.note}" for c in flagged)
                    alert(db, AppNotification, "meter_alert",
                          f"Meter check {last:%d %b}: {detail}"[:470] + ". See More -> Meter check.", last)
                else:
                    print("shift closed and meters fit for", last)


if __name__ == "__main__":
    main()
