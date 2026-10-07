from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required

from pumpvision.decorators import owner_required
from pumpvision.models import db, ManualTotalizerReading, AppNotification

meters_bp = Blueprint("meters", __name__)


@meters_bp.route("/")
@login_required
@owner_required
def index():
    """Open the last completed shift."""
    from pumpvision.services.operational import get_operational_date
    last = get_operational_date() - timedelta(days=1)
    return redirect(url_for("meters.day", date_str=last.isoformat()))


@meters_bp.route("/<date_str>")
@login_required
@owner_required
def day(date_str):
    from pumpvision.models import CngShiftReading
    from pumpvision.services.meter_check import check_day
    from pumpvision.services.operational import get_operational_date

    try:
        op_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("meters.index"))

    checks = check_day(op_date)
    closed = ManualTotalizerReading.query.filter(
        ManualTotalizerReading.operational_date == op_date,
        ManualTotalizerReading.is_locked == True,  # noqa: E712
        ManualTotalizerReading.nozzle_no != None,  # noqa: E711
    ).count() >= 6
    cng = CngShiftReading.query.filter_by(op_date=op_date).order_by(CngShiftReading.nozzle_no).all()

    AppNotification.query.filter_by(
        reference_date=op_date, notification_type="shift_close"
    ).update({"is_read": True})
    db.session.commit()

    last = get_operational_date() - timedelta(days=1)
    return render_template(
        "meters/day.html",
        op_date=op_date,
        prev_date=op_date - timedelta(days=1),
        next_date=op_date + timedelta(days=1) if op_date < last else None,
        checks=checks,
        closed=closed,
        cng=cng,
        n_flagged=sum(c.status == "check" for c in checks),
        next_day=op_date + timedelta(days=1),
    )
