from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from pumpvision.constants import ALL_LABELS, ALL_PRODUCTS, NOZZLE_LABEL_MAP, PRODUCT_LABELS
from pumpvision.decorators import attendant_required

attendant_bp = Blueprint("attendant", __name__)


def _shift_op_date() -> date:
    """Operational date the employee is closing: always yesterday."""
    return date.today() - timedelta(days=1)


# ─── Attendant home ──────────────────────────────────────────────────────────

@attendant_bp.route("/")
@login_required
@attendant_required
def home():
    from pumpvision.models import ManualTotalizerReading
    op_date = _shift_op_date()
    locked = ManualTotalizerReading.query.filter_by(
        operational_date=op_date, is_locked=True
    ).first() is not None
    return render_template("attendant/home.html", op_date=op_date, shift_locked=locked)


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

    # Check if already locked
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
