"""Manual entries (expenses, lube sales, credit sales, payments, fuel tests) that the
owner can review and remove.

Removing one has to leave every number that was built on it correct, so each kind
undoes its own side effects here and nowhere else:

  expense / fuel test  nothing is stored on top of them: the dashboard and summary
                       re-add the day from the rows, so deleting the row is enough.
  lube credit sale     the customer's balance is reduced by the sale again.
  credit sale          same; refused once an invoice covers that date, because the
                       invoice (and what was billed on it) would then be wrong.
  payment received     a confirmed payment is added back to the balance, and the
                       invoice it was against is re-checked for "paid".
"""
from datetime import date, time, timedelta

from sqlalchemy import and_, or_

KINDS = ("expense", "lube", "credit", "payment", "test")


def list_entries(op_date: date) -> dict:
    from pumpvision.models import (
        CreditTransaction, Customer, Expense, FuelTest, LubeTransaction, PaymentReceived, User, db,
    )

    nd = op_date + timedelta(days=1)
    names = {c.customer_id: c.company_name for c in Customer.query.all()}
    users = {u.id: (u.first_name or u.username) for u in User.query.all()}

    credit = CreditTransaction.query.filter(
        CreditTransaction.is_legacy_entry.isnot(True),
        or_(
            and_(CreditTransaction.transaction_date == op_date,
                 CreditTransaction.transaction_time >= time(6, 0)),
            and_(CreditTransaction.transaction_date == nd,
                 CreditTransaction.transaction_time < time(6, 0)),
        ),
    ).order_by(CreditTransaction.transaction_date, CreditTransaction.transaction_time).all()

    return {
        "expense": Expense.query.filter_by(op_date=op_date).order_by(Expense.id).all(),
        "lube": LubeTransaction.query.filter_by(op_date=op_date).order_by(LubeTransaction.id).all(),
        "credit": credit,
        "payment": PaymentReceived.query.filter_by(payment_date=op_date)
                   .order_by(PaymentReceived.payment_id).all(),
        "test": FuelTest.query.filter_by(op_date=op_date).order_by(FuelTest.id).all(),
        "names": names,
        "users": users,
    }


def _get(kind: str, entry_id: int):
    from pumpvision.models import CreditTransaction, Expense, FuelTest, LubeTransaction, PaymentReceived, db

    model = {"expense": Expense, "lube": LubeTransaction, "credit": CreditTransaction,
             "payment": PaymentReceived, "test": FuelTest}.get(kind)
    return db.session.get(model, entry_id) if model else None


def describe(kind: str, entry_id: int):
    """(entry, headline, consequence) for the confirm page; entry is None if it is gone."""
    from pumpvision.models import Customer, LubeProduct, db

    e = _get(kind, entry_id)
    if e is None:
        return None, "", ""
    if kind == "expense":
        sub = f" · {e.sub_category}" if e.sub_category else ""
        return e, f"Expense ₹{e.amount:,.2f} — {e.category}{sub}", \
            "Cash in hand for that day goes up by this amount."
    if kind == "test":
        return e, f"{e.litres:g} L of {e.product} taken for testing", \
            "That volume counts as sold again, so the day's litres and revenue go up."
    if kind == "lube":
        p = db.session.get(LubeProduct, e.product_id)
        name = p.name if p else "Lube"
        if e.payment_mode == "credit" and e.customer_id:
            c = db.session.get(Customer, e.customer_id)
            return e, f"Lube sale ₹{e.amount:,.2f} — {name} (credit, {c.company_name if c else '?'})", \
                f"The sale is removed and ₹{e.amount:,.2f} comes off that customer's balance."
        return e, f"Lube sale ₹{e.amount:,.2f} — {name} (cash)", \
            "Lube revenue and cash in hand for that day go down by this amount."
    c = db.session.get(Customer, e.customer_id)
    cname = c.company_name if c else "?"
    if kind == "credit":
        return e, f"Credit sale ₹{e.amount:,.2f} — {cname}, {e.vehicle_number}, {e.litres:g} L {e.product}", \
            f"₹{e.amount:,.2f} comes off {cname}'s balance, and the day's credit total goes down."
    if e.status == "confirmed":
        return e, f"Payment ₹{e.amount:,.2f} from {cname} ({e.payment_mode})", \
            f"₹{e.amount:,.2f} is added back to {cname}'s balance."
    return e, f"Payment ₹{e.amount:,.2f} from {cname} ({e.payment_mode}, {e.status.replace('_', ' ')})", \
        "It never reached the balance, so no balance changes."


def remove(kind: str, entry_id: int):
    """Delete one entry and undo what it did. Returns (ok, message)."""
    from pumpvision.models import Customer, Invoice, db

    e = _get(kind, entry_id)
    if e is None:
        return False, "That entry was already removed."

    if kind == "lube" and e.payment_mode == "credit" and e.customer_id:
        c = db.session.get(Customer, e.customer_id)
        if c:
            c.add_to_balance(-e.amount)

    elif kind == "credit":
        covered = Invoice.query.filter(
            Invoice.customer_id == e.customer_id,
            Invoice.period_from <= e.transaction_date,
            Invoice.period_to >= e.transaction_date,
        ).first()
        if covered:
            return False, (f"Cannot remove: invoice {covered.invoice_number} already covers "
                           f"{e.transaction_date:%d %b %Y}.")
        c = db.session.get(Customer, e.customer_id)
        if c:
            c.add_to_balance(-e.amount)

    elif kind == "payment":
        invoice = e.invoice
        if e.status == "confirmed":
            c = db.session.get(Customer, e.customer_id)
            if c:
                c.add_to_balance(e.amount)
        db.session.delete(e)
        db.session.flush()
        if invoice:
            paid = sum(p.amount for p in invoice.payments if p.status == "confirmed")
            invoice.paid_amount = round(paid, 2)
            if paid + 0.005 < invoice.total_amount:
                invoice.is_paid = False
                invoice.paid_at = None
        db.session.commit()
        return True, "Payment removed and the balance restored."

    db.session.delete(e)
    db.session.commit()
    return True, "Entry removed. All totals now reflect it."
