import os
import tempfile
import unittest
from datetime import date, datetime

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.blueprints.dashboard.routes import _cash_for_date, _cng_sdms, _product_sales  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import CngShiftReading, IrasPrice, NozzleTotalizer, SdmsSummary, User  # noqa: E402
from pumpvision.services import cng_pricing  # noqa: E402
from pumpvision.services.review import check_cng  # noqa: E402

D = date(2026, 10, 9)


class PricingMaths(unittest.TestCase):
    def test_formula_is_cost_plus_2_47(self):
        cost, price, revenue = cng_pricing.price_from_ledger(1000.0, 76500.0)
        self.assertEqual(cost, 76.50)
        self.assertEqual(price, 78.97)
        self.assertEqual(revenue, 78970.00)

    def test_rounding_is_to_the_paisa(self):
        cost, price, revenue = cng_pricing.price_from_ledger(3.0, 230.0)   # 76.666...
        self.assertEqual(cost, 76.67)
        self.assertEqual(price, 79.14)
        self.assertEqual(revenue, 237.42)

    def test_invalid_cost_returns_none(self):
        for kg, amt in [(0, 100), (-5, 100), (100, 0), (100, -1), (100, None), (None, 100),
                        (100, float("nan")), (100, 100.0),      # 1/kg: implausibly low
                        (100, 999999.0)]:                       # implausibly high
            self.assertEqual(cng_pricing.price_from_ledger(kg, amt), (None, None, None), (kg, amt))


class CngDayPricing(unittest.TestCase):
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
        self.ctx = self.app.app_context()
        self.ctx.push()

    def tearDown(self):
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit()
        self.ctx.pop()

    def _sdms(self, day, kg, amount, fleet=0.0, stored_rsp=93.40):
        db.session.add(SdmsSummary(op_date=day, cng_kg_total=kg, cng_revenue=amount,
                                   cng_rsp_per_kg=stored_rsp, fleet_card_total=fleet))
        db.session.commit()

    def _attendant(self, day, kg, rsp):
        db.session.add(User(id=1, username="a", password_hash="x", role="attendant", first_name="A"))
        db.session.add(CngShiftReading(op_date=day, nozzle_no=1, opening_reading=0, closing_reading=kg,
                                       kg_sold=kg, rsp_per_kg=rsp, revenue=kg * rsp, submitted_by=1))
        db.session.commit()

    def test_sdms_revenue_is_kg_times_cost_plus_margin(self):
        self._sdms(D, 2000.0, 153000.0)                      # cost 76.50
        c = _cng_sdms(D)
        self.assertEqual(c.source, "sdms")
        self.assertEqual(c.cost_per_kg, 76.50)
        self.assertEqual(c.rsp_per_kg, 78.97)
        self.assertEqual(c.margin_per_kg, 2.47)
        self.assertEqual(c.revenue, 157940.00)
        self.assertFalse(c.price_missing)
        self.assertEqual(c.price_basis, "eledger_cost_plus_margin")

    def test_stored_fixed_rsp_is_ignored_when_cost_is_valid(self):
        self._sdms(D, 1000.0, 80000.0, stored_rsp=93.40)     # cost 80.00, not 93.40
        c = _cng_sdms(D)
        self.assertEqual(c.rsp_per_kg, 82.47)
        self.assertEqual(c.revenue, 82470.00)

    def test_each_date_uses_its_own_cost(self):
        self._sdms(D, 1000.0, 76000.0)
        self._sdms(date(2026, 10, 10), 1000.0, 78000.0)
        self.assertEqual(_cng_sdms(D).rsp_per_kg, 78.47)
        self.assertEqual(_cng_sdms(date(2026, 10, 10)).rsp_per_kg, 80.47)
        self.assertIsNone(_cng_sdms(date(2026, 10, 11)))

    def test_missing_cost_gives_pending_not_old_rate(self):
        self._sdms(D, 1000.0, 0.0)
        c = _cng_sdms(D)
        self.assertTrue(c.price_missing)
        self.assertIsNone(c.rsp_per_kg)
        self.assertEqual(c.revenue, 0.0)

    def test_implausible_cost_is_pending(self):
        self._sdms(D, 1000.0, 5.0)
        c = _cng_sdms(D)
        self.assertTrue(c.price_missing)
        self.assertEqual(c.revenue, 0.0)

    def test_empty_sdms_row_falls_back_to_attendant_with_explicit_basis(self):
        self._sdms(D, 0.0, 0.0)
        self._attendant(D, 500.0, 95.0)
        c = _cng_sdms(D)
        self.assertEqual(c.source, "attendant_shift_close")
        self.assertEqual(c.price_basis, "owner_setting_pending_eledger")
        self.assertIsNone(c.cost_per_kg)
        self.assertEqual(c.revenue, 47500.0)

    def test_sdms_wins_over_attendant(self):
        self._sdms(D, 1000.0, 76000.0)
        self._attendant(D, 500.0, 95.0)
        c = _cng_sdms(D)
        self.assertEqual(c.source, "sdms")
        self.assertEqual(c.kg_sold, 1000.0)

    def test_cash_uses_derived_selling_price(self):
        self._sdms(D, 1000.0, 76000.0, fleet=1000.0)         # revenue 78,470
        self.assertEqual(_cash_for_date(D), 78470.0 - 1000.0)

    def test_pending_cost_does_not_inflate_cash(self):
        self._sdms(D, 1000.0, 0.0)
        self.assertEqual(_cash_for_date(D), 0.0)

    def test_reconciliation_flags_invalid_cost(self):
        from datetime import timedelta
        now = datetime(2026, 10, 12, 12, 0)
        d = now.date() - timedelta(days=2)
        self._sdms(d, 1000.0, 0.0)
        keys = [f["key"] for f in check_cng(now)]
        self.assertTrue(any(k.startswith("cng_cost_invalid") for k in keys))

    def test_liquid_fuel_unchanged_by_cng(self):
        db.session.add(IrasPrice(product="HS", rate_per_litre=100.0,
                                 effective_from=datetime(2026, 1, 1)))
        for day, v in ((D, 1000.0), (date(2026, 10, 10), 1500.0)):
            db.session.add(NozzleTotalizer(operational_date=day, nozzle_no=7, totalizer_end=v))
        db.session.commit()
        before = _product_sales(D)["HS"]
        self._sdms(D, 1000.0, 76000.0)
        after = _product_sales(D)["HS"]
        self.assertEqual(before, after)
        self.assertAlmostEqual(after["revenue"], 500.0 * 100.0, places=2)
        self.assertNotIn("CNG", _product_sales(D))


if __name__ == "__main__":
    unittest.main()
