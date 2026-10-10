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
from pumpvision.models import Customer, User  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()
