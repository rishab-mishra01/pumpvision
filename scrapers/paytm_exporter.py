"""
Paytm for Business — Payment Transaction Report Exporter
=========================================================
Downloads the previous operational day's payment transaction report CSV from
dashboard.paytm.com and saves it to data/paytm/paytm_YYYY-MM-DD.csv.

Operational day: YESTERDAY 06:00 AM → TODAY 05:59 AM
(mirrors the IRAS 6AM boundary used for fuel reconciliation)

USAGE:
    python -X utf8 scrapers/paytm_exporter.py

Credentials required in .env:
    PAYTM_EMAIL=<email or mobile registered with Paytm Business>
    PAYTM_PASSWORD=<password>
    PAYTM_STATE_PATH=scrapers/paytm_state.json  (optional, default as shown)
"""

import asyncio
import email
import imaplib
import io
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────

PAYTM_LOGIN_URL  = "https://dashboard.paytm.com/login/"
PAYTM_DASHBOARD  = "https://dashboard.paytm.com"
PAYTM_EMAIL      = os.environ.get("PAYTM_EMAIL", "")
PAYTM_PASSWORD   = os.environ.get("PAYTM_PASSWORD", "")
GMAIL_ADDRESS    = os.environ.get("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")

_state_path_raw = os.environ.get("PAYTM_STATE_PATH", "scrapers/paytm_state.json")
STATE_PATH  = (_PROJECT_ROOT / _state_path_raw).resolve()
OUTPUT_DIR  = _PROJECT_ROOT / "data" / "paytm"

POLL_INTERVAL      = 2    # seconds between polling attempts
POLL_TIMEOUT       = 900  # default max seconds to wait for async file generation (0 = indefinite)
HEARTBEAT_INTERVAL = 60   # seconds between "still waiting" log lines during poll


# ─────────────────────────────────────────────
# GMAIL OTP READER
# ─────────────────────────────────────────────

def fetch_paytm_otp_from_gmail(timeout: int = 90) -> str | None:
    """
    Poll Gmail IMAP for a new Paytm OTP email and return the 6-digit code.

    Requires GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env.
    GMAIL_APP_PASSWORD must be a Google App Password (not the account password) —
    generate one at myaccount.google.com/apppasswords with IMAP enabled.

    Returns the OTP string on success, None if credentials are missing or the
    OTP is not found within the timeout. Caller falls back to input() if None.
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        return None

    deadline = time.time() + timeout
    print(f"  [otp] Polling Gmail ({GMAIL_ADDRESS}) for Paytm OTP — up to {timeout}s...")

    while time.time() < deadline:
        try:
            with imaplib.IMAP4_SSL("imap.gmail.com") as mail:
                mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
                mail.select("INBOX")

                # Search for unseen Paytm OTP emails (exact sender + subject)
                _, data = mail.search(
                    None,
                    '(UNSEEN FROM "care@paytm.com" SUBJECT "One Time Password")',
                )
                ids = data[0].split() if data[0] else []

                for mid in reversed(ids):
                    _, msg_data = mail.fetch(mid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    # Recency check — skip OTPs older than 10 minutes (they're expired)
                    try:
                        email_dt = parsedate_to_datetime(msg.get("Date", ""))
                        age = (datetime.now(timezone.utc) - email_dt).total_seconds()
                        if age > 600:
                            print(f"  [otp] Skipping stale OTP email ({int(age)}s old)")
                            continue
                    except Exception:
                        pass  # unparseable date — proceed and try anyway

                    # Extract plain-text body
                    body = ""
                    if msg.is_multipart():
                        for part in msg.walk():
                            if part.get_content_type() == "text/plain":
                                body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                break
                    else:
                        payload = msg.get_payload(decode=True)
                        if payload:
                            body = payload.decode("utf-8", errors="ignore")

                    # Find a 6-digit OTP — try near "OTP" keyword first, then any 6-digit run
                    match = re.search(r'(?:OTP|otp|code|Code)[^\d]{0,20}(\d{6})', body)
                    if not match:
                        match = re.search(r'\b(\d{6})\b', body)
                    if match:
                        otp = match.group(1)
                        # Mark as read so it isn't re-used on a subsequent poll
                        mail.store(mid, "+FLAGS", "\\Seen")
                        print("  [otp] OTP found — proceeding")
                        return otp

        except imaplib.IMAP4.error as e:
            print(f"  [otp] IMAP auth error: {e}")
            return None          # wrong credentials — don't keep retrying
        except Exception as e:
            print(f"  [otp] Gmail check error: {e}")

        remaining = int(deadline - time.time())
        if remaining > 0:
            print(f"  [otp] OTP not yet in inbox — retrying in 5s ({remaining}s left)...")
            time.sleep(5)

    print(f"  [otp] OTP not found within {timeout}s")
    return None


# ─────────────────────────────────────────────
# DATE HELPERS
# ─────────────────────────────────────────────

def get_op_day_range():
    """
    Operational day: YESTERDAY 06:00 AM → TODAY 05:59 AM.
    Returns (op_date, start_dt, end_dt).
    """
    now       = datetime.now().replace(second=0, microsecond=0)
    yesterday = now - timedelta(days=1)
    start_dt  = yesterday.replace(hour=6, minute=0)
    end_dt    = now.replace(hour=5, minute=59)
    return yesterday.date(), start_dt, end_dt


def fmt_display(dt: datetime) -> str:
    """Human-readable datetime for log output."""
    return dt.strftime("%d-%m-%Y %I:%M %p")


# ─────────────────────────────────────────────
# SESSION CHECK
# ─────────────────────────────────────────────

async def is_logged_in(page) -> bool:
    """Return True if the merchant dashboard is loaded (MID visible on page)."""
    try:
        await page.wait_for_selector("text=SHRIPE69428535177091", timeout=5_000)
        return True
    except PlaywrightTimeout:
        pass
    try:
        if await page.locator("*:has-text('SHRIPE69428535177091')").count() > 0:
            return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────
# LOGIN
# ─────────────────────────────────────────────

async def do_login(page, context) -> bool:
    """
    Log in at dashboard.paytm.com/login/.

    The login form is rendered inside an iframe (accounts.paytm.com/oauth-js-sdk/).
    Both email and password fields appear on the same screen.

    On first run Paytm may trigger an OTP step. Run with PAYTM_HEADLESS=false
    to complete that step manually. After one successful login the persistent
    browser profile keeps the session alive.

    Returns True on success, False on failure.
    """
    print("[login] Attempting auto-login...")

    # Navigate directly to the login page
    await page.goto(PAYTM_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
    await page.wait_for_timeout(2_000)
    print(f"  [login] Login page URL: {page.url}")

    # Wait for the login iframe to load (accounts.paytm.com/oauth-js-sdk)
    login_frame = None
    for _ in range(15):   # poll up to 15s
        for frame in page.frames:
            if "accounts.paytm.com" in frame.url:
                login_frame = frame
                break
        if login_frame is not None:
            break
        await page.wait_for_timeout(1_000)

    if login_frame is None:
        # Fallback: form may be in the main document on some variants
        print("  [login] WARN: accounts.paytm.com iframe not found — trying main frame")
        login_frame = page

    print(f"  [login] Login frame: {login_frame.url if hasattr(login_frame, 'url') else 'main'}")

    # Wait for the email input to appear inside the frame
    try:
        await login_frame.wait_for_selector(
            "input[placeholder='Enter your Mobile Number or Email']",
            timeout=10_000,
        )
    except PlaywrightTimeout:
        try:
            await login_frame.wait_for_selector("input:visible", timeout=5_000)
        except PlaywrightTimeout:
            print("  [login] ERROR: Login form did not render within 15s")
            return False

    # Fill email / mobile
    email_selectors = [
        "input[placeholder='Enter your Mobile Number or Email']",
        "input[type='text']:visible",
        "input[type='email']:visible",
        "input[type='tel']:visible",
    ]
    filled_email = False
    for sel in email_selectors:
        try:
            loc = login_frame.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=1_500):
                await loc.fill(PAYTM_EMAIL)
                actual = await loc.input_value()
                if not actual:
                    el = await loc.element_handle()
                    await login_frame.evaluate(
                        "([el, val]) => { el.value = val; "
                        "el.dispatchEvent(new Event('input', {bubbles:true})); "
                        "el.dispatchEvent(new Event('change', {bubbles:true})); }",
                        [el, PAYTM_EMAIL],
                    )
                print(f"  [login] Email/mobile filled")
                filled_email = True
                break
        except Exception:
            continue

    if not filled_email:
        print("  [login] ERROR: Could not find email/mobile field")
        return False

    await page.wait_for_timeout(300)

    # Fill password
    pw_selectors = [
        "input[placeholder='Paytm Password']",
        "input[type='password']",
    ]
    filled_pw = False
    for sel in pw_selectors:
        try:
            loc = login_frame.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=2_000):
                await loc.fill(PAYTM_PASSWORD)
                print("  [login] Password filled")
                filled_pw = True
                break
        except Exception:
            continue

    if not filled_pw:
        print("  [login] ERROR: Could not find password field")
        return False

    await page.wait_for_timeout(300)

    # Click Sign in Securely
    submit_selectors = [
        "button:has-text('Sign in Securely')",
        "button:has-text('Sign In Securely')",
        "button[type='submit']",
        "button:has-text('Login')",
        "button:has-text('Sign In')",
    ]
    clicked = False
    for sel in submit_selectors:
        try:
            loc = login_frame.locator(sel).first
            if await loc.count() > 0 and await loc.is_visible(timeout=1_000):
                btn_text = await loc.inner_text()
                print(f"  [login] Clicking: {btn_text.strip()!r}")
                await loc.click()
                clicked = True
                break
        except Exception:
            continue
    if not clicked:
        try:
            btn = login_frame.locator("button:visible").first
            if await btn.count() > 0:
                await btn.click()
                clicked = True
        except Exception:
            pass
    if not clicked:
        await page.keyboard.press("Enter")

    await page.wait_for_timeout(4_000)

    # Check for OTP screen — only appears on first login from a new browser profile.
    # After one successful OTP verification the persistent profile is trusted and
    # this block is never reached again.
    try:
        otp_input = login_frame.locator("input[placeholder='Enter OTP']").first
        if await otp_input.is_visible(timeout=2_000):
            print()
            print("=" * 55)
            print("  ONE-TIME DEVICE VERIFICATION")
            print("  Paytm sent an OTP to your registered mobile/email.")
            print("  This only happens the first time on a new browser profile.")
            print("  After this the session is saved and future runs are")
            print("  fully automated — no OTP needed.")
            print("=" * 55)
            otp_code = fetch_paytm_otp_from_gmail()
            if otp_code is None:
                # Gmail not configured or OTP not found — fall back to manual entry
                try:
                    otp_code = input("  Enter the OTP you received: ").strip()
                except EOFError:
                    otp_code = ""
            if otp_code:
                await otp_input.fill(otp_code)
                await page.wait_for_timeout(300)
                # Click Verify button
                verify_btn = login_frame.locator("button:has-text('Verify'), button:has-text('VERIFY')").first
                if await verify_btn.count() > 0 and await verify_btn.is_visible(timeout=2_000):
                    await verify_btn.click()
                    await page.wait_for_timeout(4_000)
                    print("  [login] OTP submitted")
            else:
                print("  [login] No OTP entered — aborting")
                return False
    except Exception:
        pass

    # Wait for redirect away from /login
    if "/login" in page.url:
        try:
            await page.wait_for_function(
                "() => !window.location.href.includes('/login')",
                timeout=15_000,
            )
            await page.wait_for_timeout(2_000)
        except PlaywrightTimeout:
            pass

    if await is_logged_in(page):
        print("  [login] SUCCESS — MID confirmed")
        await context.storage_state(path=str(STATE_PATH))
        print(f"  [login] State saved → {STATE_PATH}")
        return True

    print(f"  [login] FAILED — still at: {page.url}")
    return False


# ─────────────────────────────────────────────
# CUSTOM DATE RANGE HELPERS
# ─────────────────────────────────────────────

async def _fill_time(page, dt: datetime):
    """Fill hours / minutes / meridiem spinners for the currently-active date slot."""
    h12  = dt.hour % 12 or 12
    ampm = "AM" if dt.hour < 12 else "PM"
    for name, val in [("hours", f"{h12:02d}"), ("minutes", f"{dt.minute:02d}"), ("meridiem", ampm)]:
        inp = page.locator(f'input[name="{name}"]').first
        await inp.click(click_count=3)
        await inp.fill(val)
        await page.wait_for_timeout(150)


async def set_custom_date_range(page, start_dt: datetime, end_dt: datetime):
    """
    Open the Paytm Custom Date Range picker and set start/end with 6AM precision.

    Interaction model (confirmed via DOM inspection):
    - Clicking a calendar day while Start Date is active sets Start Date and
      auto-switches the picker to End Date mode.
    - Time spinners (input[name='hours/minutes/meridiem']) apply to whichever
      date slot is currently active.
    - Re-clicking the Start Date chip switches back to Start Date mode.

    Flow:
    1. Open duration dropdown → click "Custom Range"
    2. Calendar opens in Start Date mode → click yesterday (Start Date day)
    3. Auto-switched to End Date mode → switch BACK to Start Date mode
    4. Fill Start time: 06:00 AM
    5. Switch to End Date mode → click today (End Date day)
    6. Fill End time: 05:59 AM
    7. Click Done
    """
    today = datetime.now().date()

    # 1. Open duration dropdown
    dur_trigger = page.locator('[data-select-container="true"]').first
    await dur_trigger.wait_for(state="visible", timeout=6_000)
    await dur_trigger.click()
    await page.wait_for_timeout(800)

    # 2. Click "Custom Range"
    custom_li = page.locator("ul[class*='date-picker-range-list'] li").filter(has_text="Custom Range")
    await custom_li.wait_for(state="visible", timeout=5_000)
    await custom_li.click()
    await page.wait_for_timeout(1_000)

    # Navigate calendar back one month if start date is last month
    if start_dt.date().month != today.month:
        await page.locator('[data-testid="leftArrow"]').click()
        await page.wait_for_timeout(500)

    # 3. Click Start Date day
    start_attr = start_dt.strftime("%b %d %Y")  # e.g. "May 08 2026"
    await page.locator(f'span[data-testid="day"][date*="{start_attr}"]').click()
    await page.wait_for_timeout(500)

    # 4. Calendar auto-switched to End Date mode — switch BACK to Start Date
    await page.locator("[class*='calenderChoose']").nth(0).click()
    await page.wait_for_timeout(300)
    await _fill_time(page, start_dt)

    # 5. Switch to End Date mode
    await page.locator("[class*='calenderChoose']").nth(1).click()
    await page.wait_for_timeout(500)

    # Navigate forward one month if end date is in the next month
    if end_dt.date().month != start_dt.date().month:
        await page.locator('[data-testid="rightArrow"]').click()
        await page.wait_for_timeout(500)

    # 6. Click End Date day
    end_attr = end_dt.strftime("%b %d %Y")  # e.g. "May 09 2026"
    await page.locator(f'span[data-testid="day"][date*="{end_attr}"]').click()
    await page.wait_for_timeout(500)
    await _fill_time(page, end_dt)

    # 7. Click Done
    done_btn = page.locator("button:has-text('Done')").first
    await done_btn.wait_for(state="visible", timeout=5_000)
    await done_btn.click()
    await page.wait_for_timeout(1_500)


# ─────────────────────────────────────────────
# SUMMARY SCRAPE
# ─────────────────────────────────────────────

async def scrape_summary(page) -> tuple[str, str]:
    """Read TOTAL PAYMENTS and TOTAL TRANSACTIONS from the Payments summary bar."""
    total_payments     = "—"
    total_transactions = "—"

    try:
        # Each summary card lives in a [class*='sd-list'] div: "TOTAL PAYMENTS₹2,40,519.70"
        sd_divs = page.locator("[class*='sd-list']")
        count = await sd_divs.count()
        for i in range(count):
            text = (await sd_divs.nth(i).inner_text()).strip()
            if "TOTAL PAYMENTS" in text:
                val = text.replace("TOTAL PAYMENTS", "").replace("\n", "").strip()
                if val:
                    total_payments = val
            elif "TOTAL TRANSACTIONS" in text:
                val = text.replace("TOTAL TRANSACTIONS", "").replace("\n", "").strip()
                if val:
                    total_transactions = val
    except Exception:
        pass

    return total_payments, total_transactions


# ─────────────────────────────────────────────
# MAIN FLOW
# ─────────────────────────────────────────────

async def run(target_date: str | None = None, poll_timeout: int | None = None) -> bool:
    """
    Full Paytm report download flow. Manages its own browser context.
    target_date: YYYY-MM-DD accounting op_date to scrape. If None, uses yesterday.
    Returns True on success, False on any failure.
    """
    if target_date is not None:
        _d = datetime.strptime(target_date, "%Y-%m-%d")
        op_date  = _d.date()
        start_dt = _d.replace(hour=6, minute=0, second=0, microsecond=0)
        end_dt   = (_d + timedelta(days=1)).replace(hour=5, minute=59, second=0, microsecond=0)
    else:
        op_date, start_dt, end_dt = get_op_day_range()
    output_csv = OUTPUT_DIR / f"paytm_{op_date.strftime('%Y-%m-%d')}.csv"

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print()
    print("=" * 55)
    print("  Paytm Payment Report Exporter")
    print(f"  Operational date : {op_date}")
    print(f"  Start            : {fmt_display(start_dt)}")
    print(f"  End              : {fmt_display(end_dt)}")
    print(f"  Output           : {output_csv}")
    print("=" * 55)
    print()

    profile_dir = STATE_PATH.parent / "paytm_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    headless = os.environ.get("PAYTM_HEADLESS", "true").lower() != "false"

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            accept_downloads=True,
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
        # Mask the headless fingerprint — removes navigator.webdriver which
        # bot-detection checks use to identify automated browsers.
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        # Explicitly inject saved cookies — persistent profile binary format is
        # unreliable; JSON export is the authoritative session store.
        if STATE_PATH.exists():
            try:
                saved = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                cookies = saved.get("cookies", [])
                if cookies:
                    await context.add_cookies(cookies)
                    print(f"[session] Loaded {len(cookies)} cookies from {STATE_PATH.name}")
            except Exception as e:
                print(f"[session] WARN: could not load cookies from state file: {e}")

        try:
            page = context.pages[0] if context.pages else await context.new_page()

            # ── Step 1: Check session / login ─────────────────────────────────
            print("[step 1] Checking session...")
            await page.goto(PAYTM_DASHBOARD, wait_until="domcontentloaded", timeout=30_000)
            await page.wait_for_timeout(2_000)
            print(f"  Landed at: {page.url}")

            if await is_logged_in(page):
                print("[step 1] Session valid — dashboard loaded.")
            else:
                print("[step 1] Not logged in — starting auto-login.")
                if not await do_login(page, context):
                    print("Paytm login failed — check PAYTM_EMAIL and PAYTM_PASSWORD in .env")
                    return False

            # ── Step 2: Navigate to Payments ──────────────────────────────────
            print("[step 2] Navigating to Payments...")
            payments_nav_selectors = [
                "nav a:has-text('Payments')",
                "[role='navigation'] a:has-text('Payments')",
                "aside a:has-text('Payments')",
                "[class*='sidebar'] a:has-text('Payments')",
                "[class*='nav'] a:has-text('Payments')",
                "a[href*='payment']",
            ]
            nav_clicked = False
            for sel in payments_nav_selectors:
                try:
                    loc = page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=3_000):
                        await loc.click()
                        nav_clicked = True
                        break
                except Exception:
                    continue

            if not nav_clicked:
                print("  [WARN] Nav click failed — trying URL fallback")
                await page.goto(
                    f"{PAYTM_DASHBOARD}/next/transactions",
                    wait_until="domcontentloaded",
                    timeout=20_000,
                )

            await page.wait_for_timeout(3_000)
            print(f"  [step 2] URL after nav: {page.url}")

            # ── Step 3: Set Status filter to "All" ─────────────────────────────
            # Status trigger: [data-testid="select-trigger"]
            # "All" option:   li[data-item-value=""] inside [data-testid="select-ul-status"]
            print("[step 3] Setting Status filter to 'All'...")
            try:
                trigger = page.locator('[data-testid="select-trigger"]').first
                await trigger.wait_for(state="visible", timeout=6_000)
                await trigger.click()
                await page.wait_for_timeout(600)

                all_opt = page.locator(
                    '[data-testid="select-ul-status"] li[data-item-value=""]'
                ).first
                await all_opt.wait_for(state="visible", timeout=4_000)
                await all_opt.click()
                await page.wait_for_timeout(600)
                print("  [OK] Status = All")
            except Exception as e:
                print(f"  [WARN] Could not set Status filter: {e}")

            # ── Step 4: Set Custom Date Range (6AM operational boundary) ──────
            print(f"[step 4] Setting Custom Date Range: {fmt_display(start_dt)} → {fmt_display(end_dt)}")
            try:
                await set_custom_date_range(page, start_dt, end_dt)
                print(f"  [OK] Range set")
            except Exception as e:
                print(f"  [ERROR] Custom date range failed: {e}")
                await page.screenshot(path=str(OUTPUT_DIR / "debug_step4_error.png"))
                return False

            await page.wait_for_timeout(2_000)

            # ── Step 5: Read summary figures ───────────────────────────────────
            print("[step 5] Reading summary figures...")
            total_payments, total_transactions = await scrape_summary(page)
            print(f"  TOTAL PAYMENTS    : {total_payments}")
            print(f"  TOTAL TRANSACTIONS: {total_transactions}")

            # ── Step 6: Click Download → Download Report ──────────────────────
            # Clicking "Download" opens a small dropdown:
            #   • Download Report  — triggers async CSV generation on Paytm's servers
            #   • Send to Email
            #
            # BEFORE clicking, snapshot the number of ready files in the
            # Files to Download panel so step 7 can detect the NEW file.
            print("[step 6] Triggering Download Report...")
            try:
                # Snapshot all download-style links before triggering so we can detect new ones
                pre_hrefs = await page.evaluate('''() => {
                    const sels = ['a[download]', 'a[href*="s3.amazonaws.com"]',
                                  'a[href*="s3-ap-southeast"]', 'a[href*="s3-us-east"]'];
                    const hrefs = new Set();
                    for (const sel of sels) {
                        for (const a of document.querySelectorAll(sel)) {
                            const h = a.getAttribute('href') || a.href;
                            if (h && h.startsWith('http')) hrefs.add(h);
                        }
                    }
                    return [...hrefs];
                }''')
                pre_count = len(pre_hrefs)
                pre_href_set = set(pre_hrefs)
                print(f"  [step 6] Files in panel before request: {pre_count}")

                dl_btn = page.locator("button:has-text('Download')").first
                await dl_btn.wait_for(state="visible", timeout=6_000)
                await dl_btn.click()
                await page.wait_for_timeout(600)

                dl_report = page.locator("text=Download Report").first
                await dl_report.wait_for(state="visible", timeout=5_000)
                await dl_report.click()
                await page.wait_for_timeout(3_000)
                print("  [OK] 'Download Report' clicked — CSV generating on server")

            except Exception as e:
                print(f"  [ERROR] Could not trigger download: {e}")
                await page.screenshot(path=str(OUTPUT_DIR / "debug_step6_error.png"))
                return False

            await page.wait_for_timeout(1_000)

            # ── Step 6b: Expand "Files to Download" panel ─────────────────────
            # Paytm shows a collapsed panel at the bottom — clicking it reveals
            # the ready-to-download file links in the DOM.
            for _attempt in range(3):
                try:
                    panel = page.locator(
                        "text=Files to Download, "
                        "[class*='fileDownload'], [class*='file-download'], "
                        "[class*='downloadPanel'], [class*='download-panel']"
                    ).first
                    if await panel.count() > 0 and await panel.is_visible(timeout=4_000):
                        await panel.click()
                        await page.wait_for_timeout(600)
                        print("  [step 6b] Opened 'Files to Download' panel")
                        break
                except Exception:
                    pass
                await asyncio.sleep(2)

            # ── Step 7: Poll for the NEW download link ─────────────────────────
            # Paytm appends a new entry to the Files to Download panel when ready.
            # Re-open the panel every 10s in case it collapsed, and look for any
            # S3 / CSV href that wasn't present before the Download Report click.
            #
            # poll_timeout arg (seconds): None → use module POLL_TIMEOUT (900 s default)
            #                             0    → wait indefinitely (Ctrl-C to abort)
            #                             N>0  → wait at most N seconds
            _poll_timeout_eff = POLL_TIMEOUT if poll_timeout is None else poll_timeout
            if _poll_timeout_eff == 0:
                print(f"[step 7] Polling indefinitely (Ctrl-C to abort) for new S3 download link...")
            else:
                print(f"[step 7] Polling up to {_poll_timeout_eff}s for new S3 download link...")
            download_href = None
            _poll_start     = time.time()
            _last_expand    = _poll_start
            _next_heartbeat = _poll_start + HEARTBEAT_INTERVAL
            while True:
                _now     = time.time()
                _elapsed = _now - _poll_start

                # Timeout check — skipped when polling indefinitely
                if _poll_timeout_eff > 0 and _elapsed >= _poll_timeout_eff:
                    break

                # Re-expand "Files to Download" panel every 10s in case it auto-collapsed
                if _elapsed > 0 and (_now - _last_expand) >= 10:
                    try:
                        panel = page.locator(
                            "text=Files to Download, "
                            "[class*='fileDownload'], [class*='file-download'], "
                            "[class*='downloadPanel'], [class*='download-panel']"
                        ).first
                        if await panel.count() > 0:
                            await panel.click()
                            await page.wait_for_timeout(300)
                    except Exception:
                        pass
                    _last_expand = _now

                try:
                    result = await page.evaluate(f'''() => {{
                        const known = new Set({list(pre_href_set)!r});
                        const sels = [
                            'a[download]',
                            'a[href*="s3.amazonaws.com"]',
                            'a[href*="s3-ap-southeast"]',
                            'a[href*="s3-us-east"]',
                            'a[href*=".csv"]',
                        ];
                        for (const sel of sels) {{
                            for (const a of document.querySelectorAll(sel)) {{
                                const href = a.getAttribute('href') || a.href;
                                if (href && href.startsWith('http') && !known.has(href)) return href;
                            }}
                        }}
                        return null;
                    }}''')
                    if result:
                        download_href = result
                        print(f"  [OK] New download link appeared after ~{int(_elapsed)}s")
                        break
                except Exception:
                    pass

                await asyncio.sleep(POLL_INTERVAL)

                # Heartbeat — lets the operator know the scraper is still alive
                _now = time.time()
                if _now >= _next_heartbeat:
                    _int_elapsed = int(_now - _poll_start)
                    if _poll_timeout_eff == 0:
                        print(f"  [Paytm] Still waiting for report link... elapsed {_int_elapsed}s")
                    else:
                        _remaining = max(0, _poll_timeout_eff - _int_elapsed)
                        print(f"  [Paytm] Still waiting for report link... elapsed {_int_elapsed}s ({_remaining}s remaining)")
                    _next_heartbeat = _now + HEARTBEAT_INTERVAL

            if not download_href:
                # Last resort: if Paytm reused the one pre-existing link, use it
                if len(pre_hrefs) == 1:
                    print(f"  [WARN] No new link — using pre-existing link (Paytm may have reused it)")
                    download_href = pre_hrefs[0]
                else:
                    try:
                        await page.screenshot(path=str(OUTPUT_DIR / "debug_step7_timeout.png"))
                    except Exception:
                        pass

            # ── Step 8: Download the CSV directly via S3 presigned URL ─────────
            downloaded = False
            if download_href:
                print("[step 8] Downloading CSV via S3 presigned URL...")
                try:
                    urllib.request.urlretrieve(download_href, str(output_csv))
                    print(f"  [saved] {output_csv.name}")
                    downloaded = True
                except Exception as e:
                    print(f"  [ERROR] Direct download failed: {e}")

            if not downloaded:
                ts = datetime.now().strftime("%H:%M:%S")
                _timeout_desc = (
                    "indefinite wait" if _poll_timeout_eff == 0
                    else f"{_poll_timeout_eff}s"
                )
                print(
                    f"[ERROR] No download link found after {_timeout_desc} ({ts})"
                )
                return False

            print()
            print("=" * 55)
            print("  SUCCESS")
            print(f"  Date             : {op_date}")
            print(f"  Total payments   : {total_payments}")
            print(f"  Total transact.  : {total_transactions}")
            print(f"  File             : {output_csv}")
            print("=" * 55)
            return True

        finally:
            await context.close()


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import argparse as _ap

    def _paytm_wait_type(value: str) -> int:
        """argparse type for --paytm-wait-seconds: 0 (indefinite) or a positive integer."""
        try:
            n = int(value)
        except ValueError:
            raise _ap.ArgumentTypeError(f"invalid int value: {value!r}")
        if n < 0:
            raise _ap.ArgumentTypeError(
                f"--paytm-wait-seconds requires 0 (wait indefinitely) or a positive integer; got {n}"
            )
        return n

    _parser = _ap.ArgumentParser(description="Paytm Payment Report Exporter")
    _parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Accounting op_date to scrape (default: yesterday).",
    )
    _parser.add_argument(
        "--paytm-wait-seconds", type=_paytm_wait_type, default=None, metavar="N",
        dest="paytm_wait_seconds",
        help=(
            "Max seconds to wait for the Paytm report download link. "
            "0 = wait indefinitely (Ctrl-C to abort). "
            f"Default: {POLL_TIMEOUT} s."
        ),
    )
    _args = _parser.parse_args()

    missing = [v for v in ("PAYTM_EMAIL", "PAYTM_PASSWORD") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: missing environment variable(s): {', '.join(missing)}")
        sys.exit(1)

    success = asyncio.run(run(target_date=_args.date, poll_timeout=_args.paytm_wait_seconds))
    sys.exit(0 if success else 1)
