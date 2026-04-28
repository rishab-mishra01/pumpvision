from datetime import date, datetime

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

attendant_bp = Blueprint("attendant", __name__)


def attendant_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(403)
        # Both owner and attendant can access this screen
        return f(*args, **kwargs)

    return decorated


@attendant_bp.route("/log", methods=["GET", "POST"])
@login_required
def log_transaction():
    from models import AppSetting, AuthorizedVehicle, CreditTransaction, Customer, LocalPrice, db

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()

    # Build current price map for display on the form
    from datetime import datetime as _dt
    now_for_prices = _dt.utcnow()
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
        customer_id = request.form.get("customer_id")
        vehicle_number = request.form.get("vehicle_number", "").strip().upper()
        product = request.form.get("product", "").strip().upper()
        litres_str = request.form.get("litres", "")
        amount_str = request.form.get("amount", "")
        attendant_name = request.form.get("attendant_name", "").strip()
        notes = request.form.get("notes", "").strip()

        # Validate
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
            return render_template("attendant/log_transaction.html", customers=customers, price_map=price_map)

        # Look up rate from local_prices
        now = datetime.utcnow()
        price_row = (
            LocalPrice.query.filter(
                LocalPrice.product == product,
                LocalPrice.effective_from <= now,
                (LocalPrice.effective_to == None) | (LocalPrice.effective_to >= now),
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

        # Update outstanding balance atomically
        customer.outstanding_balance = (customer.outstanding_balance or 0.0) + amount

        db.session.commit()

        # Check credit alert threshold
        threshold_setting = AppSetting.query.get("alert_threshold")
        threshold = float(threshold_setting.value) if threshold_setting else 80.0
        utilization = customer.utilization_pct

        if utilization >= threshold:
            flash(
                f"Transaction saved. "
                f"Note: {customer.company_name} is now at {utilization:.0f}% credit utilization.",
                "warning",
            )
        else:
            flash("Transaction saved successfully.", "success")

        return redirect(url_for("attendant.log_transaction"))

    return render_template("attendant/log_transaction.html", customers=customers, price_map=price_map)


@attendant_bp.route("/vehicles/<int:customer_id>")
@login_required
def customer_vehicles(customer_id):
    """AJAX endpoint: returns JSON list of active vehicles for a customer."""
    from flask import jsonify
    from models import AuthorizedVehicle

    vehicles = (
        AuthorizedVehicle.query.filter_by(customer_id=customer_id, is_active=True)
        .order_by(AuthorizedVehicle.vehicle_number)
        .all()
    )
    vehicle_list = [
        {"number": v.vehicle_number, "description": v.vehicle_description or ""}
        for v in vehicles
    ]
    return jsonify(vehicle_list)
