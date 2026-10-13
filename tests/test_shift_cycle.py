"""Closing-to-closing shift cycle: the 00:00-00:30 IST close window and everything keyed to it.

A close at 00:15 on date E finalizes cycle E-1 and is the opening of cycle E. Cycles before
CYCLE_CUTOVER (2026-10-11) keep the old 06:00-to-06:00 behaviour.
"""
import os
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest import mock

from werkzeug.security import generate_password_hash

_DB_FILE = tempfile.NamedTemporaryFile(prefix="pumpvision-test-", suffix=".db", delete=False)
_DB_FILE.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_FILE.name}"
os.environ["PUMPVISION_SKIP_BOOTSTRAP"] = "1"
os.environ.pop("PUMPVISION_CYCLE_CUTOVER", None)

from pumpvision import create_app  # noqa: E402
from pumpvision.blueprints.attendant.routes import _cng_opening_reading, _shift_closed  # noqa: E402
from pumpvision.blueprints.dashboard.routes import _credit_total, _paytm_cycle, _product_sales  # noqa: E402
from pumpvision.extensions import db  # noqa: E402
from pumpvision.models import (  # noqa: E402
    AppNotification, CngShiftReading, CreditTransaction, IrasMidnightTotalizer,
    ManualTotalizerReading, NozzleTotalizer,
    PaytmTransaction, User,
)
from pumpvision.services import operational as op  # noqa: E402
from pumpvision.services.entries import sale_op_date  # noqa: E402
from pumpvision.services.totalizers import opening_reading, valid_manual_closes  # noqa: E402

NOW = "pumpvision.services.operational.now_ist"
CUT = date(2026, 10, 11)                    # first cycle closed in the window; label of the cutover cycle
NOZZLES = {"HS1": ("HSD 1", 7), "HS2": ("HSD 2", 16), "MS1": ("MS 1", 18),
           "MS2": ("MS 2", 15), "X2": ("XP", 17), "XG": ("XG", 11)}


def at(day, hh, mm, ss=0):
    return datetime.combine(day, time(hh, mm, ss))


class WindowAndDates(unittest.TestCase):
    """Pure date logic, no database."""

    E = date(2026, 10, 12)

    def test_window_edges(self):
        for hh, mm, ss, expect in ((0, 0, 0, True), (0, 15, 0, True), (0, 30, 0, True), (0, 30, 59, True),
                                   (0, 31, 0, False), (6, 0, 0, False), (12, 0, 0, False), (23, 59, 59, False)):
            with self.subTest(t=f"{hh:02}:{mm:02}:{ss:02}"):
                self.assertEqual(op.is_close_window(at(self.E, hh, mm, ss)), expect)

    def test_close_at_0015_finalizes_the_previous_date(self):
        self.assertEqual(op.close_window_cycle(at(self.E, 0, 15)), date(2026, 10, 11))
        self.assertEqual(op.close_window_cycle(at(self.E, 0, 0)), date(2026, 10, 11))
        self.assertEqual(op.close_window_cycle(at(self.E, 0, 30)), date(2026, 10, 11))
        self.assertIsNone(op.close_window_cycle(at(self.E, 0, 31)))

    def test_dates_around_midnight(self):
        # month and year turn over inside the window
        self.assertEqual(op.close_window_cycle(at(date(2026, 11, 1), 0, 15)), date(2026, 10, 31))
        self.assertEqual(op.close_window_cycle(at(date(2027, 1, 1), 0, 0)), date(2026, 12, 31))
        # 23:59 the evening before is outside the window; the close is still a day away
        self.assertIsNone(op.close_window_cycle(at(date(2026, 10, 11), 23, 59)))
        self.assertEqual(op.shift_cycle_date(at(date(2026, 10, 11), 23, 59)), date(2026, 10, 10))
        self.assertEqual(op.shift_cycle_date(at(self.E, 0, 0)), date(2026, 10, 11))

    def test_timezone_aware_input_is_read_in_ist(self):
        utc = timezone.utc
        # 18:45 UTC on 11 Oct is 00:15 IST on 12 Oct
        self.assertEqual(op.close_window_cycle(datetime(2026, 10, 11, 18, 45, tzinfo=utc)), date(2026, 10, 11))
        self.assertEqual(op.close_window_cycle(datetime(2026, 10, 11, 18, 30, tzinfo=utc)), date(2026, 10, 11))
        self.assertIsNone(op.close_window_cycle(datetime(2026, 10, 11, 19, 1, tzinfo=utc)))     # 00:31 IST
        # 00:15 UTC is 05:45 IST: outside, and not mistaken for the window
        self.assertIsNone(op.close_window_cycle(datetime(2026, 10, 12, 0, 15, tzinfo=utc)))

    def test_running_cycle_rolls_over_after_the_window(self):
        self.assertEqual(op.get_operational_date(at(self.E, 0, 10)), date(2026, 10, 11))
        self.assertEqual(op.get_operational_date(at(self.E, 0, 30, 59)), date(2026, 10, 11))
        self.assertEqual(op.get_operational_date(at(self.E, 0, 31)), self.E)
        self.assertEqual(op.get_operational_date(at(self.E, 5, 59)), self.E)    # no longer 06:00

    def test_history_keeps_the_06_00_rule(self):
        d = date(2026, 10, 9)
        self.assertEqual(op.get_operational_date(at(d, 5, 59)), date(2026, 10, 8))
        self.assertEqual(op.get_operational_date(at(d, 6, 0)), d)
        self.assertEqual(op.cycle_bounds(date(2026, 10, 9)), (at(d, 6, 0), at(date(2026, 10, 10), 6, 0)))

    def test_cycle_bounds_are_closing_to_closing_from_the_cutover(self):
        # the cutover cycle opens at the old 06:00 close and ends at the new close
        self.assertEqual(op.cycle_bounds(CUT), (at(CUT, 6, 0), at(self.E, 0, 31)))
        # every later cycle opens where the previous one ended
        nxt = op.cycle_bounds(self.E)
        self.assertEqual(nxt[0], op.cycle_bounds(CUT)[1])
        self.assertEqual(nxt[1], at(date(2026, 10, 13), 0, 31))

    def test_cycle_of_timestamp(self):
        self.assertEqual(op.cycle_of(at(self.E, 0, 30)), CUT)
        self.assertEqual(op.cycle_of(at(self.E, 0, 31)), self.E)
        self.assertEqual(op.cycle_of(at(CUT, 5, 59)), date(2026, 10, 10))     # still the old 06:00 cycle
        self.assertEqual(op.cycle_of(at(CUT, 6, 0)), CUT)
        self.assertEqual(sale_op_date(self.E, time(0, 20)), CUT)
        self.assertEqual(sale_op_date(date(2026, 10, 10), time(0, 40)), date(2026, 10, 9))   # legacy

    def test_cutover_can_be_set_from_the_environment(self):
        with mock.patch.dict(os.environ, {op.CUTOVER_ENV: "2026-12-01"}):
            self.assertEqual(op.cycle_cutover(), date(2026, 12, 1))
            self.assertEqual(op.cycle_end(date(2026, 11, 30)), at(date(2026, 12, 1), 6, 0))
        with mock.patch.dict(os.environ, {op.CUTOVER_ENV: "nonsense"}):
            self.assertEqual(op.cycle_cutover(), CUT)


class _DbCase(unittest.TestCase):
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
        try:
            os.unlink(_DB_FILE.name)
        except FileNotFoundError:
            pass

    def setUp(self):
        self.ctx = self.app.app_context(); self.ctx.push()
        db.session.add(User(username="att", password_hash=generate_password_hash("x"), role="attendant",
                            first_name="Att", is_active=True))
        db.session.commit()

    def tearDown(self):
        db.session.rollback()
        for t in reversed(db.metadata.sorted_tables):
            db.session.execute(t.delete())
        db.session.commit(); self.ctx.pop()

    def client(self):
        c = self.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(User.query.one().id); s["_fresh"] = True
        return c


class ClosingRoutes(_DbCase):
    E = date(2026, 10, 12)

    def _close(self, c, when, base=1000.0):
        """Enter all six nozzles and submit, with the clock at `when`. Returns the submit response."""
        with mock.patch(NOW, return_value=when):
            for i, name in enumerate(NOZZLES):
                c.post(f"/attendant/shift/numpad/{name}", data={"closing_reading": str(base + i)})
            return c.post("/attendant/shift/submit")

    def test_close_inside_the_window_finalizes_yesterday(self):
        for hh, mm in ((0, 0), (0, 15), (0, 30)):
            with self.subTest(t=f"{hh:02}:{mm:02}"):
                c = self.client()
                self._close(c, at(self.E, hh, mm))
                self.assertTrue(_shift_closed(CUT))
                self.assertEqual(ManualTotalizerReading.query.filter_by(operational_date=CUT).count(), 6)
                self.assertEqual(ManualTotalizerReading.query.filter_by(operational_date=self.E).count(), 0)
                self.assertEqual(AppNotification.query.filter_by(notification_type="shift_close").count(), 1)
                db.session.query(ManualTotalizerReading).delete(); db.session.query(AppNotification).delete()
                db.session.commit()

    def test_recorded_time_is_the_ist_clock(self):
        self._close(self.client(), at(self.E, 0, 15))
        times = {r.recorded_at for r in ManualTotalizerReading.query.filter_by(operational_date=CUT)}
        self.assertEqual(times, {at(self.E, 0, 15)})

    def test_close_outside_the_window_is_rejected(self):
        for when in (at(self.E, 0, 31), at(self.E, 6, 5), at(self.E, 23, 59)):
            with self.subTest(at=str(when)):
                self._close(self.client(), when)
                self.assertEqual(ManualTotalizerReading.query.count(), 0)     # not even a draft reading
                self.assertEqual(AppNotification.query.count(), 0)
                self.assertFalse(_shift_closed(op.shift_cycle_date(when)))

    def test_submit_after_drafts_is_rejected_when_the_window_has_passed(self):
        c = self.client()
        with mock.patch(NOW, return_value=at(self.E, 0, 29)):
            for i, name in enumerate(NOZZLES):
                c.post(f"/attendant/shift/numpad/{name}", data={"closing_reading": str(1000 + i)})
        with mock.patch(NOW, return_value=at(self.E, 0, 31)):
            c.post("/attendant/shift/submit")
        self.assertEqual(ManualTotalizerReading.query.filter_by(is_locked=True).count(), 0)
        self.assertEqual(AppNotification.query.count(), 0)

    def test_duplicate_close_for_the_same_cycle_is_refused(self):
        c = self.client()
        self._close(c, at(self.E, 0, 10))
        before = {r.nozzle_label: (r.totalizer_value, r.recorded_at) for r in ManualTotalizerReading.query}
        # same cycle again, even with different numbers and still inside the window
        self._close(c, at(self.E, 0, 20), base=5000.0)
        self.assertEqual(AppNotification.query.filter_by(notification_type="shift_close").count(), 1)
        self.assertEqual(ManualTotalizerReading.query.count(), 6)
        after = {r.nozzle_label: (r.totalizer_value, r.recorded_at) for r in ManualTotalizerReading.query}
        self.assertEqual(before, after)

    def test_next_cycle_closes_independently_and_opens_from_the_previous_close(self):
        c = self.client()
        self._close(c, at(self.E, 0, 15), base=1000.0)
        with mock.patch(NOW, return_value=at(self.E + timedelta(days=1), 0, 15)):
            self.assertEqual(op.shift_cycle_date(), self.E)
            self.assertEqual(opening_reading(7, self.E), 1000.0)            # cycle E opens from the close
            # a closing below the opening is refused
            c.post("/attendant/shift/numpad/HS1", data={"closing_reading": "999"})
            self.assertEqual(ManualTotalizerReading.query.filter_by(operational_date=self.E).count(), 0)
            c.post("/attendant/shift/numpad/HS1", data={"closing_reading": "1040"})
        self._close(c, at(self.E + timedelta(days=1), 0, 16), base=1100.0)
        self.assertTrue(_shift_closed(CUT)); self.assertTrue(_shift_closed(self.E))
        self.assertEqual(AppNotification.query.filter_by(notification_type="shift_close").count(), 2)

    def test_cycle_before_the_cutover_is_not_window_gated(self):
        """Historical behaviour: the Oct 10 shift was closed in the morning, any time."""
        c = self.client()
        self._close(c, at(date(2026, 10, 11), 9, 30))
        self.assertTrue(_shift_closed(date(2026, 10, 10)))

    # ---- CNG ----------------------------------------------------------------
    def test_cng_reading_follows_the_same_window_and_chains_to_the_next_cycle(self):
        c = self.client()
        with mock.patch(NOW, return_value=at(self.E, 0, 40)):
            c.post("/attendant/shift/cng/1", data={"closing_reading": "678400.5"})
        self.assertEqual(CngShiftReading.query.count(), 0)
        with mock.patch(NOW, return_value=at(self.E, 0, 15)):
            c.post("/attendant/shift/cng/1", data={"closing_reading": "678400.5"})
            c.post("/attendant/shift/cng/2", data={"closing_reading": "312300.0"})
        rows = {r.nozzle_no: r for r in CngShiftReading.query.filter_by(op_date=CUT)}
        self.assertEqual(set(rows), {1, 2})
        self.assertEqual(rows[1].closing_reading, 678400.5)
        # the next cycle's opening is this close
        self.assertEqual(_cng_opening_reading(self.E, 1), 678400.5)
        self.assertEqual(_cng_opening_reading(self.E, 2), 312300.0)
        # and a later close for it is measured from there
        with mock.patch(NOW, return_value=at(self.E + timedelta(days=1), 0, 15)):
            c.post("/attendant/shift/cng/1", data={"closing_reading": "678500.0"})
        self.assertAlmostEqual(CngShiftReading.query.filter_by(op_date=self.E, nozzle_no=1).one().kg_sold, 99.5)

    def test_cng_cannot_be_changed_after_the_shift_is_closed(self):
        c = self.client()
        with mock.patch(NOW, return_value=at(self.E, 0, 5)):
            c.post("/attendant/shift/cng/1", data={"closing_reading": "100"})
        self._close(c, at(self.E, 0, 10))
        with mock.patch(NOW, return_value=at(self.E, 0, 20)):
            c.post("/attendant/shift/cng/1", data={"closing_reading": "150"})
        self.assertEqual(CngShiftReading.query.filter_by(op_date=CUT, nozzle_no=1).one().closing_reading, 100.0)


class TotalizersAndCash(_DbCase):
    E = date(2026, 10, 12)

    def _m(self, day, n, label, v, recorded_at):
        db.session.add(ManualTotalizerReading(operational_date=day, nozzle_label=label, nozzle_no=n, product="HSD",
                                              totalizer_value=v, recorded_at=recorded_at))

    def test_only_a_close_in_the_window_counts_for_a_new_cycle(self):
        self._m(CUT, 7, "HSD 1", 1100.0, at(self.E, 0, 15))
        self._m(CUT, 16, "HSD 2", 2200.0, at(self.E, 0, 31))       # just too late
        self._m(CUT, 18, "MS 1", 3300.0, at(self.E, 6, 10))         # the old 06:00 habit no longer counts
        self._m(CUT, 15, "MS 2", 4400.0, at(CUT, 23, 50))           # before midnight: not a close
        db.session.commit()
        self.assertEqual(valid_manual_closes(CUT), {7: 1100.0})

    def test_history_is_still_judged_by_the_06_00_boundary(self):
        d = date(2026, 10, 9)
        self._m(d, 7, "HSD 1", 1.0, at(date(2026, 10, 10), 6, 10))
        self._m(d, 16, "HSD 2", 2.0, at(date(2026, 10, 10), 0, 15))       # 5h45 early: never was valid
        self._m(d, 18, "MS 1", 3.0, at(date(2026, 10, 10), 12, 5))        # 6h05 late
        db.session.commit()
        self.assertEqual(valid_manual_closes(d), {7: 1.0})

    def test_opening_prefers_the_previous_window_close_over_iras(self):
        self._m(CUT, 7, "HSD 1", 1100.0, at(self.E, 0, 15))
        db.session.add(NozzleTotalizer(operational_date=self.E, nozzle_no=7, totalizer_end=1112.0))   # IRAS 06:00
        db.session.commit()
        self.assertEqual(opening_reading(7, self.E), 1100.0)

    def test_sales_run_closing_to_closing(self):
        """Cycle CUT: opened with the Oct 10 close (06:10 on Oct 11, old rule), ended 00:15 on Oct 12."""
        self._m(date(2026, 10, 10), 7, "HSD 1", 1010.0, at(CUT, 6, 10))
        self._m(CUT, 7, "HSD 1", 1105.0, at(self.E, 0, 15))
        db.session.add_all([NozzleTotalizer(operational_date=CUT, nozzle_no=7, totalizer_end=1000.0),
                            NozzleTotalizer(operational_date=self.E, nozzle_no=7, totalizer_end=1120.0)])
        db.session.commit()
        self.assertEqual(_product_sales(CUT)["HS"]["litres"], 95.0)      # not IRAS's 120

    def test_sales_fall_back_to_iras_when_the_close_is_outside_the_window(self):
        self._m(date(2026, 10, 10), 7, "HSD 1", 1010.0, at(CUT, 6, 10))
        self._m(CUT, 7, "HSD 1", 1105.0, at(self.E, 0, 45))               # too late to be the close
        db.session.add_all([
            NozzleTotalizer(operational_date=CUT, nozzle_no=7, totalizer_end=1000.0),
            IrasMidnightTotalizer(
                operational_date=CUT, nozzle_no=7, totalizer_open=1000.0,
                totalizer_close=1120.0, effective_at=at(self.E, 0, 0),
                fetched_at=at(self.E, 6, 30),
            ),
        ])
        db.session.commit()
        self.assertEqual(_product_sales(CUT)["HS"]["litres"], 120.0)

    def _credit(self, d, t, amount):
        db.session.add(CreditTransaction(customer_id=1, vehicle_number="V", transaction_date=d, transaction_time=t,
                                         product="HS", litres=1, rate_per_litre=amount, amount=amount,
                                         attendant_name="a"))

    def test_credit_sales_are_filed_by_the_cycle_boundary(self):
        self._credit(CUT, time(5, 59), 1)                    # still the old cycle (Oct 10)
        self._credit(CUT, time(6, 0), 10)                    # cutover cycle opens
        self._credit(self.E, time(0, 30), 100)               # last minute of the cutover cycle
        self._credit(self.E, time(0, 31), 1000)              # next cycle
        self._credit(self.E, time(5, 0), 10000)              # next cycle (used to be the previous day)
        db.session.commit()
        self.assertEqual(_credit_total(date(2026, 10, 10)), 1)
        self.assertEqual(_credit_total(CUT), 110)
        self.assertEqual(_credit_total(self.E), 11000)

    def test_credit_history_unchanged(self):
        d = date(2026, 10, 9)
        self._credit(d, time(5, 59), 1)
        self._credit(d, time(6, 0), 10)
        self._credit(date(2026, 10, 10), time(0, 40), 100)   # after 00:31 but still the old Oct 9 cycle
        self._credit(date(2026, 10, 10), time(5, 59), 1000)
        self._credit(date(2026, 10, 10), time(6, 0), 10000)
        db.session.commit()
        self.assertEqual(_credit_total(d), 1110)
        self.assertEqual(_credit_total(date(2026, 10, 8)), 1)

    def _paytm(self, tid, when, amount, op_date):
        db.session.add(PaytmTransaction(paytm_txn_id=tid, transaction_datetime=when, operational_date=op_date,
                                        amount=amount, payment_category="UPI"))

    def test_paytm_is_totalled_by_transaction_time_so_cash_matches_the_cycle(self):
        # The importer still files these under the 06:00 day (CUT); the cycle is decided by the time.
        self._paytm("a", at(CUT, 9, 0), 1000, CUT)
        self._paytm("b", at(self.E, 0, 20), 200, CUT)
        self._paytm("c", at(self.E, 0, 40), 30, CUT)          # next cycle
        self._paytm("d", at(self.E, 4, 0), 4, CUT)            # next cycle
        db.session.commit()
        self.assertEqual(_paytm_cycle(CUT), (1200.0, 2))
        self.assertEqual(_paytm_cycle(self.E), (34.0, 2))

    def test_paytm_history_unchanged(self):
        d = date(2026, 10, 9)
        self._paytm("h1", at(date(2026, 10, 10), 0, 40), 5, d)        # old rule: still Oct 9
        self._paytm("h2", at(date(2026, 10, 10), 6, 0), 7, date(2026, 10, 10))
        db.session.commit()
        self.assertEqual(_paytm_cycle(d), (5.0, 1))


if __name__ == "__main__":
    unittest.main()
