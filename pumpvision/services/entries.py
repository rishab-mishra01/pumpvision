"""Manual entries (expenses, lube sales, credit sales, payments, fuel tests) that the
owner can review, archive and restore, and credit sales the manager can correct.

Archiving takes an entry out of every total without losing it: the row is deleted from
its own table (so no report has to learn to skip it) and kept in `entry_audit` as a
JSON snapshot, from which `restore()` puts it back with the same id. Every edit,
archive and restore leaves an `entry_audit` row saying who, when and why.

Each kind undoes its own side effects here and nowhere else:

  expense / fuel test  nothing is stored on top of them: the dashboard and summary
                       re-add the day from the rows, so removing the row is enough.
  lube credit sale     the customer's balance is reduced by the sale again.
  credit sale          same; refused once an invoice covers that date, because the
                       invoice (and what was billed on it) would then be wrong.
  payment received     a confirmed payment is added back to the balance, and the
                       invoice it was against is re-checked for "paid".

Restoring applies the same effects the other way round.
"""
import json
import math
from datetime import date, datetime, time, timedelta

from sqlalchemy import and_, or_

KINDS = ("expense", "lube", "credit", "payment", "test")
MIN_REASON = 5          # characters; a manager must say why they changed or archived a sale


def _models():
    from pumpvision.models import CreditTransaction, Expense, FuelTest, LubeTransaction, PaymentReceived
    return {"expense": Expense, "lube": LubeTransaction, "credit": CreditTransaction,
            "payment": PaymentReceived, "test": FuelTest}


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
        "archived": [(a, summary(a)) for a in archived_for_day(op_date)],
        "names": names,
        "users": users,
    }


def _get(kind: str, entry_id: int):
    from pumpvision.models import db

    model = _models().get(kind)
    return db.session.get(model, entry_id) if model else None


def sale_op_date(d: date, t: time) -> date:
    """A sale before 06:00 belongs to the previous operational day."""
    return d if t >= time(6, 0) else d - timedelta(days=1)


def entry_op_date(kind: str, e) -> date | None:
    if kind == "credit":
        return sale_op_date(e.transaction_date, e.transaction_time)
    if kind == "payment":
        return e.payment_date
    return getattr(e, "op_date", None)


# ─── snapshots ────────────────────────────────────────────────────────────────

def snapshot(e) -> dict:
    out = {}
    for c in e.__table__.columns:
        v = getattr(e, c.key)
        out[c.key] = v.isoformat() if isinstance(v, (date, datetime, time)) else v
    return out


def _rebuild(model, data: dict, keep_pk: bool):
    import sqlalchemy as sa

    pk = model.__mapper__.primary_key[0].key
    kw = {}
    for c in model.__table__.columns:
        if c.key not in data or (c.key == pk and not keep_pk):
            continue
        v = data[c.key]
        if v is not None:
            if isinstance(c.type, sa.DateTime):
                v = datetime.fromisoformat(v)
            elif isinstance(c.type, sa.Date):
                v = date.fromisoformat(v)
            elif isinstance(c.type, sa.Time):
                v = time.fromisoformat(v)
        kw[c.key] = v
    return model(**kw)


# ─── side effects ─────────────────────────────────────────────────────────────

def _covering_invoice(customer_id, day: date):
    from pumpvision.models import Invoice

    return Invoice.query.filter(
        Invoice.customer_id == customer_id,
        Invoice.period_from <= day,
        Invoice.period_to >= day,
    ).first()


def _apply_balance(kind: str, e, sign: int):
    """sign +1 = the entry counts, -1 = it is taken back out."""
    from pumpvision.models import Customer, db

    if kind in ("credit", "lube"):
        if kind == "lube" and not (e.payment_mode == "credit" and e.customer_id):
            return
        c = db.session.get(Customer, e.customer_id)
        if c:
            c.add_to_balance(sign * e.amount)
    elif kind == "payment" and e.status == "confirmed":
        c = db.session.get(Customer, e.customer_id)
        if c:
            c.add_to_balance(-sign * e.amount)


def _recheck_invoice(invoice):
    from pumpvision.models import db

    if invoice is None:
        return
    db.session.expire(invoice, ["payments"])     # the collection still holds a just-deleted payment
    paid = sum(p.amount for p in invoice.payments if p.status == "confirmed")
    invoice.paid_amount = round(paid, 2)
    if paid + 0.005 < invoice.total_amount:
        invoice.is_paid = False
        invoice.paid_at = None
    elif not invoice.is_paid:
        invoice.is_paid = True
        invoice.paid_at = datetime.utcnow()


def _audit(kind, entry_id, action, actor, reason=None, before=None, after=None, op_date=None):
    from pumpvision.models import EntryAudit, db

    row = EntryAudit(
        kind=kind, entry_id=entry_id, action=action, op_date=op_date,
        reason=(reason or None) and reason[:300],
        before_json=json.dumps(before) if before is not None else None,
        after_json=json.dumps(after) if after is not None else None,
        actor_id=getattr(actor, "id", None), actor_role=getattr(actor, "role", None),
    )
    db.session.add(row)
    return row


def _notify_owner(actor, message: str, day: date | None):
    """A manager's correction to money already counted is something the owner should see."""
    from pumpvision.models import AppNotification, db

    if getattr(actor, "role", None) != "manager":
        return
    who = getattr(actor, "first_name", None) or getattr(actor, "username", "Manager")
    db.session.add(AppNotification(message=f"{message} (by {who})"[:500],
                                   notification_type="credit_alert", reference_date=day))


def _reason_ok(actor, reason) -> bool:
    return getattr(actor, "role", None) != "manager" or len((reason or "").strip()) >= MIN_REASON


# ─── describe / archive / restore ─────────────────────────────────────────────

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
                f"The sale is taken out and ₹{e.amount:,.2f} comes off that customer's balance."
        return e, f"Lube sale ₹{e.amount:,.2f} — {name} (cash)", \
            "Lube revenue and cash in hand for that day go down by this amount."
    c = db.session.get(Customer, e.customer_id)
    cname = c.company_name if c else "?"
    if kind == "credit":
        unit = "kg" if e.product == "CNG" else "L"
        return e, f"Credit sale ₹{e.amount:,.2f} — {cname}, {e.vehicle_number}, {e.litres:g} {unit} {e.product}", \
            f"₹{e.amount:,.2f} comes off {cname}'s balance, and the day's credit total goes down."
    if e.status == "confirmed":
        return e, f"Payment ₹{e.amount:,.2f} from {cname} ({e.payment_mode})", \
            f"₹{e.amount:,.2f} is added back to {cname}'s balance."
    return e, f"Payment ₹{e.amount:,.2f} from {cname} ({e.payment_mode}, {e.status.replace('_', ' ')})", \
        "It never reached the balance, so no balance changes."


def archive(kind: str, entry_id: int, actor=None, reason: str | None = None):
    """Take one entry out of every total, keeping it restorable. Returns (ok, message)."""
    from pumpvision.models import db

    e = _get(kind, entry_id)
    if e is None:
        return False, "That entry was already removed."
    if not _reason_ok(actor, reason):
        return False, f"Give a reason (at least {MIN_REASON} characters)."

    if kind == "credit":
        if getattr(actor, "role", None) == "manager" and e.is_legacy_entry:
            return False, "This sale came from the old ledger; only the owner can change it."
        covered = _covering_invoice(e.customer_id, e.transaction_date)
        if covered:
            return False, (f"Cannot remove: invoice {covered.invoice_number} already covers "
                           f"{e.transaction_date:%d %b %Y}.")

    day = entry_op_date(kind, e)
    before = snapshot(e)
    _, headline, _ = describe(kind, entry_id)
    invoice = e.invoice if kind == "payment" else None

    _apply_balance(kind, e, -1)
    _audit(kind, entry_id, "archive", actor, reason, before=before, op_date=day)
    db.session.delete(e)
    db.session.flush()
    _recheck_invoice(invoice)
    _notify_owner(actor, f"Archived: {headline}. Reason: {(reason or '').strip()}", day)
    db.session.commit()
    if kind == "payment":
        return True, "Payment archived and the balance restored. It can be restored from the archive."
    return True, "Entry archived. All totals now leave it out; it can be restored from the archive."


def restore(audit_id: int, actor=None):
    """Put an archived entry back exactly as it was. Returns (ok, message)."""
    from pumpvision.models import Customer, EntryAudit, Invoice, db

    a = db.session.get(EntryAudit, audit_id)
    if a is None or a.action != "archive" or a.kind not in KINDS:
        return False, "That archived entry was not found."
    if a.restored_at is not None:
        return False, "That entry was already restored."

    model = _models()[a.kind]
    data = json.loads(a.before_json)
    if a.kind == "credit" and data.get("is_legacy_entry") and getattr(actor, "role", None) == "manager":
        return False, "This sale came from the old ledger; only the owner can change it."
    customer_id = data.get("customer_id")
    if a.kind in ("credit", "payment") or (a.kind == "lube" and customer_id):
        if customer_id is None or db.session.get(Customer, customer_id) is None:
            return False, "The customer on that entry no longer exists."
    if a.kind == "credit":
        covered = _covering_invoice(customer_id, date.fromisoformat(data["transaction_date"]))
        if covered:
            return False, (f"Cannot restore: invoice {covered.invoice_number} already covers "
                           f"{data['transaction_date']}.")
    if a.kind == "payment" and data.get("invoice_id") and db.session.get(Invoice, data["invoice_id"]) is None:
        return False, "The invoice that payment was against no longer exists."

    # Postgres never reuses an id, but SQLite can; never overwrite a different entry.
    keep_pk = db.session.get(model, a.entry_id) is None
    e = _rebuild(model, data, keep_pk=keep_pk)
    db.session.add(e)
    db.session.flush()
    new_id = getattr(e, model.__mapper__.primary_key[0].key)

    _apply_balance(a.kind, e, +1)
    if a.kind == "payment":
        _recheck_invoice(e.invoice)
    a.restored_at = datetime.utcnow()
    a.restored_by = getattr(actor, "id", None)
    _audit(a.kind, new_id, "restore", actor, f"restored archive #{a.id}", after=snapshot(e), op_date=a.op_date)
    _notify_owner(actor, f"Restored: {summary(a)}", a.op_date)
    db.session.commit()
    return True, "Entry restored. Totals and balances include it again."


def archived_for_day(op_date: date) -> list:
    from pumpvision.models import EntryAudit

    return (EntryAudit.query.filter_by(action="archive", op_date=op_date)
            .filter(EntryAudit.restored_at.is_(None))
            .order_by(EntryAudit.created_at.desc()).all())


def recent_archived(kind: str, limit: int = 5) -> list:
    from pumpvision.models import EntryAudit

    return (EntryAudit.query.filter_by(action="archive", kind=kind)
            .filter(EntryAudit.restored_at.is_(None))
            .order_by(EntryAudit.created_at.desc(), EntryAudit.id.desc()).limit(limit).all())


def history(kind: str, entry_id: int) -> list:
    from pumpvision.models import EntryAudit

    return (EntryAudit.query.filter_by(kind=kind, entry_id=entry_id)
            .order_by(EntryAudit.created_at, EntryAudit.id).all())


def summary(a) -> str:
    """One line describing an archived (or edited) entry from its snapshot."""
    from pumpvision.models import Customer, db

    d = json.loads(a.before_json or a.after_json or "{}")
    amount = d.get("amount")
    money = f"₹{amount:,.2f}" if isinstance(amount, (int, float)) else ""
    if a.kind == "credit":
        c = db.session.get(Customer, d.get("customer_id"))
        unit = "kg" if d.get("product") == "CNG" else "L"
        return (f"Credit sale {money} — {c.company_name if c else '?'}, {d.get('vehicle_number')}, "
                f"{d.get('litres', 0):g} {unit} {d.get('product')}")
    if a.kind == "payment":
        c = db.session.get(Customer, d.get("customer_id"))
        return f"Payment {money} from {c.company_name if c else '?'} ({d.get('payment_mode')})"
    if a.kind == "expense":
        return f"Expense {money} — {d.get('category')}"
    if a.kind == "lube":
        return f"Lube sale {money} ({d.get('payment_mode')})"
    return f"{d.get('litres', 0):g} L of {d.get('product')} taken for testing"


# ─── credit sales: latest list and edit ───────────────────────────────────────

def latest_credit_sales(limit: int = 10) -> list:
    from pumpvision.models import CreditTransaction

    return (CreditTransaction.query.filter(CreditTransaction.is_legacy_entry.isnot(True))
            .order_by(CreditTransaction.transaction_date.desc(), CreditTransaction.transaction_time.desc(),
                      CreditTransaction.transaction_id.desc())
            .limit(limit).all())


def edit_credit(txn_id: int, actor, *, customer_id, vehicle_number, product, input_mode,
                quantity_raw, date_raw, time_raw, reason, now: datetime | None = None):
    """Correct a credit sale. Returns (transaction, errors); nothing is written on errors.

    Error codes (each screen words them): not_found, legacy, invoiced, bad_customer,
    bad_when, future, no_reason, no_change, plus record_sale's no_vehicle, bad_product,
    no_rate, qty_not_number, qty_not_positive.

    The rate is kept when product and operational day are unchanged (it is the price the
    sale was made at); otherwise it is that day's rate. Both balances are corrected when
    the sale moves to another customer.
    """
    from pumpvision.models import CreditTransaction, Customer, db
    from pumpvision.services.credit_sale import rate_map

    t = db.session.get(CreditTransaction, txn_id)
    if t is None:
        return None, ["not_found"]
    if t.is_legacy_entry and getattr(actor, "role", None) != "owner":
        return None, ["legacy"]

    errors = []
    reason = (reason or "").strip()
    if len(reason) < MIN_REASON:
        errors.append("no_reason")

    try:
        customer = db.session.get(Customer, int(customer_id))
    except (TypeError, ValueError):
        customer = None
    if customer is None or (not customer.is_active and customer.customer_id != t.customer_id):
        errors.append("bad_customer")

    vehicle_number = (vehicle_number or "").strip().upper()[:20]
    if not vehicle_number:
        errors.append("no_vehicle")

    try:
        when = datetime.combine(date.fromisoformat((date_raw or "").strip()),
                                time.fromisoformat((time_raw or "").strip()))
    except ValueError:
        when = None
        errors.append("bad_when")
    if when and when.date() == t.transaction_date and f"{when:%H:%M}" == f"{t.transaction_time:%H:%M}":
        when = datetime.combine(t.transaction_date, t.transaction_time)   # form shows HH:MM; keep the seconds
    if when and when > (now or datetime.now()):
        errors.append("future")

    product = (product or "").strip().upper()
    rate = None
    if when:
        day = sale_op_date(when.date(), when.time())
        old_day = sale_op_date(t.transaction_date, t.transaction_time)
        if product == t.product and day == old_day:
            rate = t.rate_per_litre
        else:
            rates = rate_map(day)
            if product not in rates:
                errors.append("bad_product")
            else:
                rate = rates[product]
                if rate is None:
                    errors.append("no_rate")

    quantity = None
    try:
        quantity = float(quantity_raw)
        if not math.isfinite(quantity):
            raise ValueError
        if quantity <= 0:
            errors.append("qty_not_positive")
    except (TypeError, ValueError):
        errors.append("qty_not_number")

    if not errors:
        for cid, d in ((t.customer_id, t.transaction_date), (customer.customer_id, when.date())):
            if _covering_invoice(cid, d):
                errors.append("invoiced")
                break
    if errors:
        return None, errors

    if input_mode == "amount":
        litres, amount = round(quantity / rate, 3), round(quantity, 2)
    else:
        litres, amount = round(quantity, 3), round(quantity * rate, 2)
        if litres == t.litres and rate == t.rate_per_litre:
            amount = t.amount        # a sale entered in ₹ keeps its exact amount when only other fields change

    new = dict(customer_id=customer.customer_id, vehicle_number=vehicle_number,
               transaction_date=when.date(), transaction_time=when.time(),
               product=product, litres=litres, rate_per_litre=rate, amount=amount)
    if all(getattr(t, k) == v for k, v in new.items()):
        return None, ["no_change"]

    before = snapshot(t)
    old_customer = db.session.get(Customer, t.customer_id)
    if old_customer:
        old_customer.add_to_balance(-t.amount)
    for k, v in new.items():
        setattr(t, k, v)
    customer.add_to_balance(t.amount)
    db.session.flush()
    after = snapshot(t)
    _audit("credit", t.transaction_id, "edit", actor, reason, before=before, after=after,
           op_date=sale_op_date(t.transaction_date, t.transaction_time))
    unit = "kg" if t.product == "CNG" else "L"
    _notify_owner(actor, (f"Credit sale #{t.transaction_id} edited: ₹{before['amount']:,.2f} → ₹{t.amount:,.2f}, "
                          f"now {customer.company_name}, {t.litres:g} {unit} {t.product}, "
                          f"{t.transaction_date:%d %b} {t.transaction_time:%H:%M}. Reason: {reason}"),
                  sale_op_date(t.transaction_date, t.transaction_time))
    db.session.commit()
    return t, []
