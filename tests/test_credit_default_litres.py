import os
import re
import tempfile
import unittest

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import AppSetting, CreditTransaction, Customer, User  # noqa: E402


class AttendantCreditDefaultsToLitres(unittest.TestCase):
    """Typing '5' on the attendant credit screen must mean 5 litres, not Rs 5
    (Oct 2026: ~46 test sales were stored as Rs amounts when litres were meant)."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True, SECRET_KEY="test-secret")
        with cls.app.app_context():
            db.create_all()
            db.session.add(User(username="att", password_hash=generate_password_hash("x"),
                                role="attendant", first_name="Att", is_active=True))
            db.session.add(User(username="mgr", password_hash=generate_password_hash("x"),
                                role="manager", first_name="Mgr", is_active=True))
            db.session.add(AppSetting(key="cng_rsp_per_kg", value="100"))
            db.session.add(Customer(company_name="T", fleet_manager_name="T", whatsapp_number="1",
                                    credit_limit=1000, payment_terms_days=7, is_active=True))
            db.session.commit()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(_DB_FILE.name)

    def test_log_sale_form_defaults_to_litres(self):
        client = self.app.test_client()
        with self.app.app_context():
            uid = User.query.filter_by(username="att").one().id
            cid = Customer.query.one().customer_id
        with client.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        resp = client.get(f"/attendant/credit/log/{cid}")
        self.assertEqual(resp.status_code, 200, resp.status_code)
        html = resp.get_data(as_text=True)
        self.assertRegex(html, r'id="input-mode-hidden" value="litres"')
        self.assertIn("let inputMode = 'litres';", html)
        self.assertRegex(html, r'id="btn-litres"[^>]*\n?\s*class="mode-btn active"')
        self.assertNotRegex(html, r'id="btn-amount"[^>]*\n?\s*class="mode-btn active"')

    def _manager_client(self):
        client = self.app.test_client()
        with self.app.app_context():
            uid = User.query.filter_by(username="mgr").one().id
        with client.session_transaction() as sess:
            sess["_user_id"] = str(uid)
            sess["_fresh"] = True
        return client

    def _cid(self):
        with self.app.app_context():
            return Customer.query.one().customer_id

    def test_manager_form_defaults_to_litres(self):
        resp = self._manager_client().get(f"/manager/credit/{self._cid()}")
        self.assertEqual(resp.status_code, 200)
        html = resp.get_data(as_text=True)
        self.assertRegex(html, r'value="litres"\s+checked')
        self.assertNotRegex(html, r'value="amount"\s+checked')

    def _post(self, **form):
        client = self._manager_client()
        client.post(f"/manager/credit/{self._cid()}",
                    data={"vehicle_number": "UNREGISTERED", "product": "CNG", "quantity": "5", **form})
        with self.app.app_context():
            t = CreditTransaction.query.order_by(CreditTransaction.transaction_id.desc()).first()
            return t.litres, t.amount

    def test_manager_post_without_mode_records_litres(self):
        self.assertEqual(self._post(), (5.0, 500.0))          # 5 kg at Rs100, not Rs5

    def test_manager_post_explicit_amount_still_honoured(self):
        self.assertEqual(self._post(input_mode="amount", quantity="500"), (5.0, 500.0))


if __name__ == "__main__":
    unittest.main()
