from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required

from pumpvision.decorators import owner_required

owner_bp = Blueprint("owner", __name__)


@owner_bp.route("/")
@login_required
@owner_required
def index():
    return redirect(url_for("dashboard.index"))


@owner_bp.route("/tanks")
@login_required
@owner_required
def tanks():
    from collections import defaultdict
    from datetime import date, timedelta
    from pumpvision.models import AppSetting, NozzleTotalizer, TankReading, db

    _NOZZLE_PRODUCT = {7: 'HS', 16: 'HS', 18: 'MS', 15: 'MS', 17: 'X2', 11: 'XG'}
    _TANKS = [
        {"tank_id": 1, "product": "HS", "label": "HSD", "capacity": 20000},
        {"tank_id": 2, "product": "MS", "label": "MS",  "capacity": 20000},
        {"tank_id": 3, "product": "X2", "label": "XP",  "capacity": 10000},
        {"tank_id": 4, "product": "XG", "label": "XG",  "capacity": 20000},
    ]

    op_date = date.today() - timedelta(days=1)
    tanks_data = []
    latest_ts = None

    # 7-day avg consumption per product
    avg_daily = {}
    for product in ('HS', 'MS', 'X2', 'XG'):
        nozzles = [n for n, p in _NOZZLE_PRODUCT.items() if p == product]
        total, days = 0.0, 0
        for i in range(1, 8):
            day = op_date - timedelta(days=i)
            nd = day + timedelta(days=1)
            op = {r.nozzle_no: r.totalizer_end
                  for r in NozzleTotalizer.query.filter_by(operational_date=day)}
            cl = {r.nozzle_no: r.totalizer_end
                  for r in NozzleTotalizer.query.filter_by(operational_date=nd)}
            if not all(n in op and n in cl for n in nozzles):
                continue
            day_l = 0.0
            for n in nozzles:
                s = db.session.get(AppSetting, f"pump_test_nozzle_{n}")
                pt = float(s.value) if s else 0.0
                day_l += max(0.0, cl[n] - op[n] - pt)
            if day_l > 0:
                total += day_l
                days += 1
        avg_daily[product] = (total / days) if days else None

    for t in _TANKS:
        reading = TankReading.query.filter_by(
            tank_id=t["tank_id"]
        ).order_by(TankReading.scraped_at.desc()).first()

        if reading and reading.scraped_at:
            if latest_ts is None or reading.scraped_at > latest_ts:
                latest_ts = reading.scraped_at

        # Days remaining
        days_rem = None
        avg = avg_daily.get(t["product"])
        if reading and reading.volume_litres and avg:
            days_rem = reading.volume_litres / avg

        # State: ok (>7), warn (3-7), crit (≤2)
        if days_rem is None:
            state = "ok"
        elif days_rem <= 2:
            state = "crit"
        elif days_rem <= 7:
            state = "warn"
        else:
            state = "ok"

        # Gauge segments: 14 total, lit = round(pct * 14 / 100)
        pct = reading.pct_full if reading else None
        lit_count = round((pct or 0) / 100 * 14)

        tanks_data.append({
            **t,
            "reading": reading,
            "days_rem": days_rem,
            "days_int": int(days_rem) if days_rem is not None else None,
            "state": state,
            "lit_count": lit_count,
        })

    refresh_time = latest_ts.strftime("%H:%M") if latest_ts else None

    return render_template(
        "owner/tanks.html",
        tanks=tanks_data,
        refresh_time=refresh_time,
    )
