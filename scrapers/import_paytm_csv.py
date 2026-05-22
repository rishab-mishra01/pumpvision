"""
import_paytm_csv.py — Import an existing Paytm Transaction Report CSV into the DB.

Does NOT download from Paytm. Use this when the CSV is already on disk but the
scraper download step failed and the DB import was never reached.

Target database is determined by DATABASE_URL (env var).
  - Not set → SQLite (instance/pumpvision.db)
  - Set to Railway URL → Railway PostgreSQL

Usage:
    python -X utf8 scrapers/import_paytm_csv.py data/paytm/paytm_2026-05-21.csv
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

if len(sys.argv) != 2:
    print("Usage: python -X utf8 scrapers/import_paytm_csv.py <path_to_csv>")
    sys.exit(1)

csv_path = Path(sys.argv[1])
if not csv_path.exists():
    print(f"ERROR: file not found: {csv_path.resolve()}")
    sys.exit(1)

from pumpvision import create_app
from pumpvision.models import db, PaytmTransaction
from pumpvision.blueprints.paytm.routes import _parse_paytm_csv

app = create_app()
with app.app_context():
    db_url = app.config.get("SQLALCHEMY_DATABASE_URI", "")
    target = "Railway PostgreSQL" if db_url.startswith("postgresql") else f"SQLite ({db_url})"
    print(f"Target DB : {target}")
    print(f"CSV file  : {csv_path.resolve()}")
    print()

    with open(csv_path, "rb") as f:
        records, warnings = _parse_paytm_csv(f)

    print(f"Parsed    : {len(records)} valid ACQUIRING+SUCCESS transactions")
    if warnings:
        print(f"Warnings  : {len(warnings)} rows skipped during parse")
        for w in warnings:
            print(f"  {w}")

    if not records:
        print("ERROR: no valid transactions found — nothing to import.")
        sys.exit(1)

    inserted = skipped = 0
    for rec in records:
        if db.session.query(PaytmTransaction).filter_by(
            paytm_txn_id=rec["paytm_txn_id"]
        ).first():
            skipped += 1
        else:
            db.session.add(PaytmTransaction(**rec))
            inserted += 1

    db.session.commit()
    print(f"Inserted  : {inserted}")
    print(f"Skipped   : {skipped} (already in DB)")
    print()
    print("Done.")
