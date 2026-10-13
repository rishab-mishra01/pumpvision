import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from html.parser import HTMLParser

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-ops-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import (  # noqa: E402
    Expense, IrasMidnightTotalizer, IrasPrice, ManualTotalizerReading,
    NozzleTotalizer, OpsEvent, User,
)
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
                self.assertIn("Operations Control Room", body)
                self.assertIn("Skip to live operations", body)
                self.assertIn("What is happening right now", body)
                self.assertIn("Activity", body)
                self.assertIn("Expenses", body)
                self.assertIn("Credit &amp; collections", body)
                self.assertIn("Shift close", body)
                self.assertIn("Liquid nozzles", body)
                self.assertIn("CNG", body)
                self.assertIn("Current completion", body)
                self.assertIn("Data freshness", body)
                self.assertIn("Source diagnostics", body)
                self.assertIn("Back to PumpVision app", body)
                self.assertIn("Accessibility", body)
                stack_ids = (
                    'id="ops-activity-workspace"',
                    'id="ops-completion-workspace"',
                    'id="ops-prices-workspace"',
                    'id="ops-money-workspace"',
                    'id="ops-records-detail"',
                    'id="ops-alerts-workspace"',
                    'id="ops-freshness-workspace"',
                    'id="ops-sources-detail"',
                )
                positions = [body.index(marker) for marker in stack_ids]
                self.assertEqual(positions, sorted(positions))
                self.assertNotIn('id="ops-activity-workspace" open', body)
                self.assertNotIn('id="ops-completion-workspace" open', body)
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

    def test_midnight_iras_fills_missing_manual_close_and_marks_no_sale(self):
        op_date = date(2026, 10, 13)
        nozzle_values = {7: 1000, 16: 2000, 18: 3000, 15: 4000, 17: 5000, 11: 6000}
        with self.app.app_context():
            for nozzle_no, value in nozzle_values.items():
                db.session.add(IrasMidnightTotalizer(
                    operational_date=op_date - timedelta(days=1),
                    nozzle_no=nozzle_no,
                    totalizer_open=value - 50,
                    totalizer_close=value,
                    effective_at=datetime(2026, 10, 13, 0, 0),
                    fetched_at=datetime(2026, 10, 13, 1, 0),
                    source_file="previous.xlsx",
                ))
                db.session.add(IrasMidnightTotalizer(
                    operational_date=op_date,
                    nozzle_no=nozzle_no,
                    totalizer_open=value,
                    totalizer_close=value if nozzle_no == 7 else value + 100,
                    effective_at=datetime(2026, 10, 14, 0, 0),
                    fetched_at=datetime(2026, 10, 14, 1, 0),
                    source_file="current.xlsx",
                ))
            db.session.commit()

        self.login("owner")
        payload = self.client.get(
            f"/ops/api/v1/snapshot?cycle_date={op_date.isoformat()}"
        ).get_json()
        hs1 = next(row for row in payload["nozzles"] if row["nozzle_no"] == 7)
        self.assertEqual(hs1["status"], "iras_fallback")
        self.assertEqual(hs1["opening"], 1000)
        self.assertEqual(hs1["calculation_close"], 1000)
        self.assertTrue(hs1["no_sale"])
        self.assertIn("Manual close was not done", hs1["note"])
        self.assertIn("No sale detected", hs1["note"])
        self.assertIn("T00:00:00+05:30", hs1["iras_at"])
        self.assertIn("T06:30:00+05:30", hs1["iras_fetched_at"])
        self.assertEqual(payload["shift"]["state"], "iras_fallback")

    def test_valid_manual_close_wins_over_midnight_iras(self):
        op_date = date(2026, 10, 13)
        with self.app.app_context():
            db.session.add(IrasMidnightTotalizer(
                operational_date=op_date,
                nozzle_no=7,
                totalizer_open=1000,
                totalizer_close=1100,
                effective_at=datetime(2026, 10, 14, 0, 0),
                fetched_at=datetime(2026, 10, 14, 1, 0),
            ))
            db.session.add(ManualTotalizerReading(
                operational_date=op_date,
                nozzle_label="HSD 1",
                nozzle_no=7,
                product="HSD",
                totalizer_value=1100.5,
                recorded_at=datetime(2026, 10, 14, 0, 10),
                is_locked=True,
            ))
            db.session.commit()

        self.login("owner")
        payload = self.client.get(
            f"/ops/api/v1/snapshot?cycle_date={op_date.isoformat()}"
        ).get_json()
        hs1 = next(row for row in payload["nozzles"] if row["nozzle_no"] == 7)
        self.assertEqual(hs1["calculation_source"], "manual")
        self.assertEqual(hs1["calculation_close"], 1100.5)
        self.assertEqual(hs1["variance"], 0.5)
        self.assertEqual(hs1["status"], "reconciled")

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
        self.assertIn("Detailed records", body)
        self.assertIn("CNG reconciliation", body)
        self.assertNotIn('id="ops-audit-title"', body)
        self.assertEqual(self.client.get("/ops/api/v1/audit").status_code, 403)


if __name__ == "__main__":
    unittest.main()
