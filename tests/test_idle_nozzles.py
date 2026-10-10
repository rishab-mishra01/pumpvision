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
from pumpvision.blueprints.attendant.routes import _shift_closed  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import AppNotification, ManualTotalizerReading, NozzleTotalizer, User  # noqa: E402
from pumpvision.services.idle_nozzles import iras_idle_nozzles  # noqa: E402

D = date(2026, 10, 9)                       # shift being closed (morning of Oct 10)
OPERATIONAL = "pumpvision.services.operational.get_operational_date"
LABELS = {"HSD 1": (7, "HSD"), "HSD 2": (16, "HSD"), "MS 1": (18, "MS"), "MS 2": (15, "MS"),
          "XP": (17, "XP"), "XG": (11, "XG")}
MOVED = {"HSD 1", "HSD 2", "MS 1", "MS 2"}  # XP and XG sold nothing


class IdleNozzlesCloseThemselves(unittest.TestCase):
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
        for label, (n, _) in LABELS.items():
            db.session.add(NozzleTotalizer(operational_date=D, nozzle_no=n, totalizer_end=1000.0))
            db.session.add(NozzleTotalizer(operational_date=D + timedelta(days=1), nozzle_no=n,
                                           totalizer_end=1100.0 if label in MOVED else 1000.0))
        db.session.add(User(username="att", password_hash=generate_password_hash("x"), role="attendant",
                            first_name="Att", is_active=True))
        db.session.commit()
        self.patch = mock.patch(OPERATIONAL, return_value=D + timedelta(days=1)); self.patch.start()

    def tearDown(self):
        self.patch.stop()
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit(); self.ctx.pop()

    def _draft(self, labels):
        for label in labels:
            n, prod = LABELS[label]
            db.session.add(ManualTotalizerReading(operational_date=D, nozzle_label=label, nozzle_no=n, product=prod,
                                                  totalizer_value=1100.0, recorded_at=datetime(2026, 10, 10, 6, 10)))
        db.session.commit()

    def _client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(User.query.one().id); s["_fresh"] = True
        return c

    def test_only_unmoved_nozzles_are_idle(self):
        self.assertEqual(iras_idle_nozzles(D), {17: 1000.0, 11: 1000.0})

    def test_no_iras_closing_yet_means_nothing_is_idle(self):
        NozzleTotalizer.query.filter_by(operational_date=D + timedelta(days=1)).delete(); db.session.commit()
        self.assertEqual(iras_idle_nozzles(D), {})

    def test_staff_reading_that_disagrees_blocks_auto_close(self):
        db.session.add(ManualTotalizerReading(operational_date=D - timedelta(days=1), nozzle_label="XG", nozzle_no=11,
                                              product="XG", totalizer_value=990.0, recorded_at=datetime(2026, 10, 9, 6, 5)))
        db.session.commit()
        self.assertEqual(iras_idle_nozzles(D), {17: 1000.0})

    def test_submit_closes_idle_nozzles_from_iras(self):
        self._draft(MOVED)
        r = self._client().post("/attendant/shift/submit")
        self.assertEqual(r.status_code, 302)
        rows = {m.nozzle_label: m for m in ManualTotalizerReading.query.filter_by(operational_date=D)}
        self.assertEqual(set(rows), set(LABELS))
        self.assertTrue(all(m.is_locked for m in rows.values()))
        self.assertEqual((rows["XP"].totalizer_value, rows["XG"].totalizer_value), (1000.0, 1000.0))
        self.assertTrue(_shift_closed(D))
        msg = AppNotification.query.one().message
        self.assertIn("closed automatically", msg); self.assertIn("XP", msg); self.assertIn("XG", msg)

    def test_submit_still_refuses_when_a_moving_nozzle_is_missing(self):
        self._draft({"HSD 1", "HSD 2", "MS 1"})                                  # MS 2 moved, not entered
        self._client().post("/attendant/shift/submit")
        self.assertEqual(ManualTotalizerReading.query.filter_by(operational_date=D).count(), 3)   # nothing added
        self.assertFalse(_shift_closed(D))
        self.assertEqual(AppNotification.query.count(), 0)

    def test_summary_marks_idle_nozzles_and_enables_submit(self):
        self._draft(MOVED)
        html = self._client().get("/attendant/shift/summary").get_data(as_text=True)
        self.assertEqual(html.count("IRAS: कोई बिक्री नहीं"), 2)
        form = html[html.index('action="/attendant/shift/submit"'):]
        button_tag = form[form.index("<button"):form.index(">", form.index("<button"))]
        self.assertNotIn("disabled", button_tag)


if __name__ == "__main__":
    unittest.main()
