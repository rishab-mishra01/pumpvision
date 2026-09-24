#!/usr/bin/env python3
"""
Rebuild ATG tank readings that never reached the database, from the cron logs.

Why only ATG
------------
When the database is unreachable, every other scraper's data survives on disk
and can simply be re-scraped by date (SDMS, Paytm, prices, and ISS boundaries
are all date-parameterised, and the portals keep history).  The ATG snapshot is
different: IRAS shows only the *current* tank state, and the scraper writes it
straight to the database with no file artifact.  A missed hour is gone —
except that run_atg() prints every parsed reading before saving it.

So this reads those printed lines back:

      Tank 3 (X2): 1050 L  10.5%  @ 12:00

and re-emits them as spool payloads for scrapers/replay_spool.py to apply.

What is exact and what is not
-----------------------------
exact     tank_id, product, capacity_litres, is_reliable  (from _TANK_MAP,
          the same static table the scraper uses), and scraped_at
lossy     volume_litres — the log prints it rounded to whole litres, so a
          recovered row can differ from the true reading by up to 0.5 L
derived   pct_full — recomputed from the rounded volume, exactly as the
          scraper computes it, so volume and percentage stay consistent
lost      level_mm (product dip) is not printed and is left NULL

Validated against 36 readings from 2026-08-19 that this parser reconstructed
and the database also holds: every scraped_at and tank identity matched exactly
(a wrong timestamp would duplicate rows rather than match them), volumes agreed
within the documented 0.5 L, and 33/36 percentages matched to 2 dp with the
other 3 off by 0.01 from the rounding above.

Usage
-----
    python scripts/recover_atg_from_logs.py --log-dir /data/logs
    python scripts/recover_atg_from_logs.py --log-dir /data/logs --since 2026-08-20
    python scripts/recover_atg_from_logs.py --log-dir /data/logs --dry-run

By default only blocks whose DB save did NOT succeed are recovered.  Use --all
to re-emit every block in range; replay is idempotent either way, since
tank_readings has a UniqueConstraint on (scraped_at, tank_id).
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from scrapers import db_spool  # noqa: E402

# The scraper's own static tank table — capacity and probe reliability are
# properties of the hardware, not of the reading, so they are recovered exactly.
_TANK_MAP = {
    "HS": {"tank_id": 1, "capacity_litres": 20000.0, "is_reliable": True},
    "MS": {"tank_id": 2, "capacity_litres": 20000.0, "is_reliable": True},
    "X2": {"tank_id": 3, "capacity_litres": 10000.0, "is_reliable": True},
    "XG": {"tank_id": 4, "capacity_litres": 20000.0, "is_reliable": False},
}

_RE_START = re.compile(r"\[wrapper\] start(?: try \d+/\d+)? (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC")
_RE_READING = re.compile(
    r"^\s+Tank (\d+) \(([A-Z0-9]+)\):\s+(\S+) L\s+(\S+)%?\s+@ (\d{2}):(\d{2})"
)
_RE_SAVED = re.compile(r"\[db\] Saved (\d+) ATG reading\(s\)")
_RE_DB_ERROR = re.compile(r"\[db\] ERROR saving ATG readings")


class _Block:
    """One ATG scrape: the readings it printed, and whether its save succeeded."""

    def __init__(self, run_utc: datetime | None):
        self.run_utc = run_utc
        self.readings: list[dict] = []
        self.saved = False
        self.db_error = False


def _resolve_scraped_at(run_utc: datetime | None, hh: int, mm: int) -> datetime | None:
    """
    The log prints only HH:MM; the date comes from the run that printed it.

    IRAS reports stock times on the hour in UTC (verified against pre-outage
    rows: the 12:30 UTC run recorded '@ 12:00' and stored 12:00). A reading is
    therefore at or just before its run — if it looks like it is ahead of the
    run, it belongs to the previous day.
    """
    if run_utc is None:
        return None
    candidate = run_utc.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if candidate - run_utc > timedelta(hours=12):
        candidate -= timedelta(days=1)
    elif run_utc - candidate > timedelta(hours=12):
        candidate += timedelta(days=1)
    return candidate


def parse_log(path: Path) -> list[_Block]:
    blocks: list[_Block] = []
    current = _Block(None)
    run_utc: datetime | None = None

    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _RE_START.search(line)
        if m:
            if current.readings:
                blocks.append(current)
            run_utc = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M:%S")
            current = _Block(run_utc)
            continue

        m = _RE_READING.match(line)
        if m:
            tank_id, product, vol_raw, pct_raw, hh, mm = m.groups()
            if product not in _TANK_MAP:
                print(f"  [warn] unknown product {product!r} in {path.name} — skipped")
                continue
            if vol_raw in ("—", "-"):
                continue  # the scraper prints an em dash when volume is None
            info = _TANK_MAP[product]
            scraped_at = _resolve_scraped_at(current.run_utc, int(hh), int(mm))
            if scraped_at is None:
                print(f"  [warn] reading with no preceding run timestamp in {path.name} — skipped")
                continue
            volume = float(vol_raw.replace(",", ""))
            current.readings.append({
                "scraped_at": scraped_at,
                "tank_id": info["tank_id"],
                "product": product,
                "level_mm": None,           # product dip is not printed — unrecoverable
                "volume_litres": volume,
                "capacity_litres": info["capacity_litres"],
                "pct_full": round(volume / info["capacity_litres"] * 100, 2),
                "is_reliable": info["is_reliable"],
            })
            continue

        if _RE_SAVED.search(line):
            current.saved = True
        elif _RE_DB_ERROR.search(line):
            current.db_error = True

    if current.readings:
        blocks.append(current)
    return blocks


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-dir", default="/data/logs", type=Path,
                    help="directory holding atg_YYYY-MM-DD.log (default: /data/logs)")
    ap.add_argument("--since", type=date.fromisoformat,
                    help="only logs for this date onward (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true",
                    help="recover every block, not just those whose DB save failed")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be spooled, write nothing")
    args = ap.parse_args()

    if not args.log_dir.is_dir():
        print(f"[recover] No such log directory: {args.log_dir}")
        return 1

    logs = sorted(args.log_dir.glob("atg_*.log"))
    if args.since:
        logs = [p for p in logs
                if date.fromisoformat(p.stem.removeprefix("atg_")) >= args.since]
    if not logs:
        print(f"[recover] No ATG logs matched in {args.log_dir}")
        return 1

    print(f"[recover] Scanning {len(logs)} log file(s) in {args.log_dir}")
    total_blocks = recovered_blocks = total_readings = 0
    unique: dict[tuple[str, int], dict] = {}

    for path in logs:
        blocks = parse_log(path)
        wanted = [b for b in blocks if args.all or not b.saved]
        total_blocks += len(blocks)
        recovered_blocks += len(wanted)
        print(f"    {path.name}: {len(blocks)} scrape block(s), "
              f"{sum(1 for b in blocks if b.db_error)} with a DB error, "
              f"{len(wanted)} to recover")
        for b in wanted:
            for r in b.readings:
                # A 30-minute cron against hourly portal data prints the same
                # reading twice; the DB de-duplicates on (scraped_at, tank_id)
                # and so do we, so the spool carries each snapshot once.
                unique[(r["scraped_at"].isoformat(), r["tank_id"])] = r
                total_readings += 1

    by_stamp: dict[str, list[dict]] = {}
    for r in unique.values():
        by_stamp.setdefault(r["scraped_at"].strftime("%Y%m%dT%H%M%S"), []).append(r)

    print(f"\n[recover] {recovered_blocks}/{total_blocks} block(s) → "
          f"{len(unique)} distinct reading(s) across {len(by_stamp)} snapshot(s) "
          f"({total_readings - len(unique)} duplicate line(s) collapsed)")
    if by_stamp:
        stamps = sorted(by_stamp)
        print(f"[recover] Range: {stamps[0]} → {stamps[-1]} (UTC)")

    if args.dry_run:
        print("[recover] --dry-run: nothing written.")
        for stamp in sorted(by_stamp):
            rows = by_stamp[stamp]
            print(f"    {stamp}: " + ", ".join(
                f"T{r['tank_id']}/{r['product']}={r['volume_litres']:.0f}L" for r in sorted(rows, key=lambda x: x["tank_id"])))
        return 0

    for stamp, rows in sorted(by_stamp.items()):
        db_spool.spool("atg_readings", f"recovered_{stamp}", {"readings": rows},
                       error="recovered from cron log — level_mm unavailable")

    print(f"\n[recover] Spooled {len(by_stamp)} snapshot(s). Apply with:")
    print("    python scrapers/replay_spool.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
