from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from pumpvision.decorators import manager_required
from pumpvision.services.operational import get_operational_date

DEFAULT_EXPENSE_CATEGORIES = ["Staff", "Maintenance", "Utilities", "Supplies", "Misc"]

manager_bp = Blueprint("manager", __name__)

_SHIFT_DB_LABELS = ["HSD 1", "HSD 2", "MS 1", "MS 2", "XP", "XG"]


def _greeting() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _fmt_date(d) -> str:
    """Portable date format: '7 May 2026' (no leading zero, works on Windows + Linux)."""
    return d.strftime("%d %b %Y").lstrip("0")


def _fmt_short(d) -> str:
    return d.strftime("%d %b").lstrip("0")


@manager_bp.route("/")
@login_required
@manager_required
def home():
    from pumpvision.models import (
        ManualTotalizerReading, Expense, PaytmTransaction, PaymentReceived, Customer
    )

    today_op = get_operational_date()
    prev_op = today_op - timedelta(days=1)

    # Checklist: previous shift readings (all 6 nozzles locked)
    submitted_labels = {
        r.nozzle_label
        for r in ManualTotalizerReading.query
            .filter_by(operational_date=prev_op, is_locked=True).all()
    }
    shift_readings_done = all(lbl in submitted_labels for lbl in _SHIFT_DB_LABELS)

    # Checklist: expenses logged today
    expenses_done = Expense.query.filter_by(op_date=today_op).count() > 0

    # Checklist: Paytm report uploaded (yesterday's operational date)
    paytm_done = PaytmTransaction.query.filter_by(operational_date=prev_op).count() > 0

    # Pending bank transfers
    pending_payments = (
        PaymentReceived.query
        .filter_by(status="pending_verification")
        .order_by(PaymentReceived.payment_date.desc())
        .all()
    )
    pending_with_customers = []
    for pmt in pending_payments:
        customer = Customer.query.get(pmt.customer_id)
        pending_with_customers.append({
            "payment": pmt,
            "customer_name": customer.company_name if customer else "Unknown",
            "date_str": _fmt_short(pmt.payment_date),
        })

    return render_template(
        "manager/home.html",
        greeting=_greeting(),
        today_str=_fmt_date(today_op),
        day_name=today_op.strftime("%A"),
        prev_str=_fmt_short(prev_op),
        shift_readings_done=shift_readings_done,
        expenses_done=expenses_done,
        paytm_done=paytm_done,
        pending_payments=pending_with_customers,
    )


@manager_bp.route("/lube/", methods=["GET", "POST"])
@login_required
@manager_required
def lube():
    from pumpvision.models import Customer, LubeProduct, LubeTransaction, db

    products = LubeProduct.query.filter_by(is_active=True).order_by(LubeProduct.name).all()
    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()

    form_values = {
        "product_id": "",
        "quantity": "",
        "unit_price": "",
        "payment_mode": "cash",
        "customer_id": "",
    }

    if request.method == "POST":
        product_id_raw = request.form.get("product_id", "").strip()
        raw_quantity = request.form.get("quantity", "").strip()
        raw_unit_price = request.form.get("unit_price", "").strip()
        payment_mode = request.form.get("payment_mode", "").strip()
        customer_id_raw = request.form.get("customer_id", "").strip()

        form_values.update({
            "product_id": product_id_raw,
            "quantity": raw_quantity,
            "unit_price": raw_unit_price,
            "payment_mode": payment_mode or "cash",
            "customer_id": customer_id_raw,
        })

        error = None
        product = None
        customer = None
        try:
            product_id = int(product_id_raw)
        except ValueError:
            product_id = None
        if product_id is not None:
            product = LubeProduct.query.filter_by(id=product_id, is_active=True).first()

        try:
            quantity = float(raw_quantity)
        except ValueError:
            quantity = None

        try:
            unit_price = float(raw_unit_price)
        except ValueError:
            unit_price = None

        if not product:
            error = "Choose a valid product."
        elif quantity is None or quantity <= 0:
            error = "Enter a valid quantity greater than zero."
        elif unit_price is None or unit_price <= 0:
            error = "Enter a valid unit price greater than zero."
        elif payment_mode not in ("cash", "credit"):
            error = "Choose a valid payment mode."
        elif payment_mode == "credit":
            try:
                customer_id = int(customer_id_raw)
            except ValueError:
                customer_id = None
            if customer_id is not None:
                customer = Customer.query.filter_by(
                    customer_id=customer_id,
                    is_active=True,
                ).first()
            if not customer:
                error = "Choose a valid customer for credit sale."

        if error:
            flash(error, "error")
        else:
            amount = round(quantity * unit_price, 2)
            db.session.add(LubeTransaction(
                product_id=product.id,
                quantity=quantity,
                unit_price=unit_price,
                amount=amount,
                payment_mode=payment_mode,
                customer_id=customer.customer_id if customer else None,
                op_date=get_operational_date(),
                transaction_time=datetime.now(),
                logged_by=current_user.id,
            ))
            if customer:
                customer.outstanding_balance = (customer.outstanding_balance or 0.0) + amount
            db.session.commit()
            message = f"Lube sale logged: ₹{amount:,.2f} — {product.name}"
            if customer:
                message += f" ({customer.company_name})"
            flash(message, "ok")
            return redirect(url_for("manager.home"))

    return render_template(
        "manager/lube.html",
        products=products,
        customers=customers,
        values=form_values,
    )


def _expense_categories():
    from pumpvision.models import AppSetting

    setting = AppSetting.query.get("expense_categories")
    if not setting or not setting.value.strip():
        return list(DEFAULT_EXPENSE_CATEGORIES)
    cats = [c.strip() for c in setting.value.split(",") if c.strip()]
    return cats or list(DEFAULT_EXPENSE_CATEGORIES)


@manager_bp.route("/expense/", methods=["GET", "POST"])
@login_required
@manager_required
def expense():
    from pumpvision.models import Expense, db

    categories = _expense_categories()
    today_op = get_operational_date()

    form_values = {
        "amount": "",
        "category": categories[0] if categories else "",
        "description": "",
        "op_date": today_op.isoformat(),
    }

    if request.method == "POST":
        raw_amount = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        description = request.form.get("description", "").strip()[:200]
        op_date_str = request.form.get("op_date", "").strip()

        form_values.update({
            "amount": raw_amount,
            "category": category,
            "description": description,
            "op_date": op_date_str or today_op.isoformat(),
        })

        error = None
        try:
            amount = float(raw_amount)
        except ValueError:
            amount = None
        if amount is None or amount <= 0:
            error = "Enter a valid amount greater than zero."
        elif category not in categories:
            error = "Choose a valid category."
        else:
            try:
                op_date = date.fromisoformat(op_date_str) if op_date_str else today_op
            except ValueError:
                op_date = today_op

        if error:
            flash(error, "error")
        else:
            db.session.add(Expense(
                amount=amount,
                category=category,
                description=description or None,
                op_date=op_date,
                logged_by=current_user.id,
            ))
            db.session.commit()
            flash(f"Expense logged: ₹{amount:,.2f} — {category}", "ok")
            return redirect(url_for("manager.home"))

    return render_template(
        "manager/expense.html",
        categories=categories,
        values=form_values,
    )


@manager_bp.route("/payment/", methods=["GET", "POST"])
@login_required
@manager_required
def payment():
    from pumpvision.models import Customer, PaymentReceived, db

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()

    form_values = {
        "customer_id": "",
        "amount": "",
        "payment_mode": "Cash",
        "reference_number": "",
        "notes": "",
    }

    if request.method == "POST":
        customer_id_raw = request.form.get("customer_id", "").strip()
        raw_amount = request.form.get("amount", "").strip()
        payment_mode = request.form.get("payment_mode", "").strip()
        reference_number = request.form.get("reference_number", "").strip()[:50]
        notes = request.form.get("notes", "").strip()

        form_values.update({
            "customer_id": customer_id_raw,
            "amount": raw_amount,
            "payment_mode": payment_mode or "Cash",
            "reference_number": reference_number,
            "notes": notes,
        })

        error = None
        customer = None
        try:
            customer_id = int(customer_id_raw)
        except ValueError:
            customer_id = None
        if customer_id is not None:
            customer = Customer.query.filter_by(customer_id=customer_id, is_active=True).first()

        try:
            amount = float(raw_amount)
        except ValueError:
            amount = None

        if not customer:
            error = "Choose a valid customer."
        elif amount is None or amount <= 0:
            error = "Enter a valid amount greater than zero."
        elif payment_mode not in ("Cash", "Cheque", "Bank Transfer"):
            error = "Choose a valid payment mode."

        if error:
            flash(error, "error")
        else:
            status = "pending_verification" if payment_mode == "Bank Transfer" else "confirmed"
            db.session.add(PaymentReceived(
                invoice_id=None,
                customer_id=customer.customer_id,
                amount=amount,
                payment_date=date.today(),
                payment_mode=payment_mode,
                reference_number=reference_number or None,
                notes=notes or None,
                status=status,
            ))
            if status == "confirmed":
                customer.outstanding_balance = max(0.0, (customer.outstanding_balance or 0.0) - amount)
            db.session.commit()
            if status == "confirmed":
                flash(f"Payment recorded: ₹{amount:,.2f} from {customer.company_name}", "ok")
            else:
                flash("Bank transfer recorded — awaiting owner verification", "ok")
            return redirect(url_for("manager.home"))

    return render_template(
        "manager/payment.html",
        customers=customers,
        values=form_values,
    )


@manager_bp.route("/invoice/")
@login_required
@manager_required
def invoice():
    return render_template("manager/coming_soon.html", feature="Generate Invoice")
