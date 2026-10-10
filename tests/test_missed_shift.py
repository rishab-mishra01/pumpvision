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
from pumpvision.blueprints.attendant.routes import _missed_shift_days, _shift_op_date  # noqa: E402
from pumpvision.blueprints.dashboard.routes import _product_sales  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import ManualTotalizerReading, NozzleTotalizer, User  # noqa: E402

NOZZLES = {"HSD 1": 7, "HSD 2": 16, "MS 1": 18, "MS 2": 15, "XP": 17, "XG": 11}
OPERATIONAL = "pumpvision.services.operational.get_operational_date"


def _boundary(day):
    return datetime.combine(day + timedelta(days=1), datetime.min.time()) + timedelta(hours=6)


def _close_shift(day, value=1000.0, hours_after=0.5):
    """A fully submitted (locked) shift: six nozzle readings recorded `hours_after` h after 06:00."""
    for label, n in NOZZLES.items():
        db.session.add(ManualTotalizerReading(
            operational_date=day, nozzle_label=label, nozzle_no=n, product="HSD", totalizer_value=value,
            recorded_at=_boundary(day) + timedelta(hours=hours_after), is_locked=True))
    db.session.commit()


class MissedShiftNeverMislabelled(unittest.TestCase):
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

    # ---- which shift can be closed ------------------------------------
    def test_missed_day_is_not_offered_for_closing(self):
        """Oct 8 morning: Oct 7 is the last completed shift; Oct 6 was missed. The old rule offered
        Oct 6 and filed Oct 8's meter values under it."""
        _close_shift(date(2026, 10, 5))
        with mock.patch(OPERATIONAL, return_value=date(2026, 10, 8)):
            self.assertEqual(_shift_op_date(), date(2026, 10, 7))
            self.assertEqual(_missed_shift_days(), [date(2026, 10, 6)])

    def test_missed_days_clear_once_the_latest_shift_is_closed(self):
        _close_shift(date(2026, 10, 5)); _close_shift(date(2026, 10, 7))
        with mock.patch(OPERATIONAL, return_value=date(2026, 10, 8)):
            self.assertEqual(_missed_shift_days(), [])

    def test_several_missed_days_listed_oldest_first(self):
        _close_shift(date(2026, 10, 3))
        with mock.patch(OPERATIONAL, return_value=date(2026, 10, 8)):
            self.assertEqual(_missed_shift_days(), [date(2026, 10, 4), date(2026, 10, 5), date(2026, 10, 6)])

    def test_nothing_known_closed_means_no_notice(self):
        with mock.patch(OPERATIONAL, return_value=date(2026, 10, 8)):
            self.assertEqual(_missed_shift_days(), [])

    def test_attendant_home_shows_the_notice(self):
        db.session.add(User(username="att", password_hash=generate_password_hash("x"), role="attendant",
                            first_name="Att", is_active=True)); db.session.commit()
        _close_shift(date(2026, 10, 5))
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(User.query.one().id); s["_fresh"] = True
        with mock.patch(OPERATIONAL, return_value=date(2026, 10, 8)):
            html = c.get("/attendant/").get_data(as_text=True)
        self.assertIn("06 Oct", html)
        self.assertIn("छूटी या जमा न हुई शिफ्ट", html)

    # ---- calculations ignore a reading that is not from that boundary ----
    def _scraped(self, label_day, n, v):
        db.session.add(NozzleTotalizer(operational_date=label_day, nozzle_no=n, totalizer_end=v))

    def _manual(self, op_day, n, label, v, recorded_at):
        db.session.add(ManualTotalizerReading(operational_date=op_day, nozzle_label=label, nozzle_no=n,
                                              product="HSD", totalizer_value=v, recorded_at=recorded_at))

    def test_reading_recorded_a_day_late_is_ignored(self):
        """Nozzle 7: machine says Oct 6 moved 100 L. A reading filed under op Oct 6 but taken on Oct 8
        (24 h+ after its boundary) would say 1,000 L; it must not be used."""
        for d, v in ((date(2026, 10, 6), 1000.0), (date(2026, 10, 7), 1100.0)):
            self._scraped(d, 7, v)
        self._manual(date(2026, 10, 5), 7, "HSD 1", 1000.0, _boundary(date(2026, 10, 5)) + timedelta(minutes=40))
        self._manual(date(2026, 10, 6), 7, "HSD 1", 2000.0, datetime(2026, 10, 8, 6, 6))      # the Oct 8 mislabel
        db.session.commit()
        self.assertEqual(_product_sales(date(2026, 10, 6))["HS"]["litres"], 100.0)

    def test_reading_close_to_its_boundary_is_used(self):
        self._scraped(date(2026, 10, 6), 7, 1000.0); self._scraped(date(2026, 10, 7), 7, 1100.0)
        for hours in (0.2, 3.5):                       # prompt, and the 3.5 h late case seen in practice
            db.session.query(ManualTotalizerReading).delete()
            self._manual(date(2026, 10, 5), 7, "HSD 1", 1000.0, _boundary(date(2026, 10, 5)) + timedelta(minutes=30))
            self._manual(date(2026, 10, 6), 7, "HSD 1", 1090.0, _boundary(date(2026, 10, 6)) + timedelta(hours=hours))
            db.session.commit()
            self.assertEqual(_product_sales(date(2026, 10, 6))["HS"]["litres"], 90.0, hours)

    def test_reading_seven_hours_late_is_ignored(self):
        self._scraped(date(2026, 10, 6), 7, 1000.0); self._scraped(date(2026, 10, 7), 7, 1100.0)
        self._manual(date(2026, 10, 5), 7, "HSD 1", 1000.0, _boundary(date(2026, 10, 5)) + timedelta(minutes=30))
        self._manual(date(2026, 10, 6), 7, "HSD 1", 1090.0, _boundary(date(2026, 10, 6)) + timedelta(hours=7))
        db.session.commit()
        self.assertEqual(_product_sales(date(2026, 10, 6))["HS"]["litres"], 100.0)


if __name__ == "__main__":
    unittest.main()
