import csv
import io
from datetime import datetime, date, time, timedelta

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required
from sqlalchemy import func

from pumpvision.decorators import owner_required
from pumpvision.models import db, PaytmTransaction

paytm_bp = Blueprint("paytm", __name__)


# ─── Parser ──────────────────────────────────────────────────────────────────

def _parse_paytm_csv(file_stream) -> tuple[list[dict], list[str]]:
    """
    Parse a Paytm Transaction Report CSV.

    Returns (records, warnings):
    - records: list of dicts ready for PaytmTransaction insertion
    - warnings: list of skipped-row messages (unknown Request_Type, parse errors)
    """
    content = file_stream.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig")  # strip BOM if present

    reader = csv.DictReader(io.StringIO(content))
    records = []
    warnings = []

    def _v(row, key):
        """Get a field value, stripping surrounding single-quotes Paytm adds."""
        return row.get(key, "").strip().strip("'")

    for i, row in enumerate(reader, start=2):  # row 1 is header
        if _v(row, "Transaction_Type") != "ACQUIRING":
            continue
        if _v(row, "Status") != "SUCCESS":
            continue

        request_type = _v(row, "Request_Type")
        if request_type == "SEAMLESS_3D_FORM":
            category = "UPI"
        elif request_type == "EDC":
            category = "CARD"
        else:
            # Skip any other types silently (refunds, etc. that slipped through)
            continue

        try:
            txn_id = _v(row, "Transaction_ID")
            txn_dt = datetime.strptime(_v(row, "Transaction_Date"), "%Y-%m-%d %H:%M:%S")
            amount = float(_v(row, "Amount") or 0)
        except (ValueError, KeyError) as e:
            warnings.append(f"Row {i}: skipped — {e}")
            continue

        # 06:00 operational day boundary
        if txn_dt.time() >= time(6, 0):
            op_date = txn_dt.date()
        else:
            op_date = txn_dt.date() - timedelta(days=1)

        records.append(
            {
                "paytm_txn_id": txn_id,
                "transaction_datetime": txn_dt,
                "operational_date": op_date,
                "amount": amount,
                "payment_mode": _v(row, "Payment_Mode"),
                "payment_category": category,
                "pos_id": _v(row, "POS_ID"),
            }
        )

    return records, warnings


# ─── Routes ──────────────────────────────────────────────────────────────────

@paytm_bp.route("/")
@login_required
@owner_required
def index():
    """List of uploaded operational days with UPI + card totals."""
    rows = (
        db.session.query(
            PaytmTransaction.operational_date,
            PaytmTransaction.payment_category,
            func.count(PaytmTransaction.id).label("txn_count"),
            func.sum(PaytmTransaction.amount).label("total"),
        )
        .group_by(PaytmTransaction.operational_date, PaytmTransaction.payment_category)
        .order_by(PaytmTransaction.operational_date.desc())
        .all()
    )

    # Reshape into {date: {UPI: {count, total}, CARD: {count, total}}}
    summary: dict[date, dict] = {}
    for r in rows:
        d = r.operational_date
        if d not in summary:
            summary[d] = {"UPI": {"count": 0, "total": 0.0}, "CARD": {"count": 0, "total": 0.0}}
        summary[d][r.payment_category] = {
            "count": r.txn_count,
            "total": round(r.total, 2),
        }

    # Sort descending
    days = sorted(summary.items(), key=lambda x: x[0], reverse=True)

    return render_template("paytm/index.html", days=days)


@paytm_bp.route("/upload", methods=["GET", "POST"])
@login_required
@owner_required
def upload():
    if request.method == "POST":
        f = request.files.get("csv_file")
        if not f or not f.filename:
            flash("Please select a CSV file.", "error")
            return redirect(url_for("paytm.upload"))

        if not f.filename.lower().endswith(".csv"):
            flash("Only .csv files are accepted.", "error")
            return redirect(url_for("paytm.upload"))

        records, warnings = _parse_paytm_csv(f.stream)

        if not records:
            flash("No valid ACQUIRING+SUCCESS transactions found in file.", "warning")
            return redirect(url_for("paytm.upload"))

        inserted = 0
        skipped = 0
        for rec in records:
            existing = db.session.query(PaytmTransaction).filter_by(
                paytm_txn_id=rec["paytm_txn_id"]
            ).first()
            if existing:
                skipped += 1
                continue
            db.session.add(PaytmTransaction(**rec))
            inserted += 1

        db.session.commit()

        msg = f"Imported {inserted} transactions."
        if skipped:
            msg += f" {skipped} duplicates skipped."
        if warnings:
            msg += f" {len(warnings)} rows had parse errors."
        flash(msg, "success" if inserted else "warning")

        # Redirect to the day view for the first date found
        first_date = records[0]["operational_date"]
        return redirect(url_for("paytm.day", date_str=first_date.strftime("%Y-%m-%d")))

    return render_template("paytm/upload.html")


@paytm_bp.route("/day/<date_str>")
@login_required
@owner_required
def day(date_str):
    try:
        op_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date.", "error")
        return redirect(url_for("paytm.index"))

    txns = (
        PaytmTransaction.query
        .filter_by(operational_date=op_date)
        .order_by(PaytmTransaction.transaction_datetime)
        .all()
    )

    upi_total = sum(t.amount for t in txns if t.payment_category == "UPI")
    card_total = sum(t.amount for t in txns if t.payment_category == "CARD")
    grand_total = upi_total + card_total

    return render_template(
        "paytm/day.html",
        op_date=op_date,
        txns=txns,
        upi_total=upi_total,
        card_total=card_total,
        grand_total=grand_total,
    )
