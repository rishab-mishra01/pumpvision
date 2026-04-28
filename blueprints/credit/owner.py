from datetime import date, datetime, timedelta

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required

from pumpvision.decorators import owner_required

credit_bp = Blueprint("credit", __name__)


@credit_bp.route("/")
@login_required
@owner_required
def dashboard():
    from pumpvision.models import AppSetting, CreditTransaction, Customer, Invoice, db

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()

    threshold_setting = db.session.get(AppSetting, "alert_threshold")
    threshold = float(threshold_setting.value) if threshold_setting else 80.0

    alert_customers = [c for c in customers if c.utilization_pct >= threshold]

    today = date.today()
    overdue_customers = []
    for c in customers:
        unpaid = Invoice.query.filter_by(customer_id=c.customer_id, is_paid=False).all()
        if any(inv.due_date and inv.due_date < today for inv in unpaid):
            overdue_customers.append(c)

    month_start = today.replace(day=1)
    this_month_total = (
        db.session.query(db.func.sum(CreditTransaction.amount))
        .filter(CreditTransaction.transaction_date >= month_start)
        .scalar() or 0.0
    )
    total_outstanding = sum(c.outstanding_balance or 0 for c in customers)

    return render_template(
        "credit/owner/dashboard.html",
        customers=customers,
        alert_customers=alert_customers,
        overdue_customers=overdue_customers,
        total_outstanding=total_outstanding,
        this_month_total=this_month_total,
        threshold=threshold,
    )


@credit_bp.route("/customers")
@login_required
@owner_required
def customers():
    from pumpvision.models import Customer
    customers = Customer.query.order_by(Customer.company_name).all()
    return render_template("credit/owner/customers.html", customers=customers)


@credit_bp.route("/customers/new", methods=["GET", "POST"])
@login_required
@owner_required
def new_customer():
    from pumpvision.models import AuthorizedVehicle, Customer, db

    if request.method == "POST":
        company_name       = request.form.get("company_name", "").strip()
        gst_number         = request.form.get("gst_number", "").strip() or None
        fleet_manager_name = request.form.get("fleet_manager_name", "").strip()
        whatsapp_number    = request.form.get("whatsapp_number", "").strip()
        notes              = request.form.get("notes", "").strip() or None

        try:
            credit_limit = float(request.form.get("credit_limit", 0))
        except ValueError:
            credit_limit = 0.0
        try:
            payment_terms_days = int(request.form.get("payment_terms_days", 30))
        except ValueError:
            payment_terms_days = 30
        try:
            opening_balance = float(request.form.get("opening_balance", 0))
        except ValueError:
            opening_balance = 0.0

        if not company_name or not fleet_manager_name or not whatsapp_number:
            flash("Company name, fleet manager, and WhatsApp number are required.", "error")
            return render_template("credit/owner/customer_form.html", customer=None)

        customer = Customer(
            company_name=company_name,
            gst_number=gst_number,
            fleet_manager_name=fleet_manager_name,
            whatsapp_number=whatsapp_number,
            credit_limit=credit_limit,
            payment_terms_days=payment_terms_days,
            outstanding_balance=opening_balance,
            notes=notes,
        )
        db.session.add(customer)
        db.session.flush()

        raw_vehicles = request.form.get("vehicle_numbers", "")
        for line in raw_vehicles.splitlines():
            vnum = line.strip().upper()
            if vnum:
                db.session.add(AuthorizedVehicle(
                    customer_id=customer.customer_id,
                    vehicle_number=vnum,
                ))

        db.session.commit()
        flash(f"Customer {company_name} created.", "success")
        return redirect(url_for("credit.customers"))

    return render_template("credit/owner/customer_form.html", customer=None)


@credit_bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@owner_required
def edit_customer(customer_id):
    from pumpvision.models import AuthorizedVehicle, Customer, db

    customer = Customer.query.get_or_404(customer_id)

    if request.method == "POST":
        customer.company_name       = request.form.get("company_name", "").strip()
        customer.gst_number         = request.form.get("gst_number", "").strip() or None
        customer.fleet_manager_name = request.form.get("fleet_manager_name", "").strip()
        customer.whatsapp_number    = request.form.get("whatsapp_number", "").strip()
        customer.notes              = request.form.get("notes", "").strip() or None
        customer.is_active          = request.form.get("is_active") == "on"

        try:
            customer.credit_limit = float(request.form.get("credit_limit", 0))
        except ValueError:
            pass
        try:
            customer.payment_terms_days = int(request.form.get("payment_terms_days", 30))
        except ValueError:
            pass
        try:
            customer.outstanding_balance = float(request.form.get("opening_balance", 0))
        except ValueError:
            pass

        # Rebuild vehicles: deactivate all then re-add from form
        for v in customer.vehicles:
            v.is_active = False

        raw_vehicles = request.form.get("vehicle_numbers", "")
        existing = {v.vehicle_number: v for v in customer.vehicles}
        for line in raw_vehicles.splitlines():
            vnum = line.strip().upper()
            if not vnum:
                continue
            if vnum in existing:
                existing[vnum].is_active = True
            else:
                db.session.add(AuthorizedVehicle(
                    customer_id=customer.customer_id,
                    vehicle_number=vnum,
                ))

        db.session.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("credit.customers"))

    return render_template("credit/owner/customer_form.html", customer=customer)


@credit_bp.route("/customers/<int:customer_id>/ledger")
@login_required
@owner_required
def ledger(customer_id):
    from pumpvision.models import CreditTransaction, Customer, Invoice, PaymentReceived

    customer = Customer.query.get_or_404(customer_id)
    transactions = (
        CreditTransaction.query
        .filter_by(customer_id=customer_id)
        .order_by(CreditTransaction.transaction_date.desc(), CreditTransaction.transaction_time.desc())
        .all()
    )
    invoices = (
        Invoice.query
        .filter_by(customer_id=customer_id)
        .order_by(Invoice.generated_at.desc())
        .all()
    )
    payments = (
        PaymentReceived.query
        .filter_by(customer_id=customer_id)
        .order_by(PaymentReceived.payment_date.desc())
        .all()
    )
    return render_template(
        "credit/owner/ledger.html",
        customer=customer,
        transactions=transactions,
        invoices=invoices,
        payments=payments,
        now_date=date.today().isoformat(),
    )


@credit_bp.route("/customers/<int:customer_id>/payment", methods=["POST"])
@login_required
@owner_required
def record_payment(customer_id):
    from pumpvision.models import Customer, Invoice, PaymentReceived, db

    customer = Customer.query.get_or_404(customer_id)
    invoice_id = request.form.get("invoice_id")
    invoice = Invoice.query.get_or_404(int(invoice_id))

    try:
        amount = float(request.form.get("amount", 0))
    except ValueError:
        flash("Invalid amount.", "error")
        return redirect(url_for("credit.ledger", customer_id=customer_id))

    payment_date_str = request.form.get("payment_date", "")
    try:
        payment_date = date.fromisoformat(payment_date_str)
    except ValueError:
        payment_date = date.today()

    payment = PaymentReceived(
        invoice_id=invoice.invoice_id,
        customer_id=customer_id,
        amount=amount,
        payment_date=payment_date,
        payment_mode=request.form.get("payment_mode") or None,
        reference_number=request.form.get("reference_number", "").strip() or None,
    )
    db.session.add(payment)

    customer.outstanding_balance = max(0.0, (customer.outstanding_balance or 0.0) - amount)
    if amount >= invoice.total_amount:
        invoice.is_paid = True
        invoice.paid_at = datetime.utcnow()
        invoice.paid_amount = amount

    db.session.commit()
    flash(f"Payment of ₹{amount:,.2f} recorded.", "success")
    return redirect(url_for("credit.ledger", customer_id=customer_id))


@credit_bp.route("/invoices")
@login_required
@owner_required
def invoices():
    from pumpvision.models import Customer, Invoice

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()
    invoices = (
        Invoice.query
        .order_by(Invoice.generated_at.desc())
        .all()
    )
    return render_template("credit/owner/invoices.html", invoices=invoices, customers=customers)


@credit_bp.route("/invoices/generate", methods=["POST"])
@login_required
@owner_required
def generate_invoice():
    from pumpvision.models import CreditTransaction, Customer, Invoice, db

    customer_id = request.form.get("customer_id")
    period_from_str = request.form.get("period_from", "")
    period_to_str   = request.form.get("period_to", "")

    try:
        customer = Customer.query.get_or_404(int(customer_id))
        period_from = date.fromisoformat(period_from_str)
        period_to   = date.fromisoformat(period_to_str)
    except (ValueError, TypeError):
        flash("Invalid customer or date range.", "error")
        return redirect(url_for("credit.invoices"))

    txns = CreditTransaction.query.filter(
        CreditTransaction.customer_id == customer.customer_id,
        CreditTransaction.transaction_date >= period_from,
        CreditTransaction.transaction_date <= period_to,
    ).all()

    total_amount = sum(t.amount for t in txns)
    if total_amount == 0:
        flash("No transactions found for this period.", "error")
        return redirect(url_for("credit.invoices"))

    month_str = period_to.strftime("%Y-%m")
    count = Invoice.query.filter(Invoice.invoice_number.like(f"INV-{month_str}-%")).count()
    invoice_number = f"INV-{month_str}-{count + 1:03d}"
    due_date = period_to + timedelta(days=customer.payment_terms_days)

    invoice = Invoice(
        customer_id=customer.customer_id,
        invoice_number=invoice_number,
        period_from=period_from,
        period_to=period_to,
        total_amount=total_amount,
        due_date=due_date,
    )
    db.session.add(invoice)
    db.session.commit()

    flash(f"Invoice {invoice_number} generated for ₹{total_amount:,.2f}.", "success")
    return redirect(url_for("credit.invoices"))


@credit_bp.route("/invoices/<int:invoice_id>/pdf")
@login_required
@owner_required
def invoice_pdf(invoice_id):
    from io import BytesIO

    from flask import Response
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    from pumpvision.models import CreditTransaction, Customer, Invoice

    invoice  = Invoice.query.get_or_404(invoice_id)
    customer = Customer.query.get(invoice.customer_id)
    txns = (
        CreditTransaction.query
        .filter(
            CreditTransaction.customer_id == invoice.customer_id,
            CreditTransaction.transaction_date >= invoice.period_from,
            CreditTransaction.transaction_date <= invoice.period_to,
        )
        .order_by(CreditTransaction.transaction_date, CreditTransaction.transaction_time)
        .all()
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    navy  = colors.HexColor("#1e3a8a")
    grey  = colors.HexColor("#f3f4f6")
    light = colors.HexColor("#f9fafb")

    h1     = ParagraphStyle("h1",   fontSize=18, textColor=navy, spaceAfter=2)
    small  = ParagraphStyle("small",fontSize=9,  textColor=colors.HexColor("#555555"), spaceAfter=2)
    normal = ParagraphStyle("normal",fontSize=10, spaceAfter=3)
    bold   = ParagraphStyle("bold", fontSize=10, fontName="Helvetica-Bold", spaceAfter=3)
    right  = ParagraphStyle("right",fontSize=10, alignment=TA_RIGHT, spaceAfter=3)
    rsmall = ParagraphStyle("rsmall",fontSize=9, alignment=TA_RIGHT)

    story = []

    gen_date = invoice.generated_at.strftime("%d %b %Y") if invoice.generated_at else "—"
    due_date = invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "—"

    header_data = [[
        [Paragraph("TAX INVOICE", h1),
         Paragraph("Shree Petroleum · RO 206858 · Rewa, Madhya Pradesh", small),
         Paragraph("IndianOil Dealer · IOCL Retail Outlet", small)],
        [Paragraph(f"<b>{invoice.invoice_number}</b>", right),
         Paragraph(f"Generated: {gen_date}", rsmall),
         Paragraph(f"Due: {due_date}", rsmall)],
    ]]
    header_table = Table(header_data, colWidths=["60%", "40%"])
    header_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(header_table)
    story.append(Spacer(1, 6*mm))

    story.append(Paragraph("<b>Bill To:</b>", normal))
    story.append(Paragraph(customer.company_name, bold))
    if customer.gst_number:
        story.append(Paragraph(f"GSTIN: {customer.gst_number}", normal))
    story.append(Paragraph(f"{customer.fleet_manager_name} · {customer.whatsapp_number}", normal))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph(
        f"Period: {invoice.period_from.strftime('%d %b %Y')} to {invoice.period_to.strftime('%d %b %Y')}",
        normal,
    ))
    story.append(Spacer(1, 4*mm))

    col_headers = ["Date", "Time", "Vehicle", "Product", "Litres", "Rate (Rs)", "Amount (Rs)"]
    rows = [col_headers]
    for t in txns:
        rows.append([
            t.transaction_date.strftime("%d/%m/%Y"),
            t.transaction_time.strftime("%H:%M"),
            t.vehicle_number,
            t.product,
            f"{t.litres:.2f}",
            f"{t.rate_per_litre:.2f}",
            f"{t.amount:,.2f}",
        ])
    total_litres = sum(t.litres for t in txns)
    rows.append(["Total", "", "", "", f"{total_litres:.2f}", "", f"Rs {invoice.total_amount:,.2f}"])

    col_widths = [25*mm, 16*mm, 35*mm, 20*mm, 20*mm, 22*mm, 28*mm]
    txn_table = Table(rows, colWidths=col_widths, repeatRows=1)
    txn_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0),  (-1, 0),  navy),
        ("TEXTCOLOR",    (0, 0),  (-1, 0),  colors.white),
        ("FONTNAME",     (0, 0),  (-1, 0),  "Helvetica-Bold"),
        ("FONTSIZE",     (0, 0),  (-1, -1), 9),
        ("ALIGN",        (4, 0),  (-1, -1), "RIGHT"),
        ("GRID",         (0, 0),  (-1, -2), 0.25, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -2), [colors.white, light]),
        ("BACKGROUND",   (0, -1), (-1, -1), grey),
        ("FONTNAME",     (0, -1), (-1, -1), "Helvetica-Bold"),
        ("LINEABOVE",    (0, -1), (-1, -1), 1.5, navy),
        ("TOPPADDING",   (0, 0),  (-1, -1), 4),
        ("BOTTOMPADDING",(0, 0),  (-1, -1), 4),
    ]))
    story.append(txn_table)
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph(f"Payment terms: {customer.payment_terms_days} days from invoice date.", small))
    story.append(Paragraph("This is a computer-generated invoice.", small))

    doc.build(story)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={invoice.invoice_number}.pdf"},
    )


@credit_bp.route("/prices", methods=["GET", "POST"])
@login_required
@owner_required
def prices():
    from pumpvision.models import LocalPrice, db

    if request.method == "POST":
        product  = request.form.get("product", "").strip().upper()
        rate_str = request.form.get("rate_per_litre", "")

        try:
            rate = float(rate_str)
        except ValueError:
            flash("Invalid rate.", "error")
            return redirect(url_for("credit.prices"))

        if product not in ("HS", "MS", "X2", "XG"):
            flash("Invalid product.", "error")
            return redirect(url_for("credit.prices"))

        now = datetime.utcnow()
        active = LocalPrice.query.filter(
            LocalPrice.product == product,
            LocalPrice.effective_to == None,
        ).all()
        for p in active:
            p.effective_to = now

        db.session.add(LocalPrice(product=product, rate_per_litre=rate, effective_from=now))
        db.session.commit()
        flash(f"{product} price updated to ₹{rate:.2f}/L.", "success")
        return redirect(url_for("credit.prices"))

    current_prices = {}
    for prod in ("HS", "MS", "X2", "XG"):
        current_prices[prod] = (
            LocalPrice.query
            .filter(LocalPrice.product == prod, LocalPrice.effective_to == None)
            .order_by(LocalPrice.effective_from.desc())
            .first()
        )

    return render_template("credit/owner/prices.html", current_prices=current_prices)


PUMP_TEST_NOZZLES = {
    7:  "HSD 1 (Nozzle 7)",
    16: "HSD 2 (Nozzle 16)",
    18: "MS 1 (Nozzle 18)",
    15: "MS 2 (Nozzle 15)",
    17: "XP (Nozzle 17)",
    11: "XG (Nozzle 11)",
}


@credit_bp.route("/settings", methods=["GET", "POST"])
@login_required
@owner_required
def settings():
    from pumpvision.models import AppSetting, db

    if request.method == "POST":
        errors = []

        threshold_str = request.form.get("alert_threshold", "80").strip()
        try:
            threshold_val = float(threshold_str)
            if not (0 <= threshold_val <= 100):
                raise ValueError
        except ValueError:
            errors.append("Credit alert threshold must be a number between 0 and 100.")

        pump_test_vals = {}
        for n in PUMP_TEST_NOZZLES:
            raw = request.form.get(f"pump_test_nozzle_{n}", "").strip()
            try:
                pump_test_vals[n] = max(0.0, float(raw))
            except (ValueError, TypeError):
                errors.append(f"Pump test value for {PUMP_TEST_NOZZLES[n]} must be a number.")

        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for("credit.settings"))

        setting = db.session.get(AppSetting, "alert_threshold")
        if setting:
            setting.value = str(threshold_val)
        else:
            db.session.add(AppSetting(key="alert_threshold", value=str(threshold_val)))

        for n, val in pump_test_vals.items():
            s = db.session.get(AppSetting, f"pump_test_nozzle_{n}")
            if s:
                s.value = str(val)
            else:
                db.session.add(AppSetting(key=f"pump_test_nozzle_{n}", value=str(val)))

        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("credit.settings"))

    threshold_setting = db.session.get(AppSetting, "alert_threshold")
    threshold = float(threshold_setting.value) if threshold_setting else 80.0

    pump_test_settings = {
        n: float((db.session.get(AppSetting, f"pump_test_nozzle_{n}") or AppSetting(value="0")).value)
        for n in PUMP_TEST_NOZZLES
    }

    return render_template(
        "credit/owner/settings.html",
        threshold=threshold,
        pump_test_settings=pump_test_settings,
        pump_test_nozzles=PUMP_TEST_NOZZLES,
    )
