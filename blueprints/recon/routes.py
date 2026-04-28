import subprocess
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import func, or_, and_

from pumpvision.decorators import owner_required
from pumpvision.models import (
    db, NozzleTotalizer, PaytmTransaction,
    CreditTransaction, IrasPrice, LocalPrice, AppSetting,
)

recon_bp = Blueprint("recon", __name__)

# Per-nozzle display order for the totalizer sales table.
# Matches the manual accounting sheet layout (HS 1/2, MS 1/2, X2, XG).
NOZZLE_ROWS = [
    {"label": "HS 1", "nozzle_no": 7,  "product": "HS"},
    {"label": "HS 2", "nozzle_no": 16, "product": "HS"},
    {"label": "MS 1", "nozzle_no": 18, "product": "MS"},
    {"label": "MS 2", "nozzle_no": 15, "product": "MS"},
    {"label": "X2",   "nozzle_no": 17, "product": "X2"},
    {"label": "XG",   "nozzle_no": 11, "product": "XG"},
]


def _default_date() -> date:
    """Default to yesterday's operational date (today's shift isn't closed yet)."""
    return date.today() - timedelta(days=1)


def _get_rsp(product: str, op_date: date) -> float | None:
    """
    Look up RSP for a product on a given operational date.
    Tries IrasPrice first (accurate historical), falls back to LocalPrice.
    """
    target_dt = datetime.combine(op_date, time(6, 0))

    price = IrasPrice.query.filter(
        IrasPrice.product == product,
        IrasPrice.effective_from <= target_dt,
        or_(IrasPrice.effective_to == None, IrasPrice.effective_to >= target_dt),
    ).order_by(IrasPrice.effective_from.desc()).first()

    if price:
        return price.rate_per_litre

    # Fall back to LocalPrice (manually maintained current RSP)
    local = LocalPrice.query.filter_by(product=product).order_by(
        LocalPrice.effective_from.desc()
    ).first()
    return local.rate_per_litre if local else None


def _checklist_status(op_date: date) -> dict:
    """Return readiness status for each reconciliation input."""
    next_date = op_date + timedelta(days=1)

    # Need opening (op_date boundary) AND closing (op_date+1 boundary) to compute diff
    opening_nozzles = {
        r.nozzle_no
        for r in NozzleTotalizer.query.filter_by(operational_date=op_date).all()
    }
    closing_nozzles = {
        r.nozzle_no
        for r in NozzleTotalizer.query.filter_by(operational_date=next_date).all()
    }
    totalizer_ready = bool(opening_nozzles and closing_nozzles)

    # Closing is only available after 06:00 on next_date (the shift boundary)
    closing_available = datetime.now() >= datetime.combine(next_date, time(6, 0))

    # Paytm: any transactions for this operational date
    paytm_ready = (
        db.session.query(PaytmTransaction.id)
        .filter_by(operational_date=op_date)
        .first() is not None
    )

    return {
        "totalizer": {
            "ready": totalizer_ready,
            "opening_nozzles": sorted(opening_nozzles),
            "closing_nozzles": sorted(closing_nozzles),
            "closing_available": closing_available,
        },
        "paytm": {"ready": paytm_ready},
        "credit": {"ready": True},
    }


def _calculate(op_date: date) -> dict:
    """
    Run the reconciliation calculation for an operational day.
    Returns a dict with per-product breakdown and cash derivation.
    """
    next_date = op_date + timedelta(days=1)

    # Opening = boundary reading at op_date 06:00 (shift start)
    # Closing = boundary reading at next_date 06:00 (shift end)
    opening_tots = {
        r.nozzle_no: r.totalizer_end
        for r in NozzleTotalizer.query.filter_by(operational_date=op_date).all()
    }
    closing_rows = NozzleTotalizer.query.filter_by(operational_date=next_date).all()
    closing_tots = {r.nozzle_no: r.totalizer_end for r in closing_rows}
    # Pump test deduction per nozzle — read from AppSetting (owner-configurable, default 5L)
    pump_tests = {}
    for nozzle_def in NOZZLE_ROWS:
        n = nozzle_def["nozzle_no"]
        setting = db.session.get(AppSetting, f"pump_test_nozzle_{n}")
        pump_tests[n] = float(setting.value) if setting else 0.0

    # Per-nozzle: litres sold + RSP + sales value
    nozzle_rows = []
    total_sales_value = 0.0

    for nozzle_def in NOZZLE_ROWS:
        label   = nozzle_def["label"]
        n       = nozzle_def["nozzle_no"]
        product = nozzle_def["product"]

        t_open  = opening_tots.get(n)
        t_close = closing_tots.get(n)
        diff    = round(t_close - t_open, 2) if (t_open is not None and t_close is not None) else None
        pt      = pump_tests.get(n, 0.0)
        net     = round(diff - pt, 2) if diff is not None else None
        rsp     = _get_rsp(product, op_date)
        sales_value = round(net * rsp, 2) if (net is not None and rsp) else 0.0
        total_sales_value += sales_value

        nozzle_rows.append({
            "label":           label,
            "nozzle_no":       n,
            "product":         product,
            "totalizer_open":  round(t_open, 2)  if t_open  is not None else None,
            "totalizer_close": round(t_close, 2) if t_close is not None else None,
            "totalizer_diff":  diff,
            "pump_test_litres": round(pt, 2),
            "litres":          net,
            "rsp":             rsp,
            "sales_value":     sales_value,
        })

    # Paytm total for this operational day
    paytm_total = db.session.query(
        func.sum(PaytmTransaction.amount)
    ).filter_by(operational_date=op_date).scalar() or 0.0

    paytm_upi = db.session.query(
        func.sum(PaytmTransaction.amount)
    ).filter_by(operational_date=op_date, payment_category="UPI").scalar() or 0.0

    paytm_card = db.session.query(
        func.sum(PaytmTransaction.amount)
    ).filter_by(operational_date=op_date, payment_category="CARD").scalar() or 0.0

    # Credit total: transactions within the 06:00-to-06:00 operational day boundary
    credit_total = db.session.query(
        func.sum(CreditTransaction.amount)
    ).filter(
        or_(
            and_(
                CreditTransaction.transaction_date == op_date,
                CreditTransaction.transaction_time >= time(6, 0),
            ),
            and_(
                CreditTransaction.transaction_date == next_date,
                CreditTransaction.transaction_time < time(6, 0),
            ),
        )
    ).scalar() or 0.0

    derived_cash = round(total_sales_value - paytm_total - credit_total, 2)

    return {
        "nozzle_rows": nozzle_rows,
        "total_sales_value": round(total_sales_value, 2),
        "paytm_total": round(paytm_total, 2),
        "paytm_upi": round(paytm_upi, 2),
        "paytm_card": round(paytm_card, 2),
        "credit_total": round(credit_total, 2),
        "derived_cash": derived_cash,
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@recon_bp.route("/run-scraper/<date_str>", methods=["POST"])
@login_required
@owner_required
def run_scraper(date_str):
    """
    Launch daily_scrape.py autonomously (headless, no CAPTCHA prompt) for the
    two shift dates that bracket op_date: op_date itself (opening totalizer)
    and op_date+1 (closing totalizer).
    """
    try:
        op_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("recon.index"))

    next_date = op_date + timedelta(days=1)
    scraper = Path(__file__).parent.parent.parent.parent / "scrapers" / "daily_scrape.py"
    log_path = Path(__file__).parent.parent.parent.parent / "instance" / "scraper.log"

    # Only fetch boundaries that are actually missing from the DB.
    # A boundary is complete when all 6 nozzles are present for that date.
    def _boundary_complete(d):
        return NozzleTotalizer.query.filter_by(operational_date=d).count() >= 6

    dates_needed = [
        d for d in [op_date, next_date]
        if not _boundary_complete(d)
    ]

    if not dates_needed:
        flash("Totalizer data is already complete for this date.", "info")
        return redirect(url_for("recon.day", date_str=date_str))

    with open(log_path, "a") as log:
        subprocess.Popen(
            [sys.executable, "-u", "-X", "utf8", str(scraper),
             "--dates", *[d.strftime("%Y-%m-%d") for d in dates_needed]],
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    flash(
        "Scraper running in the background — solving CAPTCHA and fetching data automatically. "
        "Come back and refresh in a minute or two.",
        "success",
    )
    return redirect(url_for("recon.day", date_str=date_str))


@recon_bp.route("/totalizer-help")
@login_required
@owner_required
def totalizer_help():
    return render_template("recon/totalizer_help.html")


@recon_bp.route("/")
@login_required
@owner_required
def index():
    return redirect(url_for("recon.day", date_str=_default_date().strftime("%Y-%m-%d")))


@recon_bp.route("/<date_str>")
@login_required
@owner_required
def day(date_str):
    try:
        op_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return redirect(url_for("recon.index"))

    status = _checklist_status(op_date)

    # Auto-calculate whenever both inputs are ready (GET or POST)
    if status["totalizer"]["ready"] and status["paytm"]["ready"]:
        result = _calculate(op_date)
    else:
        result = None

    prev_date = op_date - timedelta(days=1)
    next_date = op_date + timedelta(days=1)

    return render_template(
        "recon/day.html",
        op_date=op_date,
        prev_date=prev_date,
        next_date=next_date,
        status=status,
        result=result,
    )
