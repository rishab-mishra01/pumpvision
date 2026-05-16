"""
IRAS ATG Stock Exporter
========================
Scrapes the FCC Data > Stock tab from the IRAS portal and writes ATG tank
level snapshots to the `tank_readings` database table.

The Stock tab shows the current ATG reading per tank, refreshed every ~30 min
by the GVR MAG PLUS probes. Each run captures one point-in-time snapshot.

Tank map (static, outlet-specific):
  Tank 1 → HS   20,000 L
  Tank 2 → MS   20,000 L
  Tank 3 → X2   10,000 L
  Tank 4 → XG   20,000 L  — probe unreliable, is_reliable=False

Usage (standalone, manual login):
    python -X utf8 scrapers/iras_atg_exporter.py

Integrated use:
    Called from daily_scrape.py as Job 5 with an active authenticated page.
    Entry point: run_atg(page) — returns list of saved TankReading rows.
"""

import asyncio
import io
import os
import sys
from datetime import datetime
from pathlib import Path

import openpyxl
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

IRAS_URL = "https://iras.iocliras.in/login"

# product code → tank_id, capacity, reliability
PRODUCT_TANK_MAP = {
    "HS": {"tank_id": 1, "capacity_litres": 20000.0, "is_reliable": True},
    "MS": {"tank_id": 2, "capacity_litres": 20000.0, "is_reliable": True},
    "X2": {"tank_id": 3, "capacity_litres": 10000.0, "is_reliable": True},
    "XG": {"tank_id": 4, "capacity_litres": 20000.0, "is_reliable": False},
}

TABLE_LOAD_TIMEOUT = 30   # seconds
DOWNLOAD_TIMEOUT   = 30   # seconds


# ─────────────────────────────────────────────
# NAVIGATION
# ─────────────────────────────────────────────

async def navigate_to_stock(page):
    """Navigate to FCC Data > Stock tab."""
    await page.get_by_role("button", name="FCC Data").click()
    await page.wait_for_timeout(2000)

    # Try direct tab first (visible without overflow)
    stock_tab = page.locator("button[role='tab']:has-text('Stock')")
    try:
        await stock_tab.wait_for(state="visible", timeout=4_000)
        await stock_tab.click()
    except Exception:
        # Stock is in the overflow '...' menu — same pattern as Shift Totalizer
        overflow = page.locator("button[role='tab']:has-text('...')")
        await overflow.wait_for(state="visible", timeout=10_000)
        await overflow.click()
        await page.wait_for_timeout(800)
        stock_item = page.locator("li.app-tab-list:has-text('Stock')")
        await stock_item.wait_for(state="visible", timeout=5_000)
        await stock_item.click()

    await page.wait_for_timeout(1500)
    print("[OK] Navigated to FCC Data > Stock")


# ─────────────────────────────────────────────
# AG-GRID READER
# ─────────────────────────────────────────────

async def _read_ag_grid(page) -> list[dict]:
    """
    Read all visible ag-Grid rows into a list of {header: value} dicts.
    Returns empty list if headers cannot be found.
    """
    try:
        header_cells = page.locator(".ag-header-cell-text")
        count = await header_cells.count()
        headers = []
        for i in range(count):
            text = (await header_cells.nth(i).text_content() or "").strip().lower()
            headers.append(text)

        if not headers:
            return []

        rows = []
        row_els = page.locator(".ag-row")
        row_count = await row_els.count()

        for r in range(row_count):
            cells = row_els.nth(r).locator(".ag-cell")
            cell_count = await cells.count()
            row_data = {}
            for c in range(min(cell_count, len(headers))):
                val = (await cells.nth(c).text_content() or "").strip()
                row_data[headers[c]] = val
            if any(row_data.values()):
                rows.append(row_data)

        return rows

    except Exception as e:
        print(f"  [ATG] ag-Grid read error: {e}")
        return []


# ─────────────────────────────────────────────
# EXCEL PARSER
# ─────────────────────────────────────────────

def _parse_excel(fpath: Path) -> list[dict]:
    """Parse a Stock Excel export into a list of {header: value} dicts."""
    try:
        wb = openpyxl.load_workbook(fpath, read_only=True, data_only=True)
    except Exception as e:
        print(f"  [ATG] Could not open {fpath.name}: {e}")
        return []

    ws = wb[wb.sheetnames[0]]

    # Find header row — look for recognisable Stock keywords
    headers = None
    header_row_idx = 0
    for row_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
        cells = [str(c).strip().lower() if c is not None else "" for c in row]
        joined = " ".join(cells)
        if any(k in joined for k in ("tank", "volume", "level", "product")):
            headers = cells
            header_row_idx = row_idx
            break

    if not headers:
        wb.close()
        print(f"  [ATG] Header row not found in {fpath.name}")
        return []

    rows = []
    for row in ws.iter_rows(min_row=header_row_idx + 1, values_only=True):
        row_data = {
            headers[i]: (str(v).strip() if v is not None else "")
            for i, v in enumerate(row)
            if i < len(headers)
        }
        if any(row_data.values()):
            rows.append(row_data)

    wb.close()
    return rows


# ─────────────────────────────────────────────
# ROW PARSER
# ─────────────────────────────────────────────

def _find_col(row: dict, *keywords) -> str | None:
    """Return the value of the first key containing all keywords (case-insensitive)."""
    kw = [k.lower() for k in keywords]
    for key, val in row.items():
        if all(k in key for k in kw):
            return val
    return None


def _safe_float(val: str | None) -> float | None:
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _parse_row(row: dict) -> dict | None:
    """
    Convert one raw grid/Excel row into a structured ATG reading dict.

    Returns None if the row cannot be mapped to a known product.
    The IRAS Stock tab column names are not guaranteed — we match by keyword
    substring so that "Product Code", "Prod Code", "Product" all resolve.
    """
    # Product — try "product code" first, then bare "product"
    product_raw = _find_col(row, "product", "code") or _find_col(row, "product")
    if not product_raw:
        return None
    product = product_raw.strip().upper()
    if product not in PRODUCT_TANK_MAP:
        return None

    tank_info = PRODUCT_TANK_MAP[product]

    level_mm      = _safe_float(_find_col(row, "level"))
    volume_litres = _safe_float(_find_col(row, "volume"))
    capacity_str  = _find_col(row, "capacity")
    capacity_litres = _safe_float(capacity_str) if capacity_str else tank_info["capacity_litres"]

    # pct_full: prefer computed from volume/capacity for consistency
    pct_full = None
    if volume_litres is not None and capacity_litres and capacity_litres > 0:
        pct_full = round(volume_litres / capacity_litres * 100, 2)

    # scraped_at: look for a timestamp column; fall back to utcnow
    ts_raw = (
        _find_col(row, "date", "time")
        or _find_col(row, "timestamp")
        or _find_col(row, "reading", "time")
        or _find_col(row, "time")
    )
    scraped_at = None
    if ts_raw:
        for fmt in ("%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M"):
            try:
                scraped_at = datetime.strptime(ts_raw, fmt)
                break
            except ValueError:
                continue
    if scraped_at is None:
        scraped_at = datetime.utcnow()

    return {
        "tank_id":        tank_info["tank_id"],
        "product":        product,
        "level_mm":       level_mm,
        "volume_litres":  volume_litres,
        "capacity_litres": capacity_litres,
        "pct_full":       pct_full,
        "is_reliable":    tank_info["is_reliable"],
        "scraped_at":     scraped_at,
    }


# ─────────────────────────────────────────────
# DB WRITE
# ─────────────────────────────────────────────

def save_readings_to_db(readings: list[dict]) -> int:
    """
    Upsert ATG readings into `tank_readings`.

    Uses the UniqueConstraint (scraped_at, tank_id) — skips if a row already
    exists for that snapshot time + tank to keep reruns idempotent.
    Returns the count of rows inserted.
    """
    if not readings:
        print("  [db] No ATG readings to save.")
        return 0

    try:
        from pumpvision import create_app
        from pumpvision.models import db, TankReading

        app = create_app()
        with app.app_context():
            saved = 0
            for r in readings:
                existing = db.session.query(TankReading).filter_by(
                    scraped_at=r["scraped_at"],
                    tank_id=r["tank_id"],
                ).first()

                if existing:
                    print(f"  [db] Skip duplicate: tank {r['tank_id']} @ {r['scraped_at']}")
                    continue

                db.session.add(TankReading(
                    scraped_at=r["scraped_at"],
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
            print(f"  [db] Saved {saved} ATG reading(s) to tank_readings")
            return saved

    except Exception as e:
        print(f"  [db] ERROR saving ATG readings: {e}")
        return 0


# ─────────────────────────────────────────────
# MAIN ENTRY (called from daily_scrape.py)
# ─────────────────────────────────────────────

async def run_atg(page, output_dir: Path | None = None) -> list[dict]:
    """
    Navigate to the Stock tab, scrape the current ATG snapshot, and write to DB.

    Called from daily_scrape.py with an already-authenticated IRAS page.
    Returns the list of parsed reading dicts (empty on failure).
    """
    print(f"\n{'='*55}")
    print(f"  JOB 5 — ATG Stock Snapshot")
    print(f"{'='*55}")

    if output_dir is None:
        output_dir = Path(os.environ.get("OUTPUT_FOLDER", r"C:\IRAS_Data")) / "ATG"
    output_dir.mkdir(parents=True, exist_ok=True)

    await navigate_to_stock(page)

    # Click Show to load the table (Stock tab may not need date inputs)
    try:
        show_btn = page.locator("button:has-text('Show')").first
        await show_btn.wait_for(state="visible", timeout=5_000)
        await show_btn.click()
        await page.wait_for_timeout(2000)
    except Exception:
        print("  [ATG] No Show button — table may auto-load")
        await page.wait_for_timeout(2000)

    # Wait for rows
    try:
        await page.wait_for_selector(
            ".ag-row, .ag-overlay-no-rows-wrapper",
            timeout=TABLE_LOAD_TIMEOUT * 1000,
        )
    except PlaywrightTimeout:
        print("  [ATG] Table load timeout")

    row_count = await page.locator(".ag-row").count()
    if row_count == 0:
        print("  [ATG] No rows in Stock table — skipping")
        return []

    print(f"  [ATG] {row_count} row(s) in Stock table")

    # Strategy 1: read ag-Grid cells directly
    raw_rows = await _read_ag_grid(page)

    # Strategy 2: fallback to Excel download
    if not raw_rows:
        print("  [ATG] Grid read empty — trying Excel download")
        now = datetime.now()
        fname = f"ATG_Stock_{now.strftime('%Y%m%d_%H%M')}.xlsx"
        fpath = output_dir / fname
        try:
            async with page.expect_download(timeout=DOWNLOAD_TIMEOUT * 1000) as dl_info:
                await page.locator("button.export-excel-button").click()
            download = await dl_info.value
            await download.save_as(str(fpath))
            print(f"  [ATG] Downloaded: {fname}")
            raw_rows = _parse_excel(fpath)
        except Exception as e:
            print(f"  [ATG] Excel download failed: {e}")

    if not raw_rows:
        print("  [ATG] Could not read Stock table by any method")
        return []

    # Parse rows into structured readings
    readings = []
    for row in raw_rows:
        parsed = _parse_row(row)
        if parsed:
            readings.append(parsed)
        else:
            # Log unrecognised rows for debugging
            product_hint = _find_col(row, "product") or "?"
            print(f"  [ATG] Skipped row (product={product_hint!r}): {list(row.keys())[:6]}")

    print(f"  [ATG] Parsed {len(readings)}/{len(raw_rows)} rows → products: "
          f"{[r['product'] for r in readings]}")

    if readings:
        for r in readings:
            rel = "" if r["is_reliable"] else " [UNRELIABLE]"
            vol = f"{r['volume_litres']:.0f} L" if r["volume_litres"] is not None else "—"
            pct = f"{r['pct_full']:.1f}%" if r["pct_full"] is not None else "—"
            print(f"    Tank {r['tank_id']} ({r['product']}){rel}: {vol}  {pct}  "
                  f"@ {r['scraped_at'].strftime('%H:%M')}")

        save_readings_to_db(readings)

    return readings


# ─────────────────────────────────────────────
# STANDALONE ENTRY POINT (manual login)
# ─────────────────────────────────────────────

async def _main_standalone():
    """Run ATG scraper standalone with manual IRAS login."""
    output_dir = Path(os.environ.get("OUTPUT_FOLDER", r"C:\IRAS_Data")) / "ATG"
    output_dir.mkdir(parents=True, exist_ok=True)

    print()
    print("IRAS ATG Stock Exporter (standalone)")
    print(f"Output: {output_dir}")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(
            accept_downloads=True,
            viewport={"width": 1400, "height": 900},
        )
        page = await context.new_page()

        await page.goto(IRAS_URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # Pre-fill credentials from env
        username = os.environ.get("IRAS_USERNAME", "")
        password = os.environ.get("IRAS_PASSWORD", "")
        if username and password:
            try:
                for sel in ["input[name='username']", "input[name='userId']",
                            "input[placeholder*='User']", "input[type='text']"]:
                    un = page.locator(sel).first
                    if await un.count() > 0 and await un.is_visible(timeout=1000):
                        await un.fill(username)
                        break
                await page.locator("input[type='password']").fill(password)
                print("[OK] Credentials pre-filled — solve CAPTCHA and click Login")
            except Exception:
                pass

        # Wait for manual login
        print()
        print("=" * 55)
        print("  Solve the CAPTCHA and log in.")
        print("  Script continues automatically after login.")
        print("  (waits up to 5 minutes)")
        print("=" * 55)
        await page.wait_for_function(
            "() => !window.location.href.includes('/login')",
            timeout=300_000,
        )
        print("[OK] Login detected")
        await page.wait_for_timeout(2000)

        await run_atg(page, output_dir)

        print("\n[DONE]")
        await browser.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    asyncio.run(_main_standalone())
