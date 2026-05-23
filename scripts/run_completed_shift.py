#!/usr/bin/env python3
"""
Railway-friendly completed-shift cron entrypoint.

Calculates accounting op_date in IST (UTC+05:30) and delegates to:
    python -X utf8 scrapers/daily_scrape.py --completed-shift --date YYYY-MM-DD
                                            [--paytm-wait-seconds N]

The Railway cron schedule is evaluated in UTC. The recommended schedule is
0 1 * * * (01:00 UTC = 06:30 IST), which runs after the 06:00 IST shift
boundary. op_date is always IST calendar date - 1 day.

Usage:
    python -X utf8 scripts/run_completed_shift.py
    python -X utf8 scripts/run_completed_shift.py --date 2026-05-22
    python -X utf8 scripts/run_completed_shift.py --date 2026-05-22 --paytm-wait-seconds 1800

Environment:
    DATABASE_URL  Required. Must be set before running. Never printed.
    (all other scraper env vars are read by daily_scrape.py, not this script)

Do NOT pass --iras-manual-captcha here -- it would block a scheduled run.
"""

import argparse
import datetime
import os
import subprocess
import sys

# Fixed IST offset: UTC+05:30. Does not require zoneinfo or pytz.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# Default Paytm wait matching the PowerShell wrapper (15 minutes).
_PAYTM_WAIT_DEFAULT = 900


def _ist_now() -> datetime.datetime:
    return datetime.datetime.now(tz=_IST)


def _default_op_date(now_ist: datetime.datetime) -> str:
    """Return the accounting op_date as YYYY-MM-DD.

    The outlet shift runs 06:00 IST -> 06:00 IST the next calendar day.
    After 06:00 IST on day D, the completed shift is op_date = D - 1.
    op_date is always IST_today - 1 day; the cron schedule handles timing.
    """
    return (now_ist.date() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Completed-shift cron entrypoint (Railway / cross-platform).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        default=None,
        help=(
            "Accounting op_date to scrape. "
            "Default: yesterday in IST (UTC+05:30). "
            "Always supply --date when testing manually."
        ),
    )
    parser.add_argument(
        "--paytm-wait-seconds",
        type=int,
        default=_PAYTM_WAIT_DEFAULT,
        dest="paytm_wait_seconds",
        metavar="N",
        help=f"Max seconds to wait for Paytm report download (default: {_PAYTM_WAIT_DEFAULT}).",
    )
    args = parser.parse_args()

    # Guard: DATABASE_URL must be set but is never printed.
    if not os.environ.get("DATABASE_URL"):
        print("[ERROR] DATABASE_URL is not set in the environment.", file=sys.stderr)
        print("        Set it in Railway service variables (or .env locally).", file=sys.stderr)
        print("        Do not paste the value into this script.", file=sys.stderr)
        return 1

    now_ist = _ist_now()

    # Determine op_date.
    if args.date is not None:
        try:
            datetime.datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(
                f"[ERROR] --date '{args.date}' is not a valid YYYY-MM-DD date.",
                file=sys.stderr,
            )
            return 1
        op_date = args.date
        date_source = "explicit --date"
    else:
        op_date = _default_op_date(now_ist)
        date_source = "auto (IST today - 1 day)"
        if now_ist.hour < 6:
            print(
                f"[WARN] Current IST time is {now_ist.strftime('%H:%M')} -- "
                "before 06:00. The shift boundary may not have passed yet. "
                "Pass --date explicitly if running early.",
                file=sys.stderr,
            )

    # Locate daily_scrape.py relative to this script.
    script_dir = os.path.dirname(os.path.abspath(__file__))
    repo_root = os.path.dirname(script_dir)
    daily_scrape = os.path.join(repo_root, "scrapers", "daily_scrape.py")

    if not os.path.isfile(daily_scrape):
        print(
            f"[ERROR] scrapers/daily_scrape.py not found at: {daily_scrape}",
            file=sys.stderr,
        )
        return 1

    # Build command.
    cmd = [
        sys.executable, "-X", "utf8",
        daily_scrape,
        "--completed-shift",
        "--date", op_date,
        "--paytm-wait-seconds", str(args.paytm_wait_seconds),
    ]

    # Header.
    print("=======================================================")
    print("  Pumpvision - completed-shift scrape (Railway cron)")
    print("=======================================================")
    print(f"  op_date     : {op_date}  ({date_source})")
    print(f"  IST now     : {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")
    print(f"  paytm wait  : {args.paytm_wait_seconds}s")
    print(f"  mode        : --completed-shift")
    print("=======================================================")
    print(flush=True)

    # Delegate to daily_scrape.py. Inherits environment (including DATABASE_URL).
    result = subprocess.run(cmd, cwd=repo_root)
    exit_code = result.returncode

    # Footer.
    status = "SUCCESS" if exit_code == 0 else f"FAILED (exit {exit_code})"
    print()
    print("=======================================================")
    print(f"  RESULT      : {status}")
    print(f"  op_date     : {op_date}")
    print(f"  finished    : {_ist_now().strftime('%Y-%m-%d %H:%M:%S')} IST")
    print("=======================================================")
    sys.stdout.flush()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
