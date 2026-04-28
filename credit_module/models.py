from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


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
        if self.credit_limit == 0:
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


class LocalPrice(db.Model):
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
