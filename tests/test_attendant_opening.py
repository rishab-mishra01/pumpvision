import os
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.blueprints.attendant.routes import _opening_reading  # noqa: E402
from pumpvision.blueprints.dashboard.routes import _product_sales  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import ManualTotalizerReading, NozzleTotalizer, User  # noqa: E402

D = date(2026, 10, 9)
OPERATIONAL = "pumpvision.services.operational.shift_cycle_date"


def _close(day, minutes=10):
    return datetime.combine(day + timedelta(days=1), datetime.min.time()) + timedelta(hours=6, minutes=minutes)


class AttendantOpeningMatchesDashboard(unittest.TestCase):
    """Real Oct 9 2026 HS1 figures: IRAS 06:00 opening 3,306,546.28; staff's Oct 8 close
    3,306,563.28 (17 L later); staff's Oct 9 close 3,307,293.49."""

    @classmethod
    def setUpClass(cls):
        cls.app = create_app()
        cls.app.config.update(TESTING=True, SECRET_KEY="t")
        with cls.app.app_context():
            db.create_all()

    @classmethod
    def tearDownClass(cls):
        with cls.app.app_context():
            db.session.remove()
            db.drop_all()
        os.unlink(_DB_FILE.name)

    def setUp(self):
        self.ctx = self.app.app_context(); self.ctx.push()

    def tearDown(self):
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit(); self.ctx.pop()

    def _iras(self, day, n, v):
        db.session.add(NozzleTotalizer(operational_date=day, nozzle_no=n, totalizer_end=v))

    def _manual(self, day, n, label, v, recorded_at):
        db.session.add(ManualTotalizerReading(operational_date=day, nozzle_label=label, nozzle_no=n, product="HSD",
                                              totalizer_value=v, recorded_at=recorded_at))

    def test_previous_shift_close_wins_over_iras(self):
        self._iras(D, 7, 3306546.28); self._manual(D - timedelta(days=1), 7, "HSD 1", 3306563.28, _close(D - timedelta(days=1)))
        db.session.commit()
        self.assertEqual(_opening_reading(7, D), 3306563.28)

    def test_late_previous_close_is_not_trusted_iras_used(self):
        self._iras(D, 7, 3306546.28)
        self._manual(D - timedelta(days=1), 7, "HSD 1", 3306999.00, _close(D - timedelta(days=1)) + timedelta(hours=20))
        db.session.commit()
        self.assertEqual(_opening_reading(7, D), 3306546.28)

    def test_no_previous_close_uses_iras(self):
        self._iras(D, 7, 3306546.28); db.session.commit()
        self.assertEqual(_opening_reading(7, D), 3306546.28)

    def test_no_iras_and_no_previous_close_uses_older_staff_reading(self):
        self._manual(D - timedelta(days=3), 7, "HSD 1", 3300000.00, _close(D - timedelta(days=3)))
        db.session.commit()
        self.assertEqual(_opening_reading(7, D), 3300000.00)

    def test_attendant_summary_total_equals_dashboard(self):
        db.session.add(User(username="att", password_hash=generate_password_hash("x"), role="attendant",
                            first_name="Att", is_active=True))
        for n, label, iras_open, prev_close, close in ((7, "HSD 1", 3306546.28, 3306563.28, 3307293.49),
                                                      (16, "HSD 2", 589250.98, 589250.98, 590483.28)):
            self._iras(D, n, iras_open); self._iras(D + timedelta(days=1), n, close)
            self._manual(D - timedelta(days=1), n, label, prev_close, _close(D - timedelta(days=1)))
            self._manual(D, n, label, close, _close(D, 7))
        db.session.commit()
        dash = _product_sales(D)["HS"]["litres"]
        self.assertAlmostEqual(dash, 1962.51, places=2)
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(User.query.one().id); s["_fresh"] = True
        with mock.patch(OPERATIONAL, return_value=D):
            html = c.get("/attendant/shift/summary").get_data(as_text=True)
        self.assertIn("1,962.51", html)          # same number as the dashboard
        self.assertNotIn("1,979.51", html)       # the old IRAS-opening figure


if __name__ == "__main__":
    unittest.main()
