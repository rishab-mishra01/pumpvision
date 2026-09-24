#!/usr/bin/env python3
"""
Backfill tank_readings from the IRAS Stock tab at full resolution.

Why this exists: until 2026-08-22 the ATG snapshot read the Stock grid's
rendered DOM, which is a single 10-row page, so each hourly run stored only the
handful of rows that happened to be on page 1 -- roughly 19 reading times a day
out of the ~46 the forecourt actually posts. The portal still holds the
complete set (172-200 rows/day), and the Excel export returns all of it for a
given date window. This script replays that export day by day.

Idempotent: save_readings_to_db skips any (scraped_at, tank_id) already
present, so re-running a day only adds what is missing.

Usage:
    python -X utf8 scripts/backfill_atg_from_portal.py --since 2026-05-22
    python -X utf8 scripts/backfill_atg_from_portal.py --since 2026-08-01 --until 2026-08-21
    python -X utf8 scripts/backfill_atg_from_portal.py --since 2026-08-20 --dry-run

Always run it under the daily_scrape lock -- a second IRAS session for the same
dealer ID can invalidate the cron's login:

    flock -n /data/locks/daily_scrape.lock \\
        ./.venv/bin/python -X utf8 scripts/backfill_atg_from_portal.py --since ...

Set PUMPVISION_SKIP_BOOTSTRAP=1 so the per-day DB writes do not re-run
create_all/upgrade/_seed_data against production on every call.
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

from dotenv import load_dotenv

_REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_REPO_ROOT / ".env")
sys.path.insert(0, str(_REPO_ROOT / "scrapers"))

from playwright.async_api import async_playwright          # noqa: E402
import daily_scrape as ds                                   # noqa: E402
import iras_atg_exporter as atg                             # noqa: E402


def _daterange(since: date, until: date):
    d = since
    while d <= until:
        yield d
        d += timedelta(days=1)


async def _backfill_day(page, day: date, out_dir: Path, dry_run: bool) -> dict:
    """Query one whole day, read it via Excel export, and save. Returns a stat dict."""
    stat = {"day": day, "portal": 0, "parsed": 0, "saved": 0, "note": ""}

    frm = datetime(day.year, day.month, day.day, 0, 0, 0)
    to = datetime(day.year, day.month, day.day, 23, 59, 59)
    if not await atg.set_stock_range(page, frm, to):
        stat["note"] = "could not set range"
        return stat

    try:
        await page.locator("button:has-text('Show')").first.click()
    except Exception as exc:
        stat["note"] = f"Show failed: {type(exc).__name__}"
        return stat
    await page.wait_for_timeout(3500)

    try:
        await page.wait_for_selector(".ag-row, .ag-overlay-no-rows-wrapper",
                                     timeout=atg.TABLE_LOAD_TIMEOUT * 1000)
    except Exception:
        pass

    rendered = await page.locator(".ag-row").count()
    total = await atg._pager_total(page)
    stat["portal"] = total if total is not None else rendered

    if stat["portal"] == 0:
        stat["note"] = "portal has no rows for this day"
        return stat

    # Always take the export: a full day is far past one page.
    raw_rows = await atg._download_excel_rows(page, out_dir)
    if not raw_rows:
        raw_rows = await atg._read_ag_grid(page)
        if raw_rows:
            stat["note"] = f"export failed; DOM page only ({rendered} rows)"

    readings = []
    for row in raw_rows:
        parsed = atg._parse_row(row)
        if parsed is not None:
            readings.append(parsed)
    stat["parsed"] = len(readings)

    # The window filters on the row's UPDATE timestamp, so a day's export can
    # carry readings stamped on a neighbouring day. Keep them: that neighbour's
    # own query filters on ITS update stamps and may never return them, so
    # dropping here would lose the row outright. (scraped_at, tank_id) dedupes.
    outside = sum(1 for r in readings if r["scraped_at"].date() != day)
    if outside:
        stat["note"] = (stat["note"] + "; " if stat["note"] else "") + \
            f"{outside} row(s) stamped outside the day (kept)"

    if dry_run:
        stat["note"] = (stat["note"] + "; " if stat["note"] else "") + "dry-run"
        return stat

    saved = atg.save_readings_to_db(readings)
    if saved < 0:
        stat["note"] = (stat["note"] + "; " if stat["note"] else "") + "DB write FAILED (spooled)"
        stat["saved"] = 0
    else:
        stat["saved"] = saved
    return stat


async def main_async(args) -> int:
    since = datetime.strptime(args.since, "%Y-%m-%d").date()
    until = (datetime.strptime(args.until, "%Y-%m-%d").date()
             if args.until else date.today() - timedelta(days=1))
    if until < since:
        print(f"[ERROR] --until {until} is before --since {since}", file=sys.stderr)
        return 2

    days = list(_daterange(since, until))
    out_dir = Path(os.environ.get("OUTPUT_FOLDER", ".")) / "ATG"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 72)
    print("  ATG full-resolution backfill from the IRAS Stock tab")
    print("=" * 72)
    print(f"  range   : {since} -> {until}  ({len(days)} day(s))")
    print(f"  mode    : {'DRY RUN (no DB writes)' if args.dry_run else 'writing to the database'}")
    print(f"  exports : {out_dir}")
    print("=" * 72, flush=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1400, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"))
        page = await ctx.new_page()
        try:
            await page.goto(ds.LOGIN_URL, wait_until="networkidle", timeout=30_000)
        except Exception as exc:
            print(f"  [IRAS] navigation: {type(exc).__name__} — continuing")
        await page.wait_for_timeout(1000)

        if not await ds._autonomous_login(page, debug_dir=None, allow_manual=False):
            print("\n[ABORT] login failed", file=sys.stderr)
            await browser.close()
            return 1

        await atg.navigate_to_stock(page)

        stats = []
        for i, day in enumerate(days, 1):
            print(f"\n--- [{i}/{len(days)}] {day} " + "-" * 40, flush=True)
            try:
                stat = await _backfill_day(page, day, out_dir, args.dry_run)
            except Exception as exc:
                stat = {"day": day, "portal": 0, "parsed": 0, "saved": 0,
                        "note": f"ERROR {type(exc).__name__}: {exc}"}
            stats.append(stat)
            await page.wait_for_timeout(args.pause_ms)

        await browser.close()

    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print(f"  {'day':<12} {'portal':>7} {'parsed':>7} {'saved':>7}  note")
    for s in stats:
        print(f"  {str(s['day']):<12} {s['portal']:>7} {s['parsed']:>7} "
              f"{s['saved']:>7}  {s['note']}")
    print("-" * 72)
    print(f"  {'TOTAL':<12} {sum(s['portal'] for s in stats):>7} "
          f"{sum(s['parsed'] for s in stats):>7} {sum(s['saved'] for s in stats):>7}")
    print("=" * 72)

    failed = [s for s in stats if "FAILED" in s["note"] or s["note"].startswith("ERROR")]
    if failed:
        print(f"\n[WARN] {len(failed)} day(s) had errors — listed above.")
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", required=True, help="first day to backfill, YYYY-MM-DD")
    ap.add_argument("--until", help="last day (default: yesterday)")
    ap.add_argument("--dry-run", action="store_true",
                    help="query and parse, but do not write to the database")
    ap.add_argument("--pause-ms", type=int, default=800,
                    help="pause between days, milliseconds (default 800)")
    args = ap.parse_args()

    if not os.environ.get("DATABASE_URL") and not args.dry_run:
        print("[ERROR] DATABASE_URL is not set.", file=sys.stderr)
        return 1
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
