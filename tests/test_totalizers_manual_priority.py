import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.blueprints.dashboard.routes import _product_sales  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import ManualTotalizerReading, NozzleTotalizer  # noqa: E402


def _scraped(day, n, v):
    db.session.add(NozzleTotalizer(operational_date=day, nozzle_no=n, totalizer_end=v))


def _manual(day, n, label, v):
    db.session.add(ManualTotalizerReading(operational_date=day, nozzle_no=n, nozzle_label=label,
                                          product="HSD", totalizer_value=v,
                                          recorded_at=datetime.combine(day, datetime.min.time()) + timedelta(days=1, hours=6, minutes=10)))


class ManualTotalizersWinOverStaleScrape(unittest.TestCase):
    """Oct 9 2026: scraped nozzle 16 was stale for 3 days then jumped +1,807 L, so the app
    showed HS 2,554 L; the staff's manual readings gave 1,962.5 L."""

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
        # scraper rows are labelled op_day+1; nozzle 16 stuck at 588676.10 until the jump
        _scraped(date(2026, 10, 9), 7, 3306546.28); _scraped(date(2026, 10, 10), 7, 3307293.49)
        _scraped(date(2026, 10, 9), 16, 588676.10); _scraped(date(2026, 10, 10), 16, 590483.28)
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit(); self.ctx.pop()

    def test_scraped_only_overstates_hs(self):
        hs = _product_sales(date(2026, 10, 9))["HS"]["litres"]
        self.assertAlmostEqual(hs, 747.21 + 1807.18, places=1)          # the bug: ~2,554

    def test_manual_readings_give_staff_figure(self):
        _manual(date(2026, 10, 8), 7, "HSD 1", 3306563.28); _manual(date(2026, 10, 9), 7, "HSD 1", 3307293.49)
        _manual(date(2026, 10, 8), 16, "HSD 2", 589250.98); _manual(date(2026, 10, 9), 16, "HSD 2", 590483.28)
        db.session.commit()
        hs = _product_sales(date(2026, 10, 9))["HS"]["litres"]
        self.assertAlmostEqual(hs, 730.21 + 1232.30, places=1)           # 1,962.5 = staff's 1,963

    def test_nozzle_with_only_one_manual_end_falls_back_to_scraped(self):
        _manual(date(2026, 10, 9), 16, "HSD 2", 590483.28)               # no opening manual for 16
        db.session.commit()
        hs = _product_sales(date(2026, 10, 9))["HS"]["litres"]
        self.assertAlmostEqual(hs, 747.21 + 1807.18, places=1)


if __name__ == "__main__":
    unittest.main()
