"""Manager credit-sale corrections, reversible archive, and strict expense dates.

Archive must take a sale out of the day's credit total and the customer's balance, keep
an audit snapshot, and restore must put everything back exactly; edits must re-price and
move balances; and an expense must land on exactly the date entered or be refused.
"""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta

from flask import g
from flask.testing import FlaskClient
from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import (  # noqa: E402
    AppNotification, AppSetting, CreditTransaction, Customer, EntryAudit, Expense, Invoice,
    IrasPrice, User,
)
from pumpvision.services import entries as svc  # noqa: E402
from pumpvision.services.operational import get_operational_date  # noqa: E402

YDAY = date.today() - timedelta(days=1)


class _FreshUserClient(FlaskClient):
    """setUp keeps one app context pushed, so every request shares flask.g and Flask-Login's
    cached g._login_user would leak from one client to the next. Drop it before each request
    so the user comes from this client's own session cookie."""

    def open(self, *args, **kwargs):
        g.pop("_login_user", None)
        return super().open(*args, **kwargs)


class EntryCorrections(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
        cls.app = create_app()
        cls.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        cls.app.test_client_class = _FreshUserClient

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        if os.path.exists(_DB_FILE.name):
            os.unlink(_DB_FILE.name)

    def setUp(self):
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.drop_all()
        db.create_all()
        for name, role in (("mgr", "manager"), ("own", "owner"), ("att", "attendant")):
            db.session.add(User(username=name, password_hash=generate_password_hash("x"),
                                role=role, first_name=name.title(), is_active=True))
        db.session.add(AppSetting(key="cng_rsp_per_kg", value="90"))
        for p, r in (("HS", 100.0), ("MS", 110.0), ("X2", 120.0), ("XG", 105.0)):
            db.session.add(IrasPrice(product=p, rate_per_litre=r, effective_from=datetime(2026, 1, 1, 6, 0)))
        self.a = Customer(company_name="Alpha", fleet_manager_name="A", whatsapp_number="1",
                          credit_limit=100000, payment_terms_days=7, is_active=True, outstanding_balance=0)
        self.b = Customer(company_name="Beta", fleet_manager_name="B", whatsapp_number="2",
                          credit_limit=100000, payment_terms_days=7, is_active=True, outstanding_balance=0)
        db.session.add_all([self.a, self.b])
        db.session.commit()
        self.mgr = User.query.filter_by(username="mgr").one()
        self.own = User.query.filter_by(username="own").one()

    def tearDown(self):
        db.session.remove()
        self.ctx.pop()

    # ── helpers ────────────────────────────────────────────────────────────
    def _sale(self, customer, product="HS", litres=10.0, rate=100.0, day=YDAY, at=time(10, 0), legacy=False):
        t = CreditTransaction(customer_id=customer.customer_id, vehicle_number="MP17AB1234",
                              transaction_date=day, transaction_time=at, product=product, litres=litres,
                              rate_per_litre=rate, amount=round(litres * rate, 2), attendant_name="Att",
                              is_legacy_entry=legacy)
        db.session.add(t)
        customer.add_to_balance(t.amount)
        db.session.commit()
        return t

    def _client(self, username):
        client = self.app.test_client()
        uid = User.query.filter_by(username=username).one().id
        with client.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        return client

    def _credit_total(self, day):
        from pumpvision.blueprints.dashboard.routes import _credit_total
        return _credit_total(day)

    def _edit(self, t, **over):
        kw = dict(customer_id=t.customer_id, vehicle_number=t.vehicle_number, product=t.product,
                  input_mode="litres", quantity_raw=str(t.litres), date_raw=t.transaction_date.isoformat(),
                  time_raw=t.transaction_time.strftime("%H:%M"), reason="corrected entry")
        kw.update(over)
        return svc.edit_credit(t.transaction_id, self.mgr, **kw)

    # ── archive / restore ──────────────────────────────────────────────────
    def test_archive_and_restore_round_trip(self):
        t = self._sale(self.a)
        tid = t.transaction_id
        self.assertEqual(self._credit_total(YDAY), 1000.0)

        ok, _ = svc.archive("credit", tid, self.mgr, "entered twice")
        self.assertTrue(ok)
        self.assertIsNone(db.session.get(CreditTransaction, tid))
        self.assertEqual(self._credit_total(YDAY), 0.0)
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 0.0)
        audit = EntryAudit.query.filter_by(action="archive").one()
        self.assertEqual((audit.kind, audit.entry_id, audit.op_date, audit.reason), ("credit", tid, YDAY, "entered twice"))
        self.assertEqual(json.loads(audit.before_json)["amount"], 1000.0)
        self.assertEqual(AppNotification.query.filter_by(notification_type="credit_alert").count(), 1)

        ok, _ = svc.restore(audit.id, self.mgr)
        self.assertTrue(ok)
        back = db.session.get(CreditTransaction, tid)
        self.assertIsNotNone(back)
        self.assertEqual((back.transaction_time, back.amount), (time(10, 0), 1000.0))
        self.assertEqual(self._credit_total(YDAY), 1000.0)
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 1000.0)
        self.assertIsNotNone(db.session.get(EntryAudit, audit.id).restored_at)
        ok, msg = svc.restore(audit.id, self.mgr)
        self.assertFalse(ok)

    def test_manager_archive_needs_reason(self):
        t = self._sale(self.a)
        ok, _ = svc.archive("credit", t.transaction_id, self.mgr, "no")
        self.assertFalse(ok)
        self.assertIsNotNone(db.session.get(CreditTransaction, t.transaction_id))

    def test_archive_refused_when_invoiced(self):
        t = self._sale(self.a)
        db.session.add(Invoice(customer_id=self.a.customer_id, invoice_number="INV1", period_from=YDAY,
                               period_to=YDAY, total_amount=1000))
        db.session.commit()
        ok, msg = svc.archive("credit", t.transaction_id, self.mgr, "entered twice")
        self.assertFalse(ok)
        self.assertIn("INV1", msg)
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 1000.0)

    def test_manager_cannot_archive_or_edit_legacy(self):
        t = self._sale(self.a, legacy=True)
        self.assertFalse(svc.archive("credit", t.transaction_id, self.mgr, "entered twice")[0])
        self.assertEqual(self._edit(t, quantity_raw="5")[1], ["legacy"])

    def test_manager_cannot_restore_legacy_archived_by_owner(self):
        t = self._sale(self.a, legacy=True)
        self.assertTrue(svc.archive("credit", t.transaction_id, self.own)[0])
        audit = EntryAudit.query.filter_by(action="archive").one()
        self.assertFalse(svc.restore(audit.id, self.mgr)[0])
        self.assertNotIn(f"/archived/{audit.id}/restore", self._client("mgr").get("/manager/credit/").get_data(as_text=True))
        self.assertTrue(svc.restore(audit.id, self.own)[0])

    # ── edit ───────────────────────────────────────────────────────────────
    def test_edit_quantity_reprices_and_moves_balance(self):
        t = self._sale(self.a)
        saved, errs = self._edit(t, quantity_raw="12.5")
        self.assertEqual(errs, [])
        self.assertEqual((saved.litres, saved.amount), (12.5, 1250.0))
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 1250.0)
        self.assertEqual(self._credit_total(YDAY), 1250.0)
        audit = EntryAudit.query.filter_by(action="edit").one()
        self.assertEqual(json.loads(audit.before_json)["amount"], 1000.0)
        self.assertEqual(json.loads(audit.after_json)["amount"], 1250.0)

    def test_edit_to_another_customer_moves_both_balances(self):
        t = self._sale(self.a)
        saved, errs = self._edit(t, customer_id=str(self.b.customer_id))
        self.assertEqual(errs, [])
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 0.0)
        self.assertEqual(db.session.get(Customer, self.b.customer_id).outstanding_balance, 1000.0)

    def test_edit_to_cng_uses_kg_rate(self):
        t = self._sale(self.a)
        saved, errs = self._edit(t, product="CNG", quantity_raw="20")
        self.assertEqual(errs, [])
        self.assertEqual((saved.product, saved.litres, saved.rate_per_litre, saved.amount), ("CNG", 20.0, 90.0, 1800.0))
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 1800.0)

    def test_edit_by_amount(self):
        t = self._sale(self.a, product="CNG", litres=10, rate=90.0)
        saved, errs = self._edit(t, input_mode="amount", quantity_raw="450")
        self.assertEqual(errs, [])
        self.assertEqual((saved.litres, saved.amount), (5.0, 450.0))

    def test_edit_validation(self):
        t = self._sale(self.a)
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        self.assertIn("future", self._edit(t, date_raw=tomorrow)[1])
        self.assertIn("bad_when", self._edit(t, date_raw="2026-02-30")[1])
        self.assertIn("no_reason", self._edit(t, reason="x")[1])
        self.assertIn("qty_not_positive", self._edit(t, quantity_raw="0")[1])
        self.assertIn("qty_not_number", self._edit(t, quantity_raw="nan")[1])
        self.assertIn("bad_customer", self._edit(t, customer_id="999")[1])
        self.assertIn("no_vehicle", self._edit(t, vehicle_number=" ")[1])
        self.assertEqual(self._edit(t)[1], ["no_change"])
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 1000.0)
        self.assertEqual(EntryAudit.query.count(), 0)

    def test_edit_refused_when_invoiced(self):
        t = self._sale(self.a)
        db.session.add(Invoice(customer_id=self.a.customer_id, invoice_number="INV2", period_from=YDAY,
                               period_to=YDAY, total_amount=1000))
        db.session.commit()
        self.assertEqual(self._edit(t, quantity_raw="11")[1], ["invoiced"])

    # ── manager screens ────────────────────────────────────────────────────
    def test_manager_list_shows_latest_ten_with_cng_kg(self):
        for i in range(12):
            self._sale(self.a, litres=1 + i, at=time(8, i))
        self._sale(self.b, product="CNG", litres=7.5, rate=90.0, at=time(9, 0))
        html = self._client("mgr").get("/manager/credit/").get_data(as_text=True)
        section = html.split('id="latest"', 1)[1]
        self.assertEqual(section.count("/manager/credit/sale/") // 3, 10)    # view + edit + archive per row
        self.assertIn("7.5 kg CNG", section)
        self.assertNotIn(f"/manager/credit/sale/1/edit", section)          # the oldest two drop off

    def test_manager_edit_and_archive_routes(self):
        t = self._sale(self.a)
        c = self._client("mgr")
        tid = t.transaction_id
        self.assertEqual(c.get(f"/manager/credit/sale/{tid}").status_code, 200)
        self.assertEqual(c.get(f"/manager/credit/sale/{tid}/edit").status_code, 200)
        r = c.post(f"/manager/credit/sale/{tid}/edit", data={
            "customer_id": str(self.a.customer_id), "vehicle_number": "MP17AB1234", "product": "MS",
            "input_mode": "litres", "quantity": "10", "date": YDAY.isoformat(), "time": "10:00",
            "reason": "wrong fuel chosen"})
        self.assertEqual(r.status_code, 302)
        db.session.expire_all()
        self.assertEqual(db.session.get(CreditTransaction, tid).amount, 1100.0)

        self.assertEqual(c.get(f"/manager/credit/sale/{tid}/archive").status_code, 200)
        c.post(f"/manager/credit/sale/{tid}/archive", data={"reason": "no"})        # too short: kept
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(CreditTransaction, tid))
        c.post(f"/manager/credit/sale/{tid}/archive", data={"reason": "duplicate entry"})
        db.session.expire_all()
        self.assertIsNone(db.session.get(CreditTransaction, tid))
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 0.0)

        audit = EntryAudit.query.filter_by(action="archive").one()
        self.assertIn("duplicate entry", c.get("/manager/credit/").get_data(as_text=True))
        self.assertEqual(c.get(f"/manager/credit/archived/{audit.id}/restore").status_code, 200)
        c.post(f"/manager/credit/archived/{audit.id}/restore")
        db.session.expire_all()
        self.assertIsNotNone(db.session.get(CreditTransaction, tid))
        self.assertEqual(db.session.get(Customer, self.a.customer_id).outstanding_balance, 1100.0)

    def test_only_manager_reaches_manager_correction_routes(self):
        t = self._sale(self.a)
        for who in ("own", "att"):
            c = self._client(who)
            self.assertEqual(c.get(f"/manager/credit/sale/{t.transaction_id}/edit").status_code, 403)
            self.assertEqual(c.post(f"/manager/credit/sale/{t.transaction_id}/archive",
                                    data={"reason": "duplicate entry"}).status_code, 403)
        self.assertIsNotNone(db.session.get(CreditTransaction, t.transaction_id))

    # ── owner removal goes through the archive ─────────────────────────────
    def test_owner_remove_archives_and_restores(self):
        e = Expense(amount=500, category="Misc", op_date=YDAY, logged_by=self.mgr.id)
        db.session.add(e)
        db.session.commit()
        eid = e.id
        c = self._client("own")
        c.post(f"/owner/entries/expense/{eid}/remove", data={"date": YDAY.isoformat(), "reason": ""})
        db.session.expire_all()
        self.assertIsNone(db.session.get(Expense, eid))
        audit = EntryAudit.query.filter_by(kind="expense", action="archive").one()
        self.assertIn("Restore", c.get(f"/owner/entries/{YDAY.isoformat()}").get_data(as_text=True))
        self.assertEqual(AppNotification.query.count(), 0)          # owner's own action: no alert
        self.assertEqual(self._client("mgr").get(f"/owner/entries/archived/{audit.id}/restore").status_code, 403)
        c.post(f"/owner/entries/archived/{audit.id}/restore", data={"date": YDAY.isoformat()})
        db.session.expire_all()
        self.assertEqual(db.session.get(Expense, eid).amount, 500)

    # ── expense date ───────────────────────────────────────────────────────
    def _post_expense(self, op_date):
        return self._client("mgr").post("/manager/expense/", data={
            "amount": "250", "category": "Misc", "sub_category": "", "description": "", "op_date": op_date})

    def test_expense_date_is_stored_exactly(self):
        day = get_operational_date() - timedelta(days=3)
        self.assertEqual(self._post_expense(day.isoformat()).status_code, 302)
        self.assertEqual(Expense.query.one().op_date, day)

    def test_expense_bad_dates_are_refused_not_moved_to_today(self):
        today_op = get_operational_date()
        for bad in ("", "not-a-date", "2026-02-30", (today_op + timedelta(days=1)).isoformat(),
                    (today_op - timedelta(days=400)).isoformat()):
            r = self._post_expense(bad)
            self.assertEqual(r.status_code, 200, bad)
        self.assertEqual(Expense.query.count(), 0)
        html = self._post_expense("2026-02-30").get_data(as_text=True)
        self.assertIn('value="2026-02-30"', html)                     # what was typed is kept


if __name__ == "__main__":
    unittest.main()
