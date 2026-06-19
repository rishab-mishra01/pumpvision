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
from urllib.parse import urlsplit
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

# Regex for filtering network responses worth logging in debug mode.
# Matches URL fragments common in report/export flows — no auth headers are logged.
_DIAG_URL_RE = re.compile(r"report|export|download|transaction|file", re.I)


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
    # Wait for the page JS to finish loading (iframe injection happens after JS runs)
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except PlaywrightTimeout:
        pass  # networkidle may not fire — proceed anyway
    await page.wait_for_timeout(2_000)
    print(f"  [login] Login page URL: {page.url}")

    # Wait for the login iframe to load (accounts.paytm.com/oauth-js-sdk)
    login_frame = None
    for _ in range(60):   # poll up to 60s
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
    print(f"  [login] Frames on page: {[f.url for f in page.frames]}")

    # Wait for the email input to appear inside the frame (up to 90s)
    email_appeared = False
    try:
        await login_frame.wait_for_selector(
            "input[placeholder='Enter your Mobile Number or Email']",
            timeout=90_000,
        )
        email_appeared = True
    except PlaywrightTimeout:
        try:
            await login_frame.wait_for_selector("input:visible", timeout=30_000)
            email_appeared = True
        except PlaywrightTimeout:
            pass

    if not email_appeared:
        # Log diagnostic info before giving up
        try:
            visible_text = await login_frame.locator("body").inner_text()
            print(f"  [login] Frame body at timeout: {visible_text[:400]!r}")
            all_inputs = await login_frame.locator("input").all_text_contents()
            print(f"  [login] Inputs in frame: {all_inputs}")
        except Exception:
            pass
        print("  [login] ERROR: Login form did not render within 120s")
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
# DEBUG SESSION
# ─────────────────────────────────────────────

class _DebugSession:
    """
    Collects diagnostic screenshots, HTML snapshots, anchor audits, and filtered
    network events when --paytm-debug is active.

    Privacy:
    - network.log records sanitized URLs only (scheme + host + path).  Query
      strings and fragments are stripped to remove signed parameters, expiry
      tokens, and any other auth-like values.
    - Cookies, auth headers, request bodies, and response bodies are never logged.
    - All debug artifacts are written locally and should NOT be committed or
      shared externally, because page HTML, anchor lists, and candidate-link
      files may contain signed S3 URLs or other sensitive query parameters in
      their raw form.
    """

    def __init__(self, debug_dir: Path, op_date):
        self.dir = debug_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self.dir / "debug.log"
        self._net_path = self.dir / "network.log"
        self._seq = 0
        self.log(f"Debug session open  op_date={op_date}  dir={self.dir}")

    # ── public helpers ───────────────────────────────────────────────────

    def log(self, msg: str):
        """Print msg and append to debug.log."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        print(line)
        try:
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def note_network(self, url: str, status: int, method: str = ""):
        """Append one sanitized network event to network.log.

        Only scheme + host + path are written.  Query strings and fragments
        (which may carry signed params, expiry values, or tokens) are stripped
        before the URL reaches disk.  No cookies, auth headers, request bodies,
        or response bodies are ever logged.
        """
        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        try:
            _parts = urlsplit(url)
            _safe_url = _parts._replace(query="", fragment="").geturl()
        except Exception:
            _safe_url = url  # parsing failed — use as-is (rare edge case)
        try:
            with open(self._net_path, "a", encoding="utf-8") as fh:
                fh.write(f"[{ts}] {method} {status}  {_safe_url}\n")
        except Exception:
            pass

    def make_response_handler(self):
        """Return an async response handler that logs relevant Paytm network calls."""
        dbg = self

        async def _handler(response):
            try:
                url = response.url
                if _DIAG_URL_RE.search(url):
                    # Sanitized URL (scheme+host+path only) + status code.
                    # Query strings/fragments stripped — no signed params, no tokens.
                    dbg.note_network(url, response.status, response.request.method)
            except Exception:
                pass

        return _handler

    async def capture(self, page, label: str):
        """Save screenshot + page HTML + panel HTML/text + anchors + candidates + status text."""
        self._seq += 1
        safe = f"{self._seq:02d}_{label}".replace(" ", "_").replace("/", "-")
        self.log(f"--- capture: {safe} ---")

        # Screenshot (viewport only — faster, avoids very tall pages)
        try:
            await page.screenshot(
                path=str(self.dir / f"{safe}.png"), full_page=False)
        except Exception as exc:
            self.log(f"  screenshot failed: {exc}")

        # Full page HTML
        try:
            html = await page.content()
            (self.dir / f"{safe}_page.html").write_text(html, encoding="utf-8")
        except Exception as exc:
            self.log(f"  page-html failed: {exc}")

        await self._dump_panel(page, safe)
        await self._dump_anchors(page, safe)
        await self._dump_candidates(page, safe)
        await self._dump_status_text(page, safe)

    # ── private snapshot helpers ─────────────────────────────────────────

    async def _dump_panel(self, page, safe: str):
        try:
            result = await page.evaluate(r'''() => {
                const sels = [
                    "[class*='fileDownload']", "[class*='file-download']",
                    "[class*='downloadPanel']", "[class*='download-panel']",
                ];
                let panel = null;
                for (const sel of sels) {
                    panel = document.querySelector(sel);
                    if (panel) break;
                }
                if (!panel) {
                    for (const el of document.querySelectorAll("div,section,aside,footer")) {
                        if (el.textContent.includes("Files to Download")) {
                            panel = el;
                            break;
                        }
                    }
                }
                if (!panel) return { found: false, html: "", text: "", rowCount: 0,
                                     firstRowText: "", lastRowText: "" };
                const rows = panel.querySelectorAll(
                    "tr, li, [class*='row'], [class*='item']");
                return {
                    found:        true,
                    html:         panel.innerHTML.slice(0, 50000),
                    text:         panel.innerText,
                    rowCount:     rows.length,
                    firstRowText: rows.length > 0 ? rows[0].innerText.trim().slice(0, 200) : "",
                    lastRowText:  rows.length > 0
                                  ? rows[rows.length - 1].innerText.trim().slice(0, 200) : "",
                };
            }''')
            if result["found"]:
                (self.dir / f"{safe}_panel.html").write_text(
                    result["html"], encoding="utf-8")
                (self.dir / f"{safe}_panel.txt").write_text(
                    result["text"], encoding="utf-8")
                self.log(
                    f"  panel: {result['rowCount']} rows | "
                    f"first={result['firstRowText']!r} | "
                    f"last={result['lastRowText']!r}"
                )
            else:
                self.log("  panel: NOT FOUND in DOM")
                (self.dir / f"{safe}_panel.txt").write_text(
                    "FILES-TO-DOWNLOAD PANEL NOT FOUND IN DOM\n", encoding="utf-8")
        except Exception as exc:
            self.log(f"  panel dump failed: {exc}")

    async def _dump_anchors(self, page, safe: str):
        try:
            anchors = await page.evaluate(r'''() =>
                Array.from(document.querySelectorAll("a")).map(a => ({
                    text:     (a.innerText || a.textContent || "").trim().slice(0, 120),
                    href:     (a.getAttribute("href") || a.href || "").slice(0, 300),
                    download: a.getAttribute("download") || "",
                }))
            ''')
            lines = ["text\thref\tdownload"]
            for a in anchors:
                lines.append(f"{a['text']}\t{a['href']}\t{a['download']}")
            (self.dir / f"{safe}_anchors.txt").write_text(
                "\n".join(lines), encoding="utf-8")
            self.log(f"  anchors on page: {len(anchors)}")
        except Exception as exc:
            self.log(f"  anchor dump failed: {exc}")

    async def _dump_candidates(self, page, safe: str):
        """Dump all links matching the scraper's download-detection selectors."""
        try:
            candidates = await page.evaluate(r'''() => {
                const sels = [
                    'a[download]',
                    'a[href*="s3.amazonaws.com"]',
                    'a[href*="s3-ap-southeast"]',
                    'a[href*="s3-us-east"]',
                    'a[href*=".csv"]',
                ];
                const seen = new Set();
                const out  = [];
                for (const sel of sels) {
                    for (const a of document.querySelectorAll(sel)) {
                        const href = (a.getAttribute("href") || a.href || "").slice(0, 300);
                        if (!seen.has(href)) {
                            seen.add(href);
                            out.push({
                                sel,
                                text:     (a.innerText || "").trim().slice(0, 80),
                                href,
                                download: a.getAttribute("download") || "",
                            });
                        }
                    }
                }
                return out;
            }''')
            lines = [f"candidate_links={len(candidates)}"]
            for c in candidates:
                lines.append(
                    f"  sel={c['sel']}  download={c['download']!r}  text={c['text']!r}")
                lines.append(f"    href={c['href']}")
            (self.dir / f"{safe}_candidates.txt").write_text(
                "\n".join(lines), encoding="utf-8")
            self.log(f"  candidate download links: {len(candidates)}")
        except Exception as exc:
            self.log(f"  candidate dump failed: {exc}")

    async def _dump_status_text(self, page, safe: str):
        """Find visible text matching status keywords (Processing, Pending, etc.)."""
        try:
            found = await page.evaluate(r'''() => {
                const kws = ['Processing','Pending','Failed','Completed','Ready',
                             'Download','Error','Generating','Queued'];
                const out  = [];
                const seen = new Set();
                document.querySelectorAll("*").forEach(el => {
                    if (el.children.length === 0) {
                        const t = (el.innerText || el.textContent || "").trim();
                        if (!t || seen.has(t)) return;
                        for (const kw of kws) {
                            if (t.toLowerCase().includes(kw.toLowerCase())) {
                                out.push(t.slice(0, 200));
                                seen.add(t);
                                break;
                            }
                        }
                    }
                });
                return out.slice(0, 60);
            }''')
            if found:
                (self.dir / f"{safe}_status_text.txt").write_text(
                    "\n".join(found), encoding="utf-8")
                self.log(f"  status keywords visible: {found[:6]!r}")
            else:
                self.log("  status keywords: none found")
        except Exception as exc:
            self.log(f"  status-text dump failed: {exc}")


# ─────────────────────────────────────────────
# MAIN FLOW
# ─────────────────────────────────────────────

async def run(
    target_date: str | None = None,
    poll_timeout: int | None = None,
    debug: bool = False,
) -> bool:
    """
    Full Paytm report download flow. Manages its own browser context.
    target_date: YYYY-MM-DD accounting op_date to scrape. If None, uses yesterday.
    poll_timeout: seconds to wait for download link (None = module default 900 s; 0 = indefinite).
    debug: save screenshots, HTML, anchor/network logs to data/paytm/debug/paytm_<date>_<time>/.
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

    # ── Debug session ─────────────────────────────────────────────────────────
    _dbg: _DebugSession | None = None
    if debug:
        _dbg_dir = OUTPUT_DIR / "debug" / f"paytm_{op_date}_{datetime.now().strftime('%H%M%S')}"
        _dbg = _DebugSession(_dbg_dir, op_date)
        print(f"[debug] Artifacts → {_dbg_dir}")

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

    # Proxy config — reads PAYTM_PROXY_* env vars, falls back to IRAS_PROXY_*.
    # Paytm's accounts.paytm.com OAuth SDK shows an error page from non-India IPs.
    _paytm_proxy_server = (
        os.environ.get("PAYTM_PROXY_SERVER", "").strip()
        or os.environ.get("IRAS_PROXY_SERVER", "").strip()
    )
    _paytm_proxy_cfg: dict | None = None
    if _paytm_proxy_server:
        _paytm_proxy_cfg = {"server": _paytm_proxy_server}
        _pu = (
            os.environ.get("PAYTM_PROXY_USERNAME", "").strip()
            or os.environ.get("IRAS_PROXY_USERNAME", "").strip()
        )
        _pp = (
            os.environ.get("PAYTM_PROXY_PASSWORD", "").strip()
            or os.environ.get("IRAS_PROXY_PASSWORD", "").strip()
        )
        if _pu:
            _paytm_proxy_cfg["username"] = _pu
        if _pp:
            _paytm_proxy_cfg["password"] = _pp
    print(f"  [Paytm] proxy : {'yes' if _paytm_proxy_cfg else 'no'}")

    async with async_playwright() as p:
        _ctx_kw: dict = dict(
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
        if _paytm_proxy_cfg is not None:
            _ctx_kw["proxy"] = _paytm_proxy_cfg
        context = await p.chromium.launch_persistent_context(**_ctx_kw)
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

            # Attach network listener before any navigation so we capture all responses.
            if _dbg:
                page.on("response", _dbg.make_response_handler())

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

            if _dbg:
                await _dbg.capture(page, "step1_session_ok")

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

            if _dbg:
                await _dbg.capture(page, "step4_date_range_set")

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

                if _dbg:
                    await _dbg.capture(page, "step6_pre_download_click")

                dl_btn = page.locator("button:has-text('Download')").first
                await dl_btn.wait_for(state="visible", timeout=6_000)
                await dl_btn.click()
                await page.wait_for_timeout(600)

                dl_report = page.locator("text=Download Report").first
                await dl_report.wait_for(state="visible", timeout=5_000)
                await dl_report.click()
                await page.wait_for_timeout(3_000)
                print("  [OK] 'Download Report' clicked — CSV generating on server")

                if _dbg:
                    await _dbg.capture(page, "step6_post_download_click")

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
            download_href   = None
            _poll_start     = time.time()
            _last_expand    = _poll_start
            _next_heartbeat = _poll_start + HEARTBEAT_INTERVAL
            _expand_count   = 0   # how many times the panel was successfully re-clicked

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
                            _expand_count += 1
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

                # ── Heartbeat ─────────────────────────────────────────────────
                # Always: elapsed time + lightweight DOM status query.
                # Debug only: full screenshot/HTML capture.
                _now = time.time()
                if _now >= _next_heartbeat:
                    _int_elapsed = int(_now - _poll_start)

                    # Lightweight DOM query — panel row count, newest row text,
                    # new candidate link count. Runs in all modes (cheap at 60s interval).
                    _status_str = ""
                    try:
                        _hb = await page.evaluate(f'''() => {{
                            const known = new Set({list(pre_href_set)!r});
                            let panel = null;
                            const panelSels = ["[class*='fileDownload']","[class*='file-download']",
                                               "[class*='downloadPanel']","[class*='download-panel']"];
                            for (const sel of panelSels) {{
                                panel = document.querySelector(sel);
                                if (panel) break;
                            }}
                            if (!panel) {{
                                for (const el of document.querySelectorAll("div,section,aside,footer")) {{
                                    if (el.textContent.includes("Files to Download")) {{
                                        panel = el; break;
                                    }}
                                }}
                            }}
                            let rowCount = 0, lastRow = "";
                            if (panel) {{
                                const rows = panel.querySelectorAll(
                                    "tr,li,[class*='row'],[class*='item']");
                                rowCount = rows.length;
                                if (rows.length)
                                    lastRow = rows[rows.length-1].innerText.trim().slice(0,120);
                            }}
                            let newLinks = 0;
                            const dlSels = ['a[download]','a[href*="s3.amazonaws.com"]',
                                            'a[href*="s3-ap-southeast"]','a[href*="s3-us-east"]',
                                            'a[href*=".csv"]'];
                            for (const sel of dlSels) {{
                                for (const a of document.querySelectorAll(sel)) {{
                                    const href = a.getAttribute("href") || a.href;
                                    if (href && href.startsWith("http") && !known.has(href))
                                        newLinks++;
                                }}
                            }}
                            return {{ panelFound: panel !== null, rowCount, lastRow, newLinks }};
                        }}''')
                        _status_str = (
                            f"panel={'yes' if _hb['panelFound'] else 'NO'}  "
                            f"rows={_hb['rowCount']}  "
                            f"new_links={_hb['newLinks']}  "
                            f"re-expanded={_expand_count}x"
                        )
                        if _hb["lastRow"]:
                            _status_str += f"  last_row={_hb['lastRow']!r}"
                    except Exception:
                        _status_str = f"(DOM query failed)  re-expanded={_expand_count}x"

                    if _poll_timeout_eff == 0:
                        print(f"  [Paytm] Still waiting... elapsed {_int_elapsed}s  {_status_str}")
                    else:
                        _remaining = max(0, _poll_timeout_eff - _int_elapsed)
                        print(
                            f"  [Paytm] Still waiting... elapsed {_int_elapsed}s "
                            f"({_remaining}s remaining)  {_status_str}"
                        )

                    if _dbg:
                        await _dbg.capture(page, f"poll_{_int_elapsed}s")

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
                    if _dbg:
                        await _dbg.capture(page, "final_failure")
                        _dbg.log("[debug] FINAL FAILURE — no new download link detected")

            # ── Step 8: Download the CSV directly via S3 presigned URL ─────────
            downloaded = False
            if download_href:
                print("[step 8] Downloading CSV via S3 presigned URL...")
                try:
                    urllib.request.urlretrieve(download_href, str(output_csv))
                    print(f"  [saved] {output_csv.name}")
                    downloaded = True
                    if _dbg:
                        await _dbg.capture(page, "step8_success")
                except Exception as e:
                    print(f"  [ERROR] Direct download failed: {e}")
                    if _dbg:
                        await _dbg.capture(page, "step8_download_failed")
                        _dbg.log(f"[debug] Direct download error: {e}")

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
    _parser.add_argument(
        "--paytm-debug", action="store_true", dest="paytm_debug",
        help=(
            "Save diagnostic screenshots, HTML, anchor/candidate lists, "
            "status-keyword text, and filtered network log to "
            "data/paytm/debug/paytm_<date>_<HHMMSS>/. "
            "Use when the report download link is not detected."
        ),
    )
    _args = _parser.parse_args()

    missing = [v for v in ("PAYTM_EMAIL", "PAYTM_PASSWORD") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: missing environment variable(s): {', '.join(missing)}")
        sys.exit(1)

    success = asyncio.run(run(
        target_date=_args.date,
        poll_timeout=_args.paytm_wait_seconds,
        debug=_args.paytm_debug,
    ))
    sys.exit(0 if success else 1)
