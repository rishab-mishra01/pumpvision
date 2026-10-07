import math
from datetime import date, datetime, timedelta

from flask import Blueprint, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from pumpvision import i18n_manager as i18n
from pumpvision.decorators import manager_required
from pumpvision.i18n_manager import tr
from pumpvision.services.operational import get_operational_date

from pumpvision.constants import EXPENSE_SUBCATEGORIES
from pumpvision.services.regulatory import (
    MOCK_DRILL_INTERVAL_DAYS, drill_needs_attention, mock_drill_status,
)

DEFAULT_EXPENSE_CATEGORIES = list(EXPENSE_SUBCATEGORIES)

manager_bp = Blueprint("manager", __name__)


@manager_bp.context_processor
def _i18n():
    """t('key', ...) in the manager templates; Hindi by default, English by the switch."""
    return {"t": tr, "lang": i18n.lang(), "label": i18n.label}


@manager_bp.route("/lang", methods=["POST"])
@login_required
@manager_required
def set_lang():
    """Switch the app between Hindi and English (a cookie on this phone)."""
    chosen = "en" if request.form.get("lang") == "en" else "hi"
    nxt = request.form.get("next", "")
    # only ever return to a manager page on this site
    if not (nxt.startswith("/manager/") and not nxt.startswith("//") and "\\" not in nxt):
        nxt = url_for("manager.home")
    resp = make_response(redirect(nxt))
    resp.set_cookie(i18n.COOKIE, chosen, max_age=i18n.MAX_AGE, samesite="Lax", httponly=True)
    return resp

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
    return i18n.fmt_date(d)


def _fmt_short(d) -> str:
    return i18n.fmt_short(d)


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

    drill = mock_drill_status()

    return render_template(
        "manager/home.html",
        greeting=_greeting(),
        today_str=_fmt_date(today_op),
        day_name=i18n.weekday(today_op),
        prev_str=_fmt_short(prev_op),
        shift_readings_done=shift_readings_done,
        expenses_done=expenses_done,
        paytm_done=paytm_done,
        pending_payments=pending_with_customers,
        drill=drill,
        drill_attention=drill_needs_attention(drill),
        drill_due_str=_fmt_date(drill["due"]) if drill["due"] else "",
        drill_last_str=_fmt_date(drill["last"]) if drill["last"] else "",
    )


@manager_bp.route("/lube/", methods=["GET", "POST"])
@login_required
@manager_required
def lube():
    from pumpvision.models import AppNotification, Customer, LubeProduct, LubeTransaction, db

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
            error = tr("err_product")
        elif quantity is None or not math.isfinite(quantity) or quantity <= 0:
            error = tr("err_qty")
        elif unit_price is None or not math.isfinite(unit_price) or unit_price <= 0:
            error = tr("err_unit_price")
        elif payment_mode not in ("cash", "credit"):
            error = tr("err_mode")
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
                error = tr("err_credit_customer")

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
            if product.purchase_rate and unit_price < product.purchase_rate:
                db.session.add(AppNotification(
                    message=(f"Lube sold below cost: {product.name} {product.pack_size} at "
                             f"₹{unit_price:,.2f} (cost ₹{product.purchase_rate:,.2f}), "
                             f"qty {quantity:g}, {get_operational_date():%d %b %Y}."),
                    notification_type="lube_alert",
                    reference_date=get_operational_date(),
                ))
            db.session.commit()
            message = tr("lube_logged", amount=f"{amount:,.2f}", product=product.name)
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
    # A category that has sub-categories defined in code (e.g. EMI) is always offered,
    # even if the stored setting predates it.
    cats += [c for c in DEFAULT_EXPENSE_CATEGORIES if c not in cats]
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
        "sub_category": "",
        "description": "",
        "op_date": today_op.isoformat(),
    }

    if request.method == "POST":
        raw_amount = request.form.get("amount", "").strip()
        category = request.form.get("category", "").strip()
        sub_category = request.form.get("sub_category", "").strip()
        description = request.form.get("description", "").strip()[:200]
        op_date_str = request.form.get("op_date", "").strip()

        form_values.update({
            "amount": raw_amount,
            "category": category,
            "sub_category": sub_category,
            "description": description,
            "op_date": op_date_str or today_op.isoformat(),
        })

        error = None
        try:
            amount = float(raw_amount)
        except ValueError:
            amount = None
        if amount is None or not math.isfinite(amount) or amount <= 0:
            error = tr("err_amount")
        elif category not in categories:
            error = tr("err_category")
        elif EXPENSE_SUBCATEGORIES.get(category) and sub_category not in EXPENSE_SUBCATEGORIES[category]:
            error = tr("err_subcategory")
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
                sub_category=sub_category or None,
                description=description or None,
                op_date=op_date,
                logged_by=current_user.id,
            ))
            db.session.commit()
            shown = i18n.label("cat", category)
            if sub_category:
                shown += f" / {i18n.label('sub', sub_category)}"
            flash(tr("expense_logged", amount=f"{amount:,.2f}", category=shown), "ok")
            return redirect(url_for("manager.home"))

    return render_template(
        "manager/expense.html",
        categories=categories,
        subcategories=EXPENSE_SUBCATEGORIES,
        values=form_values,
    )


@manager_bp.route("/drill/", methods=["GET", "POST"])
@login_required
@manager_required
def drill():
    """Record a completed mock drill. One is mandatory every three months."""
    from pumpvision.models import MockDrill, db

    today = date.today()
    values = {"drill_date": today.isoformat(), "notes": ""}

    if request.method == "POST":
        raw_date = request.form.get("drill_date", "").strip()
        notes = request.form.get("notes", "").strip()[:300]
        values.update({"drill_date": raw_date or today.isoformat(), "notes": notes})
        try:
            drill_date = date.fromisoformat(raw_date)
        except ValueError:
            drill_date = None
        if drill_date is None or drill_date > today:
            flash(tr("err_drill_date"), "error")
        else:
            db.session.add(MockDrill(drill_date=drill_date, notes=notes or None, logged_by=current_user.id))
            db.session.commit()
            flash(tr("drill_logged", date=_fmt_date(drill_date)), "ok")
            return redirect(url_for("manager.home"))

    status = mock_drill_status()
    history = MockDrill.query.order_by(MockDrill.drill_date.desc(), MockDrill.id.desc()).limit(8).all()
    return render_template(
        "manager/drill.html",
        values=values,
        status=status,
        due_str=_fmt_date(status["due"]) if status["due"] else "",
        history=[(_fmt_date(d.drill_date), d.notes) for d in history],
        interval_months=MOCK_DRILL_INTERVAL_DAYS // 30,
        today=today.isoformat(),
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
            error = tr("err_customer")
        elif amount is None or not math.isfinite(amount) or amount <= 0:
            error = tr("err_amount")
        elif payment_mode not in ("Cash", "Cheque", "Bank Transfer"):
            error = tr("err_mode")

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
                customer.add_to_balance(-amount)
            db.session.commit()
            if status == "confirmed":
                flash(tr("payment_recorded", amount=f"{amount:,.2f}", customer=customer.company_name), "ok")
            else:
                flash(tr("transfer_recorded"), "ok")
            return redirect(url_for("manager.home"))

    return render_template(
        "manager/payment.html",
        customers=customers,
        values=form_values,
    )


# ─── Credit sales (the manager may enter them as well as the attendants) ──────



@manager_bp.route("/credit/")
@login_required
@manager_required
def credit_select():
    from pumpvision.models import Customer

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()
    return render_template("manager/credit_select.html", customers=customers)


@manager_bp.route("/credit/<int:customer_id>", methods=["GET", "POST"])
@login_required
@manager_required
def credit_sale(customer_id):
    from pumpvision.models import Customer
    from pumpvision.services.credit_sale import rate_map, record_sale

    customer = Customer.query.get_or_404(customer_id)
    if not customer.is_active:
        flash(tr("suspended"), "error")
        return redirect(url_for("manager.credit_select"))

    rates = rate_map()
    values = {"vehicle_number": "", "product": "", "input_mode": "amount", "quantity": ""}
    if request.method == "POST":
        values.update({k: request.form.get(k, "").strip() for k in values})
        txn, errors = record_sale(
            customer, values["vehicle_number"], values["product"],
            "litres" if values["input_mode"] == "litres" else "amount",
            values["quantity"], current_user.first_name or current_user.username, rates=rates,
        )
        if txn:
            return redirect(url_for("manager.credit_done", transaction_id=txn.transaction_id))
        for e in errors:
            flash(tr(e), "error")

    return render_template(
        "manager/credit_form.html",
        customer=customer,
        vehicles=[v.vehicle_number for v in customer.vehicles if v.is_active],
        rates=rates,
        values=values,
    )


@manager_bp.route("/credit/done/<int:transaction_id>")
@login_required
@manager_required
def credit_done(transaction_id):
    from pumpvision.models import CreditTransaction, Customer

    txn = CreditTransaction.query.get_or_404(transaction_id)
    return render_template("manager/credit_done.html", txn=txn,
                           customer=Customer.query.get_or_404(txn.customer_id))


@manager_bp.route("/invoice/")
@login_required
@manager_required
def invoice():
    return render_template("manager/coming_soon.html", feature=tr("feature_invoice"))
