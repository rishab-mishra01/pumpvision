"""
sdms_pad_exporter.py — SDMS PAD Statement (Eledger) daily scraper
==================================================================
Downloads the previous calendar day's PAD Statement from the SDMS portal
(https://sdms.indianoil.in/sdmspro), parses the HTML table, and saves:
  - data/sdms/sdms_pad_YYYY-MM-DD.csv          full row data (11 columns)
  - data/sdms/sdms_pad_YYYY-MM-DD_summary.json  key balances + fleet card total

The PAD Statement is a calendar-day ledger. From Date = To Date = yesterday.

USAGE:
    python -X utf8 scrapers/sdms_pad_exporter.py

Credentials required in .env:
    SDMS_USERNAME=<SDMS login username>
    SDMS_PASSWORD=<SDMS login password>
    SDMS_STATE_PATH=scrapers/sdms_state.json   (optional, default as shown)
    SDMS_HEADLESS=false                         (set false for debug/OTP)
"""

import asyncio
import base64
import csv
import io
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import anthropic
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

SDMS_LOGIN_URL  = "https://sdms.indianoil.in/sdmspro/auth/login"
SDMS_BASE       = "https://sdms.indianoil.in/sdmspro"
SDMS_USERNAME   = os.environ.get("SDMS_USERNAME", "")
SDMS_PASSWORD   = os.environ.get("SDMS_PASSWORD", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")

_state_raw  = os.environ.get("SDMS_STATE_PATH", "scrapers/sdms_state.json")
STATE_PATH  = (_PROJECT_ROOT / _state_raw).resolve()
OUTPUT_DIR  = _PROJECT_ROOT / "data" / "sdms"

MAX_CAPTCHA_ATTEMPTS = 2
TABLE_LOAD_TIMEOUT   = 30_000   # ms
NEW_TAB_TIMEOUT      = 10_000   # ms

CAPTCHA_PROMPT = (
    "Read the characters in this CAPTCHA image exactly as they appear. "
    "Reply with only the characters, no spaces, no punctuation, nothing else. "
    "Ignore any strikethrough or diagonal lines across the text."
)

FLEET_CARD_DOC_TYPE = "Fleet- Card Posting"

CUSTOMER_LABEL = "SHREE PETROLEUM (206858)"


# ─────────────────────────────────────────────
# DATE HELPERS
# ─────────────────────────────────────────────

def get_yesterday():
    """Returns (date_obj, 'DD-MM-YYYY', 'YYYY-MM-DD')."""
    y = date.today() - timedelta(days=1)
    return y, y.strftime("%d-%m-%Y"), y.strftime("%Y-%m-%d")


# ─────────────────────────────────────────────
# CAPTCHA SOLVER
# ─────────────────────────────────────────────

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


# ─────────────────────────────────────────────
# SESSION CHECK
# ─────────────────────────────────────────────

async def is_logged_in(page) -> bool:
    """Return True if the dashboard is loaded (login form not present)."""
    if "/login" in page.url or "/auth/login" in page.url:
        return False
    # Look for a nav element that only exists post-login
    for sel in [
        "[class*='sidebar']",
        "[class*='nav-menu']",
        "[class*='left-nav']",
        "nav",
        "[class*='navbar']",
    ]:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2_000):
                return True
        except Exception:
            continue
    # URL-based fallback: if we're not on a /login or /auth page, assume OK
    return "/auth" not in page.url and "/login" not in page.url


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

async def _find(page, selectors: list[str], *, visible_check: bool = True):
    """Return first locator from list that exists (and is visible)."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0:
                if not visible_check or await loc.is_visible(timeout=1_500):
                    return loc
        except Exception:
            continue
    return None


async def do_login(page, context) -> bool:
    """
    Fill username, password, select Retail radio, solve CAPTCHA, submit.
    Retries CAPTCHA up to MAX_CAPTCHA_ATTEMPTS times.
    Returns True on success, False on failure.
    """
    print("[login] Navigating to SDMS login page...")
    await page.goto(SDMS_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2_000)

    for attempt in range(1, MAX_CAPTCHA_ATTEMPTS + 1):
        print(f"  [login] Attempt {attempt}/{MAX_CAPTCHA_ATTEMPTS}")

        if attempt > 1:
            # Refresh CAPTCHA for retry
            refresh = await _find(page, [
                "img[src*='refresh']", "img[src*='reload']",
                "a[onclick*='captcha']", "a[onclick*='Captcha']",
                ".captcha-refresh", "#captchaRefresh", "#refreshCaptcha",
                "[title*='efresh']",
            ], visible_check=False)
            if refresh:
                await refresh.click()
                await page.wait_for_timeout(1_000)
            else:
                await page.goto(SDMS_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1_500)

        # ── Fill username ─────────────────────────────────────────────────
        user_loc = await _find(page, [
            "input[name='username']", "input[name='userId']", "input[name='user']",
            "input[id='username']", "input[id='userId']",
            "input[placeholder*='sername']", "input[placeholder*='User']",
            "input[type='text']:visible",
        ])
        if user_loc:
            await user_loc.fill(SDMS_USERNAME)
            print("  [login] Username filled")
        else:
            print("  [login] ERROR: username field not found")
            return False

        # ── Fill password ─────────────────────────────────────────────────
        pw_loc = await _find(page, ["input[type='password']"])
        if pw_loc:
            await pw_loc.fill(SDMS_PASSWORD)
            print("  [login] Password filled")
        else:
            print("  [login] ERROR: password field not found")
            return False

        # ── Select Retail radio ───────────────────────────────────────────
        retail_selectors = [
            "input[type='radio'][value='Retail']",
            "input[type='radio'][value='retail']",
            "input[type='radio'][value='RETAIL']",
        ]
        radio_clicked = False
        for sel in retail_selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    await loc.click()
                    radio_clicked = True
                    print("  [login] Retail radio selected")
                    break
            except Exception:
                continue
        if not radio_clicked:
            # Try clicking a label containing "Retail"
            try:
                label = page.locator("label:has-text('Retail')").first
                if await label.count() > 0 and await label.is_visible(timeout=2_000):
                    await label.click()
                    radio_clicked = True
                    print("  [login] Retail label clicked")
            except Exception:
                pass
        if not radio_clicked:
            print("  [login] WARN: Retail radio not found — continuing anyway")

        await page.wait_for_timeout(400)

        # ── Solve CAPTCHA ─────────────────────────────────────────────────
        captcha_img = await _find(page, [
            "img[src*='captcha']", "img[src*='Captcha']", "img[src*='kaptcha']",
            "img[id*='captcha']", "img[id*='Captcha']", "img[class*='captcha']",
            "img[alt*='aptcha']", "img[name*='captcha']", "form img",
        ])
        if captcha_img is None:
            print(f"  [login] ERROR: CAPTCHA image not found on attempt {attempt}")
            if attempt == MAX_CAPTCHA_ATTEMPTS:
                await page.screenshot(path=str(OUTPUT_DIR / "debug_login_no_captcha.png"))
            continue

        img_bytes = await captcha_img.screenshot()
        captcha_text = _solve_captcha(img_bytes)
        print(f"  [login] CAPTCHA solved: {captcha_text!r}")

        # ── Fill CAPTCHA input ────────────────────────────────────────────
        cap_input = await _find(page, [
            "input[name*='captcha']", "input[name*='Captcha']",
            "input[id*='captcha']", "input[id*='Captcha']",
            "input[placeholder*='aptcha']", "input[placeholder*='APTCHA']",
            "input[placeholder*='code']", "input[placeholder*='Code']",
        ])
        if cap_input is None:
            # Fallback: last visible text input
            inputs = page.locator("input[type='text']:visible, input:not([type]):visible")
            cnt = await inputs.count()
            if cnt > 0:
                cap_input = inputs.nth(cnt - 1)
        if cap_input:
            await cap_input.fill(captcha_text)
        else:
            print("  [login] ERROR: CAPTCHA input not found")
            return False

        # ── Submit ────────────────────────────────────────────────────────
        submit = await _find(page, [
            "button[type='submit']", "input[type='submit']",
            "button:has-text('Sign In')", "button:has-text('Login')",
            "button:has-text('LOG IN')", "button:has-text('Submit')",
            "[role='button']:has-text('Sign In')",
        ])
        if submit:
            await submit.click()
        else:
            await page.keyboard.press("Enter")

        await page.wait_for_timeout(4_000)

        # ── Check result ──────────────────────────────────────────────────
        try:
            await page.wait_for_function(
                "() => !window.location.href.includes('/login') && "
                "      !window.location.href.includes('/auth/login')",
                timeout=8_000,
            )
            print("  [login] SUCCESS")
            # Persist session
            try:
                state = await context.storage_state()
                STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
                print(f"  [login] State saved → {STATE_PATH.name}")
            except Exception as e:
                print(f"  [login] WARN: could not save state: {e}")
            return True
        except PlaywrightTimeout:
            pass

        if "/login" not in page.url and "/auth/login" not in page.url:
            print("  [login] SUCCESS (URL check)")
            try:
                state = await context.storage_state()
                STATE_PATH.write_text(json.dumps(state), encoding="utf-8")
            except Exception:
                pass
            return True

        print(f"  [login] Failed — CAPTCHA was: {captcha_text!r} — still at: {page.url}")

    print("SDMS login failed — check credentials or CAPTCHA solve")
    return False


# ─────────────────────────────────────────────
# NAVIGATION → PAD STATEMENT TAB
# ─────────────────────────────────────────────

async def navigate_to_pad_statement(page, context):
    """
    Expand the left nav → click Account → click PAD Statement (Eledger).
    The link opens in a new browser tab; returns the new page object.
    """
    print("[nav] Expanding left sidebar...")

    # Hover over the left nav to expand it (it's collapsed by default)
    nav_selectors = [
        "[class*='sidebar']", "[class*='side-nav']", "[class*='left-nav']",
        "[class*='sidenav']", "[class*='nav-menu']", "nav", ".navbar",
    ]
    hovered = False
    for sel in nav_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2_000):
                await loc.hover()
                hovered = True
                print(f"  [nav] Hovered: {sel}")
                break
        except Exception:
            continue
    if not hovered:
        print("  [nav] WARN: could not find nav to hover — trying direct click")

    await page.wait_for_timeout(1_000)

    # Click "Account" heading to expand submenu
    account_selectors = [
        "text=Account", ":has-text('Account')",
        "a:has-text('Account')", "span:has-text('Account')",
        "li:has-text('Account')",
    ]
    account_clicked = False
    for sel in account_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=3_000):
                await loc.click()
                account_clicked = True
                print("  [nav] 'Account' clicked")
                break
        except Exception:
            continue

    if not account_clicked:
        print("  [nav] ERROR: 'Account' menu item not found")
        await page.screenshot(path=str(OUTPUT_DIR / "debug_nav_account.png"))
        raise RuntimeError("Could not find 'Account' nav item")

    await page.wait_for_timeout(800)

    # Click "PAD Statement (Eledger)" — opens a new tab
    pad_selectors = [
        "text=PAD Statement (Eledger)",
        "a:has-text('PAD Statement')",
        "span:has-text('PAD Statement')",
        ":has-text('PAD Statement (Eledger)')",
        ":has-text('Eledger')",
    ]
    pad_loc = None
    for sel in pad_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=3_000):
                pad_loc = loc
                break
        except Exception:
            continue

    if pad_loc is None:
        print("  [nav] ERROR: 'PAD Statement (Eledger)' link not found")
        await page.screenshot(path=str(OUTPUT_DIR / "debug_nav_pad.png"))
        raise RuntimeError("Could not find PAD Statement (Eledger) link")

    print("[nav] Clicking PAD Statement (Eledger) — expecting new tab...")
    try:
        async with context.expect_page(timeout=NEW_TAB_TIMEOUT) as new_page_event:
            await pad_loc.click()
        new_page = await new_page_event.value
        await new_page.wait_for_load_state("domcontentloaded", timeout=15_000)
        await new_page.bring_to_front()
        print(f"  [nav] New tab opened: {new_page.url}")
        return new_page
    except Exception as e:
        print(f"  [nav] WARN: new tab not caught ({e}) — trying direct URL navigation")
        # Fallback: navigate directly to the PAD Statement page
        await page.goto(
            f"{SDMS_BASE}/account/padStatement",
            wait_until="domcontentloaded",
            timeout=20_000,
        )
        await page.wait_for_timeout(2_000)
        return page


# ─────────────────────────────────────────────
# DATE RANGE + VIEW
# ─────────────────────────────────────────────

async def set_date_and_view(page, dd_mm_yyyy: str):
    """
    Clear and fill From Date and To Date with dd_mm_yyyy, then click View.
    """
    print(f"[date] Setting date range: {dd_mm_yyyy} → {dd_mm_yyyy}")

    for label, selectors in [
        ("From Date", [
            "input[name*='from']", "input[id*='from']", "input[id*='From']",
            "input[name*='From']", "input[placeholder*='From']",
            "input[placeholder*='from']", "input[placeholder*='DD-MM-YYYY']",
        ]),
        ("To Date", [
            "input[name*='to']", "input[id*='to']", "input[id*='To']",
            "input[name*='To']", "input[placeholder*='To']",
            "input[placeholder*='to']",
        ]),
    ]:
        filled = False
        for sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=2_000):
                    await loc.triple_click()
                    await loc.fill(dd_mm_yyyy)
                    # Confirm with Tab so JS date-change handlers fire
                    await loc.press("Tab")
                    print(f"  [date] {label} filled: {dd_mm_yyyy}")
                    filled = True
                    break
            except Exception:
                continue
        if not filled:
            print(f"  [date] WARN: {label} input not found — skipping")

    await page.wait_for_timeout(500)

    # Click View button
    view_selectors = [
        "button:has-text('View')", "input[type='submit'][value*='View']",
        "input[type='button'][value*='View']", "a:has-text('View')",
        "button:has-text('view')", "button[type='submit']",
    ]
    clicked = False
    for sel in view_selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2_000):
                await loc.click()
                clicked = True
                print("  [date] View clicked")
                break
        except Exception:
            continue
    if not clicked:
        print("  [date] WARN: View button not found")


# ─────────────────────────────────────────────
# AMOUNT PARSER
# ─────────────────────────────────────────────

def _parse_amount(s: str) -> float:
    """Strip 'Rs.', commas, whitespace; preserve sign. Returns 0.0 on empty."""
    s = re.sub(r"Rs\.?\s*", "", s or "").replace(",", "").strip()
    if not s or s in ("-", "—", "N/A"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


# ─────────────────────────────────────────────
# TABLE + METADATA EXTRACTION
# ─────────────────────────────────────────────

async def extract_report_data(page, report_date_iso: str) -> tuple[dict, list[dict]]:
    """
    Wait for the table, then extract:
      - metadata: opening_balance, closing_balance, generated_at
      - rows: list of dicts (all 11 columns)
    """
    print("[extract] Waiting for PAD table to load...")

    # Wait for a table to appear on the page
    try:
        await page.wait_for_selector("table", timeout=TABLE_LOAD_TIMEOUT)
        print("  [extract] Table found")
    except PlaywrightTimeout:
        await page.screenshot(path=str(OUTPUT_DIR / "debug_table_timeout.png"))
        raise RuntimeError(
            f"PAD table did not load within {TABLE_LOAD_TIMEOUT // 1000}s after clicking View"
        )

    await page.wait_for_timeout(1_000)

    # ── Metadata ──────────────────────────────────────────────────────────
    page_text = await page.inner_text("body")

    opening_balance = 0.0
    closing_balance = 0.0
    generated_at    = ""

    m = re.search(r"Opening\s+Balance\s*:?\s*Rs\.?\s*([-\d,\.]+)", page_text, re.IGNORECASE)
    if m:
        opening_balance = _parse_amount(m.group(1))

    m = re.search(r"Closing\s+[Bb]alance\s*:?\s*Rs\.?\s*([-\d,\.]+)", page_text, re.IGNORECASE)
    if m:
        closing_balance = _parse_amount(m.group(1))

    m = re.search(
        r"Report\s+Generated\s+Date[/\s]*Time\s*:?\s*([^\n\r]+)",
        page_text, re.IGNORECASE,
    )
    if m:
        generated_at = m.group(1).strip()

    metadata = {
        "report_date":      report_date_iso,
        "opening_balance":  opening_balance,
        "closing_balance":  closing_balance,
        "generated_at":     generated_at,
        "customer":         CUSTOMER_LABEL,
    }

    # ── Table rows ────────────────────────────────────────────────────────
    raw_rows = await page.evaluate("""() => {
        const tables = document.querySelectorAll('table');
        // Pick the largest table (most rows) — likely the data table
        let best = null, bestLen = 0;
        for (const t of tables) {
            const rows = t.querySelectorAll('tr');
            if (rows.length > bestLen) { best = t; bestLen = rows.length; }
        }
        if (!best) return [];
        const result = [];
        for (const tr of best.querySelectorAll('tr')) {
            const cells = Array.from(tr.querySelectorAll('td, th'))
                .map(c => c.innerText.replace(/\\n/g, ' ').trim());
            result.push(cells);
        }
        return result;
    }""")

    # Identify header row — find the row that contains "Document" or "Plant"
    header_idx = None
    for i, row in enumerate(raw_rows):
        joined = " ".join(row).lower()
        if "document" in joined or "plant" in joined:
            header_idx = i
            break

    expected_cols = [
        "plant", "item_text", "document_type", "document_number",
        "date", "material_group", "quantity", "unit", "debit", "credit", "balance",
    ]

    rows: list[dict] = []
    data_start = (header_idx + 1) if header_idx is not None else 0

    for raw in raw_rows[data_start:]:
        # Skip blank rows and summary rows that have fewer cells
        if not any(raw):
            continue
        # Pad / trim to 11 columns
        padded = (raw + [""] * 11)[:11]
        rows.append({
            "report_date":     report_date_iso,
            "plant":           padded[0],
            "item_text":       padded[1],
            "document_type":   padded[2],
            "document_number": padded[3],
            "date":            padded[4],
            "material_group":  padded[5],
            "quantity":        padded[6],
            "unit":            padded[7],
            "debit":           padded[8],
            "credit":          padded[9],
            "balance":         padded[10],
        })

    print(f"  [extract] {len(rows)} rows extracted")
    print(f"  [extract] Opening balance : Rs. {opening_balance:,.2f}")
    print(f"  [extract] Closing balance : Rs. {closing_balance:,.2f}")
    if generated_at:
        print(f"  [extract] Generated at   : {generated_at}")

    return metadata, rows


# ─────────────────────────────────────────────
# FLEET CARD SUMMARY
# ─────────────────────────────────────────────

def compute_fleet_card_summary(rows: list[dict]) -> tuple[float, int]:
    """Sum Credit column for Fleet-Card Posting rows."""
    total = 0.0
    count = 0
    for row in rows:
        if row.get("document_type", "").strip() == FLEET_CARD_DOC_TYPE:
            total += _parse_amount(row.get("credit", "0"))
            count += 1
    return total, count


# ─────────────────────────────────────────────
# SAVE OUTPUTS
# ─────────────────────────────────────────────

def save_outputs(
    report_date_iso: str,
    metadata: dict,
    rows: list[dict],
    fleet_total: float,
    fleet_count: int,
) -> tuple[Path, Path]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    csv_path  = OUTPUT_DIR / f"sdms_pad_{report_date_iso}.csv"
    json_path = OUTPUT_DIR / f"sdms_pad_{report_date_iso}_summary.json"

    # CSV — all rows
    fieldnames = [
        "report_date", "plant", "item_text", "document_type", "document_number",
        "date", "material_group", "quantity", "unit", "debit", "credit", "balance",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # JSON summary
    summary = {
        "report_date":      metadata["report_date"],
        "opening_balance":  metadata["opening_balance"],
        "closing_balance":  metadata["closing_balance"],
        "fleet_card_total": fleet_total,
        "fleet_card_count": fleet_count,
        "generated_at":     metadata["generated_at"],
        "customer":         CUSTOMER_LABEL,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    return csv_path, json_path


# ─────────────────────────────────────────────
# MAIN FLOW
# ─────────────────────────────────────────────

async def run() -> bool:
    """
    Full SDMS PAD export flow. Returns True on success, False on any failure.
    """
    _, date_ddmmyyyy, date_iso = get_yesterday()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 55)
    print("  SDMS PAD Statement Exporter")
    print(f"  Date (calendar day) : {date_ddmmyyyy}")
    print(f"  Output              : {OUTPUT_DIR / f'sdms_pad_{date_iso}.csv'}")
    print("=" * 55)
    print()

    headless = os.environ.get("SDMS_HEADLESS", "true").lower() != "false"
    profile_dir = STATE_PATH.parent / "sdms_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            accept_downloads=False,
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Inject persisted cookies if available
        if STATE_PATH.exists():
            try:
                saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                cookies = saved.get("cookies", [])
                if cookies:
                    await context.add_cookies(cookies)
                    print(f"[session] Loaded {len(cookies)} cookies from {STATE_PATH.name}")
            except Exception as e:
                print(f"[session] WARN: could not load cookies: {e}")

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── Step 1: Check session / login ─────────────────────────────
            print("[step 1] Checking session...")
            await page.goto(SDMS_BASE, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)
            print(f"  Landed at: {page.url}")

            if await is_logged_in(page):
                print("[step 1] Session valid.")
            else:
                print("[step 1] Not logged in — starting login.")
                if not await do_login(page, context):
                    return False

            # ── Step 2: Navigate to PAD Statement (opens new tab) ─────────
            print("[step 2] Navigating to PAD Statement (Eledger)...")
            try:
                report_page = await navigate_to_pad_statement(page, context)
            except RuntimeError as e:
                print(f"[step 2] ERROR: {e}")
                return False

            # ── Step 3: Set date range and click View ─────────────────────
            print("[step 3] Setting date range and requesting report...")
            await set_date_and_view(report_page, date_ddmmyyyy)

            # ── Step 4: Extract data ───────────────────────────────────────
            print("[step 4] Extracting report data...")
            try:
                metadata, rows = await extract_report_data(report_page, date_iso)
            except RuntimeError as e:
                print(f"[step 4] ERROR: {e}")
                return False

            if not rows:
                print("[step 4] WARN: No rows extracted — the report may be empty for this date")

            # ── Step 5: Compute fleet card summary ─────────────────────────
            fleet_total, fleet_count = compute_fleet_card_summary(rows)

            # ── Step 6: Save outputs ───────────────────────────────────────
            print("[step 6] Saving outputs...")
            csv_path, json_path = save_outputs(
                date_iso, metadata, rows, fleet_total, fleet_count
            )

            print()
            print("=" * 55)
            print("  SUCCESS")
            print(f"  Date              : {date_ddmmyyyy}")
            print(f"  Opening balance   : Rs. {metadata['opening_balance']:,.2f}")
            print(f"  Closing balance   : Rs. {metadata['closing_balance']:,.2f}")
            print(f"  Fleet card total  : Rs. {fleet_total:,.2f} ({fleet_count} txns)")
            print(f"  Table rows        : {len(rows)}")
            print(f"  CSV               : {csv_path.name}")
            print(f"  Summary JSON      : {json_path.name}")
            print("=" * 55)
            return True

        except Exception as e:
            print(f"\n[ERROR] Unexpected error: {e}")
            try:
                await page.screenshot(path=str(OUTPUT_DIR / "debug_unexpected_error.png"))
            except Exception:
                pass
            return False

        finally:
            await context.close()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    missing = [
        v for v in ("SDMS_USERNAME", "SDMS_PASSWORD", "ANTHROPIC_API_KEY")
        if not os.environ.get(v)
    ]
    if missing:
        print(f"ERROR: missing environment variable(s): {', '.join(missing)}")
        sys.exit(1)

    success = asyncio.run(run())
    sys.exit(0 if success else 1)
