"""Reconcile the nozzle totalizer chain against IRAS's own Shift Totalizer files.

READ-ONLY on totalizer data: it never corrects a value, it only reports. The one
write is a technical finding on the developer dashboard (review_findings) when
something disagrees; it never alerts the owner.

Row convention: nozzle_totalizers.operational_date = D holds the meter at the
06:00 boundary that OPENS day D (= the closing of D-1).

Truth for the boundary of day D is the Shift Totalizer file's own numbers:
  ShiftTotalizer_D.xlsx  'O' row   (opening of D), else
  ShiftTotalizer_(D-1).xlsx 'C' row (closing of D-1).
The file for the latest day has no 'C' rows until IRAS posts them, so the newest
boundary stays "pending" rather than being guessed.

Checks, for each op date D in the window:
  A  DB boundary(D) vs file truth(D)                   > TOL_BOUNDARY L  -> MISMATCH
  B  DB boundary(D) < DB boundary(D-1)                 (a meter never runs back)
  C  attendant closing(D) vs file truth(D+1)           gap < -TOL_BOUNDARY or > MAX_ATT_GAP
     (the attendant reads ~06:40, so a small positive gap is normal sales since 06:00)

Small differences (<= TOL_BOUNDARY, seen on ISS-derived boundaries) are ignored.

Usage:  reconcile_totalizers.py [--days N] [--no-alert]
Needs DATABASE_URL (env or ./.env) and the Shift Totalizer folder (VPS: /data/iras_data).
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import openpyxl
from sqlalchemy import create_engine, text

ST_DIR = Path(os.environ.get("ST_DIR", "/data/iras_data/ShiftTotalizer"))
NOZZLES = (7, 11, 15, 16, 17, 18)
TOL_BOUNDARY = 25.0     # L; ISS-derived boundaries differ from the file by up to ~18 L
MAX_ATT_GAP = 150.0     # L; attendant reading minus 06:00 boundary
IST = timezone(timedelta(hours=5, minutes=30))


def load_env_db_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    for line in Path(".env").read_text().splitlines():
        line = line.strip()
        if line.startswith("DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("DATABASE_URL not found")


def read_st(d: date):
    """{nozzle: {'O': x, 'C': y}} for one shift date, or None if the file is missing."""
    f = ST_DIR / f"ShiftTotalizer_{d.isoformat()}.xlsx"
    if not f.exists():
        return None
    wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
    rows = wb.active.iter_rows(values_only=True)
    head = next(rows)
    ni, si, ri = head.index("Nozzle No"), head.index("Shift Type"), head.index("Tot Reading")
    ti = head.index("Shift Time")
    out = defaultdict(dict)
    seen = {}   # (nozzle, type) -> shift time of the row kept
    for r in rows:
        if r[ni] is None or r[si] is None or r[ri] is None:
            continue
        key = (int(r[ni]), str(r[si]).strip().upper())
        t = str(r[ti])
        # A day's file holds several rows of each type: the 00:40 shift change (which is
        # the PREVIOUS day's close) and the 23:58 midnight close. The opening is the
        # earliest 'O' row, the closing the latest 'C' row; taking whichever came last in
        # the file compared attendants against a day-old meter (false +940 L alerts).
        if key in seen and (t >= seen[key] if key[1] != "O" else t <= seen[key]):
            pass
        elif key in seen:
            continue
        seen[key] = t
        out[key[0]][key[1]] = float(r[ri])
    wb.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=4, help="op dates back from today (IST)")
    ap.add_argument("--no-alert", action="store_true")
    args = ap.parse_args()

    eng = create_engine(load_env_db_url())
    today = datetime.now(IST).date()
    dates = [today - timedelta(days=i) for i in range(args.days, -1, -1)]

    st_cache = {}

    def st(d):
        if d not in st_cache:
            st_cache[d] = read_st(d)
        return st_cache[d]

    def truth(d, n):
        """Authoritative meter at the 06:00 boundary opening day d, or None."""
        s = st(d)
        if s and "O" in s.get(n, {}):
            return s[n]["O"]
        p = st(d - timedelta(days=1))
        if p and "C" in p.get(n, {}):
            return p[n]["C"]
        return None

    with eng.connect() as c:
        lo = dates[0] - timedelta(days=1)
        db = {(r[0], r[1]): r[2] for r in c.execute(text(
            "select operational_date, nozzle_no, totalizer_end from nozzle_totalizers "
            "where operational_date >= :lo"), {"lo": lo})}
        att = {(r[0], r[1]): r[2] for r in c.execute(text(
            "select operational_date, nozzle_no, totalizer_value from manual_totalizer_readings "
            "where operational_date >= :lo and nozzle_no is not null"), {"lo": lo})}

    problems, pending, ok = [], [], 0
    for d in dates:
        for n in NOZZLES:
            have = db.get((d, n))
            if have is None:
                problems.append((d, n, "MISSING", "no totalizer row in DB"))
                continue
            t = truth(d, n)
            if t is None:
                pending.append((d, n))
            elif abs(have - t) > TOL_BOUNDARY:
                problems.append((d, n, "MISMATCH", f"DB {have:,.2f} vs IRAS file {t:,.2f} (diff {have - t:+,.2f} L)"))
            else:
                ok += 1
            prev = db.get((d - timedelta(days=1), n))
            if prev is not None and have < prev - 0.01:
                problems.append((d, n, "BACKWARDS", f"DB {have:,.2f} is below previous day {prev:,.2f}"))
            a = att.get((d, n))
            if a is not None:
                nxt = truth(d + timedelta(days=1), n)
                if nxt is not None:
                    gap = a - nxt
                    if gap < -TOL_BOUNDARY or gap > MAX_ATT_GAP:
                        problems.append((d, n, "ATTENDANT",
                                        f"attendant closing {a:,.2f} vs IRAS boundary {nxt:,.2f} (gap {gap:+,.2f} L)"))

    print(f"[reconcile] {today} window {dates[0]}..{dates[-1]}: {ok} boundaries agree, "
          f"{len(pending)} pending IRAS data, {len(problems)} problem(s)")
    for d, n in pending:
        print(f"  pending  {d} nozzle {n}: IRAS file has no boundary yet")
    for d, n, kind, msg in problems:
        print(f"  {kind:9s}{d} nozzle {n}: {msg}")

    if not args.no_alert:
        # Technical findings go to the developer dashboard (review_findings), never to the owner.
        SEV = {"ATTENDANT": "warn"}
        FIX = {
            "MISMATCH": "The stored boundary differs from IRAS's own Shift Totalizer file: correct the nozzle_totalizers row to the file value (after a pg_dump).",
            "BACKWARDS": "A meter cannot run backwards: one of the two stored boundaries is wrong; compare with the Shift Totalizer file.",
            "MISSING": "Re-run `daily_scrape.py --completed-shift --date <date>` on the VPS.",
            "ATTENDANT": "Attendant mis-keyed, or the stored boundary is wrong: check against the Shift Totalizer file.",
        }
        keys = []
        try:
            with eng.begin() as c:
                for d, n, kind, msg in problems:
                    key = f"recon:{kind}:{n}:{d}"
                    keys.append(key)
                    c.execute(text(
                        "insert into review_findings (key, source, severity, category, audience, title, detail, suggestion, "
                        "first_seen, last_seen, occurrences) values (:k, 'recon', :sev, 'reconciliation', 'dev', :t, :d, :f, "
                        "now() at time zone 'utc', now() at time zone 'utc', 1) "
                        "on conflict (key) do update set last_seen = now() at time zone 'utc', occurrences = review_findings.occurrences + 1, "
                        "detail = excluded.detail, resolved_at = null"),
                        {"k": key, "sev": SEV.get(kind, "critical"), "t": f"Reconcile {kind}: {d:%d %b} nozzle {n}",
                         "d": msg, "f": FIX.get(kind, "")})
                c.execute(text(
                    "update review_findings set resolved_at = now() at time zone 'utc' "
                    "where source = 'recon' and resolved_at is null and not (key = any(:keys))"), {"keys": keys})
            if problems:
                print(f"  {len(problems)} finding(s) recorded for the developer dashboard")
        except Exception as e:                                   # noqa: BLE001
            print(f"  [WARN] could not record findings: {e}")
    sys.exit(2 if problems else 0)


if __name__ == "__main__":
    main()
