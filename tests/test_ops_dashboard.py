import json
import os
import tempfile
import unittest
from datetime import date, datetime
from html.parser import HTMLParser

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-ops-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import Expense, OpsEvent, User  # noqa: E402
from pumpvision.services.ops import record_event  # noqa: E402


class _OpsHtmlParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.labels = []
        self.form_controls = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "label" and values.get("for"):
            self.labels.append(values["for"])
        if tag in ("input", "select", "textarea") and values.get("id"):
            self.form_controls.append(values["id"])


class OpsDashboardTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True, SECRET_KEY="ops-test-secret")
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(_DB_FILE.name)

    def setUp(self):
        with self.app.app_context():
            for role in ("owner", "manager", "attendant", "developer", "super_admin"):
                db.session.add(User(
                    username=role,
                    password_hash=generate_password_hash("testing-password"),
                    role=role,
                    first_name=role.replace("_", " ").title(),
                    is_active=True,
                ))
            db.session.commit()
        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            for table in reversed(db.metadata.sorted_tables):
                db.session.execute(table.delete())
            db.session.commit()

    def login(self, role):
        with self.app.app_context():
            user_id = User.query.filter_by(role=role).one().id
        with self.client.session_transaction() as session:
            session["_user_id"] = str(user_id)
            session["_fresh"] = True

    def test_allowed_roles_can_open_professional_accessible_shell(self):
        for role in ("owner", "manager", "developer", "super_admin"):
            with self.subTest(role=role):
                self.client = self.app.test_client()
                self.login(role)
                response = self.client.get("/ops/")
                body = response.get_data(as_text=True)
                self.assertEqual(response.status_code, 200)
                self.assertIn("Live Operations", body)
                self.assertIn("Skip to dashboard content", body)
                self.assertIn('role="tablist"', body)
                self.assertIn("Accessibility", body)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                parser = _OpsHtmlParser()
                parser.feed(body)
                self.assertEqual(len(parser.ids), len(set(parser.ids)))
                self.assertTrue(set(parser.labels).issubset(set(parser.ids)))

    def test_attendant_is_denied_and_anonymous_is_redirected(self):
        self.login("attendant")
        self.assertEqual(self.client.get("/ops/").status_code, 403)
        anonymous = self.app.test_client().get("/ops/")
        self.assertEqual(anonymous.status_code, 302)

    def test_snapshot_never_turns_missing_financial_sources_into_zero(self):
        self.login("owner")
        response = self.client.get("/ops/api/v1/snapshot?cycle_date=2026-10-13")
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(payload["financial"]["gross_fuel_sales"])
        self.assertIsNone(payload["financial"]["expected_sales_cash"])
        self.assertIsNone(payload["financial"]["physical_cash"])
        self.assertTrue(payload["financial"]["missing"])
        self.assertIn("private", response.headers["Cache-Control"])
        self.assertIn("no-cache", response.headers["Cache-Control"])

        conditional = self.client.get(
            "/ops/api/v1/snapshot?cycle_date=2026-10-13",
            headers={"If-None-Match": response.headers["ETag"]},
        )
        self.assertEqual(conditional.status_code, 304)

    def test_monotonic_event_feed_exposes_actor_source_and_ist_time(self):
        with self.app.app_context():
            manager = User.query.filter_by(role="manager").one()
            expense = Expense(
                amount=2400,
                category="Utilities",
                description="Electricity",
                op_date=date(2026, 10, 13),
                logged_by=manager.id,
            )
            db.session.add(expense)
            db.session.flush()
            event = record_event(
                "expense", "Expense added · ₹2,400.00",
                detail="Utilities / Electricity", source="manual",
                op_date=date(2026, 10, 13),
                business_at=datetime(2026, 10, 13, 14, 28),
                actor=manager, entity_type="expense", entity_id=expense.id,
            )
            db.session.commit()
            event_id = event.id

        self.login("owner")
        response = self.client.get("/ops/api/v1/events?after_id=0&cycle_date=2026-10-13")
        payload = response.get_json()
        self.assertEqual(payload["last_id"], event_id)
        self.assertEqual(payload["events"][0]["actor"], "Manager")
        self.assertEqual(payload["events"][0]["source"], "manual")
        self.assertIn("+05:30", payload["events"][0]["at"])
        empty = self.client.get(f"/ops/api/v1/events?after_id={event_id}&cycle_date=2026-10-13").get_json()
        self.assertEqual(empty["events"], [])
        self.assertEqual(empty["last_id"], event_id)

    def test_invalid_api_arguments_are_rejected(self):
        self.login("owner")
        self.assertEqual(self.client.get("/ops/api/v1/snapshot?cycle_date=not-a-date").status_code, 400)
        self.assertEqual(self.client.get("/ops/api/v1/events?after_id=not-a-number").status_code, 400)

    def test_manager_gets_operational_views_but_not_audit_history(self):
        self.login("manager")
        body = self.client.get("/ops/").get_data(as_text=True)
        self.assertIn("Reconciliation", body)
        self.assertNotIn('id="tab-audit"', body)
        self.assertEqual(self.client.get("/ops/api/v1/audit").status_code, 403)


if __name__ == "__main__":
    unittest.main()
