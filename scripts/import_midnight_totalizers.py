#!/usr/bin/env python3
"""Import cached IRAS Shift Totalizer files as midnight close boundaries.

This is safe to re-run: rows are upserted by operational date and nozzle. It is
used once after the midnight-boundary migration and remains available for repair.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scrapers"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_ROOT / ".env")
os.environ.setdefault("PUMPVISION_SKIP_BOOTSTRAP", "1")

from scrapers.iras_iss_exporter import (  # noqa: E402
    NOZZLES,
    parse_shift_totalizer_nozzle,
    save_midnight_totalizers_to_db,
)

NAME_RE = re.compile(r"^ShiftTotalizer_(\d{4}-\d{2}-\d{2})\.xlsx$")


def main() -> int:
    default_dir = Path(
        os.environ.get("SHIFT_TOTALIZER_FOLDER", "/data/iras_data/ShiftTotalizer")
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=default_dir)
    parser.add_argument("--dates", nargs="*", help="optional YYYY-MM-DD allow-list")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    allowed = set(args.dates or [])
    files = []
    for path in sorted(args.dir.glob("ShiftTotalizer_*.xlsx")):
        match = NAME_RE.match(path.name)
        if match and (not allowed or match.group(1) in allowed):
            files.append((match.group(1), path))
    if not files:
        print(f"No matching Shift Totalizer files found in {args.dir}")
        return 1

    failed = 0
    for op_date, path in files:
        readings = {
            nozzle_no: values
            for nozzle_no in sorted(NOZZLES)
            if (values := parse_shift_totalizer_nozzle(path, nozzle_no)) is not None
            and values.get("close") is not None
        }
        if args.dry_run:
            print(f"{op_date}: would import {len(readings)}/6 rows from {path.name}")
            continue
        if not save_midnight_totalizers_to_db(op_date, readings, path.name):
            failed += 1

    print(f"Processed {len(files)} file(s); failures={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
