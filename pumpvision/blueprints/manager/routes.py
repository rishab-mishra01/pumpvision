from datetime import datetime, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required

from pumpvision.decorators import manager_required
from pumpvision.services.operational import get_operational_date

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


@manager_bp.route("/lube/")
@login_required
@manager_required
def lube():
    return render_template("manager/coming_soon.html", feature="Log Lube Sale")


@manager_bp.route("/expense/")
@login_required
@manager_required
def expense():
    return render_template("manager/coming_soon.html", feature="Log Expense")


@manager_bp.route("/fleet/")
@login_required
@manager_required
def fleet():
    return render_template("manager/coming_soon.html", feature="Log Fleet Card")


@manager_bp.route("/payment/")
@login_required
@manager_required
def payment():
    return render_template("manager/coming_soon.html", feature="Record Payment")


@manager_bp.route("/invoice/")
@login_required
@manager_required
def invoice():
    return render_template("manager/coming_soon.html", feature="Generate Invoice")
