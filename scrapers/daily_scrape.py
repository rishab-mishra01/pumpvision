"""
daily_scrape.py — Daily IRAS data orchestrator with autonomous CAPTCHA login.

Why one login covers everything:
  All three daily scraping jobs — Shift Totalizer, Price (PRM), and ISS boundary —
  live under the same IRAS session. One CAPTCHA solve gets us in; we navigate
  between tabs freely without ever being asked to log in again.

Jobs run in this fixed order per day:
  0. Paytm            — payment transaction CSV for yesterday (own browser context).
  1. Price (PRM)      — RSP for the exact op day(s) being reconciled.
                        Downloaded first so the price is always available before
                        ISS results are written to the DB.
  2. Shift Totalizer  — downloaded before ISS because ISS boundary mode reads the
                        ST file from disk before searching any ISS windows.
                        XG pre-check and OOO nozzle detection both depend on it.
  3. ISS boundary     — finds 6AM totalizer readings for all 6 nozzles using
                        the ST file already on disk, then writes to NozzleTotalizer DB.
  4. SDMS PAD         — fleet card posting summary from SDMS portal (own browser context).
  5. ATG snapshot     — current tank levels from FCC > Stock (same IRAS session as 1-3).

Delivery jobs (RDB Invoice, SAP Invoice, TT Receipt, density records) are
event-driven — they fire when a tanker arrives, not on a daily schedule.
They are not part of this orchestrator.

Date semantics (four distinct systems — do not mix):
  --completed-shift --date D →  Full completed-shift run: checks opening boundary D and
                                closing boundary D+1 in DB; scrapes only the ones missing.
                                Then Paytm, Price, SDMS for accounting op_date D.
                                Existence checks: skips sources already in DB.
                                IRAS failure does not block SDMS.
                                ATG is excluded — tank stock is live/current, not historical.
                                Shift window: D 06:00 → D+1 05:59.
  --boundary-only   --date D →  captures 06:00 boundary on D; writes NozzleTotalizer row
                                for op_date D.  To see fuel data for accounting op_date D,
                                run twice: --date D (opening) and --date D+1 (closing).
  --accounting-only --date D →  Paytm, Price (PRM) + SDMS for accounting op_date D.
                                Existence checks: skips sources already in DB.
                                IRAS failure does not block SDMS.
                                Shift window: D 06:00 → D+1 05:59.
  --paytm-only      --date D →  Paytm only for accounting op_date D.
                                Skips if PaytmTransaction rows already exist for the date.
  --price-only      --date D →  IRAS Price (PRM) only for accounting op_date D.
                                Skips if IrasPrice rows already exist for the date.
  --sdms-only       --date D →  SDMS PAD only for accounting op_date D.
                                Skips if SdmsSummary row already exists for the date.
  --atg-only                 →  current tank stock snapshot; --date ignored.
                                Run independently every 30 min — do not include in
                                completed-shift because tank stock is live, not historical.
  (default/all)   --date D   →  all jobs; same boundary semantics as --boundary-only.

Usage:
    # Daily cron (all jobs, today as boundary date):
    python -X utf8 scrapers/daily_scrape.py

    # Completed-shift for op_date 2026-05-20 — skips existing boundaries + sources:
    #   shift window: 2026-05-20 06:00 → 2026-05-21 05:59
    python -X utf8 scrapers/daily_scrape.py --completed-shift --date 2026-05-20

    # Completed-shift dry-run (shows what would run/skip, no DB writes):
    python -X utf8 scrapers/daily_scrape.py --completed-shift --date 2026-05-20 --dry-run

    # Backfill accounting data for op_date 2026-05-21 (Paytm, Price, SDMS):
    python -X utf8 scrapers/daily_scrape.py --accounting-only --date 2026-05-21

    # Retry Paytm only for a specific date (skips if already imported):
    python -X utf8 scrapers/daily_scrape.py --paytm-only --date 2026-05-20

    # Retry Price only for a specific date (skips if already in DB):
    python -X utf8 scrapers/daily_scrape.py --price-only --date 2026-05-20

    # Retry SDMS only for a specific date (skips if already in DB):
    python -X utf8 scrapers/daily_scrape.py --sdms-only --date 2026-05-20

    # Boundary jobs only (writes one NozzleTotalizer row for 2026-05-21 06:00):
    python -X utf8 scrapers/daily_scrape.py --boundary-only --date 2026-05-21

    # Current ATG snapshot only — run independently, not part of completed-shift:
    python -X utf8 scrapers/daily_scrape.py --atg-only

    # Batch boundary backfill:
    python -X utf8 scrapers/daily_scrape.py --boundary-only --dates 2026-04-20 2026-04-21
"""

import argparse
import asyncio
import base64
import io
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Duplicate fd 1 NOW, before any scraper imports touch sys.stdout.
# iras_price_exporter wraps sys.stdout at import time; the orphaned wrapper
# left behind by iras_iss_exporter closes the underlying fd during GC.
# Capturing a dup here lets us restore a working stdout after both imports.
_stdout_fd = os.dup(1)

# ── sys.path: scrapers/ must come before project root ────────────────────────
# An old copy of iras_iss_exporter.py exists in the project root.
# Without explicit ordering, sys.path[0] (project root) wins and loads the
# wrong version. Insert scrapers/ first so the current scrapers take priority.
_PROJECT_ROOT = Path(__file__).parent.parent
_SCRAPERS_DIR = Path(__file__).parent
sys.path.insert(0, str(_PROJECT_ROOT))  # needed for pumpvision package imports
sys.path.insert(0, str(_SCRAPERS_DIR))  # overrides project root for scraper modules

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import anthropic
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Credentials + output paths from environment ───────────────────────────────
_base_url     = os.environ.get("IRAS_URL", "https://iras.iocliras.in").rstrip("/")
LOGIN_URL     = _base_url if _base_url.endswith("/login") else _base_url + "/login"
IRAS_USERNAME = os.environ.get("IRAS_USERNAME", "")
IRAS_PASSWORD = os.environ.get("IRAS_PASSWORD", "")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# OUTPUT_FOLDER in .env is the root data directory (e.g. C:\IRAS_Data).
# Each data type lives in its own subfolder — matching the existing scraper layout.
_data_root = Path(os.environ.get("OUTPUT_FOLDER", r"C:\IRAS_Data"))
ISS_DIR    = _data_root / "ISS"
ST_DIR     = _data_root / "ShiftTotalizer"
PRICE_DIR  = _data_root / "Price"

MAX_LOGIN_ATTEMPTS = 3

CAPTCHA_PROMPT = (
    "Read the characters in this CAPTCHA image exactly as they appear. "
    "Reply with only the characters, no spaces, no punctuation, nothing else. "
    "Ignore any strikethrough or diagonal lines across the text."
)

# ── Load scraper modules by explicit file path, bypassing sys.path entirely.
# Both modules exist in multiple locations (project root has old copies); using
# importlib with the scrapers/ path guarantees we always get the current version
# regardless of how sys.path is ordered or mutated by the modules themselves.
import importlib.util as _ilu

def _load_scraper(name: str):
    spec = _ilu.spec_from_file_location(name, _SCRAPERS_DIR / f"{name}.py")
    mod = _ilu.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

_iss  = _load_scraper("iras_iss_exporter")
_prm  = _load_scraper("iras_price_exporter")

# iras_iss_exporter and iras_price_exporter both wrap sys.stdout at import time,
# leaving nested TextIOWrappers on the same fd. Reset to a fresh fd before loading
# paytm/sdms scrapers, which also wrap sys.stdout and fail on a stale inner buffer.
sys.stdout = open(os.dup(_stdout_fd), "w", encoding="utf-8", errors="replace", closefd=True)
_ptm  = _load_scraper("paytm_exporter")

sys.stdout = open(os.dup(_stdout_fd), "w", encoding="utf-8", errors="replace", closefd=True)
_sdms = _load_scraper("sdms_pad_exporter")

sys.stdout = open(os.dup(_stdout_fd), "w", encoding="utf-8", errors="replace", closefd=True)
_atg  = _load_scraper("iras_atg_exporter")

# Final reset — clean stdout for the rest of the run.
sys.stdout = open(os.dup(_stdout_fd), "w", encoding="utf-8", errors="replace", closefd=True)

_iss.OUTPUT_FOLDER          = str(ISS_DIR)
_iss.SHIFT_TOTALIZER_FOLDER = str(ST_DIR)

PAYTM_EMAIL    = os.environ.get("PAYTM_EMAIL", "")
PAYTM_PASSWORD = os.environ.get("PAYTM_PASSWORD", "")
SDMS_USERNAME  = os.environ.get("SDMS_USERNAME", "")
SDMS_PASSWORD  = os.environ.get("SDMS_PASSWORD", "")


# ─────────────────────────────────────────────────────────────────────────────
# CAPTCHA SOLVER  (same logic as captcha_test.py)
# ─────────────────────────────────────────────────────────────────────────────

def _solve_captcha(image_bytes: bytes) -> str:
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=64,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                {"type": "text", "text": CAPTCHA_PROMPT},
            ],
        }],
    )
    return msg.content[0].text.strip()


async def _find(page, selectors: list[str], *, visible_check: bool = True):
    """Return the first locator from the list that exists (and is visible)."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0:
                if not visible_check or await loc.is_visible(timeout=1500):
                    return loc
        except Exception:
            continue
    return None


async def _autonomous_login(page) -> bool:
    """
    Solve the IRAS CAPTCHA with Claude Vision and log in.
    Retries up to MAX_LOGIN_ATTEMPTS times, refreshing the CAPTCHA between each.
    Returns True on success, False if all attempts are exhausted.
    """
    print(f"\n[login] Autonomous login — up to {MAX_LOGIN_ATTEMPTS} attempts")

    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        print(f"  [login] Attempt {attempt}/{MAX_LOGIN_ATTEMPTS}")

        if attempt > 1:
            refresh = await _find(page, [
                "img[src*='refresh']", "img[src*='reload']",
                "a[onclick*='captcha']", ".captcha-refresh", "#captchaRefresh",
            ], visible_check=False)
            if refresh:
                await refresh.click()
                await page.wait_for_timeout(1000)
            else:
                await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
                await page.wait_for_timeout(800)

        # Screenshot CAPTCHA image only
        captcha_img = await _find(page, [
            "img[src*='captcha']", "img[src*='Captcha']", "img[src*='kaptcha']",
            "img[id*='captcha']", "img[id*='Captcha']", "img[class*='captcha']",
            "img[alt*='aptcha']", "form img",
        ])
        if captcha_img is None:
            print("  [login] CAPTCHA image not found — retrying")
            continue

        img_bytes = await captcha_img.screenshot()
        captcha_text = _solve_captcha(img_bytes)
        print(f"  [login] CAPTCHA solved: {captcha_text}")

        # Fill form: Dealer role → username → password → CAPTCHA → submit
        await page.wait_for_timeout(800)
        try:
            native = page.locator("select").first
            if await native.count() > 0 and await native.is_visible(timeout=2000):
                await native.select_option(label="Dealer")
            else:
                combo = page.locator("div[role='combobox'], .MuiSelect-select").first
                if await combo.count() > 0 and await combo.is_visible(timeout=2000):
                    await combo.click()
                    await page.wait_for_timeout(500)
                    dealer = page.locator("li[role='option']:has-text('Dealer')").first
                    await dealer.wait_for(state="visible", timeout=3000)
                    await dealer.click()
        except Exception:
            pass

        await page.wait_for_timeout(600)

        for sel in ["input[name='username']", "input[name='userId']",
                    "input[placeholder*='Username']", "input[placeholder*='User']"]:
            loc = page.locator(sel).first
            try:
                if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                    await loc.fill(IRAS_USERNAME)
                    break
            except Exception:
                continue

        pw = page.locator("input[type='password']").first
        try:
            if await pw.count() > 0 and await pw.is_visible(timeout=2000):
                await pw.fill(IRAS_PASSWORD)
        except Exception:
            pass

        cap_input = await _find(page, [
            "input[name*='captcha']", "input[name*='Captcha']",
            "input[id*='captcha']", "input[id*='Captcha']",
            "input[placeholder*='aptcha']",
        ])
        if cap_input is None:
            # Last resort: last visible text input (captcha field is usually last)
            inputs = page.locator("input[type='text']:visible")
            count = await inputs.count()
            if count > 0:
                cap_input = inputs.nth(count - 1)
        if cap_input:
            await cap_input.fill(captcha_text)

        submit = await _find(page, [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Login')", "button:has-text('Sign In')",
            "[role='button']:has-text('Login')",
        ])
        if submit:
            await submit.click()
        else:
            await page.keyboard.press("Enter")

        await page.wait_for_timeout(3000)

        # Check success: URL no longer contains /login
        try:
            await page.wait_for_function(
                "() => !window.location.href.includes('/login')", timeout=5000)
            print("  [login] SUCCESS")
            await page.wait_for_timeout(2000)
            return True
        except PlaywrightTimeout:
            pass

        if "/login" not in page.url:
            print("  [login] SUCCESS")
            await page.wait_for_timeout(2000)
            return True

        print(f"  [login] Failed — CAPTCHA was: {captcha_text}")

    print(f"[login] FAILED after {MAX_LOGIN_ATTEMPTS} attempts")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# JOB 0 — PAYTM PAYMENT REPORT
# ─────────────────────────────────────────────────────────────────────────────

async def _job_paytm(dry_run: bool = False, target_date: str | None = None, paytm_wait_seconds: int | None = None, paytm_debug: bool = False) -> bool:
    """
    Download Paytm payment transaction CSV.

    Runs in its own browser context (independent of the IRAS session).
    Skipped silently when PAYTM_EMAIL / PAYTM_PASSWORD are not configured.
    target_date: YYYY-MM-DD accounting op_date. If None, uses implicit yesterday.

    Returns True if completed successfully (download + import, or dry-run download ok).
    Returns False if credentials are missing, download failed, or DB import failed.
    """
    label = f" for {target_date}" if target_date else " (yesterday)"
    print(f"\n{'='*55}")
    print(f"  JOB 0 — Paytm Payment Report{label}")
    print(f"{'='*55}")

    if not PAYTM_EMAIL or not PAYTM_PASSWORD:
        print("  [SKIP] PAYTM_EMAIL or PAYTM_PASSWORD not set in .env")
        return False

    success = await _ptm.run(target_date=target_date, poll_timeout=paytm_wait_seconds, debug=paytm_debug)
    if not success:
        print("  [WARN] Paytm download failed — continuing with remaining jobs")
        return False

    if dry_run:
        print("  [dry-run] DB import skipped — Paytm CSV downloaded but not written to DB")
        return True

    # Import the downloaded CSV into the DB automatically
    try:
        from pumpvision import create_app as _create_app
        from pumpvision.models import db as _db, PaytmTransaction as _PT
        from pumpvision.blueprints.paytm.routes import _parse_paytm_csv

        if target_date is not None:
            op_date = datetime.strptime(target_date, "%Y-%m-%d").date()
        else:
            op_date, _, _ = _ptm.get_op_day_range()
        csv_path = _ptm.OUTPUT_DIR / f"paytm_{op_date.strftime('%Y-%m-%d')}.csv"
        if not csv_path.exists():
            print("  [WARN] Paytm CSV not found after download — skipping DB import")
            return False

        _app = _create_app()
        with _app.app_context():
            with open(csv_path, "rb") as f:
                records, warnings = _parse_paytm_csv(f)
            inserted = skipped = 0
            for rec in records:
                if _db.session.query(_PT).filter_by(paytm_txn_id=rec["paytm_txn_id"]).first():
                    skipped += 1
                else:
                    _db.session.add(_PT(**rec))
                    inserted += 1
            _db.session.commit()
            print(f"  [db] Paytm import: {inserted} new, {skipped} already in DB")
            if warnings:
                print(f"  [db] {len(warnings)} parse warnings")
        return True
    except Exception as e:
        print(f"  [WARN] Paytm DB import failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# JOB 5 — ATG STOCK SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

async def _job_atg(page, dry_run: bool = False):
    """
    Scrape the current ATG tank level snapshot from FCC Data > Stock.

    Runs inside the existing IRAS browser session (after ISS, before close)
    so no additional CAPTCHA solve is needed.
    Writes one TankReading row per tank to the database.
    """
    atg_dir = _data_root / "ATG"
    await _atg.run_atg(page, output_dir=atg_dir, dry_run=dry_run)


# ─────────────────────────────────────────────────────────────────────────────
# JOB 4 — SDMS PAD STATEMENT
# ─────────────────────────────────────────────────────────────────────────────

async def _job_sdms(dry_run: bool = False, target_date: str | None = None) -> bool:
    """
    Download SDMS PAD Statement and compute fleet card posting total.

    Runs in its own persistent browser context (independent of the IRAS session).
    Skipped silently when SDMS_USERNAME / SDMS_PASSWORD are not configured.
    Outputs: data/sdms/sdms_pad_YYYY-MM-DD.csv + _summary.json
    target_date: YYYY-MM-DD accounting op_date. If None, uses implicit yesterday.
    dry_run=True: download and parse, but skip DB write.

    Returns True if completed successfully, False if credentials missing or download failed.
    """
    label = f" for {target_date}" if target_date else " (yesterday)"
    print(f"\n{'='*55}")
    print(f"  JOB 4 — SDMS PAD Statement{label}")
    print(f"{'='*55}")

    if not SDMS_USERNAME or not SDMS_PASSWORD:
        print("  [SKIP] SDMS_USERNAME or SDMS_PASSWORD not set in .env")
        return False

    success = await _sdms.run(dry_run=dry_run, target_date=target_date)
    if not success:
        print("  [WARN] SDMS download failed — daily scrape continues")
        return False
    return True


# ─────────────────────────────────────────────────────────────────────────────
# JOB 1 — SHIFT TOTALIZER
# ─────────────────────────────────────────────────────────────────────────────

async def _job_shift_totalizer(page, op_dates: list[str], st_dir: Path):
    """
    Download Shift Totalizer for every op_date in the list.

    op_date = shift_date - 1 calendar day. The ST file covers midnight-to-midnight
    on op_date, which is exactly the data needed by the XG pre-check and the
    OOO nozzle pre-check inside run_boundary().

    Uses download_all_shift_totalizers() from the ISS module — it navigates to
    the ST tab once, batches all downloads, and skips files already on disk.
    """
    print(f"\n{'='*55}")
    print(f"  JOB 2 — Shift Totalizer")
    print(f"  op_dates : {op_dates}")
    print(f"{'='*55}")

    st_dir.mkdir(parents=True, exist_ok=True)
    await _iss.download_all_shift_totalizers(page, st_dir, op_dates)


def _save_prices_to_db(records: list[dict]) -> bool:
    """
    Upsert IRAS Price (PRM) records into the iras_prices table.

    Returns True if all records were persisted successfully.
    Returns False if records is empty or a DB exception occurred.
    Logs: downloaded file path is shown by _job_price(); this function logs
    inserted/skipped counts and any DB error.
    """
    if not records:
        print("  [db] No price records to save.")
        return False
    try:
        from pumpvision import create_app
        from pumpvision.models import IrasPrice, db

        app = create_app()
        with app.app_context():
            saved = 0
            for r in records:
                existing = IrasPrice.query.filter_by(
                    product=r["product"],
                    effective_from=r["effective_from"],
                ).first()
                if existing:
                    existing.rate_per_litre = r["rate_per_litre"]
                    existing.effective_to = r["effective_to"]
                else:
                    db.session.add(IrasPrice(
                        product=r["product"],
                        rate_per_litre=r["rate_per_litre"],
                        effective_from=r["effective_from"],
                        effective_to=r["effective_to"],
                    ))
                    saved += 1
            db.session.commit()
            print(f"  [db] Saved {saved} new price record(s) to iras_prices "
                  f"({len(records) - saved} already existed)")
        return True
    except Exception as e:
        print(f"  [db] ERROR saving prices: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# BOUNDARY COMPLETENESS CHECK  (used by completed-shift mode)
# ─────────────────────────────────────────────────────────────────────────────

# All six liquid-fuel nozzle numbers that must be present for a boundary to be
# considered complete.  Matches the CLAUDE.md hardware spec exactly.
_EXPECTED_NOZZLES: frozenset[int] = frozenset({7, 11, 15, 16, 17, 18})


def _boundary_status(shift_date: str) -> tuple[str, frozenset, frozenset]:
    """
    Check completeness of the 06:00 boundary for shift_date in nozzle_totalizers.

    Returns (status, present_nozzles, missing_nozzles) where status is one of:
      'COMPLETE'   — all expected nozzles (7, 11, 15, 16, 17, 18) have rows.
      'INCOMPLETE' — at least one nozzle row exists but at least one is missing.
      'MISSING'    — no rows at all for this operational_date.

    Uses a direct read-only SQLAlchemy Core connection — does NOT call create_app(),
    so it never triggers db.create_all(), Alembic upgrade(), or seed logic.
    Safe to call in --dry-run.  postgres:// → postgresql:// normalisation applied
    to match the app factory behaviour.  Falls back to the absolute SQLite instance
    path when DATABASE_URL is not set.
    """
    import sqlalchemy as _sa

    _instance_db = (_PROJECT_ROOT / "instance" / "pumpvision.db").as_posix()
    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{_instance_db}")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)

    try:
        _d   = datetime.strptime(shift_date, "%Y-%m-%d").date()
        eng  = _sa.create_engine(db_url, pool_pre_ping=False)
        meta = _sa.MetaData()
        nt   = _sa.Table(
            "nozzle_totalizers", meta,
            _sa.Column("operational_date", _sa.Date),
            _sa.Column("nozzle_no",        _sa.Integer),
        )
        with eng.connect() as conn:
            rows = conn.execute(
                _sa.select(nt.c.nozzle_no).where(nt.c.operational_date == _d)
            ).fetchall()
        present = frozenset(r.nozzle_no for r in rows)
        missing = _EXPECTED_NOZZLES - present
        if not present:
            return ('MISSING',    frozenset(),  _EXPECTED_NOZZLES)
        if not missing:
            return ('COMPLETE',   present,      frozenset())
        return     ('INCOMPLETE', present,      missing)
    except Exception as e:
        print(f"  [db] WARNING: could not check nozzle_totalizers for {shift_date}: {e}")
        # Treat as MISSING so the boundary scrape is attempted rather than silently skipped.
        return ('MISSING', frozenset(), _EXPECTED_NOZZLES)


def _status_label(stat: str, missing: frozenset) -> str:
    """Format a boundary completeness status for log output."""
    if stat == 'COMPLETE':
        return "[COMPLETE — will skip]"
    if stat == 'INCOMPLETE':
        return f"[INCOMPLETE — missing nozzles {sorted(missing)} — will scrape]"
    return "[MISSING — will scrape]"


# ─────────────────────────────────────────────────────────────────────────────
# ACCOUNTING SOURCE EXISTENCE CHECKS  (used by accounting / completed-shift /
# single-source modes to skip sources already present in the DB)
# ─────────────────────────────────────────────────────────────────────────────
# All three functions use the same read-only SQLAlchemy Core pattern as
# _boundary_status: no create_app(), no migrations, no seed logic.
# Returns 'COMPLETE' (all expected data present), 'INCOMPLETE' (partial),
# or 'MISSING' (no rows / DB unreachable).
# Anything other than 'COMPLETE' causes the source to be re-scraped.

# The four liquid-fuel products that must ALL have IrasPrice rows for a date to
# be considered COMPLETE.  CNG does not appear in the IRAS Price table.
_EXPECTED_PRICE_PRODUCTS: frozenset[str] = frozenset({'HS', 'MS', 'X2', 'XG'})

def _acct_status_paytm(acct_date: str) -> str:
    """
    Return 'COMPLETE' if PaytmTransaction rows already exist for acct_date
    (matched on operational_date column), 'MISSING' otherwise.
    """
    import sqlalchemy as _sa

    _instance_db = (_PROJECT_ROOT / "instance" / "pumpvision.db").as_posix()
    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{_instance_db}")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    try:
        _d   = datetime.strptime(acct_date, "%Y-%m-%d").date()
        eng  = _sa.create_engine(db_url, pool_pre_ping=False)
        meta = _sa.MetaData()
        pt   = _sa.Table("paytm_transactions", meta,
                         _sa.Column("operational_date", _sa.Date))
        with eng.connect() as conn:
            count = conn.execute(
                _sa.select(_sa.func.count()).select_from(pt)
                   .where(pt.c.operational_date == _d)
            ).scalar()
        if count and count > 0:
            print(f"  [db] Paytm  {acct_date}: {count} row(s) already in DB — COMPLETE")
            return 'COMPLETE'
        print(f"  [db] Paytm  {acct_date}: no rows in paytm_transactions — MISSING")
        return 'MISSING'
    except Exception as e:
        print(f"  [db] WARNING: could not check paytm_transactions for {acct_date}: {e}")
        return 'MISSING'


def _acct_status_price(acct_date: str) -> str:
    """
    Return 'COMPLETE' if all four liquid-fuel products (HS, MS, X2, XG) have
    IrasPrice rows covering acct_date.

    A price row covers acct_date if its effective_from falls in the window
    [acct_date 06:00:00, (acct_date + 1) 06:00:00).

    Distinct product codes in that window are compared against
    _EXPECTED_PRICE_PRODUCTS = {'HS', 'MS', 'X2', 'XG'}.  Duplicate rows for
    the same product are collapsed by the DISTINCT query and do not falsely
    inflate the completeness check.

    Returns:
      'COMPLETE'   — all four products present.
      'INCOMPLETE' — at least one product row exists but at least one is missing.
      'MISSING'    — no rows in the window at all, or DB unreachable.

    Anything other than 'COMPLETE' causes the Price job to run.
    """
    import sqlalchemy as _sa
    from datetime import time as _time

    _instance_db = (_PROJECT_ROOT / "instance" / "pumpvision.db").as_posix()
    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{_instance_db}")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    try:
        _d      = datetime.strptime(acct_date, "%Y-%m-%d").date()
        _win_lo = datetime.combine(_d,                     _time(6, 0, 0))
        _win_hi = datetime.combine(_d + timedelta(days=1), _time(6, 0, 0))
        eng  = _sa.create_engine(db_url, pool_pre_ping=False)
        meta = _sa.MetaData()
        ip   = _sa.Table("iras_prices", meta,
                         _sa.Column("product",        _sa.String(5)),
                         _sa.Column("effective_from", _sa.DateTime))
        with eng.connect() as conn:
            rows = conn.execute(
                _sa.select(_sa.distinct(ip.c.product)).where(
                    (ip.c.effective_from >= _win_lo) & (ip.c.effective_from < _win_hi)
                )
            ).fetchall()
        present = frozenset(r[0] for r in rows)
        missing = _EXPECTED_PRICE_PRODUCTS - present
        if not present:
            print(f"  [db] Price  {acct_date}: no rows in iras_prices — MISSING")
            return 'MISSING'
        if not missing:
            print(f"  [db] Price  {acct_date}: all products present {sorted(present)} — COMPLETE")
            return 'COMPLETE'
        print(f"  [db] Price  {acct_date}: partial — present={sorted(present)} "
              f"missing={sorted(missing)} — INCOMPLETE")
        return 'INCOMPLETE'
    except Exception as e:
        print(f"  [db] WARNING: could not check iras_prices for {acct_date}: {e}")
        return 'MISSING'


def _acct_status_sdms(acct_date: str) -> str:
    """
    Return 'COMPLETE' if an SdmsSummary row already exists for acct_date
    (matched on op_date column), 'MISSING' otherwise.
    """
    import sqlalchemy as _sa

    _instance_db = (_PROJECT_ROOT / "instance" / "pumpvision.db").as_posix()
    db_url = os.environ.get("DATABASE_URL", f"sqlite:///{_instance_db}")
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    try:
        _d   = datetime.strptime(acct_date, "%Y-%m-%d").date()
        eng  = _sa.create_engine(db_url, pool_pre_ping=False)
        meta = _sa.MetaData()
        ss   = _sa.Table("sdms_summaries", meta,
                         _sa.Column("op_date", _sa.Date))
        with eng.connect() as conn:
            count = conn.execute(
                _sa.select(_sa.func.count()).select_from(ss)
                   .where(ss.c.op_date == _d)
            ).scalar()
        if count and count > 0:
            print(f"  [db] SDMS   {acct_date}: row already in DB — COMPLETE")
            return 'COMPLETE'
        print(f"  [db] SDMS   {acct_date}: no row in sdms_summaries — MISSING")
        return 'MISSING'
    except Exception as e:
        print(f"  [db] WARNING: could not check sdms_summaries for {acct_date}: {e}")
        return 'MISSING'


# ─────────────────────────────────────────────────────────────────────────────
# JOB 1 — PRICE (PRM)
# ─────────────────────────────────────────────────────────────────────────────

async def _job_price(page, shift_dates: list[str], price_dir: Path, dry_run: bool = False,
                     acct_dates: list[str] | None = None) -> bool:
    """
    Download Price (PRM) for exactly the op day(s) being reconciled.

    boundary/all mode (acct_dates=None): op_date = shift_date - 1 for each shift_date.
    accounting mode (acct_dates provided): op_dates used directly — these are shift start
    dates so no -1 derivation is needed.

    RSP is pushed by IOC at 06:00 each day. A single Price Excel covers the full range;
    we download only the exact op dates needed.

    Returns True if the file was downloaded and (for non-dry-run) DB save succeeded.
    Returns False if download failed or DB save failed. Dry-run with a successful download
    returns True (DB write intentionally skipped).
    """
    print(f"\n{'='*55}")
    print(f"  JOB 1 — Price (PRM)")
    print(f"{'='*55}")

    price_dir.mkdir(parents=True, exist_ok=True)

    if acct_dates is not None:
        # accounting mode: supplied dates ARE the op_dates (shift start dates)
        op_dates_for_price = acct_dates
    else:
        # boundary/all mode: op_date = shift_date - 1
        op_dates_for_price = [
            (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            for d in shift_dates
        ]
    from_date = min(op_dates_for_price)
    to_date   = max(op_dates_for_price)

    print(f"  Range: {from_date} → {to_date}")

    await _prm.navigate_to_price(page)
    fpath = await _prm.export_price_range(page, price_dir, from_date, to_date)

    if fpath:
        records = _prm.parse_price_file(fpath)
        print(f"  [PRM] {len(records)} price record(s) in {fpath}")
        _prm.print_price_summary(records)
        if dry_run:
            print(f"  [dry-run] DB write skipped — would have saved {len(records)} price record(s)")
            return True
        db_ok = _save_prices_to_db(records)
        if not db_ok:
            print("  [PRM] WARNING: Price file downloaded but DB save failed")
        return db_ok
    else:
        print("  [PRM] WARNING: Price download failed")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# JOB 3 — ISS BOUNDARY MODE
# ─────────────────────────────────────────────────────────────────────────────

async def _job_iss_boundary(page, shift_dates: list[str], iss_dir: Path, dry_run: bool = False):
    """
    Run ISS boundary mode for each shift date and save results to DB.

    Navigates to the ISS tab once, then loops over shift_dates. For each date:
      - XG pre-check reads the ST file already on disk (downloaded in Job 1)
      - OOO nozzle pre-check does the same — no extra downloads needed
      - ISS backward search only fires for nozzles not resolved by the pre-checks
      - Results are written to the NozzleTotalizer table via save_totalizers_to_db()

    The archive toggle is managed per-date inside run_boundary() via
    ensure_iss_archive_mode(), so batch backfills across the 7-day boundary work.
    """
    print(f"\n{'='*55}")
    print(f"  JOB 3 — ISS Boundary Mode")
    print(f"  shift_dates : {shift_dates}")
    print(f"{'='*55}")

    iss_dir.mkdir(parents=True, exist_ok=True)

    # Navigate to ISS once — subsequent dates stay in the same tab
    await _iss.navigate_to_iss(page, shift_date=shift_dates[0])

    for shift_date in shift_dates:
        print(f"\n  [{shift_date}] running boundary mode...")
        totalizers, xg_check = await _iss.run_boundary(page, iss_dir, shift_date)

        if totalizers:
            print(f"\n  [ISS] 6AM totalizers for {shift_date}:")
            for nozzle in sorted(totalizers):
                print(f"         Nozzle {nozzle}: {totalizers[nozzle]}")
            if dry_run:
                print(f"  [dry-run] DB write skipped — would have saved {len(totalizers)} rows for {shift_date}")
            else:
                _iss.save_totalizers_to_db(shift_date, totalizers, xg_check)
        else:
            print(f"  [ISS] WARNING: No totalizer data resolved for {shift_date}")


# ─────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ─────────────────────────────────────────────────────────────────────────────

async def run(dates: list[str], dry_run: bool = False, mode: str = 'all',
              paytm_wait_seconds: int | None = None, paytm_debug: bool = False) -> bool:
    """
    Main orchestration entry point.

    mode='all'              Daily run — all jobs, existing behavior preserved.
                            dates = shift/boundary dates (boundary at 06:00 on each date).
    mode='boundary'         IRAS boundary jobs only (Price, ST, ISS). No Paytm/SDMS/ATG.
                            dates = shift/boundary dates.
    mode='accounting'       Paytm, Price (PRM), SDMS. IRAS login required for Price.
                            Existence checks: skips sources already in DB.
                            Failure isolation: IRAS login failure does not block SDMS.
                            dates = accounting op_dates (shift start dates).
    mode='paytm_only'       Paytm only. Skips if rows already exist for the date.
                            No IRAS login, no SDMS. dates = accounting op_dates.
    mode='price_only'       IRAS Price (PRM) only. Skips if rows already exist.
                            dates = accounting op_dates.
    mode='sdms_only'        SDMS PAD only. Skips if row already exists for the date.
                            No IRAS login. dates = accounting op_dates.
    mode='atg'              Current ATG snapshot only. dates ignored.
    mode='completed_shift'  Full completed-shift: checks opening (D) and closing (D+1)
                            boundaries in DB; scrapes only the missing ones. Then
                            Paytm, Price, SDMS for accounting op_date D.
                            Existence checks: skips accounting sources already in DB.
                            Failure isolation: IRAS login failure does not block SDMS.
                            ATG excluded — tank stock is live/current, not historical.
                            dates = accounting op_dates (shift start dates).

    Date semantics:
      shift/boundary date D → totalizer boundary captured at 06:00 on D.
      accounting op_date D  → shift window D 06:00 → D+1 05:59.
                              Paytm, Price, and SDMS are all scraped for this window.
    """
    shift_dates = dates  # clear alias — only meaningful for boundary/all modes

    # Derive op_dates (shift_date − 1) for Shift Totalizer downloads
    op_dates = [
        (datetime.strptime(d, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        for d in shift_dates
    ] if mode in ('all', 'boundary') else []

    # Output directories for IRAS file downloads
    iss_dir   = ISS_DIR
    st_dir    = ST_DIR
    price_dir = PRICE_DIR

    # In dry-run mode, redirect IRAS output to a temp dir so skip-if-exists
    # checks don't trigger and every download runs fresh.
    # Price runs in all, boundary, accounting, completed_shift, and price_only modes.
    # ISS/ST dirs also needed for completed_shift (may scrape missing boundaries).
    if dry_run:
        _dry_root = _data_root / "_dry_run"
        price_dir = _dry_root / "Price"
        if mode in ('all', 'boundary', 'completed_shift'):
            iss_dir   = _dry_root / "ISS"
            st_dir    = _dry_root / "ShiftTotalizer"
            _iss.OUTPUT_FOLDER          = str(iss_dir)
            _iss.SHIFT_TOTALIZER_FOLDER = str(st_dir)

    # ── Completed-shift: pre-check boundary DB status ────────────────────────
    # Query once before the header so the same results are used in both logging
    # and execution without double-querying. Safe in dry-run (read-only).
    _cs_status: dict[str, tuple] = {}  # shift_date → (status, present, missing)
    if mode == 'completed_shift':
        for _d in dates:
            _cl = (datetime.strptime(_d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            _cs_status[_d]  = _boundary_status(_d)
            _cs_status[_cl] = _boundary_status(_cl)

    # ── Accounting source existence checks ───────────────────────────────────
    # Run before the header log so status can be displayed upfront.
    # Each check is a read-only SQLAlchemy Core query — no create_app(), safe in dry-run.
    # Key: (source, acct_date) → 'COMPLETE' | 'MISSING'
    _src_db: dict[tuple[str, str], str] = {}
    _acct_modes = ('accounting', 'completed_shift', 'paytm_only', 'price_only', 'sdms_only')
    if mode in _acct_modes:
        for _d in dates:
            if mode in ('accounting', 'completed_shift', 'paytm_only'):
                _src_db[('paytm', _d)] = _acct_status_paytm(_d)
            if mode in ('accounting', 'completed_shift', 'price_only'):
                _src_db[('price', _d)] = _acct_status_price(_d)
            if mode in ('accounting', 'completed_shift', 'sdms_only'):
                _src_db[('sdms',  _d)] = _acct_status_sdms(_d)

    # ── Accounting results tracking ──────────────────────────────────────────
    # Key: (source, acct_date) → 'succeeded' | 'skipped' | 'failed'
    # Populated only for modes that run accounting sources.
    _acct_results: dict[tuple[str, str], str] = {}

    # ── Header log ───────────────────────────────────────────────────────────
    print()
    print("=" * 55)
    print("  IRAS Daily Scrape Orchestrator")
    if dry_run:
        print("  *** DRY RUN — fresh downloads, no DB writes ***")
    if mode == 'all':
        print(f"  Mode          : all (daily)")
        print(f"  Shift dates   : {shift_dates}  ← boundary at 06:00 each date")
        print(f"  Op dates (ST) : {op_dates}")
        print(f"  ISS output    : {iss_dir}")
        print(f"  ST output     : {st_dir}")
        print(f"  Price output  : {price_dir}")
    elif mode == 'boundary':
        print(f"  Mode          : boundary-only  (Price, ST, ISS — no Paytm/SDMS/ATG)")
        print(f"  Boundary dates: {shift_dates}  ← captures 06:00 totalizer rows")
        print(f"  Op dates (ST) : {op_dates}")
    elif mode == 'accounting':
        print(f"  Mode              : accounting-only  (Paytm, Price, SDMS)")
        for _d in dates:
            _next = (datetime.strptime(_d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            print(f"  Accounting op_date: {_d}  (shift window: {_d} 06:00 → {_next} 05:59)")
            _lp = _src_db.get(('paytm', _d), 'MISSING')
            _lr = _src_db.get(('price', _d), 'MISSING')
            _ls = _src_db.get(('sdms',  _d), 'MISSING')
            print(f"    Paytm={_lp}  Price={_lr}  SDMS={_ls}")
    elif mode == 'paytm_only':
        print(f"  Mode              : paytm-only")
        for _d in dates:
            print(f"  Accounting op_date: {_d}  Paytm={_src_db.get(('paytm', _d), 'MISSING')}")
    elif mode == 'price_only':
        print(f"  Mode              : price-only")
        for _d in dates:
            print(f"  Accounting op_date: {_d}  Price={_src_db.get(('price', _d), 'MISSING')}")
    elif mode == 'sdms_only':
        print(f"  Mode              : sdms-only")
        for _d in dates:
            print(f"  Accounting op_date: {_d}  SDMS={_src_db.get(('sdms', _d), 'MISSING')}")
    elif mode == 'completed_shift':
        print(f"  Mode               : completed-shift  (boundaries + Paytm + Price + SDMS, no ATG)")
        for _d in dates:
            _cl = (datetime.strptime(_d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            _o_stat, _, _o_miss = _cs_status.get(_d,  ('MISSING', frozenset(), _EXPECTED_NOZZLES))
            _c_stat, _, _c_miss = _cs_status.get(_cl, ('MISSING', frozenset(), _EXPECTED_NOZZLES))
            print(f"  Accounting op_date : {_d}  (shift window: {_d} 06:00 → {_cl} 05:59)")
            print(f"  Opening boundary   : {_d}    {_status_label(_o_stat, _o_miss)}")
            print(f"  Closing boundary   : {_cl}  {_status_label(_c_stat, _c_miss)}")
            _lp = _src_db.get(('paytm', _d), 'MISSING')
            _lr = _src_db.get(('price', _d), 'MISSING')
            _ls = _src_db.get(('sdms',  _d), 'MISSING')
            print(f"  Acct sources (DB)  : Paytm={_lp}  Price={_lr}  SDMS={_ls}")
            if len(dates) > 1:
                print()
        print(f"  ATG                : SKIPPED — tank stock is live/current, not historical shift data")
    elif mode == 'atg':
        print(f"  Mode: atg-only  (current snapshot — not date-specific, dates ignored)")
    print("=" * 55)

    # ── Job 0: Paytm — all mode (implicit yesterday, preserves daily behavior) ─
    if mode == 'all':
        await _job_paytm(dry_run=dry_run,
                         paytm_wait_seconds=paytm_wait_seconds, paytm_debug=paytm_debug)

    # ── IRAS browser session (boundary, atg, all modes) ──────────────────────
    if mode in ('all', 'boundary', 'atg'):
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = await browser.new_context(
                accept_downloads=True,
                viewport={"width": 1400, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()

            print(f"\n[step 0] Loading: {LOGIN_URL}")
            await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
            await page.wait_for_timeout(1000)

            if not await _autonomous_login(page):
                print("\nABORTED — login failed after all attempts.")
                await browser.close()
                return False

            if mode in ('all', 'boundary'):
                # Job 1: Price (PRM) — RSP for the op day(s) being reconciled
                await _job_price(page, shift_dates, price_dir, dry_run=dry_run)
                # Job 2: Shift Totalizer — must precede ISS (ISS reads ST from disk)
                await _job_shift_totalizer(page, op_dates, st_dir)
                # Job 3: ISS boundary → NozzleTotalizer DB rows
                await _job_iss_boundary(page, shift_dates, iss_dir, dry_run=dry_run)

            if mode in ('all', 'atg'):
                await _job_atg(page, dry_run=dry_run)

            await browser.close()

    # ── Job 4: SDMS PAD — all mode (implicit yesterday) ──────────────────────
    if mode == 'all':
        await _job_sdms(dry_run=dry_run)

    # ─────────────────────────────────────────────────────────────────────────
    # SINGLE-SOURCE MODES: paytm_only / price_only / sdms_only
    # ─────────────────────────────────────────────────────────────────────────

    # ── paytm-only ────────────────────────────────────────────────────────────
    if mode == 'paytm_only':
        for acct_date in dates:
            if _src_db.get(('paytm', acct_date)) == 'COMPLETE':
                print(f"\n  [SKIP] Paytm {acct_date}: already in DB")
                _acct_results[('paytm', acct_date)] = 'skipped'
            else:
                ok = await _job_paytm(dry_run=dry_run, target_date=acct_date,
                                      paytm_wait_seconds=paytm_wait_seconds,
                                      paytm_debug=paytm_debug)
                _acct_results[('paytm', acct_date)] = 'succeeded' if ok else 'failed'

    # ── price-only ────────────────────────────────────────────────────────────
    if mode == 'price_only':
        _price_needed  = [d for d in dates if _src_db.get(('price', d)) != 'COMPLETE']
        _price_skipped = [d for d in dates if _src_db.get(('price', d)) == 'COMPLETE']
        for d in _price_skipped:
            print(f"\n  [SKIP] Price {d}: already in DB")
            _acct_results[('price', d)] = 'skipped'
        if _price_needed:
            _iras_env_missing = [v for v in ("IRAS_URL", "IRAS_USERNAME", "IRAS_PASSWORD",
                                              "ANTHROPIC_API_KEY") if not os.environ.get(v)]
            if _iras_env_missing:
                print(f"\n  [WARN] IRAS credentials not configured "
                      f"({', '.join(_iras_env_missing)}) — Price cannot run.")
                for d in _price_needed:
                    _acct_results[('price', d)] = 'failed'
            else:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(
                        accept_downloads=True, viewport={"width": 1400, "height": 900},
                        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/124.0.0.0 Safari/537.36"))
                    page = await context.new_page()
                    print(f"\n[step 0] Loading: {LOGIN_URL}")
                    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
                    await page.wait_for_timeout(1000)
                    if not await _autonomous_login(page):
                        print("\n  [WARN] IRAS login failed — price-only run cannot complete.")
                        for d in _price_needed:
                            _acct_results[('price', d)] = 'failed'
                        await browser.close()
                    else:
                        price_ok = await _job_price(page, [], price_dir, dry_run=dry_run,
                                                    acct_dates=_price_needed)
                        _st = 'succeeded' if price_ok else 'failed'
                        for d in _price_needed:
                            _acct_results[('price', d)] = _st
                        await browser.close()

    # ── sdms-only ─────────────────────────────────────────────────────────────
    if mode == 'sdms_only':
        for acct_date in dates:
            if _src_db.get(('sdms', acct_date)) == 'COMPLETE':
                print(f"\n  [SKIP] SDMS {acct_date}: already in DB")
                _acct_results[('sdms', acct_date)] = 'skipped'
            else:
                ok = await _job_sdms(dry_run=dry_run, target_date=acct_date)
                _acct_results[('sdms', acct_date)] = 'succeeded' if ok else 'failed'

    # ─────────────────────────────────────────────────────────────────────────
    # ACCOUNTING MODE (--accounting-only)
    # Paytm, Price, SDMS — with existence checks and IRAS failure isolation.
    # ─────────────────────────────────────────────────────────────────────────
    if mode == 'accounting':
        # ── Paytm ─────────────────────────────────────────────────────────────
        for acct_date in dates:
            if _src_db.get(('paytm', acct_date)) == 'COMPLETE':
                print(f"\n  [SKIP] Paytm {acct_date}: already in DB")
                _acct_results[('paytm', acct_date)] = 'skipped'
            else:
                ok = await _job_paytm(dry_run=dry_run, target_date=acct_date,
                                      paytm_wait_seconds=paytm_wait_seconds,
                                      paytm_debug=paytm_debug)
                _acct_results[('paytm', acct_date)] = 'succeeded' if ok else 'failed'

        # ── Price — IRAS session; only opened if any date is missing ──────────
        _price_needed  = [d for d in dates if _src_db.get(('price', d)) != 'COMPLETE']
        _price_skipped = [d for d in dates if _src_db.get(('price', d)) == 'COMPLETE']
        for d in _price_skipped:
            _acct_results[('price', d)] = 'skipped'
        if _price_needed:
            _iras_env_missing = [v for v in ("IRAS_URL", "IRAS_USERNAME", "IRAS_PASSWORD",
                                              "ANTHROPIC_API_KEY") if not os.environ.get(v)]
            if _iras_env_missing:
                print(f"\n  [WARN] IRAS credentials not configured "
                      f"({', '.join(_iras_env_missing)}) — Price skipped; continuing to SDMS.")
                for d in _price_needed:
                    _acct_results[('price', d)] = 'failed'
            else:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(
                        accept_downloads=True, viewport={"width": 1400, "height": 900},
                        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/124.0.0.0 Safari/537.36"))
                    page = await context.new_page()
                    print(f"\n[step 0] Loading: {LOGIN_URL}")
                    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
                    await page.wait_for_timeout(1000)
                    if not await _autonomous_login(page):
                        print("\n  [WARN] IRAS login failed — Price skipped; continuing to SDMS.")
                        for d in _price_needed:
                            _acct_results[('price', d)] = 'failed'
                        await browser.close()
                    else:
                        price_ok = await _job_price(page, [], price_dir, dry_run=dry_run,
                                                    acct_dates=_price_needed)
                        _st = 'succeeded' if price_ok else 'failed'
                        for d in _price_needed:
                            _acct_results[('price', d)] = _st
                        await browser.close()
        else:
            print(f"\n  [SKIP] Price: already in DB for all requested dates")

        # ── SDMS — runs regardless of IRAS outcome ────────────────────────────
        for acct_date in dates:
            if _src_db.get(('sdms', acct_date)) == 'COMPLETE':
                print(f"\n  [SKIP] SDMS {acct_date}: already in DB")
                _acct_results[('sdms', acct_date)] = 'skipped'
            else:
                ok = await _job_sdms(dry_run=dry_run, target_date=acct_date)
                _acct_results[('sdms', acct_date)] = 'succeeded' if ok else 'failed'

    # ─────────────────────────────────────────────────────────────────────────
    # COMPLETED-SHIFT MODE (--completed-shift)
    # Job order: Paytm → IRAS [Price + missing boundaries] → SDMS.
    # One IRAS login covers Price + ST + ISS — no second CAPTCHA solve.
    # ATG intentionally excluded (live snapshot, not historical shift data).
    # IRAS failure does not block SDMS.
    # ─────────────────────────────────────────────────────────────────────────
    if mode == 'completed_shift':
        # ── Paytm ─────────────────────────────────────────────────────────────
        for acct_date in dates:
            if _src_db.get(('paytm', acct_date)) == 'COMPLETE':
                print(f"\n  [SKIP] Paytm {acct_date}: already in DB")
                _acct_results[('paytm', acct_date)] = 'skipped'
            else:
                ok = await _job_paytm(dry_run=dry_run, target_date=acct_date,
                                      paytm_wait_seconds=paytm_wait_seconds,
                                      paytm_debug=paytm_debug)
                _acct_results[('paytm', acct_date)] = 'succeeded' if ok else 'failed'

        # ── Build set of boundary dates still needing scraping ────────────────
        # _cs_status populated before the header log. COMPLETE = skip; anything else = scrape.
        _complete = frozenset(bd for bd, tup in _cs_status.items() if tup[0] == 'COMPLETE')
        all_needed = sorted({
            bd
            for _d in dates
            for bd in [
                _d,
                (datetime.strptime(_d, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d"),
            ]
            if bd not in _complete
        })

        # ── Price existence check ──────────────────────────────────────────────
        _price_needed  = [d for d in dates if _src_db.get(('price', d)) != 'COMPLETE']
        _price_skipped = [d for d in dates if _src_db.get(('price', d)) == 'COMPLETE']
        for d in _price_skipped:
            _acct_results[('price', d)] = 'skipped'

        # ── IRAS session: needed if Price missing OR any boundary missing ──────
        if _price_needed or all_needed:
            _iras_env_missing = [v for v in ("IRAS_URL", "IRAS_USERNAME", "IRAS_PASSWORD",
                                              "ANTHROPIC_API_KEY") if not os.environ.get(v)]
            if _iras_env_missing:
                print(f"\n  [WARN] IRAS credentials not configured "
                      f"({', '.join(_iras_env_missing)}) "
                      f"— Price and boundary scrapes skipped; continuing to SDMS.")
                for d in _price_needed:
                    _acct_results[('price', d)] = 'failed'
            else:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(
                        headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
                    context = await browser.new_context(
                        accept_downloads=True, viewport={"width": 1400, "height": 900},
                        user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                                    "Chrome/124.0.0.0 Safari/537.36"))
                    page = await context.new_page()
                    print(f"\n[step 0] Loading: {LOGIN_URL}")
                    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
                    await page.wait_for_timeout(1000)
                    if not await _autonomous_login(page):
                        print("\n  [WARN] IRAS login failed — Price and boundary scrapes skipped; continuing to SDMS.")
                        for d in _price_needed:
                            _acct_results[('price', d)] = 'failed'
                        await browser.close()
                    else:
                        # Job 1: Price — for dates not already in DB
                        if _price_needed:
                            price_ok = await _job_price(page, [], price_dir, dry_run=dry_run,
                                                        acct_dates=_price_needed)
                            _st = 'succeeded' if price_ok else 'failed'
                            for d in _price_needed:
                                _acct_results[('price', d)] = _st
                        else:
                            print(f"\n  [SKIP] Price: already in DB for all dates")

                        # Jobs 2 + 3: ST + ISS boundary — only for missing boundaries
                        if all_needed:
                            _needed_op_dates = [
                                (datetime.strptime(bd, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
                                for bd in all_needed
                            ]
                            await _job_shift_totalizer(page, _needed_op_dates, st_dir)
                            await _job_iss_boundary(page, all_needed, iss_dir, dry_run=dry_run)
                        else:
                            print(f"\n{'='*55}")
                            print(f"  Boundaries — all already in DB, ISS/ST scrape skipped")
                            print(f"{'='*55}")

                        await browser.close()
        else:
            print(f"\n  [SKIP] IRAS session: Price and all boundaries already in DB")

        # ── SDMS — runs regardless of IRAS outcome ────────────────────────────
        for acct_date in dates:
            if _src_db.get(('sdms', acct_date)) == 'COMPLETE':
                print(f"\n  [SKIP] SDMS {acct_date}: already in DB")
                _acct_results[('sdms', acct_date)] = 'skipped'
            else:
                ok = await _job_sdms(dry_run=dry_run, target_date=acct_date)
                _acct_results[('sdms', acct_date)] = 'succeeded' if ok else 'failed'

    # ── Final accounting source summary ──────────────────────────────────────
    if _acct_results:
        _status_labels = {
            'succeeded': 'SUCCEEDED',
            'skipped':   'SKIPPED (already in DB)',
            'failed':    'FAILED',
        }
        print(f"\n{'='*55}")
        print(f"  ACCOUNTING SOURCE SUMMARY")
        print(f"{'='*55}")
        _all_dates = sorted({d for (_, d) in _acct_results.keys()})
        for _d in _all_dates:
            print(f"  op_date {_d}:")
            for _src in ('paytm', 'price', 'sdms'):
                _st = _acct_results.get((_src, _d))
                if _st is not None:
                    print(f"    {_src.ljust(8)}: {_status_labels.get(_st, _st.upper())}")
        print(f"{'='*55}")

    # Return False if any requested source failed.
    # Skipped (already in DB) counts as success — no re-work needed.
    if _acct_results and any(v == 'failed' for v in _acct_results.values()):
        print("\n[DONE] One or more sources failed — see ACCOUNTING SOURCE SUMMARY above.")
        return False

    print("\n[DONE] All jobs complete.")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def _paytm_wait_seconds_type(value: str) -> int:
    """argparse type for --paytm-wait-seconds: must be 0 (indefinite) or a positive integer."""
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}")
    if n < 0:
        raise argparse.ArgumentTypeError(
            f"--paytm-wait-seconds requires 0 (wait indefinitely) or a positive integer; got {n}"
        )
    return n


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Daily IRAS orchestrator — one login, Shift Totalizer + Price + ISS."
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help=(
            "Single date. Semantics depend on mode: "
            "--boundary-only = shift boundary date (06:00 on this date captured); "
            "--accounting-only = accounting op_date (completed shift for this calendar day); "
            "default (all) = shift boundary date. Default: today."
        ),
    )
    date_group.add_argument(
        "--dates", nargs="+", metavar="YYYY-MM-DD",
        help="Multiple dates for batch backfill. Same semantics as --date per mode.",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--completed-shift", action="store_true", dest="completed_shift",
        help=(
            "Full completed-shift run: checks opening boundary (--date) and closing "
            "boundary (--date + 1 day) in DB; scrapes only the missing ones. "
            "Then runs Paytm, Price (PRM), SDMS for the accounting op_date. "
            "ATG is excluded — tank stock is live/current; run --atg-only separately. "
            "--date = accounting op_date (shift start date, e.g. 2026-05-20 = "
            "shift window 2026-05-20 06:00 → 2026-05-21 05:59)."
        ),
    )
    mode_group.add_argument(
        "--boundary-only", action="store_true", dest="boundary_only",
        help="IRAS boundary jobs only (Price, ST, ISS). --date = shift boundary date.",
    )
    mode_group.add_argument(
        "--accounting-only", action="store_true", dest="accounting_only",
        help=(
            "Accounting jobs: Paytm, Price (PRM), SDMS. IRAS login required for Price. "
            "--date = accounting op_date (shift start date). "
            "E.g. --date 2026-05-21 covers shift window 2026-05-21 06:00 → 2026-05-22 05:59."
        ),
    )
    mode_group.add_argument(
        "--atg-only", action="store_true", dest="atg_only",
        help="Current ATG snapshot only. --date is ignored.",
    )
    mode_group.add_argument(
        "--paytm-only", action="store_true", dest="paytm_only",
        help=(
            "Paytm only. Skips if PaytmTransaction rows already exist for the date. "
            "No IRAS login, no SDMS. "
            "--date = accounting op_date (shift start date)."
        ),
    )
    mode_group.add_argument(
        "--price-only", action="store_true", dest="price_only",
        help=(
            "IRAS Price (PRM) only. Skips if IrasPrice rows already exist for the date. "
            "--date = accounting op_date (shift start date)."
        ),
    )
    mode_group.add_argument(
        "--sdms-only", action="store_true", dest="sdms_only",
        help=(
            "SDMS PAD only. Skips if SdmsSummary row already exists for the date. "
            "No IRAS login. "
            "--date = accounting op_date (shift start date)."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Download and print results but do not write anything to the database.",
    )
    parser.add_argument(
        "--paytm-wait-seconds", type=_paytm_wait_seconds_type, default=None, metavar="N",
        dest="paytm_wait_seconds",
        help=(
            "Maximum seconds to wait for the Paytm report download link to appear. "
            "0 = wait indefinitely until interrupted (Ctrl-C). "
            "If not specified, the scraper default is used (900 s = 15 min). "
            "Applies to --paytm-only, --completed-shift, --accounting-only, "
            "and default (all) modes."
        ),
    )
    parser.add_argument(
        "--paytm-debug", action="store_true", dest="paytm_debug",
        help=(
            "Save Paytm diagnostic artifacts (screenshots, page HTML, panel HTML/text, "
            "anchor list, candidate links, status-keyword text, filtered network log) to "
            "data/paytm/debug/paytm_<date>_<HHMMSS>/. "
            "Use when the report download link is not detected."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()

    if args.completed_shift:
        mode = 'completed_shift'
        # IRAS may be needed for Price and/or boundary scrapes, but whether it is
        # actually needed is determined inside run() after DB existence checks.
        # Credentials are checked at that point — not here — so the script does not
        # abort if IRAS creds are absent when all data is already in the DB.
        iras_required = False
    elif args.accounting_only:
        mode = 'accounting'
        # Same deferred check: IRAS is only needed if Price is missing for the date.
        # Credential validation happens inside run() before the IRAS browser is opened.
        iras_required = False
    elif args.boundary_only:
        mode = 'boundary'
        iras_required = True   # IRAS always required for boundary scrapes
    elif args.atg_only:
        mode = 'atg'
        iras_required = True   # IRAS always required for ATG scrape
    elif args.paytm_only:
        mode = 'paytm_only'
        iras_required = False  # No IRAS login needed
    elif args.price_only:
        mode = 'price_only'
        # Deferred: IRAS only needed if price is not already in DB for the date.
        iras_required = False
    elif args.sdms_only:
        mode = 'sdms_only'
        iras_required = False  # No IRAS login needed
    else:
        mode = 'all'
        iras_required = True   # IRAS always required for full daily run

    if iras_required:
        missing = [v for v in ("IRAS_URL", "IRAS_USERNAME", "IRAS_PASSWORD", "ANTHROPIC_API_KEY")
                   if not os.environ.get(v)]
        if missing:
            print(f"ERROR: missing environment variable(s): {', '.join(missing)}")
            sys.exit(1)

    if args.dates:
        dates = args.dates
    elif args.date:
        dates = [args.date]
    else:
        # Daily cron default: today's date.
        # The cron fires at 07:00 — the shift boundary at 06:00 today has just passed,
        # so today is the correct shift_date for the shift that just completed.
        dates = [datetime.now().strftime("%Y-%m-%d")]

    success = asyncio.run(run(dates, dry_run=args.dry_run, mode=mode,
                              paytm_wait_seconds=args.paytm_wait_seconds,
                              paytm_debug=args.paytm_debug))
    sys.exit(0 if success else 1)
