import os
import tempfile
import unittest
from datetime import date, datetime

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import AppNotification, IrasPrice, ManualTotalizerReading, User  # noqa: E402
from pumpvision.services.alerts import alert_groups, clear_resolved  # noqa: E402

NOZZLES = {"HSD 1": 7, "HSD 2": 16, "MS 1": 18, "MS 2": 15, "XP": 17, "XG": 11}


class CreditAlertsGroupedAndSelfClearing(unittest.TestCase):
    """Shaped like 10 Oct 2026: shift alerts 6-9 Oct, price alerts 7-10 Oct (7-9 prices since arrived)."""

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
        for d in (6, 7, 8, 9):
            self._alert("shift_alert", date(2026, 10, d), f"The {d:02d} Oct shift has not been closed by the attendants.")
        for d in (7, 8, 9, 10):
            self._alert("price_alert", date(2026, 10, d), f"Today's IRAS price ({d:02d} Oct) is not in yet.")
        self._alert("lube_alert", date(2026, 10, 7), "Lube sold below cost: Grease MP3 1kg.")
        self._alert("review_alert", date(2026, 10, 7), "No mock drill has been recorded.")
        for d in (7, 8, 9):
            for p in ("HS", "MS", "X2", "XG"):
                db.session.add(IrasPrice(product=p, rate_per_litre=100.0, effective_from=datetime(2026, 10, d, 6, 0)))
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit(); self.ctx.pop()

    def _alert(self, kind, ref, msg):
        db.session.add(AppNotification(notification_type=kind, reference_date=ref, message=msg))

    def _messages(self):
        return [g["message"] for g in alert_groups()]

    def test_ten_alerts_become_four_lines(self):
        msgs = self._messages()
        self.assertEqual(len(msgs), 4, msgs)
        self.assertIn("Shifts not closed by the attendants: 06 Oct, 07 Oct, 08 Oct, 09 Oct. "
                      "Their sales come from IRAS machine data.", msgs)
        self.assertIn("IRAS price not in yet for: 10 Oct. Attendant credit sales used the previous day's rate.", msgs)
        self.assertIn("Lube sold below cost: Grease MP3 1kg.", msgs)

    def test_closing_a_shift_clears_its_alert(self):
        for label, n in NOZZLES.items():
            db.session.add(ManualTotalizerReading(operational_date=date(2026, 10, 9), nozzle_label=label, nozzle_no=n,
                                                  product="HSD", totalizer_value=1.0, recorded_at=datetime(2026, 10, 10, 6, 5),
                                                  is_locked=True))
        db.session.commit()
        self.assertIn("Shifts not closed by the attendants: 06 Oct, 07 Oct, 08 Oct. "
                      "Their sales come from IRAS machine data.", self._messages())

    def test_price_partly_in_is_not_resolved(self):
        db.session.add(IrasPrice(product="HS", rate_per_litre=100.0, effective_from=datetime(2026, 10, 10, 6, 0)))
        db.session.commit()
        self.assertTrue(any("10 Oct" in m and "price" in m for m in self._messages()))

    def test_clear_resolved_marks_them_read(self):
        self.assertEqual(clear_resolved(), 3)          # prices for 7, 8, 9 Oct arrived
        db.session.commit()
        self.assertEqual(AppNotification.query.filter_by(notification_type="price_alert", is_read=False).count(), 1)

    def test_dismissing_a_group_dismisses_all_its_days(self):
        db.session.add(User(username="own", password_hash=generate_password_hash("x"), role="owner",
                            first_name="O", is_active=True))
        db.session.commit()
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(User.query.one().id); s["_fresh"] = True
        html = c.get("/credit/home").get_data(as_text=True)
        self.assertIn("4 alerts to check", html)
        ids = [g["ids"] for g in alert_groups() if g["type"] == "shift_alert"][0]
        self.assertEqual(len(ids), 4)
        c.post("/credit/alerts/dismiss", data={"ids": [str(i) for i in ids]})
        self.assertEqual(AppNotification.query.filter_by(notification_type="shift_alert", is_read=False).count(), 0)
        self.assertEqual(len(alert_groups()), 3)


if __name__ == "__main__":
    unittest.main()
