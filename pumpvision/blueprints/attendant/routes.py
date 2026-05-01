import os
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from pumpvision.constants import ALL_LABELS, ALL_PRODUCTS, NOZZLE_LABEL_MAP, PRODUCT_LABELS
from pumpvision.decorators import attendant_required

attendant_bp = Blueprint("attendant", __name__)

_AVATAR_COLORS = ['#AFA9EC', '#5DCAA5', '#EF9F27', '#85B7EB', '#ED93B1', '#3ECFCF']
_PRODUCT_COLORS = {'HS': '#3b82f6', 'MS': '#10b981', 'X2': '#a855f7', 'XG': '#f97316'}


def _initials(name: str) -> str:
    words = name.split()
    return ''.join(w[0].upper() for w in words[:2])


def _shift_op_date() -> date:
    """Operational date the employee is closing: always yesterday."""
    return date.today() - timedelta(days=1)


def _greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "Good morning"
    elif 12 <= hour < 17:
        return "Good afternoon"
    elif 17 <= hour < 21:
        return "Good evening"
    return "Good night"


# ─── Attendant home ───────────────────────────────────────────────────────────

@attendant_bp.route("/")
@login_required
@attendant_required
def home():
    from pumpvision.models import ManualTotalizerReading
    op_date = _shift_op_date()
    locked = ManualTotalizerReading.query.filter_by(
        operational_date=op_date, is_locked=True
    ).first() is not None

    display_name = os.environ.get(
        "ATTENDANT_DISPLAY_NAME",
        current_user.id.capitalize()
    )

    return render_template(
        "attendant/home.html",
        op_date=op_date,
        shift_locked=locked,
        greeting=_greeting(),
        display_name=display_name,
        shift_start="06:15 AM",
    )


# ─── Activity stub ────────────────────────────────────────────────────────────

@attendant_bp.route("/activity/")
@login_required
@attendant_required
def activity():
    return render_template("attendant/activity_stub.html")


# ─── Profile stub ─────────────────────────────────────────────────────────────

@attendant_bp.route("/profile/")
@login_required
@attendant_required
def profile():
    return render_template("attendant/profile_stub.html")


# ─── Shift closing ────────────────────────────────────────────────────────────

@attendant_bp.route("/shift-close")
@login_required
@attendant_required
def shift_close():
    from pumpvision.models import ManualTotalizerReading
    op_date = _shift_op_date()

    done_labels = {
        r.nozzle_label
        for r in ManualTotalizerReading.query.filter_by(operational_date=op_date).all()
    }
    product_status = {p: all(l in done_labels for l in labels)
                      for p, labels in PRODUCT_LABELS.items()}
    all_done = all(product_status.values())
    is_locked = all_done and ManualTotalizerReading.query.filter_by(
        operational_date=op_date, is_locked=True
    ).first() is not None

    return render_template(
        "attendant/shift_close_products.html",
        op_date=op_date,
        product_status=product_status,
        all_done=all_done,
        is_locked=is_locked,
        all_products=ALL_PRODUCTS,
    )


@attendant_bp.route("/shift-close/submit", methods=["POST"])
@login_required
@attendant_required
def shift_close_submit():
    from pumpvision.models import ManualTotalizerReading, AppNotification, db
    op_date = _shift_op_date()

    done_labels = {
        r.nozzle_label
        for r in ManualTotalizerReading.query.filter_by(operational_date=op_date).all()
    }
    if not all(label in done_labels for label in ALL_LABELS):
        flash("All products must be recorded before closing the shift.", "error")
        return redirect(url_for("attendant.shift_close"))

    ManualTotalizerReading.query.filter_by(operational_date=op_date).update({"is_locked": True})
    now = datetime.now()
    db.session.add(AppNotification(
        message=f"Day close submitted for {op_date.strftime('%d %b %Y')} at {now.strftime('%H:%M')}.",
        notification_type="shift_close",
        reference_date=op_date,
    ))
    db.session.commit()

    flash(f"Day close submitted for {op_date.strftime('%d %b %Y')}. Owner has been notified.", "success")
    return redirect(url_for("attendant.home"))


@attendant_bp.route("/shift-close/<product>", methods=["GET", "POST"])
@login_required
@attendant_required
def shift_close_entry(product):
    from pumpvision.models import ManualTotalizerReading, db
    product = product.upper()
    if product not in PRODUCT_LABELS:
        return redirect(url_for("attendant.shift_close"))

    op_date = _shift_op_date()
    labels  = PRODUCT_LABELS[product]

    if ManualTotalizerReading.query.filter_by(
        operational_date=op_date, nozzle_label=labels[0], is_locked=True
    ).first():
        flash("Readings for this day are locked.", "error")
        return redirect(url_for("attendant.shift_close"))

    if request.method == "POST":
        errors, values = [], {}
        for label in labels:
            raw = request.form.get(f"totalizer_{label.replace(' ', '_')}", "").strip()
            try:
                val = float(raw)
                if val <= 0:
                    errors.append(f"{label}: must be greater than zero.")
                else:
                    values[label] = val
            except (ValueError, TypeError):
                errors.append(f"{label}: enter a valid number.")

        if errors:
            for e in errors:
                flash(e, "error")
        else:
            now = datetime.now()
            for label, val in values.items():
                info = NOZZLE_LABEL_MAP[label]
                existing = ManualTotalizerReading.query.filter_by(
                    operational_date=op_date, nozzle_label=label
                ).first()
                if existing:
                    existing.totalizer_value = val
                    existing.recorded_at = now
                else:
                    db.session.add(ManualTotalizerReading(
                        operational_date=op_date,
                        nozzle_label=label,
                        nozzle_no=info["nozzle_no"],
                        product=product,
                        totalizer_value=val,
                        recorded_at=now,
                    ))
            db.session.commit()
            flash(f"{product} readings saved.", "success")
            return redirect(url_for("attendant.shift_close"))

    current_readings = {
        r.nozzle_label: r
        for r in ManualTotalizerReading.query.filter_by(operational_date=op_date).all()
    }
    prev_readings = {}
    for label in labels:
        prev_readings[label] = (
            ManualTotalizerReading.query
            .filter(
                ManualTotalizerReading.nozzle_label == label,
                ManualTotalizerReading.operational_date < op_date,
            )
            .order_by(ManualTotalizerReading.operational_date.desc())
            .first()
        )

    return render_template(
        "attendant/shift_close_entry.html",
        op_date=op_date,
        product=product,
        labels=labels,
        current_readings=current_readings,
        prev_readings=prev_readings,
    )


# ─── Credit transaction log ───────────────────────────────────────────────────

@attendant_bp.route("/log", methods=["GET", "POST"])
@login_required
def log_transaction():
    from pumpvision.models import AppSetting, AuthorizedVehicle, CreditTransaction, Customer, LocalPrice, db

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()

    now_for_prices = datetime.utcnow()
    price_map = {}
    for prod in ("HS", "MS", "X2", "XG"):
        row = (
            LocalPrice.query.filter(
                LocalPrice.product == prod,
                LocalPrice.effective_from <= now_for_prices,
                (LocalPrice.effective_to == None) | (LocalPrice.effective_to >= now_for_prices),
            )
            .order_by(LocalPrice.effective_from.desc())
            .first()
        )
        price_map[prod] = row.rate_per_litre if row else 0.0

    if request.method == "POST":
        customer_id    = request.form.get("customer_id")
        vehicle_number = request.form.get("vehicle_number", "").strip().upper()
        product        = request.form.get("product", "").strip().upper()
        litres_str     = request.form.get("litres", "")
        amount_str     = request.form.get("amount", "")
        attendant_name = request.form.get("attendant_name", "").strip()
        notes          = request.form.get("notes", "").strip()

        errors = []
        if not customer_id:
            errors.append("Please select a customer.")
        if not vehicle_number:
            errors.append("Please select a vehicle.")
        if product not in ("HS", "MS", "X2", "XG"):
            errors.append("Please select a valid product.")
        if not attendant_name:
            errors.append("Attendant name is required.")

        try:
            litres = float(litres_str)
            if litres <= 0:
                errors.append("Litres must be greater than zero.")
        except (ValueError, TypeError):
            errors.append("Litres must be a valid number.")
            litres = 0

        try:
            amount = float(amount_str)
            if amount <= 0:
                errors.append("Amount must be greater than zero.")
        except (ValueError, TypeError):
            errors.append("Amount must be a valid number.")
            amount = 0

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("credit/attendant/log_transaction.html",
                                   customers=customers, price_map=price_map)

        price_row = (
            LocalPrice.query.filter(
                LocalPrice.product == product,
                LocalPrice.effective_from <= now_for_prices,
                (LocalPrice.effective_to == None) | (LocalPrice.effective_to >= now_for_prices),
            )
            .order_by(LocalPrice.effective_from.desc())
            .first()
        )
        rate_per_litre = price_row.rate_per_litre if price_row else 0.0

        customer = Customer.query.get_or_404(int(customer_id))

        txn = CreditTransaction(
            customer_id=customer.customer_id,
            vehicle_number=vehicle_number,
            transaction_date=date.today(),
            transaction_time=datetime.now().time(),
            product=product,
            litres=litres,
            rate_per_litre=rate_per_litre,
            amount=amount,
            attendant_name=attendant_name,
            notes=notes or None,
        )
        db.session.add(txn)
        customer.outstanding_balance = (customer.outstanding_balance or 0.0) + amount
        db.session.commit()

        threshold_setting = db.session.get(AppSetting, "alert_threshold")
        threshold = float(threshold_setting.value) if threshold_setting else 80.0

        if customer.utilization_pct >= threshold:
            flash(
                f"Transaction saved. "
                f"Note: {customer.company_name} is now at {customer.utilization_pct:.0f}% credit utilization.",
                "warning",
            )
        else:
            flash("Transaction saved successfully.", "success")

        return redirect(url_for("attendant.log_transaction"))

    return render_template("credit/attendant/log_transaction.html",
                           customers=customers, price_map=price_map)


@attendant_bp.route("/vehicles/<int:customer_id>")
@login_required
def customer_vehicles(customer_id):
    from flask import jsonify
    from pumpvision.models import AuthorizedVehicle

    vehicles = (
        AuthorizedVehicle.query
        .filter_by(customer_id=customer_id, is_active=True)
        .order_by(AuthorizedVehicle.vehicle_number)
        .all()
    )
    return jsonify([
        {"number": v.vehicle_number, "description": v.vehicle_description or ""}
        for v in vehicles
    ])


# ─── Credit sale flow ─────────────────────────────────────────────────────────

@attendant_bp.route("/credit/select-customer", strict_slashes=False)
@login_required
@attendant_required
def select_customer():
    from pumpvision.models import Customer, CreditTransaction

    customers = Customer.query.all()
    enriched = []
    for c in customers:
        last_txn = (
            CreditTransaction.query
            .filter_by(customer_id=c.customer_id)
            .order_by(CreditTransaction.transaction_date.desc())
            .first()
        )
        txn_count = CreditTransaction.query.filter_by(customer_id=c.customer_id).count()
        vehicle_numbers = [v.vehicle_number for v in c.vehicles if v.is_active]
        enriched.append({
            "id": c.customer_id,
            "company_name": c.company_name,
            "account_id": "ACC-%04d" % c.customer_id,
            "is_active": c.is_active,
            "initials": _initials(c.company_name),
            "color": _AVATAR_COLORS[c.customer_id % len(_AVATAR_COLORS)],
            "last_txn_date": last_txn.transaction_date.isoformat() if last_txn else "1900-01-01",
            "txn_count": txn_count,
            "vehicles": vehicle_numbers,
        })

    return render_template("attendant/select_customer.html", customers=enriched)


@attendant_bp.route("/credit/log/<int:customer_id>", methods=["GET", "POST"], strict_slashes=False)
@login_required
@attendant_required
def log_sale_details(customer_id):
    from pumpvision.models import Customer, CreditTransaction, db
    from pumpvision.services.prices import get_rsp

    customer = Customer.query.get_or_404(customer_id)
    if not customer.is_active:
        return redirect(url_for("attendant.select_customer"))

    vehicles = [v.vehicle_number for v in customer.vehicles if v.is_active]
    today = date.today()
    price_map = {prod: get_rsp(prod, today) for prod in ("HS", "MS", "X2", "XG")}

    if request.method == "POST":
        vehicle_number = request.form.get("vehicle_number", "").strip().upper()
        product = request.form.get("product", "").strip().upper()
        input_mode = request.form.get("input_mode", "amount")
        errors = []

        if not vehicle_number:
            errors.append("Please select a vehicle.")
        if product not in ("HS", "MS", "X2", "XG"):
            errors.append("Please select a product.")

        rate = price_map.get(product)
        if product in ("HS", "MS", "X2", "XG") and rate is None:
            errors.append(f"No current rate found for {product}. Contact owner.")

        try:
            quantity = float(request.form.get("quantity", ""))
            if quantity <= 0:
                errors.append("Quantity must be greater than zero.")
        except (ValueError, TypeError):
            errors.append("Please enter a valid quantity.")
            quantity = 0.0

        if not errors:
            if input_mode == "amount":
                amount = quantity
                litres = round(quantity / rate, 3) if rate else 0.0
            else:
                litres = quantity
                amount = round(quantity * rate, 2) if rate else 0.0

            now = datetime.now()
            txn = CreditTransaction(
                customer_id=customer.customer_id,
                vehicle_number=vehicle_number,
                transaction_date=today,
                transaction_time=now.time(),
                product=product,
                litres=litres,
                rate_per_litre=rate or 0.0,
                amount=amount,
                attendant_name=current_user.id,
                is_legacy_entry=False,
            )
            db.session.add(txn)
            customer.outstanding_balance = (customer.outstanding_balance or 0.0) + amount
            db.session.commit()
            return redirect(url_for("attendant.transaction_confirmed", transaction_id=txn.transaction_id))

        for e in errors:
            flash(e, "error")

    return render_template(
        "attendant/log_sale_details.html",
        customer=customer,
        account_id="ACC-%04d" % customer.customer_id,
        vehicles=vehicles,
        price_map=price_map,
        product_colors=_PRODUCT_COLORS,
    )


@attendant_bp.route("/credit/log/confirmed/<int:transaction_id>")
@login_required
@attendant_required
def transaction_confirmed(transaction_id):
    from pumpvision.models import CreditTransaction, Customer

    txn = CreditTransaction.query.get_or_404(transaction_id)
    customer = Customer.query.get_or_404(txn.customer_id)

    d = txn.transaction_date
    t = txn.transaction_time
    time_str = t.strftime("%I:%M %p").lstrip("0")
    formatted_dt = f"{d.day} {d.strftime('%b')} {d.year} · {time_str}"

    return render_template(
        "attendant/transaction_confirmed.html",
        txn=txn,
        customer=customer,
        account_id="ACC-%04d" % customer.customer_id,
        product_colors=_PRODUCT_COLORS,
        formatted_dt=formatted_dt,
    )
