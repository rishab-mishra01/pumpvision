import math

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
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

    # scraped_at is already IST: it is parsed from the Stock tab's own
    # "stock date"/"stock time" columns, which the portal reports in IST. The
    # date FILTER on that tab runs off the browser clock (UTC on the VPS) -- that
    # is a different thing, and conflating the two added +5:30 here and showed
    # every reading five and a half hours in the future.
    refresh_time = latest_ts.strftime("%H:%M") if latest_ts else None

    # How old the newest reading is, so the screen can say so instead of
    # promising an hourly refresh that a failed IRAS login can silently break.
    # Naive IST on both sides (datetime.now() is IST on the evo).
    age_min = None
    if latest_ts:
        from datetime import datetime
        age_min = max(0, int((datetime.now() - latest_ts).total_seconds() // 60))

    return render_template(
        "owner/tanks.html",
        tanks=tanks_data,
        refresh_time=refresh_time,
        age_min=age_min,
    )


@owner_bp.route("/more", methods=["GET", "POST"])
@login_required
@owner_required
def more():
    """The "More" tab.

    This used to link straight at auth.logout, so tapping ⋯ signed you out with
    no menu and no confirmation. It is now a real menu, and the natural home for
    actions that are not a screen.
    """
    from pumpvision.models import AppSetting, TankReading, db

    if request.method == "POST":
        # CNG pump price per kg: not in IRAS, so the owner keeps it current. It is
        # the rate an attendant's CNG credit sale and the shift-close CNG row use.
        try:
            rate = round(float(request.form.get("cng_rate", "")), 2)
        except ValueError:
            rate = 0.0
        if not math.isfinite(rate) or not 50 <= rate <= 300:
            flash("Enter the CNG rate per kg (between ₹50 and ₹300).", "error")
        else:
            s = db.session.get(AppSetting, "cng_rsp_per_kg")
            if s:
                s.value = f"{rate:.2f}"
            else:
                db.session.add(AppSetting(key="cng_rsp_per_kg", value=f"{rate:.2f}"))
            db.session.commit()
            flash(f"CNG rate set to ₹{rate:.2f}/kg.", "ok")
        return redirect(url_for("owner.more"))

    cng = db.session.get(AppSetting, "cng_rsp_per_kg")
    latest = TankReading.query.order_by(TankReading.scraped_at.desc()).first()
    # Already IST -- see the note in tanks(); do not add an offset here either.
    last_scan = (
        latest.scraped_at.strftime("%d %b, %H:%M")
        if latest and latest.scraped_at else None
    )
    return render_template("owner/more.html", last_scan=last_scan, cng_rate=cng.value if cng else None)


@owner_bp.route("/scan", methods=["POST"])
@login_required
@owner_required
def scan_start():
    from pumpvision import scan
    return jsonify(scan.start())


@owner_bp.route("/scan/status")
@login_required
@owner_required
def scan_status():
    from pumpvision import scan
    return jsonify(scan.status())


# ─── Manual entries: review and remove ────────────────────────────────────────

def _entries_date(date_str):
    from datetime import date, timedelta
    try:
        return date.fromisoformat(date_str) if date_str else date.today() - timedelta(days=1)
    except ValueError:
        return date.today() - timedelta(days=1)


@owner_bp.route("/entries")
@owner_bp.route("/entries/<date_str>")
@login_required
@owner_required
def entries(date_str=None):
    from datetime import date, timedelta
    from pumpvision.services.entries import list_entries

    op_date = _entries_date(date_str)
    today = date.today()
    return render_template(
        "owner/entries.html",
        op_date=op_date,
        data=list_entries(op_date),
        prev_date=(op_date - timedelta(days=1)).isoformat(),
        next_date=(op_date + timedelta(days=1)).isoformat() if op_date < today else None,
    )


@owner_bp.route("/entries/<kind>/<int:entry_id>/remove", methods=["GET", "POST"])
@login_required
@owner_required
def entry_remove(kind, entry_id):
    from flask import abort, current_app
    from flask_login import current_user
    from pumpvision.services import entries as svc

    if kind not in svc.KINDS:
        abort(404)
    back_date = request.values.get("date", "")
    back = redirect(url_for("owner.entries", date_str=back_date or None))

    if request.method == "POST":
        ok, message = svc.remove(kind, entry_id)
        if ok:
            current_app.logger.info("entry removed: %s #%s by %s", kind, entry_id, current_user.username)
        flash(message, "ok" if ok else "error")
        return back

    entry, headline, consequence = svc.describe(kind, entry_id)
    if entry is None:
        flash("That entry was already removed.", "error")
        return back
    return render_template("owner/entry_remove.html", kind=kind, entry_id=entry_id,
                           headline=headline, consequence=consequence, back_date=back_date)


@owner_bp.route("/entries/credit/add", methods=["GET", "POST"])
@login_required
@owner_required
def entry_add_credit():
    """Enter a credit sale that was made but never entered, on the day it happened.

    The attendant and manager screens always stamp 'now'; this lets the owner put it
    on the right day and time, at that day's rate, so that day's cash and the
    customer's balance are both corrected.
    """
    from datetime import date, datetime, time, timedelta
    from flask_login import current_user
    from pumpvision.models import Customer
    from pumpvision.services.credit_sale import rate_map, record_sale

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()
    today = date.today()
    op_date = _entries_date(request.values.get("date"))
    values = {"customer_id": "", "vehicle_number": "", "product": "", "input_mode": "litres", "quantity": "",
              "date": op_date.isoformat(), "time": "18:00"}

    if request.method == "POST":
        values.update({k: request.form.get(k, "").strip() for k in values})
        errors = []
        customer = next((c for c in customers if str(c.customer_id) == values["customer_id"]), None)
        if customer is None:
            errors.append("Choose a customer.")
        try:
            when = datetime.combine(date.fromisoformat(values["date"]), time.fromisoformat(values["time"]))
        except ValueError:
            when = None
            errors.append("Enter a valid date and time.")
        if when and when > datetime.now():
            errors.append("The sale cannot be in the future.")
        if not errors:
            txn, errs = record_sale(
                customer, values["vehicle_number"], values["product"],
                "litres" if values["input_mode"] == "litres" else "amount", values["quantity"],
                f"{current_user.first_name or current_user.username} (added later)",
                rates=rate_map(when.date()), when=when,
            )
            if txn:
                # a sale before 06:00 belongs to the previous operational day
                day = when.date() if when.time() >= time(6, 0) else when.date() - timedelta(days=1)
                flash(f"Credit sale of ₹{txn.amount:,.2f} added to {customer.company_name} on "
                      f"{when:%d %b} {when:%H:%M}. Cash and the customer's balance are updated.", "ok")
                return redirect(url_for("owner.entries", date_str=day.isoformat()))
            msgs = {"no_vehicle": "Enter the vehicle number.", "bad_product": "Choose the product.",
                    "no_rate": "No price is known for that product on that day.",
                    "qty_not_number": "Enter the quantity as a number.", "qty_not_positive": "Quantity must be above zero."}
            errors += [msgs.get(e, e) for e in errs]
        for e in errors:
            flash(e, "error")

    vehicles = sorted({v.vehicle_number for c in customers for v in c.vehicles if v.is_active})
    return render_template("owner/entry_add_credit.html", customers=customers, vehicles=vehicles,
                           values=values, back_date=op_date.isoformat(), today=today.isoformat())
