from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash

from .extensions import db


# ─────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────

class User(UserMixin, db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'owner' / 'manager' / 'attendant'
    first_name = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


# ─────────────────────────────────────────────
# CREDIT MODULE
# ─────────────────────────────────────────────

class Customer(db.Model):
    __tablename__ = "customers"

    customer_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    company_name = db.Column(db.String(200), nullable=False)
    gst_number = db.Column(db.String(20))
    fleet_manager_name = db.Column(db.String(200), nullable=False)
    whatsapp_number = db.Column(db.String(20), nullable=False)
    credit_limit = db.Column(db.Float, nullable=False)
    payment_terms_days = db.Column(db.Integer, nullable=False)
    outstanding_balance = db.Column(db.Float, default=0.0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)

    vehicles = db.relationship("AuthorizedVehicle", backref="customer", lazy=True)
    transactions = db.relationship("CreditTransaction", backref="customer", lazy=True)
    invoices = db.relationship("Invoice", backref="customer", lazy=True)
    payments = db.relationship("PaymentReceived", backref="customer", lazy=True)

    @property
    def utilization_pct(self):
        if not self.credit_limit:
            return 0.0
        return (self.outstanding_balance / self.credit_limit) * 100


class AuthorizedVehicle(db.Model):
    __tablename__ = "authorized_vehicles"

    vehicle_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    vehicle_number = db.Column(db.String(20), nullable=False)
    vehicle_description = db.Column(db.String(200))
    is_active = db.Column(db.Boolean, default=True)


class CreditTransaction(db.Model):
    __tablename__ = "credit_transactions"

    transaction_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    vehicle_number = db.Column(db.String(20), nullable=False)
    transaction_date = db.Column(db.Date, nullable=False)
    transaction_time = db.Column(db.Time, nullable=False)
    product = db.Column(db.String(5), nullable=False)
    litres = db.Column(db.Float, nullable=False)
    rate_per_litre = db.Column(db.Float, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    attendant_name = db.Column(db.String(100), nullable=False)
    whatsapp_sent = db.Column(db.Boolean, default=False)
    whatsapp_confirmed = db.Column(db.Boolean, default=False)
    confirmation_timestamp = db.Column(db.DateTime)
    is_legacy_entry = db.Column(db.Boolean, default=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Invoice(db.Model):
    __tablename__ = "invoices"

    invoice_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    invoice_number = db.Column(db.String(30), nullable=False)
    period_from = db.Column(db.Date, nullable=False)
    period_to = db.Column(db.Date, nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    generated_at = db.Column(db.DateTime, default=datetime.utcnow)
    due_date = db.Column(db.Date)
    is_paid = db.Column(db.Boolean, default=False)
    paid_at = db.Column(db.DateTime)
    paid_amount = db.Column(db.Float)
    notes = db.Column(db.Text)

    payments = db.relationship("PaymentReceived", backref="invoice", lazy=True)


class PaymentReceived(db.Model):
    __tablename__ = "payments_received"

    payment_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    invoice_id = db.Column(db.Integer, db.ForeignKey("invoices.invoice_id"), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.customer_id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_date = db.Column(db.Date, nullable=False)
    payment_mode = db.Column(db.String(20))
    reference_number = db.Column(db.String(50))
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Sprint 0: payment verification workflow
    status = db.Column(db.String(30), default='confirmed')  # confirmed / pending_verification / flagged
    verified_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)


class LocalPrice(db.Model):
    """Manually maintained RSP fallback. Replaced by IrasPrice once IRAS integration is live."""
    __tablename__ = "local_prices"

    price_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product = db.Column(db.String(5), nullable=False)
    rate_per_litre = db.Column(db.Float, nullable=False)
    effective_from = db.Column(db.DateTime, nullable=False)
    effective_to = db.Column(db.DateTime)


class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(200), nullable=False)


# ─────────────────────────────────────────────
# IRAS DATA
# ─────────────────────────────────────────────

class IrasPrice(db.Model):
    """RSP records ingested from the IRAS Price (PRM) scraper."""
    __tablename__ = "iras_prices"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    product = db.Column(db.String(5), nullable=False)
    rate_per_litre = db.Column(db.Float, nullable=False)
    effective_from = db.Column(db.DateTime, nullable=False)
    effective_to = db.Column(db.DateTime)
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("product", "effective_from", name="uq_iras_price_product_from"),
    )


# ─────────────────────────────────────────────
# PAYTM
# ─────────────────────────────────────────────

class NozzleTotalizer(db.Model):
    """
    06:00 boundary totalizer reading per nozzle per operational day.
    Written directly by the ISS scraper after each boundary run.
    Litres sold for a day = today's totalizer_end − yesterday's totalizer_end.
    """
    __tablename__ = "nozzle_totalizers"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    operational_date = db.Column(db.Date, nullable=False, index=True)
    nozzle_no = db.Column(db.Integer, nullable=False)
    totalizer_end = db.Column(db.Float, nullable=False)
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow)
    # Litres dispensed in Pump Test (105) transactions during this operational day.
    # Subtracted from the totalizer diff to get net sales litres.
    pump_test_litres = db.Column(db.Float, default=0.0)

    __table_args__ = (
        db.UniqueConstraint("operational_date", "nozzle_no", name="uq_nozzle_totalizer"),
    )


# ─────────────────────────────────────────────
# MANUAL METER READINGS
# ─────────────────────────────────────────────

class ManualTotalizerReading(db.Model):
    """Employee-entered shift-closing totalizer reading per nozzle per operational day."""
    __tablename__ = "manual_totalizer_readings"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    operational_date = db.Column(db.Date, nullable=False, index=True)
    nozzle_label = db.Column(db.String(10), nullable=False)   # "HSD 1", "HSD 2", "MS 1", "MS 2", "XP", "XG", "CNG"
    nozzle_no = db.Column(db.Integer, nullable=True)           # null for CNG
    product = db.Column(db.String(10), nullable=False)         # "HSD", "MS", "XP", "XG", "CNG"
    totalizer_value = db.Column(db.Float, nullable=False)
    recorded_at = db.Column(db.DateTime, nullable=False)
    is_locked = db.Column(db.Boolean, default=False, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("operational_date", "nozzle_label", name="uq_manual_totalizer"),
    )


class AppNotification(db.Model):
    """In-app notifications displayed to the owner."""
    __tablename__ = "app_notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    message = db.Column(db.String(500), nullable=False)
    notification_type = db.Column(db.String(30), nullable=False, default="info")
    reference_date = db.Column(db.Date, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    is_read = db.Column(db.Boolean, default=False, nullable=False)


class PaytmTransaction(db.Model):
    """Individual Paytm POS transactions imported from Transaction Report CSV."""
    __tablename__ = "paytm_transactions"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    # Paytm's own transaction ID — used for deduplication
    paytm_txn_id = db.Column(db.String(60), nullable=False, unique=True)
    transaction_datetime = db.Column(db.DateTime, nullable=False)
    # Operational day this transaction belongs to (06:00-to-06:00 boundary)
    operational_date = db.Column(db.Date, nullable=False, index=True)
    amount = db.Column(db.Float, nullable=False)
    payment_mode = db.Column(db.String(30))      # UPI / CREDIT_CARD / DEBIT_CARD etc.
    payment_category = db.Column(db.String(10))  # "UPI" or "CARD" (derived)
    pos_id = db.Column(db.String(30))
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────
# LUBE MODULE
# ─────────────────────────────────────────────

class LubeProduct(db.Model):
    __tablename__ = 'lube_products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    pack_size = db.Column(db.String(20), nullable=False)
    unit = db.Column(db.String(20), default='unit')
    sale_rate = db.Column(db.Float, nullable=False)
    is_active = db.Column(db.Boolean, default=True)

    transactions = db.relationship('LubeTransaction', backref='product', lazy=True)


class LubeTransaction(db.Model):
    __tablename__ = 'lube_transactions'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('lube_products.id'), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit_price = db.Column(db.Float, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    payment_mode = db.Column(db.String(10), nullable=False)  # 'cash' or 'credit'
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.customer_id'), nullable=True)
    op_date = db.Column(db.Date, nullable=False)
    transaction_time = db.Column(db.DateTime, nullable=False)
    logged_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────
# EXPENSES
# ─────────────────────────────────────────────

class Expense(db.Model):
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    amount = db.Column(db.Float, nullable=False)
    category = db.Column(db.String(50), nullable=False)  # Staff/Maintenance/Utilities/Supplies/Misc
    description = db.Column(db.String(200))
    op_date = db.Column(db.Date, nullable=False)
    logged_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────
# FLEET CARD
# ─────────────────────────────────────────────

class FleetCardTransaction(db.Model):
    __tablename__ = 'fleet_card_transactions'

    id = db.Column(db.Integer, primary_key=True)
    card_identifier = db.Column(db.String(100), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    op_date = db.Column(db.Date, nullable=False)
    transaction_time = db.Column(db.DateTime)
    logged_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    notes = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────
# CNG
# ─────────────────────────────────────────────

class CngShiftReading(db.Model):
    """Attendant-entered CNG meter reading at shift close."""
    __tablename__ = 'cng_shift_readings'

    id = db.Column(db.Integer, primary_key=True)
    op_date = db.Column(db.Date, nullable=False, unique=True, index=True)
    opening_reading = db.Column(db.Float, nullable=False)
    closing_reading = db.Column(db.Float, nullable=False)
    kg_sold = db.Column(db.Float, nullable=False)
    rsp_per_kg = db.Column(db.Float, nullable=False)
    revenue = db.Column(db.Float, nullable=False)
    submitted_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────
# ATG TANK READINGS
# ─────────────────────────────────────────────

class SdmsSummary(db.Model):
    """Daily SDMS PAD scrape summary — fleet card totals + CNG totals per operational date."""
    __tablename__ = 'sdms_summaries'

    id = db.Column(db.Integer, primary_key=True)
    op_date = db.Column(db.Date, unique=True, nullable=False, index=True)
    fleet_card_total = db.Column(db.Float, default=0.0)
    fleet_card_count = db.Column(db.Integer, default=0)
    cng_kg_total = db.Column(db.Float, default=0.0)
    cng_revenue = db.Column(db.Float, default=0.0)
    cng_rsp_per_kg = db.Column(db.Float, default=93.40)
    cng_count = db.Column(db.Integer, default=0)
    opening_balance = db.Column(db.Float)
    closing_balance = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class TankReading(db.Model):
    """ATG stock snapshot scraped from IRAS Stock tab every ~30 minutes."""
    __tablename__ = 'tank_readings'

    id = db.Column(db.Integer, primary_key=True)
    scraped_at = db.Column(db.DateTime, nullable=False, index=True)
    tank_id = db.Column(db.Integer, nullable=False)
    product = db.Column(db.String(5), nullable=False)
    level_mm = db.Column(db.Float)
    volume_litres = db.Column(db.Float)
    capacity_litres = db.Column(db.Float)
    pct_full = db.Column(db.Float)
    is_reliable = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint('scraped_at', 'tank_id', name='uq_tank_reading_snapshot'),
    )
