from datetime import date, datetime, timedelta

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import desc

owner_bp = Blueprint("owner", __name__)


def owner_required(f):
    from functools import wraps

    @wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != "owner":
            abort(403)
        return f(*args, **kwargs)

    return decorated


@owner_bp.route("/dashboard")
@login_required
@owner_required
def dashboard():
    from models import AppSetting, Customer, CreditTransaction

    threshold_setting = AppSetting.query.get("alert_threshold")
    threshold = float(threshold_setting.value) if threshold_setting else 80.0

    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()

    # Customers over alert threshold
    alert_customers = [c for c in customers if c.utilization_pct >= threshold]

    # Total outstanding across all active customers
    total_outstanding = sum(c.outstanding_balance for c in customers)

    # This month's credit extended
    today = date.today()
    month_start = today.replace(day=1)
    this_month_credit = (
        CreditTransaction.query.join(Customer)
        .filter(
            Customer.is_active == True,
            CreditTransaction.transaction_date >= month_start,
        )
        .with_entities(CreditTransaction.amount)
        .all()
    )
    this_month_total = sum(row.amount for row in this_month_credit)

    # Overdue customers: outstanding > 0 and oldest unpaid invoice past due
    from models import Invoice

    overdue_customers = []
    for c in customers:
        overdue_invoice = (
            Invoice.query.filter_by(customer_id=c.customer_id, is_paid=False)
            .filter(Invoice.due_date < today)
            .first()
        )
        if overdue_invoice:
            overdue_customers.append(c)

    return render_template(
        "owner/dashboard.html",
        customers=customers,
        alert_customers=alert_customers,
        total_outstanding=total_outstanding,
        this_month_total=this_month_total,
        overdue_customers=overdue_customers,
        threshold=threshold,
    )


@owner_bp.route("/customers")
@login_required
@owner_required
def customers():
    from models import Customer

    all_customers = Customer.query.order_by(Customer.company_name).all()
    return render_template("owner/customers.html", customers=all_customers)


@owner_bp.route("/customers/new", methods=["GET", "POST"])
@login_required
@owner_required
def new_customer():
    if request.method == "POST":
        return _save_customer(None)
    return render_template("owner/customer_form.html", customer=None)


@owner_bp.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@owner_required
def edit_customer(customer_id):
    from models import Customer

    customer = Customer.query.get_or_404(customer_id)
    if request.method == "POST":
        return _save_customer(customer)
    return render_template("owner/customer_form.html", customer=customer)


def _save_customer(customer):
    from models import AuthorizedVehicle, Customer, db

    company_name = request.form.get("company_name", "").strip()
    fleet_manager_name = request.form.get("fleet_manager_name", "").strip()
    whatsapp_number = request.form.get("whatsapp_number", "").strip()
    credit_limit = request.form.get("credit_limit", "0")
    payment_terms_days = request.form.get("payment_terms_days", "30")
    gst_number = request.form.get("gst_number", "").strip()
    notes = request.form.get("notes", "").strip()
    is_active = request.form.get("is_active") == "on"

    if not company_name or not fleet_manager_name or not whatsapp_number:
        flash("Company name, fleet manager name, and WhatsApp number are required.", "error")
        return render_template("owner/customer_form.html", customer=customer)

    try:
        credit_limit = float(credit_limit)
        payment_terms_days = int(payment_terms_days)
    except ValueError:
        flash("Credit limit and payment terms must be numbers.", "error")
        return render_template("owner/customer_form.html", customer=customer)

    if customer is None:
        customer = Customer()
        db.session.add(customer)

    customer.company_name = company_name
    customer.fleet_manager_name = fleet_manager_name
    customer.whatsapp_number = whatsapp_number
    customer.credit_limit = credit_limit
    customer.payment_terms_days = payment_terms_days
    customer.gst_number = gst_number or None
    customer.notes = notes or None
    customer.is_active = is_active

    db.session.flush()  # get customer_id if new

    # Handle vehicle numbers
    vehicle_numbers_raw = request.form.get("vehicle_numbers", "")
    new_vehicles = [v.strip().upper() for v in vehicle_numbers_raw.splitlines() if v.strip()]

    # Deactivate vehicles no longer listed
    for v in customer.vehicles:
        if v.vehicle_number not in new_vehicles:
            v.is_active = False

    existing_nums = {v.vehicle_number for v in customer.vehicles}
    for vnum in new_vehicles:
        if vnum not in existing_nums:
            db.session.add(
                AuthorizedVehicle(
                    customer_id=customer.customer_id,
                    vehicle_number=vnum,
                    is_active=True,
                )
            )

    db.session.commit()
    flash(f"Customer '{customer.company_name}' saved.", "success")
    return redirect(url_for("owner.customers"))


@owner_bp.route("/ledger/<int:customer_id>")
@login_required
@owner_required
def ledger(customer_id):
    from models import CreditTransaction, Customer, Invoice, PaymentReceived

    customer = Customer.query.get_or_404(customer_id)
    transactions = (
        CreditTransaction.query.filter_by(customer_id=customer_id)
        .order_by(desc(CreditTransaction.transaction_date), desc(CreditTransaction.transaction_time))
        .all()
    )
    invoices = (
        Invoice.query.filter_by(customer_id=customer_id)
        .order_by(desc(Invoice.period_to))
        .all()
    )
    payments = (
        PaymentReceived.query.filter_by(customer_id=customer_id)
        .order_by(desc(PaymentReceived.payment_date))
        .all()
    )
    return render_template(
        "owner/ledger.html",
        customer=customer,
        transactions=transactions,
        invoices=invoices,
        payments=payments,
        now_date=date.today().isoformat(),
    )


@owner_bp.route("/ledger/<int:customer_id>/record-payment", methods=["POST"])
@login_required
@owner_required
def record_payment(customer_id):
    from models import Customer, Invoice, PaymentReceived, db

    customer = Customer.query.get_or_404(customer_id)
    invoice_id = request.form.get("invoice_id")
    amount_str = request.form.get("amount", "")
    payment_date_str = request.form.get("payment_date", "")
    payment_mode = request.form.get("payment_mode", "").strip()
    reference_number = request.form.get("reference_number", "").strip()
    notes = request.form.get("notes", "").strip()

    try:
        amount = float(amount_str)
        payment_date = date.fromisoformat(payment_date_str)
    except (ValueError, TypeError):
        flash("Invalid amount or date.", "error")
        return redirect(url_for("owner.ledger", customer_id=customer_id))

    invoice = Invoice.query.get_or_404(int(invoice_id))
    if invoice.customer_id != customer_id:
        abort(403)

    payment = PaymentReceived(
        invoice_id=invoice.invoice_id,
        customer_id=customer_id,
        amount=amount,
        payment_date=payment_date,
        payment_mode=payment_mode or None,
        reference_number=reference_number or None,
        notes=notes or None,
    )
    db.session.add(payment)

    # Update outstanding balance atomically
    customer.outstanding_balance = max(0.0, customer.outstanding_balance - amount)

    # Mark invoice paid if amount >= total
    total_paid = sum(p.amount for p in invoice.payments) + amount
    if total_paid >= invoice.total_amount:
        invoice.is_paid = True
        invoice.paid_at = datetime.utcnow()
        invoice.paid_amount = total_paid

    db.session.commit()
    flash(f"Payment of ₹{amount:,.2f} recorded.", "success")
    return redirect(url_for("owner.ledger", customer_id=customer_id))


@owner_bp.route("/invoices")
@login_required
@owner_required
def invoices():
    from models import Customer, Invoice

    all_invoices = (
        Invoice.query.join(Customer)
        .order_by(desc(Invoice.generated_at))
        .all()
    )
    customers = Customer.query.filter_by(is_active=True).order_by(Customer.company_name).all()
    return render_template("owner/invoices.html", invoices=all_invoices, customers=customers)


@owner_bp.route("/invoices/generate", methods=["POST"])
@login_required
@owner_required
def generate_invoice():
    from models import CreditTransaction, Customer, Invoice, db

    customer_id = int(request.form.get("customer_id"))
    period_from_str = request.form.get("period_from")
    period_to_str = request.form.get("period_to")

    try:
        period_from = date.fromisoformat(period_from_str)
        period_to = date.fromisoformat(period_to_str)
    except (ValueError, TypeError):
        flash("Invalid date range.", "error")
        return redirect(url_for("owner.invoices"))

    customer = Customer.query.get_or_404(customer_id)

    txns = CreditTransaction.query.filter(
        CreditTransaction.customer_id == customer_id,
        CreditTransaction.transaction_date >= period_from,
        CreditTransaction.transaction_date <= period_to,
    ).all()

    if not txns:
        flash("No transactions found for this period.", "error")
        return redirect(url_for("owner.invoices"))

    total_amount = sum(t.amount for t in txns)

    # Generate invoice number: INV-YYYY-MM-NNN
    month_str = period_to.strftime("%Y-%m")
    count = Invoice.query.filter(Invoice.invoice_number.like(f"INV-{month_str}-%")).count()
    invoice_number = f"INV-{month_str}-{count + 1:03d}"

    due_date = period_to + timedelta(days=customer.payment_terms_days)

    invoice = Invoice(
        customer_id=customer_id,
        invoice_number=invoice_number,
        period_from=period_from,
        period_to=period_to,
        total_amount=total_amount,
        due_date=due_date,
    )
    db.session.add(invoice)
    db.session.commit()

    flash(f"Invoice {invoice_number} generated for ₹{total_amount:,.2f}.", "success")
    return redirect(url_for("owner.invoices"))


@owner_bp.route("/invoices/<int:invoice_id>/pdf")
@login_required
@owner_required
def invoice_pdf(invoice_id):
    from models import CreditTransaction, Customer, Invoice

    invoice = Invoice.query.get_or_404(invoice_id)
    customer = Customer.query.get(invoice.customer_id)
    txns = CreditTransaction.query.filter(
        CreditTransaction.customer_id == invoice.customer_id,
        CreditTransaction.transaction_date >= invoice.period_from,
        CreditTransaction.transaction_date <= invoice.period_to,
    ).order_by(CreditTransaction.transaction_date, CreditTransaction.transaction_time).all()

    from io import BytesIO
    from flask import Response
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_RIGHT, TA_LEFT

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            leftMargin=20*mm, rightMargin=20*mm,
                            topMargin=20*mm, bottomMargin=20*mm)

    styles = getSampleStyleSheet()
    navy = colors.HexColor("#1e3a8a")
    grey = colors.HexColor("#f3f4f6")
    light = colors.HexColor("#f9fafb")

    h1 = ParagraphStyle("h1", fontSize=18, textColor=navy, spaceAfter=2)
    small = ParagraphStyle("small", fontSize=9, textColor=colors.HexColor("#555555"), spaceAfter=2)
    normal = ParagraphStyle("normal", fontSize=10, spaceAfter=3)
    bold = ParagraphStyle("bold", fontSize=10, fontName="Helvetica-Bold", spaceAfter=3)
    right = ParagraphStyle("right", fontSize=10, alignment=TA_RIGHT, spaceAfter=3)

    story = []

    # Header: outlet info left, invoice meta right
    gen_date = invoice.generated_at.strftime("%d %b %Y") if invoice.generated_at else "—"
    due_date = invoice.due_date.strftime("%d %b %Y") if invoice.due_date else "—"
    header_data = [[
        [Paragraph("TAX INVOICE", h1),
         Paragraph("Shree Petroleum · RO 206858 · Rewa, Madhya Pradesh", small),
         Paragraph("IndianOil Dealer · IOCL Retail Outlet", small)],
        [Paragraph(f"<b>{invoice.invoice_number}</b>", right),
         Paragraph(f"Generated: {gen_date}", ParagraphStyle("r", fontSize=9, alignment=TA_RIGHT)),
         Paragraph(f"Due: {due_date}", ParagraphStyle("r2", fontSize=9, alignment=TA_RIGHT))]
    ]]
    header_table = Table(header_data, colWidths=["60%", "40%"])
    header_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    story.append(header_table)
    story.append(Spacer(1, 6*mm))

    # Bill to
    story.append(Paragraph("<b>Bill To:</b>", normal))
    story.append(Paragraph(customer.company_name, bold))
    if customer.gst_number:
        story.append(Paragraph(f"GSTIN: {customer.gst_number}", normal))
    story.append(Paragraph(f"{customer.fleet_manager_name} · {customer.whatsapp_number}", normal))
    story.append(Spacer(1, 4*mm))

    period = (f"Period: {invoice.period_from.strftime('%d %b %Y')} "
              f"to {invoice.period_to.strftime('%d %b %Y')}")
    story.append(Paragraph(period, normal))
    story.append(Spacer(1, 4*mm))

    # Transactions table
    col_headers = ["Date", "Time", "Vehicle", "Product", "Litres", "Rate (₹)", "Amount (₹)"]
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
    rows.append(["Total", "", "", "", f"{total_litres:.2f}", "", f"₹{invoice.total_amount:,.2f}"])

    col_widths = [25*mm, 16*mm, 35*mm, 20*mm, 20*mm, 22*mm, 28*mm]
    txn_table = Table(rows, colWidths=col_widths, repeatRows=1)
    txn_style = TableStyle([
        ("BACKGROUND", (0,0), (-1,0), navy),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("ALIGN", (4,0), (-1,-1), "RIGHT"),
        ("GRID", (0,0), (-1,-2), 0.25, colors.HexColor("#e5e7eb")),
        ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, light]),
        ("BACKGROUND", (0,-1), (-1,-1), grey),
        ("FONTNAME", (0,-1), (-1,-1), "Helvetica-Bold"),
        ("LINEABOVE", (0,-1), (-1,-1), 1.5, navy),
        ("TOPPADDING", (0,0), (-1,-1), 4),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
    ])
    txn_table.setStyle(txn_style)
    story.append(txn_table)
    story.append(Spacer(1, 6*mm))

    # Footer
    story.append(Paragraph(f"Payment terms: {customer.payment_terms_days} days from invoice date.", small))
    story.append(Paragraph("This is a computer-generated invoice.", small))

    doc.build(story)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={invoice.invoice_number}.pdf"},
    )


@owner_bp.route("/settings", methods=["GET", "POST"])
@login_required
@owner_required
def settings():
    from models import AppSetting, db

    if request.method == "POST":
        threshold = request.form.get("alert_threshold", "80").strip()
        try:
            val = float(threshold)
            if not (0 <= val <= 100):
                raise ValueError
        except ValueError:
            flash("Threshold must be a number between 0 and 100.", "error")
            return redirect(url_for("owner.settings"))

        setting = AppSetting.query.get("alert_threshold")
        if setting:
            setting.value = str(val)
        else:
            db.session.add(AppSetting(key="alert_threshold", value=str(val)))
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("owner.settings"))

    threshold_setting = AppSetting.query.get("alert_threshold")
    threshold = float(threshold_setting.value) if threshold_setting else 80.0
    return render_template("owner/settings.html", threshold=threshold)


@owner_bp.route("/prices", methods=["GET", "POST"])
@login_required
@owner_required
def prices():
    from models import LocalPrice, db

    if request.method == "POST":
        product = request.form.get("product", "").strip().upper()
        rate_str = request.form.get("rate_per_litre", "")
        try:
            rate = float(rate_str)
        except ValueError:
            flash("Invalid rate.", "error")
            return redirect(url_for("owner.prices"))

        if product not in ("HS", "MS", "X2", "XG"):
            flash("Invalid product.", "error")
            return redirect(url_for("owner.prices"))

        now = datetime.utcnow()

        # Close existing active price for this product
        active = LocalPrice.query.filter_by(product=product, effective_to=None).first()
        if active:
            active.effective_to = now

        db.session.add(LocalPrice(product=product, rate_per_litre=rate, effective_from=now))
        db.session.commit()
        flash(f"{product} price updated to ₹{rate:.2f}/L.", "success")
        return redirect(url_for("owner.prices"))

    # Current active prices
    current_prices = {}
    for prod in ("HS", "MS", "X2", "XG"):
        p = LocalPrice.query.filter_by(product=prod, effective_to=None).order_by(
            LocalPrice.effective_from.desc()
        ).first()
        current_prices[prod] = p

    return render_template("owner/prices.html", current_prices=current_prices)
