"""
Durable spool for scrape results whose DB write failed.

Why this exists
---------------
Every scraper used to write its result to Postgres inside a bare
``try/except`` that printed a warning and returned a falsy value the caller
ignored.  When Railway's database went unreachable on 2026-08-20 the scrapers
kept logging ``SUCCESS`` for 40 hours while nothing reached a database, and the
numbers survived only as text in the cron logs.

So: a failed DB write now (a) parks the payload on disk as JSON and (b) is
reported to the caller so the run exits non-zero.  ``replay_spool.py`` re-applies
the queue once the DB is back.  All five writers upsert, so replay is idempotent
and re-running a spooled payload is always safe.

Layout::

    data/spool/<kind>/<key>.json      pending
    data/spool/_done/<kind>/<key>.json  applied (kept for audit)
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
SPOOL_DIR = Path(os.environ.get("PUMPVISION_SPOOL_DIR", _PROJECT_ROOT / "data" / "spool"))
DONE_DIR = SPOOL_DIR / "_done"

# Every spoolable write site, and the replay handler that re-applies it.
# Keep in sync with replay_spool.HANDLERS.
KINDS = (
    "sdms_summary",       # scrapers/sdms_pad_exporter.save_summary_to_db
    "atg_readings",       # scrapers/iras_atg_exporter.save_readings_to_db
    "nozzle_totalizers",  # scrapers/iras_iss_exporter.save_totalizers_to_db
    "iras_prices",        # scrapers/daily_scrape._save_prices_to_db
    "paytm_import",       # scrapers/daily_scrape._import_paytm_to_db
)


# Payloads spooled since this process started.  Every scraper shares one
# db_spool module instance (sys.modules caches by name, even for the file-path
# imports daily_scrape uses), so run() can ask a single question at the end:
# did any DB write fail during this run?
_SPOOLED_THIS_RUN: list[tuple[str, str]] = []


def spooled_this_run() -> list[tuple[str, str]]:
    """(kind, key) pairs spooled by this process, in order."""
    return list(_SPOOLED_THIS_RUN)


def _jsonable(obj):
    """Convert date/datetime/Decimal/Path so a payload survives json.dumps."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonable(v) for v in obj]
    return obj


def _safe(key: str) -> str:
    """Filesystem-safe spool filename stem."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(key))


def spool(kind: str, key: str, payload, error: str = "") -> Path | None:
    """
    Park one failed DB write on disk.  Returns the spool path, or None if even
    the spool write failed (logged, never raised — a spool failure must not mask
    the DB error the caller is already reporting).

    An existing spool file for the same (kind, key) is overwritten: the newest
    scrape of a given date is the one worth replaying.
    """
    try:
        target_dir = SPOOL_DIR / kind
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{_safe(key)}.json"
        record = {
            "kind": kind,
            "key": str(key),
            "spooled_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "error": str(error)[:2000],
            "payload": _jsonable(payload),
        }
        # Atomic: a half-written spool file is worse than none.
        fd, tmp = tempfile.mkstemp(dir=str(target_dir), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        _SPOOLED_THIS_RUN.append((kind, str(key)))
        print(f"  [spool] Parked {kind}/{_safe(key)} for replay → {path}")
        return path
    except Exception as e:  # noqa: BLE001 — never let spooling raise
        _SPOOLED_THIS_RUN.append((kind, str(key)))
        print(f"  [spool] FAILED to spool {kind}/{key}: {e}")
        return None


def pending(kind: str | None = None) -> list[Path]:
    """Spooled payloads still awaiting replay, oldest first."""
    roots = [SPOOL_DIR / kind] if kind else [SPOOL_DIR / k for k in KINDS]
    out: list[Path] = []
    for root in roots:
        if root.is_dir():
            out.extend(p for p in root.glob("*.json") if p.is_file())
    return sorted(out, key=lambda p: p.stat().st_mtime)


def mark_done(path: Path) -> None:
    """Move an applied payload into _done/<kind>/, preserving it for audit."""
    dest_dir = DONE_DIR / path.parent.name
    dest_dir.mkdir(parents=True, exist_ok=True)
    os.replace(path, dest_dir / path.name)


def pending_summary() -> str:
    """One-line count of what is waiting, for end-of-run logging."""
    items = pending()
    if not items:
        return ""
    counts: dict[str, int] = {}
    for p in items:
        counts[p.parent.name] = counts.get(p.parent.name, 0) + 1
    detail = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    return (f"{len(items)} DB write(s) spooled and NOT in the database ({detail}). "
            f"Replay with: python scrapers/replay_spool.py")
