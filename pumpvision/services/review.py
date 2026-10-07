"""The 4-hourly review: looks for gaps in data, maths and reconciliation, and for
anything unhealthy in the system, and records what it finds as ReviewFindings.

Each check is a function returning a list of finding dicts (see `F`). A check that
raises is reported as a finding of its own rather than hiding the others. After the
run, findings are synced: new ones are created (business ones also raise an owner
alert), still-present ones are refreshed, and open ones whose check ran clean are
marked resolved. Findings from other sources (the VPS reconcile job) are left alone.

Run it with scripts/run_review.py (cron) or from the developer dashboard.
"""
import json
import os
import shutil
import statistics
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import func

NOZZLE_PRODUCT = {7: 'HS', 16: 'HS', 18: 'MS', 15: 'MS', 17: 'X2', 11: 'XG'}
BUSY_NOZZLES = (7, 15, 18)
VPS = os.environ.get("REVIEW_VPS", "ubuntu@100.96.147.27")


def F(key, severity, category, title, detail="", suggestion="", audience="dev"):
    return dict(key=key, severity=severity, category=category, title=title,
                detail=detail, suggestion=suggestion, audience=audience)


def _now():
    return datetime.now()          # the evo clock is IST


# ── data freshness ───────────────────────────────────────────────────────────

def check_data_freshness(now):
    from pumpvision.models import IrasPrice, NozzleTotalizer, PaytmTransaction, SdmsSummary, TankReading, db
    out = []
    today = now.date()

    last = db.session.query(func.max(TankReading.scraped_at)).scalar()
    in_hours = time(6, 30) <= now.time() or now.time() <= time(0, 30)
    if last is None:
        out.append(F("atg_none", "critical", "data", "No tank (ATG) readings at all",
                     suggestion="Check the VPS ATG cron (scripts/vps_run_atg_snapshot.sh) and its log in /data/logs."))
    elif in_hours:
        age_min = (now - last).total_seconds() / 60
        if age_min > 360:
            out.append(F("atg_stale", "critical", "data", f"Tank readings are {age_min/60:.1f} h old",
                         f"Newest reading {last:%d %b %H:%M} IST.",
                         "ATG cron or IRAS login failing: read /data/logs/atg_<date>.log on the VPS; run `daily_scrape.py --atg-only`."))
        elif age_min > 150:
            out.append(F("atg_stale", "warn", "data", f"Tank readings are {age_min/60:.1f} h old",
                         f"Newest reading {last:%d %b %H:%M} IST (expected hourly).",
                         "Usually one failed IRAS login; the next hourly run normally heals it."))

    if now.time() >= time(7, 30):
        have = {r[0] for r in db.session.query(NozzleTotalizer.nozzle_no).filter_by(operational_date=today)}
        missing = sorted(set(NOZZLE_PRODUCT) - have)
        if missing:
            out.append(F(f"boundary_missing:{today}", "critical", "data",
                         f"Today's 06:00 meter boundary is missing nozzles {missing}",
                         "Without it yesterday's sales cannot be computed for those products.",
                         "Re-run `daily_scrape.py --completed-shift --date <yesterday>` on the VPS. If it fails with [STALE], "
                         "IRAS data is lagging: wait and retry, or load the boundary from the Shift Totalizer file."))

    if now.time() >= time(8, 30):
        have = {r[0] for r in db.session.query(IrasPrice.product).filter(
            func.date(IrasPrice.effective_from) == today)}
        missing = sorted({'HS', 'MS', 'X2', 'XG'} - have)
        if missing:
            out.append(F(f"price_missing:{today}", "warn", "data", f"Today's IRAS price not in for {', '.join(missing)}",
                         "Credit sales fall back to the latest known rate.",
                         "Run `daily_scrape.py --price-only --date <today>` on the VPS (cron vps_run_price_today.sh)."))

        y = today - timedelta(days=1)
        if db.session.query(func.count(PaytmTransaction.id)).filter_by(operational_date=y).scalar() == 0:
            out.append(F(f"paytm_missing:{y}", "warn", "data", f"No Paytm transactions for {y:%d %b}",
                         "Card/UPI is deducted from cash, so cash in hand for that day is overstated.",
                         "`daily_scrape.py --paytm-only --date <date>`, or `scrapers/import_paytm_csv.py` if the CSV exists."))
        d2 = today - timedelta(days=2)
        if db.session.query(SdmsSummary).filter_by(op_date=d2).first() is None:
            out.append(F(f"sdms_missing:{d2}", "warn", "data", f"No SDMS (fleet card / CNG) row for {d2:%d %b}",
                         "Fleet-card and CNG figures for that day are missing from the summary.",
                         "`daily_scrape.py --sdms-only --date <date>` on the VPS."))
    return out


# ── maths ────────────────────────────────────────────────────────────────────

def _boundaries(days=16):
    from pumpvision.models import NozzleTotalizer
    since = date.today() - timedelta(days=days)
    rows = NozzleTotalizer.query.filter(NozzleTotalizer.operational_date >= since)\
        .order_by(NozzleTotalizer.nozzle_no, NozzleTotalizer.operational_date).all()
    by = {}
    for r in rows:
        by.setdefault(r.nozzle_no, []).append((r.operational_date, r.totalizer_end))
    return by


def check_totalizer_maths(now):
    out = []
    by_product = {}                      # product -> {date: litres} (nozzles swap load, so judge the product)
    for n, series in _boundaries().items():
        for (d0, v0), (d1, v1) in zip(series, series[1:]):
            if (d1 - d0).days != 1:
                continue
            delta = v1 - v0
            if delta < -0.5:
                out.append(F(f"backwards:{n}:{d1}", "critical", "maths",
                             f"Nozzle {n} meter runs backwards into {d1:%d %b} ({delta:,.1f} L)",
                             f"Boundary {v0:,.2f} on {d0:%d %b} then {v1:,.2f} on {d1:%d %b}. A totalizer never decreases, "
                             "so one of the two stored boundaries is wrong and the sales of both days are distorted.",
                             "Compare both with the IRAS Shift Totalizer file (/data/iras_data/ShiftTotalizer on the VPS); "
                             "correct the wrong nozzle_totalizers row after a pg_dump."))
            if n in BUSY_NOZZLES and abs(delta) < 0.5 and d1 >= now.date() - timedelta(days=2):
                out.append(F(f"flat:{n}:{d1}", "warn", "maths", f"Nozzle {n} sold nothing on {d1 - timedelta(days=1):%d %b}",
                             "A busy nozzle with zero movement usually means a stale boundary, not a closed pump.",
                             f"Check the {d1:%d %b} boundary against the Shift Totalizer file."))
            prod = NOZZLE_PRODUCT.get(n)
            by_product.setdefault(prod, {}).setdefault(d1, 0.0)
            by_product[prod][d1] += max(delta, 0.0)
    for prod, days in by_product.items():
        if prod == 'XG':
            continue
        ordered = sorted(days.items())
        for d1, tot in ordered[-3:]:
            hist = [v for d, v in ordered if d < d1 and v > 0][-14:]
            if len(hist) >= 5 and tot > 500:
                med = statistics.median(hist)
                if tot > 2.0 * med or tot < 0.4 * med:
                    out.append(F(f"prodswing:{prod}:{d1}", "warn", "maths",
                                 f"{prod} sales on {d1 - timedelta(days=1):%d %b}: {tot:,.0f} L (usual ~{med:,.0f} L)",
                                 "Far from the recent median: a wrong boundary on one side, or a real surge/slump.",
                                 "Check both boundaries against IRAS; a stale boundary inflates one day and shrinks the next."))
    return out


def check_sales_and_cash(now):
    from pumpvision.blueprints.dashboard.routes import _cash_for_date, _product_sales
    from pumpvision.models import FuelTest, db
    out = []
    for i in (1, 2):
        d = now.date() - timedelta(days=i)
        if now.date() == d + timedelta(days=1) and now.time() < time(7, 30):
            continue
        try:
            sales = _product_sales(d)
        except Exception as e:                                   # noqa: BLE001
            out.append(F(f"sales_calc:{d}", "critical", "maths", f"Sales for {d:%d %b} cannot be computed", str(e)))
            continue
        for p, v in sales.items():
            if v['litres'] > 0 and not v['rsp']:
                out.append(F(f"no_rate:{p}:{d}", "critical", "maths", f"{p} litres on {d:%d %b} have no price",
                             "Revenue and cash for that day are understated.",
                             "Load the IRAS price for that date (`--price-only --date`)."))
        cash = _cash_for_date(d)
        if cash is not None and cash < 0:
            out.append(F(f"neg_cash:{d}", "warn", "business", f"Cash in hand for {d:%d %b} works out negative (₹{cash:,.0f})",
                         "Paytm, credit, fleet card and expenses together exceed the day's sales. Usually a missing "
                         "fuel reading or a wrongly entered expense/credit sale.",
                         "Open More → Manual entries for that day and look for a wrong entry.", audience="owner"))
    big = db.session.query(FuelTest.op_date, FuelTest.product, func.sum(FuelTest.litres)) \
        .filter(FuelTest.op_date >= now.date() - timedelta(days=3)).group_by(FuelTest.op_date, FuelTest.product).all()
    for d, p, l in big:
        if l and l > 100:
            out.append(F(f"test_big:{p}:{d}", "warn", "business", f"{l:.0f} L of {p} logged as taken for testing on {d:%d %b}",
                         "Unusually large for testing; this volume is excluded from sales.",
                         "Confirm with the attendant; remove it under More → Manual entries if wrong.", audience="owner"))
    return out


# A single ATG reading interval (~30 min) normally loses at most ~300 L of diesel and ~150 L of
# petrol. Bigger drops are bulk dispenses: someone filling a tanker, drums or a fleet vehicle.
BULK_DROP_L = {'HS': 500.0, 'MS': 400.0, 'X2': 150.0, 'XG': 150.0}
TANK_PRODUCT = {1: 'HS', 2: 'MS', 3: 'X2', 4: 'XG'}


def check_bulk_dispense(now):
    """Bulk sales seen on the tank gauge that no credit sale or fleet-card posting covers.

    Cash in hand cannot reveal a forgotten credit sale (it swings +-65k a day), but the tank
    gauge can: it shows the litres leaving. Paytm payments are small, so a bulk dispense is
    paid by credit, fleet card or cash. If the day's credit sales + fleet-card litres cover
    it, fine; otherwise the owner is asked to confirm it was recorded."""
    from pumpvision.blueprints.dashboard.routes import _fleet_total
    from pumpvision.models import CreditTransaction, TankReading, db
    from pumpvision.services.prices import get_rsp
    out = []
    days = [now.date() - timedelta(days=2)]
    if now.time() >= time(7, 0):
        days.append(now.date() - timedelta(days=1))
    for d in days:
        start, end = datetime.combine(d, time(6, 0)), datetime.combine(d + timedelta(days=1), time(6, 0))
        rows = TankReading.query.filter(TankReading.scraped_at >= start - timedelta(hours=1),
                                        TankReading.scraped_at < end).order_by(TankReading.tank_id, TankReading.scraped_at).all()
        by_tank = {}
        for r in rows:
            by_tank.setdefault(r.tank_id, []).append(r)
        for tank, rs in by_tank.items():
            prod = TANK_PRODUCT.get(tank)
            limit = BULK_DROP_L.get(prod)
            if not limit:
                continue
            bursts = []
            for a_, b_ in zip(rs, rs[1:]):
                mins = (b_.scraped_at - a_.scraped_at).total_seconds() / 60
                if a_.volume_litres is None or b_.volume_litres is None or not 10 <= mins <= 75 or b_.scraped_at < start:
                    continue
                drop = a_.volume_litres - b_.volume_litres
                if drop >= limit:
                    bursts.append((b_.scraped_at, drop))
            if not bursts:
                continue
            total = sum(x for _, x in bursts)
            credit_l = db.session.query(func.coalesce(func.sum(CreditTransaction.litres), 0.0)).filter(
                CreditTransaction.product == prod, CreditTransaction.is_legacy_entry.isnot(True),
                CreditTransaction.transaction_date.in_([d, d + timedelta(days=1)])).scalar() or 0.0
            fleet_amt, _ = _fleet_total(d)
            rate = get_rsp(prod, d) or 0.0
            fleet_l = fleet_amt / rate if rate else 0.0
            covered = credit_l + fleet_l
            if covered >= total:
                continue
            first, last = bursts[0][0], bursts[-1][0]
            out.append(F(f"bulk:{prod}:{d}", "warn", "business",
                         f"{d:%d %b}: {total:,.0f} L of {prod} left the tank in a short burst around {first:%H:%M} — check it was recorded",
                         f"{len(bursts)} reading interval(s) between {first:%H:%M} and {last:%H:%M} each lost {limit:,.0f}+ L. "
                         f"Credit sales entered for {prod}: {credit_l:,.0f} L; fleet-card postings that day ≈ {fleet_l:,.0f} L. "
                         "A bulk fill is paid by credit, fleet card or cash (Paytm payments are small), so an unentered "
                         "credit sale leaves cash overstated and the customer's balance short.",
                         "Ask the attendants who was served. Add a missing credit sale under More → Manual entries → "
                         "Add a credit sale (it can be dated to that day).", audience="owner"))
    return out


def check_credit_maths(now):
    from pumpvision.models import CreditTransaction
    out = []
    since = now.date() - timedelta(days=7)
    for t in CreditTransaction.query.filter(CreditTransaction.transaction_date >= since,
                                            CreditTransaction.is_legacy_entry.isnot(True)):
        if abs(t.litres * t.rate_per_litre - t.amount) > 1.0:
            out.append(F(f"credit_math:{t.transaction_id}", "warn", "maths",
                         f"Credit sale #{t.transaction_id}: litres × rate ≠ amount",
                         f"{t.litres:g} × {t.rate_per_litre:g} = {t.litres * t.rate_per_litre:,.2f}, stored {t.amount:,.2f}.",
                         "Check the sale; the customer balance was changed by the stored amount."))
    return out


# ── reconciliation ───────────────────────────────────────────────────────────

def check_attendant_vs_boundary(now):
    from pumpvision.models import ManualTotalizerReading, NozzleTotalizer
    out = []
    for m in ManualTotalizerReading.query.filter(
            ManualTotalizerReading.operational_date >= now.date() - timedelta(days=5),
            ManualTotalizerReading.nozzle_no.isnot(None)):
        nb = NozzleTotalizer.query.filter_by(nozzle_no=m.nozzle_no,
                                             operational_date=m.operational_date + timedelta(days=1)).first()
        if nb is None:
            continue
        gap = m.totalizer_value - nb.totalizer_end
        # the attendant reads ~06:40, so a small positive gap (sales since 06:00) is normal
        if gap < -25 or gap > 150:
            out.append(F(f"att_gap:{m.nozzle_no}:{m.operational_date}", "warn", "reconciliation",
                         f"Nozzle {m.nozzle_no} {m.operational_date:%d %b}: attendant reading differs from the boundary by {gap:+,.1f} L",
                         f"Attendant {m.totalizer_value:,.2f} vs stored boundary {nb.totalizer_end:,.2f}.",
                         "Either the attendant mis-keyed or the stored boundary is wrong: check against the "
                         "Shift Totalizer file (VPS reconcile job does this at 08:00 IST)."))
    return out


def check_cng(now):
    from pumpvision.models import CngShiftReading, SdmsSummary
    out = []
    for d in (now.date() - timedelta(days=2), now.date() - timedelta(days=3)):
        s = SdmsSummary.query.filter_by(op_date=d).first()
        att = CngShiftReading.query.filter_by(op_date=d).all()
        # A day whose every reading has closing == opening is the starting-meter baseline
        # (the two-nozzle set-up on 5-6 Oct 2026 seeded one), not a day of zero sales.
        if att and all(a.closing_reading == a.opening_reading for a in att):
            continue
        if s and (s.cng_kg_total or 0) > 0 and att:
            kg = sum(a.kg_sold for a in att)
            if abs(kg - s.cng_kg_total) > max(30, 0.1 * s.cng_kg_total):
                out.append(F(f"cng_gap:{d}", "warn", "reconciliation", f"CNG {d:%d %b}: attendant {kg:,.0f} kg vs SDMS {s.cng_kg_total:,.0f} kg",
                             "More than 10% apart.", "Check the attendant's CNG meter entries and the SDMS billing row."))
    return out


# ── regulatory (business) ────────────────────────────────────────────────────

def check_regulatory(now):
    from pumpvision.services.regulatory import mock_drill_status
    st = mock_drill_status(now.date())
    if st["state"] == "never":
        return [F("mock_drill", "warn", "regulatory", "No mock drill has been recorded",
                  "One is mandatory every 3 months.", "The manager records it in the app once it is done.", audience="owner")]
    if st["state"] == "overdue":
        return [F("mock_drill", "critical", "regulatory", f"Mock drill overdue by {-st['days_left']} days",
                  f"Was due {st['due']:%d %b %Y}.", "Hold a drill and have the manager record it.", audience="owner")]
    if st["state"] == "soon":
        return [F("mock_drill", "info", "regulatory", f"Mock drill due in {st['days_left']} days",
                  f"Due {st['due']:%d %b %Y}.", "Plan the drill.", audience="owner")]
    return []


# ── infrastructure ───────────────────────────────────────────────────────────

def check_infra(now):
    out = []
    home = Path.home()
    du = shutil.disk_usage("/")
    if du.used / du.total > 0.85:
        out.append(F("disk_full", "warn", "infra", f"Disk {du.used / du.total:.0%} full", suggestion="Prune logs, old dumps in ~/pg_backups."))
    dumps = sorted((home / "pg_backups").glob("pumpvision_2*.dump"), key=lambda p: p.stat().st_mtime)
    if not dumps or (now - datetime.fromtimestamp(dumps[-1].stat().st_mtime)) > timedelta(hours=26):
        out.append(F("backup_old", "critical", "infra", "No fresh nightly database backup (>26 h)",
                     suggestion="Check ~/pg_backup.sh and its cron entry (03:00)."))
    log = home / "logs" / "pumpvision-web.access.log"
    if log.exists():
        cutoff = now - timedelta(hours=4)
        n500 = 0
        try:
            for line in log.read_text(errors="ignore").splitlines()[-4000:]:
                if '" 500 ' in line:
                    try:
                        ts = datetime.strptime(line.split("[")[1].split()[0], "%d/%b/%Y:%H:%M:%S")
                    except (IndexError, ValueError):
                        continue
                    if ts >= cutoff:
                        n500 += 1
        except OSError:
            pass
        if n500:
            out.append(F("web_500s", "warn", "infra", f"{n500} server error(s) (HTTP 500) in the last 4 hours",
                         suggestion="See ~/logs/pumpvision-web.log for the traceback."))
    return out


_VPS_CACHE = {}


def vps_logs(now):
    """One ssh to the scraper server per run: {KEY: value}, or {'error': why}."""
    if _VPS_CACHE.get("at") == now:
        return _VPS_CACHE["data"]
    today, yday = now.date(), now.date() - timedelta(days=1)
    last = lambda pat: f"$(grep -h RESULT {pat} 2>/dev/null | tail -1 | sed 's/ *RESULT *: *//')"   # noqa: E731
    cmd = ("cd /data/logs 2>/dev/null && "
           f"echo CS={last(f'completed_shift_{today}.log completed_shift_{yday}.log')}; "
           f"echo ATG={last(f'atg_{today}.log')}; "
           f"echo PRICE={last(f'price_today_{today}.log')}; "
           f"echo SDMS={last(f'sdms_lookback_{today}.log')}; "
           f"echo REC=$(tail -3 reconcile_{today}.log 2>/dev/null | grep -o 'exit=[0-9]' | tail -1); "
           f"echo STALE=$(grep -l STALE completed_shift_{today}.log completed_shift_{yday}.log 2>/dev/null | wc -l); "
           "echo CRON=$(pgrep -c cron); echo DISK=$(df / --output=pcent | tail -1 | tr -dc 0-9)")
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", VPS, cmd],
                           capture_output=True, text=True, timeout=25)
        data = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        if not data:
            data = {"error": (r.stderr or "no output").strip()[:200]}
    except (subprocess.TimeoutExpired, OSError) as e:
        data = {"error": str(e)[:200]}
    _VPS_CACHE.update(at=now, data=data)
    return data


def check_vps(now):
    """Scraper health, read from the VPS logs over the tailnet."""
    out = []
    info = vps_logs(now)
    if "error" in info:
        return [F("vps_unreachable", "warn", "infra", "Scraper server (VPS) not reachable from the evo", info["error"],
                  "Check the Lightsail instance and Tailscale on it (100.96.147.27). Scrapes may be silently not running.")]
    if "FAILED" in info.get("CS", ""):
        out.append(F("vps_completed_shift", "critical", "infra", "Latest completed-shift scrape FAILED",
                     info["CS"].strip(), "Read /data/logs/completed_shift_<date>.log; retry the failed source with its --*-only flag."))
    if info.get("STALE", "0").strip() not in ("", "0"):
        out.append(F("vps_stale_boundary", "warn", "infra", "Boundary scrape refused stale IRAS data",
                     "IRAS transactions are lagging; the guard stopped a wrong boundary being saved.",
                     "Retry later (the wrapper retries); verify with the Shift Totalizer file."))
    return out


# ── status board: every scraper, service and backend function ───────────────

def _st(component, grp, status, detail=""):
    return dict(component=component, grp=grp, status=status, detail=detail[:300])


def _age(dt, now):
    h = (now - dt).total_seconds() / 3600
    return f"{h:.1f} h ago" if h < 48 else f"{h / 24:.0f} d ago"


def _http(url, timeout=8):
    import urllib.request
    try:
        return urllib.request.urlopen(url, timeout=timeout).status
    except Exception as e:                                      # noqa: BLE001
        return getattr(e, "code", None) or type(e).__name__


def collect_status(now):
    from pumpvision.models import IrasPrice, NozzleTotalizer, PaytmTransaction, SdmsSummary, TankReading, db
    out = []
    today = now.date()
    after = lambda h, m=0: now.time() >= time(h, m)             # noqa: E731

    # scrapers: judged on the data they should have produced, plus the VPS run logs
    t = db.session.query(func.max(TankReading.scraped_at)).scalar()
    if t is None:
        out.append(_st("ATG tank snapshots", "scraper", "fail", "no readings"))
    else:
        mins = (now - t).total_seconds() / 60
        quiet = not (time(6, 30) <= now.time() or now.time() <= time(0, 30))
        out.append(_st("ATG tank snapshots", "scraper", "ok" if (mins <= 150 or quiet) else "warn" if mins <= 360 else "fail",
                       f"latest reading {t:%d %b %H:%M} ({_age(t, now)})"))
    nb = db.session.query(func.max(NozzleTotalizer.operational_date)).scalar()
    want = today if after(7, 30) else today - timedelta(days=1)
    out.append(_st("IRAS boundary (meter totalizers)", "scraper", "ok" if nb and nb >= want else "fail",
                   f"latest boundary {nb:%d %b}, expected {want:%d %b}" if nb else "none"))
    pr = db.session.query(func.max(IrasPrice.effective_from)).scalar()
    out.append(_st("IRAS prices", "scraper", "ok" if pr and pr.date() >= today - timedelta(days=(0 if after(8, 30) else 1)) else "warn",
                   f"latest price row {pr:%d %b}" if pr else "none"))
    pt = db.session.query(func.max(PaytmTransaction.operational_date)).scalar()
    out.append(_st("Paytm import", "scraper", "ok" if pt and pt >= today - timedelta(days=(1 if after(9) else 2)) else "warn",
                   f"latest day {pt:%d %b}" if pt else "none"))
    sd = db.session.query(func.max(SdmsSummary.op_date)).scalar()
    out.append(_st("SDMS (fleet card / CNG)", "scraper", "ok" if sd and sd >= today - timedelta(days=2) else "warn",
                   f"latest day {sd:%d %b}" if sd else "none"))

    v = vps_logs(now)
    if "error" in v:
        out.append(_st("Scraper server (VPS)", "service", "fail", v["error"]))
    else:
        out.append(_st("Scraper server (VPS)", "service", "ok", f"reachable; disk {v.get('DISK', '?')}% used"))
        cs = v.get("CS", "").strip()
        out.append(_st("Completed-shift run", "scraper", "fail" if "FAILED" in cs else "ok" if "SUCCESS" in cs else "warn",
                       cs or "no run logged yet"))
        out.append(_st("VPS cron daemon", "service", "ok" if v.get("CRON", "0").strip() not in ("", "0") else "fail",
                       f"{v.get('CRON', '?').strip()} process(es)"))
        rec = v.get("REC", "").strip()
        out.append(_st("Meter reconcile (08:00 IST)", "scraper", "ok" if rec == "exit=0" else "warn",
                       "all boundaries agree" if rec == "exit=0" else
                       "reported problems — see findings" if rec else "has not run today yet"))

    # services
    code = _http("http://127.0.0.1:8002/login")
    out.append(_st("Web app (gunicorn)", "service", "ok" if code == 200 else "fail", f"HTTP {code}"))
    code = _http("https://evo-x3-1.tail863296.ts.net:8443/login", timeout=10)
    out.append(_st("Public URL (Tailscale Funnel)", "service", "ok" if code == 200 else "fail", f"HTTP {code}"))
    t0 = datetime.now()
    try:
        db.session.execute(db.text("select 1"))
        out.append(_st("PostgreSQL", "service", "ok", f"answered in {(datetime.now() - t0).total_seconds() * 1000:.0f} ms"))
    except Exception as e:                                       # noqa: BLE001
        out.append(_st("PostgreSQL", "service", "fail", str(e)))
    try:
        r = subprocess.run(["tailscale", "status", "--json"], capture_output=True, text=True, timeout=10)
        st = json.loads(r.stdout).get("BackendState") if r.returncode == 0 else f"rc={r.returncode}"
        out.append(_st("Tailscale (evo)", "service", "ok" if st == "Running" else "fail", str(st)))
    except Exception as e:                                       # noqa: BLE001
        out.append(_st("Tailscale (evo)", "service", "fail", str(e)))
    out.append(_st("Cron daemon (evo)", "service",
                   "ok" if subprocess.run(["pgrep", "-x", "cron"], capture_output=True).returncode == 0 else "fail", ""))
    dumps = sorted((Path.home() / "pg_backups").glob("pumpvision_2*.dump"), key=lambda p: p.stat().st_mtime)
    if dumps:
        m = datetime.fromtimestamp(dumps[-1].stat().st_mtime)
        out.append(_st("Nightly DB backup", "service", "ok" if now - m < timedelta(hours=26) else "fail", f"{m:%d %b %H:%M} ({_age(m, now)})"))
    else:
        out.append(_st("Nightly DB backup", "service", "fail", "no dumps found"))
    out.append(_st("Review agent", "service", "ok", f"ran {now:%d %b %H:%M} IST (every 4 h)"))

    # backend functions: run the real code paths read-only and time them
    from pumpvision.blueprints.dashboard.routes import _cash_for_date, _credit_total, _fleet_total, _product_sales, _stock_watch
    from pumpvision.services.credit_sale import rate_map
    from pumpvision.services.entries import list_entries
    from pumpvision.services.prices import get_rsp
    from pumpvision.services.regulatory import mock_drill_status
    y = today - timedelta(days=1)
    funcs = {
        "Sales by product": lambda: _product_sales(y),
        "Cash reconciliation": lambda: _cash_for_date(y),
        "Credit totals": lambda: _credit_total(y),
        "Fleet card total": lambda: _fleet_total(y),
        "Stock watch": lambda: _stock_watch(y),
        "Price lookup": lambda: get_rsp("HS", y) or (_ for _ in ()).throw(ValueError("no HS price")),
        "Credit-sale rates": lambda: rate_map(),
        "Manual entries list": lambda: list_entries(y),
        "Mock drill status": lambda: mock_drill_status(today),
    }
    for name, fn in funcs.items():
        t0 = datetime.now()
        try:
            fn()
            out.append(_st(name, "function", "ok", f"{(datetime.now() - t0).total_seconds() * 1000:.0f} ms"))
        except Exception as e:                                   # noqa: BLE001
            db.session.rollback()
            out.append(_st(name, "function", "fail", f"{type(e).__name__}: {e}"))
    return out


CHECKS = [check_data_freshness, check_totalizer_maths, check_sales_and_cash, check_credit_maths, check_bulk_dispense,
          check_attendant_vs_boundary, check_cng, check_regulatory,
          check_infra, check_vps]


# ── run + sync ───────────────────────────────────────────────────────────────

def run_review(now=None):
    """Run every check, sync findings, return a summary dict."""
    from pumpvision.models import AppNotification, AppSetting, ReviewFinding, db

    now = now or _now()
    utc = datetime.utcnow()
    seen, ok_sources, failed = {}, set(), []
    statuses = []
    try:
        statuses = collect_status(now)
        ok_sources.add("status")
        for st in statuses:
            # scraper failures already have specific findings (atg_stale, boundary_missing, ...)
            if st["status"] == "fail" and st["grp"] != "scraper":
                seen[f"status:{st['component']}"] = dict(
                    key=f"status:{st['component']}", source="status", severity="critical", category="infra", audience="dev",
                    title=f"{st['component']} is failing", detail=st["detail"],
                    suggestion="See the Status board on this page; check the component's log.")
    except Exception as e:                                       # noqa: BLE001
        db.session.rollback()
        failed.append("status")
        seen["check_failed:status"] = dict(
            key="check_failed:status", source="review", severity="warn", category="infra", audience="dev",
            title="Review status board crashed", detail=f"{type(e).__name__}: {e}",
            suggestion="Fix collect_status in pumpvision/services/review.py.")
    for chk in CHECKS:
        name = chk.__name__.removeprefix("check_")
        try:
            for f in chk(now):
                f["source"] = name
                seen[f["key"]] = f
            ok_sources.add(name)
        except Exception as e:                                   # noqa: BLE001
            db.session.rollback()
            failed.append(name)
            seen[f"check_failed:{name}"] = dict(
                key=f"check_failed:{name}", source="review", severity="warn", category="infra", audience="dev",
                title=f"Review check '{name}' crashed", detail=f"{type(e).__name__}: {e}",
                suggestion="Fix the check in pumpvision/services/review.py.")

    new = 0
    for key, f in seen.items():
        row = ReviewFinding.query.filter_by(key=key).first()
        if row is None:
            row = ReviewFinding(key=key, first_seen=utc, last_seen=utc, occurrences=1, **{
                k: f[k] for k in ("source", "severity", "category", "audience", "title", "detail", "suggestion")})
            db.session.add(row)
            new += 1
        else:
            if row.resolved_at is not None:                      # it came back
                row.resolved_at, row.acknowledged_at, row.first_seen, row.occurrences = None, None, utc, 0
                new += 1
            for k in ("severity", "category", "audience", "title", "detail", "suggestion"):
                setattr(row, k, f[k])
            row.last_seen, row.occurrences = utc, (row.occurrences or 0) + 1
        db.session.flush()
        if row.audience == "owner" and row.notification_id is None:
            n = AppNotification(message=f"{row.title}. {row.detail or ''}".strip()[:500],
                                notification_type="review_alert", reference_date=now.date())
            db.session.add(n)
            db.session.flush()
            row.notification_id = n.id

    resolved = 0
    for row in ReviewFinding.query.filter(ReviewFinding.resolved_at.is_(None)):
        if row.source in ok_sources and row.key not in seen:
            row.resolved_at = utc
            resolved += 1
            if row.notification_id:
                n = db.session.get(AppNotification, row.notification_id)
                if n:
                    n.is_read = True
                row.notification_id = None

    from pumpvision.models import ReviewStatus
    for st in statuses:
        row = db.session.get(ReviewStatus, st["component"])
        if row is None:
            db.session.add(ReviewStatus(component=st["component"], grp=st["grp"], status=st["status"],
                                        detail=st["detail"], checked_at=utc))
        else:
            row.grp, row.status, row.detail, row.checked_at = st["grp"], st["status"], st["detail"], utc
    keep = {st["component"] for st in statuses}
    if statuses:
        for row in ReviewStatus.query.all():
            if row.component not in keep:
                db.session.delete(row)

    open_rows = ReviewFinding.query.filter(ReviewFinding.resolved_at.is_(None)).all()
    summary = {"at": now.strftime("%Y-%m-%d %H:%M"), "open": len(open_rows), "new": new, "resolved": resolved,
               "failed_checks": failed,
               "critical": sum(r.severity == "critical" for r in open_rows),
               "status_fail": sum(s["status"] == "fail" for s in statuses), "status_warn": sum(s["status"] == "warn" for s in statuses),
               "components": len(statuses)}
    s = db.session.get(AppSetting, "review_last_run")
    if s:
        s.value = json.dumps(summary)
    else:
        db.session.add(AppSetting(key="review_last_run", value=json.dumps(summary)))
    db.session.commit()
    return summary
