import os
import tempfile
import unittest
from datetime import date

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"

from pumpvision import create_app  # noqa: E402
from pumpvision.blueprints.dashboard.routes import _product_sales  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import AppSetting, FuelTest, NozzleTotalizer, User  # noqa: E402
from pumpvision.services import meter_check  # noqa: E402
from pumpvision.blueprints.attendant.routes import _shift_op_date  # noqa: E402
from pumpvision.services.pump_tests import record_test, tested_by_nozzle  # noqa: E402

DAY = date(2026, 10, 9)


class PumpTestOnlyWhenRecorded(unittest.TestCase):
    """A pump test is not daily: only a recorded test reduces sales, per nozzle."""

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
        for n in (7, 16):          # both HS nozzles sold 100 L on DAY
            db.session.add(NozzleTotalizer(operational_date=DAY, nozzle_no=n, totalizer_end=1000.0))
            db.session.add(NozzleTotalizer(operational_date=date(2026, 10, 10), nozzle_no=n, totalizer_end=1100.0))
        for role in ("owner", "manager", "attendant"):
            db.session.add(User(username=role, password_hash=generate_password_hash("x"),
                                role=role, first_name=role, is_active=True))
        db.session.commit()
        self.uid = {u.username: u.id for u in User.query.all()}

    def tearDown(self):
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit(); self.ctx.pop()

    def _client(self, role):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(self.uid[role]); s["_fresh"] = True
        return c

    # ---- calculations -------------------------------------------------
    def test_no_entry_means_no_deduction_even_if_old_setting_exists(self):
        db.session.add(AppSetting(key="pump_test_nozzle_7", value="5.0"))
        db.session.add(AppSetting(key="pump_test_nozzle_16", value="5.0"))
        db.session.commit()
        hs = _product_sales(DAY)["HS"]
        self.assertEqual((hs["litres"], hs["dispensed"], hs["tested"]), (200.0, 200.0, 0.0))

    def test_recorded_test_reduces_sales_but_not_dispensed(self):
        record_test(DAY, 7, "5", True, "", self.uid["attendant"])
        hs = _product_sales(DAY)["HS"]
        self.assertEqual((hs["litres"], hs["dispensed"], hs["tested"]), (195.0, 200.0, 5.0))
        self.assertEqual(hs["revenue"], round(195.0 * hs["rsp"], 2))      # money uses sold litres only

    def test_not_returned_test_is_still_not_a_sale(self):
        record_test(DAY, 16, "3.5", False, "lab sample", self.uid["manager"])
        self.assertEqual(_product_sales(DAY)["HS"]["litres"], 196.5)

    # ---- service ------------------------------------------------------
    def test_record_test_validates_and_derives_product(self):
        t, err = record_test(DAY, 15, "5", True, " x ", self.uid["manager"])
        self.assertIsNone(err); self.assertEqual((t.product, t.nozzle_no, t.returned_to_tank, t.note), ("MS", 15, True, "x"))
        for nozzle, litres, code in ((None, "5", "bad_nozzle"), (99, "5", "bad_nozzle"), (7, "", "bad_litres"),
                                     (7, "0", "bad_litres"), (7, "101", "bad_litres"), (7, "nan", "bad_litres")):
            self.assertEqual(record_test(DAY, nozzle, litres, True, "", 1)[1], code)
        self.assertEqual(FuelTest.query.count(), 1)
        self.assertEqual(tested_by_nozzle(DAY), {15: 5.0})

    # ---- attendant ----------------------------------------------------
    def test_attendant_entry_saves_nozzle_and_returned_flag(self):
        c = self._client("attendant")
        c.post("/attendant/shift/testing", data={"nozzle_no": "7", "litres": "5", "returned": "1"})
        c.post("/attendant/shift/testing", data={"nozzle_no": "16", "litres": "2"})      # box unticked
        rows = FuelTest.query.order_by(FuelTest.id).all()
        self.assertEqual([(r.nozzle_no, r.litres, r.returned_to_tank, r.op_date) for r in rows],
                         [(7, 5.0, True, _shift_op_date()), (16, 2.0, False, _shift_op_date())])
        c.post("/attendant/shift/testing", data={"nozzle_no": "", "litres": "5"})         # rejected
        self.assertEqual(FuelTest.query.count(), 2)

    # ---- manager ------------------------------------------------------
    def test_manager_can_add_view_and_remove(self):
        c = self._client("manager")
        self.assertEqual(c.get("/manager/pump-test/").status_code, 200)
        c.post("/manager/pump-test/", data={"op_date": DAY.isoformat(), "nozzle_no": "18", "litres": "5", "returned": "1"})
        t = FuelTest.query.one()
        self.assertEqual((t.op_date, t.nozzle_no, t.product), (DAY, 18, "MS"))
        self.assertIn("MS 1 (Nozzle 18)", c.get(f"/manager/pump-test/?date={DAY.isoformat()}").get_data(as_text=True))
        c.post(f"/manager/pump-test/{t.id}/delete")
        self.assertEqual(FuelTest.query.count(), 0)

    def test_attendant_cannot_open_the_manager_page(self):
        # own test: the setUp app context outlives requests, so a second login in one test would leak
        code = self._client("attendant").get("/manager/pump-test/").status_code
        self.assertIn(code, (302, 403), f"attendant got {code} on the manager page")

    # ---- owner settings -----------------------------------------------
    def test_owner_settings_save_works_without_pump_test_fields(self):
        c = self._client("owner")
        self.assertEqual(c.get("/credit/settings").status_code, 200)
        self.assertEqual(c.post("/credit/settings", data={"alert_threshold": "75"}).status_code, 302)
        self.assertEqual(db.session.get(AppSetting, "alert_threshold").value, "75.0")

    # ---- meter check --------------------------------------------------
    def test_meter_check_runs_without_any_recorded_test(self):
        self.assertIsInstance(meter_check.check_day(DAY), list)


if __name__ == "__main__":
    unittest.main()
