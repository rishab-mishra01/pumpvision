from datetime import datetime, timedelta

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import func

from pumpvision.constants import ALL_PRODUCTS, NOZZLE_LABEL_MAP, PRODUCT_LABELS
from pumpvision.decorators import owner_required
from pumpvision.models import db, ManualTotalizerReading, NozzleTotalizer, AppNotification, AppSetting

meters_bp = Blueprint("meters", __name__)


@meters_bp.route("/")
@login_required
@owner_required
def index():
    complete_dates = (
        db.session.query(ManualTotalizerReading.operational_date)
        .filter_by(is_locked=True)
        .group_by(ManualTotalizerReading.operational_date)
        .having(func.count(ManualTotalizerReading.id) == 7)
        .order_by(ManualTotalizerReading.operational_date.desc())
        .all()
    )
    dates = [r.operational_date for r in complete_dates]
    return render_template("meters/index.html", dates=dates)


@meters_bp.route("/<date_str>")
@login_required
@owner_required
def day(date_str):
    try:
        op_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("meters.index"))

    prev_date = op_date - timedelta(days=1)
    next_date = op_date + timedelta(days=1)
    view = request.args.get("view", "totalizer")

    emp_closing = {
        r.nozzle_label: r
        for r in ManualTotalizerReading.query.filter_by(operational_date=op_date).all()
    }
    emp_opening = {
        r.nozzle_label: r
        for r in ManualTotalizerReading.query.filter_by(operational_date=prev_date).all()
    }

    iras_opening = {
        r.nozzle_no: r
        for r in NozzleTotalizer.query.filter_by(operational_date=op_date).all()
    }
    iras_closing = {
        r.nozzle_no: r
        for r in NozzleTotalizer.query.filter_by(operational_date=next_date).all()
    }

    totalizer_rows = []
    for label, info in NOZZLE_LABEL_MAP.items():
        n = info["nozzle_no"]
        ec = emp_closing.get(label)
        eo = emp_opening.get(label)
        ic = iras_closing.get(n) if n else None
        io = iras_opening.get(n) if n else None
        totalizer_rows.append({
            "label":        label,
            "product":      info["product"],
            "emp_opening":  eo.totalizer_value if eo else None,
            "emp_closing":  ec.totalizer_value if ec else None,
            "emp_recorded": ec.recorded_at if ec else None,
            "iras_opening": io.totalizer_end if io else None,
            "iras_closing": ic.totalizer_end if ic else None,
            "has_iras":     n is not None,
        })

    litres_rows = []
    for product in ALL_PRODUCTS:
        labels = PRODUCT_LABELS[product]

        emp_litres = None
        if all(l in emp_closing and l in emp_opening for l in labels):
            emp_litres = round(sum(
                emp_closing[l].totalizer_value - emp_opening[l].totalizer_value
                for l in labels
            ), 2)

        iras_litres = None
        if product != "CNG":
            nozzle_nos = [NOZZLE_LABEL_MAP[l]["nozzle_no"] for l in labels]
            if all(n in iras_opening and n in iras_closing for n in nozzle_nos):
                iras_litres = round(sum(
                    (iras_closing[n].totalizer_end - iras_opening[n].totalizer_end)
                    - float((db.session.get(AppSetting, f"pump_test_nozzle_{n}") or AppSetting(value="0")).value)
                    for n in nozzle_nos
                ), 2)

        diff = round(emp_litres - iras_litres, 2) if (
            emp_litres is not None and iras_litres is not None
        ) else None

        litres_rows.append({
            "product":     product,
            "emp_litres":  emp_litres,
            "iras_litres": iras_litres,
            "diff":        diff,
        })

    AppNotification.query.filter_by(
        reference_date=op_date, notification_type="shift_close"
    ).update({"is_read": True})
    db.session.commit()

    return render_template(
        "meters/day.html",
        op_date=op_date,
        prev_date=prev_date,
        next_date=next_date,
        view=view,
        totalizer_rows=totalizer_rows,
        litres_rows=litres_rows,
    )
