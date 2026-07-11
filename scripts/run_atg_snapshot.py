#!/usr/bin/env python3
"""
Railway-friendly ATG tank stock snapshot cron entrypoint.

Delegates to:
    python -X utf8 scrapers/daily_scrape.py --atg-only

ATG is a live/current reading of what is in the tanks right now.
It is NOT historical accounting data. No date argument is used or needed.
Run on a separate schedule from completed-shift (every 30 or 60 minutes).

The Railway cron schedule */30 * * * * runs every 30 minutes.

Usage:
    python -X utf8 scripts/run_atg_snapshot.py

Environment:
    DATABASE_URL  Required. Must be set before running. Never printed.
    (all other scraper env vars are read by daily_scrape.py, not this script)
"""

import datetime
import os
import subprocess
import sys

# Load repo-root .env so cron/VPS runs get DATABASE_URL without shell-exporting
# secrets. Same pattern as every scraper. No-op if the file does not exist
# (e.g. Railway, where variables come from the service environment).
from dotenv import load_dotenv

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

# Fixed IST offset: UTC+05:30. Does not require zoneinfo or pytz.
_IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def _ist_now() -> datetime.datetime:
    return datetime.datetime.now(tz=_IST)


def main() -> int:
    # Guard: DATABASE_URL must be set but is never printed.
    if not os.environ.get("DATABASE_URL"):
        print("[ERROR] DATABASE_URL is not set in the environment.", file=sys.stderr)
        print("        Set it in Railway service variables (or .env locally).", file=sys.stderr)
        print("        Do not paste the value into this script.", file=sys.stderr)
        return 1

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
    cmd = [sys.executable, "-X", "utf8", daily_scrape, "--atg-only"]

    # Header.
    now_ist = _ist_now()
    print("=======================================================")
    print("  Pumpvision - ATG tank snapshot (Railway cron)")
    print("=======================================================")
    print(f"  mode        : --atg-only (live/current - no date)")
    print(f"  IST now     : {now_ist.strftime('%Y-%m-%d %H:%M:%S')} IST")
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
    print(f"  finished    : {_ist_now().strftime('%Y-%m-%d %H:%M:%S')} IST")
    print("=======================================================")
    sys.stdout.flush()

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
