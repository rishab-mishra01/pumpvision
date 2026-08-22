#!/usr/bin/env python3
"""
Re-apply DB writes that were spooled while the database was unreachable.

    python scrapers/replay_spool.py            # replay everything pending
    python scrapers/replay_spool.py --list     # show what is queued, change nothing
    python scrapers/replay_spool.py --kind atg_readings
    python scrapers/replay_spool.py --dry-run  # verify the DB is reachable only

Every handler wraps the same upsert the scraper would have run, so replaying a
payload twice is harmless.  Applied files move to data/spool/_done/<kind>/ and
are never deleted — they are the only audit trail of what was recovered.

Exit code 0 only when the queue drains completely.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "scrapers"))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(_PROJECT_ROOT / ".env")

from scrapers import db_spool  # noqa: E402


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


# ── Handlers ────────────────────────────────────────────────────────────────
# Each takes the spooled payload dict and re-runs the original write.
# Raise on failure; return a short description of what landed on success.

def _apply_sdms_summary(payload: dict) -> str:
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("sdms_pad_exporter",
                                       _PROJECT_ROOT / "scrapers" / "sdms_pad_exporter.py")
    mod = ilu.module_from_spec(spec)
    sys.modules["sdms_pad_exporter"] = mod
    spec.loader.exec_module(mod)
    ok = mod.save_summary_to_db(
        payload["date_iso"], payload["metadata"],
        payload["fleet_total"], payload["fleet_count"],
        payload["cng_kg"], payload["cng_revenue"], payload["cng_count"],
    )
    if not ok:
        raise RuntimeError("save_summary_to_db returned False")
    return f"SdmsSummary {payload['date_iso']}"


def _apply_atg_readings(payload: dict) -> str:
    from pumpvision import create_app
    from pumpvision.models import db, TankReading

    readings = payload["readings"]
    app = create_app()
    with app.app_context():
        saved = skipped = 0
        for r in readings:
            scraped_at = _dt(r["scraped_at"])
            exists = db.session.query(TankReading).filter_by(
                scraped_at=scraped_at, tank_id=r["tank_id"],
            ).first()
            if exists:
                skipped += 1
                continue
            db.session.add(TankReading(
                scraped_at=scraped_at,
                tank_id=r["tank_id"],
                product=r["product"],
                level_mm=r["level_mm"],
                volume_litres=r["volume_litres"],
                capacity_litres=r["capacity_litres"],
                pct_full=r["pct_full"],
                is_reliable=r["is_reliable"],
            ))
            saved += 1
        db.session.commit()
    return f"{saved} tank_readings inserted, {skipped} already present"


def _apply_nozzle_totalizers(payload: dict) -> str:
    import importlib.util as ilu
    spec = ilu.spec_from_file_location("iras_iss_exporter",
                                       _PROJECT_ROOT / "scrapers" / "iras_iss_exporter.py")
    mod = ilu.module_from_spec(spec)
    sys.modules["iras_iss_exporter"] = mod
    spec.loader.exec_module(mod)
    if os.environ.get("OUTPUT_FOLDER"):
        mod.OUTPUT_FOLDER = os.environ["OUTPUT_FOLDER"]
    ok = mod.save_totalizers_to_db(
        payload["shift_date"],
        {int(k): v for k, v in payload["totalizers"].items()},
        payload.get("xg_check"),
    )
    if not ok:
        raise RuntimeError("save_totalizers_to_db returned False")
    return f"NozzleTotalizer rows for {payload['shift_date']}"


def _apply_iras_prices(payload: dict) -> str:
    from pumpvision import create_app
    from pumpvision.models import IrasPrice, db

    records = payload["records"]
    app = create_app()
    with app.app_context():
        saved = updated = 0
        for r in records:
            eff_from = r["effective_from"]
            eff_to = r.get("effective_to")
            if isinstance(eff_from, str):
                eff_from = _dt(eff_from)
            if isinstance(eff_to, str):
                eff_to = _dt(eff_to)
            existing = IrasPrice.query.filter_by(
                product=r["product"], effective_from=eff_from,
            ).first()
            if existing:
                existing.rate_per_litre = r["rate_per_litre"]
                existing.effective_to = eff_to
                updated += 1
            else:
                db.session.add(IrasPrice(
                    product=r["product"],
                    rate_per_litre=r["rate_per_litre"],
                    effective_from=eff_from,
                    effective_to=eff_to,
                ))
                saved += 1
        db.session.commit()
    return f"{saved} new prices, {updated} updated"


def _apply_paytm_import(payload: dict) -> str:
    from pumpvision import create_app
    from pumpvision.models import db, PaytmTransaction
    from pumpvision.blueprints.paytm.routes import _parse_paytm_csv

    csv_path = Path(payload["csv_path"])
    if not csv_path.exists():
        raise FileNotFoundError(f"Paytm CSV missing: {csv_path}")
    app = create_app()
    with app.app_context():
        with open(csv_path, "rb") as f:
            records, _warnings = _parse_paytm_csv(f)
        inserted = skipped = 0
        for rec in records:
            if db.session.query(PaytmTransaction).filter_by(
                    paytm_txn_id=rec["paytm_txn_id"]).first():
                skipped += 1
            else:
                db.session.add(PaytmTransaction(**rec))
                inserted += 1
        db.session.commit()
    return f"{inserted} paytm txns inserted, {skipped} already present"


HANDLERS = {
    "sdms_summary":      _apply_sdms_summary,
    "atg_readings":      _apply_atg_readings,
    "nozzle_totalizers": _apply_nozzle_totalizers,
    "iras_prices":       _apply_iras_prices,
    "paytm_import":      _apply_paytm_import,
}


def _db_reachable() -> tuple[bool, str]:
    """Probe the DB once so a whole queue does not fail item by item."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False, "DATABASE_URL is not set"
    try:
        import sqlalchemy as sa
        engine = sa.create_engine(
            url,
            connect_args={"connect_timeout": 15} if url.startswith("postgres") else {},
        )
        with engine.connect() as conn:
            conn.execute(sa.text("SELECT 1"))
        return True, url.split("@")[-1]
    except Exception as e:
        return False, str(e).splitlines()[0]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="show the queue and exit")
    ap.add_argument("--kind", choices=sorted(HANDLERS), help="replay one kind only")
    ap.add_argument("--dry-run", action="store_true",
                    help="probe the DB and list what would be replayed")
    args = ap.parse_args()

    items = db_spool.pending(args.kind)
    if not items:
        print("[replay] Spool is empty — nothing to re-apply.")
        return 0

    print(f"[replay] {len(items)} payload(s) pending in {db_spool.SPOOL_DIR}")
    for p in items:
        rec = json.loads(p.read_text(encoding="utf-8"))
        print(f"    {rec['kind'].ljust(18)} {rec['key'].ljust(22)} spooled {rec['spooled_at']}")
    if args.list:
        return 0

    ok, detail = _db_reachable()
    if not ok:
        print(f"\n[replay] DB NOT reachable — {detail}")
        print("[replay] Nothing replayed; the spool is untouched. Retry when the DB is back.")
        return 1
    print(f"\n[replay] DB reachable at {detail}")
    if args.dry_run:
        print("[replay] --dry-run: stopping before any write.")
        return 0

    applied = failed = 0
    for p in items:
        rec = json.loads(p.read_text(encoding="utf-8"))
        handler = HANDLERS.get(rec["kind"])
        if handler is None:
            print(f"  [skip] unknown kind {rec['kind']!r} in {p.name}")
            failed += 1
            continue
        try:
            detail = handler(rec["payload"])
            db_spool.mark_done(p)
            applied += 1
            print(f"  [ok]   {rec['kind']}/{rec['key']}: {detail}")
        except Exception as e:
            failed += 1
            print(f"  [FAIL] {rec['kind']}/{rec['key']}: {e}")

    print(f"\n[replay] {applied} applied, {failed} still pending.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
