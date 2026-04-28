"""
captcha_test.py — Standalone PoC: solve IRAS CAPTCHA via Claude Vision API.

Run 5 times to verify reliability before integrating into main scraper.

Usage:
    python -X utf8 scrapers/captcha_test.py

Saves captcha_attempt<N>.png next to this script for visual inspection.
Reads all credentials from .env in the project root.
"""

import asyncio
import base64
import io
import os
import sys
from pathlib import Path

# ── Bootstrap: ensure project root is on sys.path and .env is loaded ────────
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(_PROJECT_ROOT / ".env")

import anthropic
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ── Config ──────────────────────────────────────────────────────────────────
_base_url      = os.environ.get("IRAS_URL", "https://iras.iocliras.in").rstrip("/")
LOGIN_URL      = _base_url if _base_url.endswith("/login") else _base_url + "/login"
IRAS_USERNAME  = os.environ.get("IRAS_USERNAME", "")
IRAS_PASSWORD  = os.environ.get("IRAS_PASSWORD", "")
ANTHROPIC_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")

MAX_ATTEMPTS   = 3
SCRIPT_DIR     = Path(__file__).parent

CAPTCHA_PROMPT = (
    "Read the characters in this CAPTCHA image exactly as they appear. "
    "Reply with only the characters, no spaces, no punctuation, nothing else. "
    "Ignore any strikethrough or diagonal lines across the text."
)


# ── Claude Vision ────────────────────────────────────────────────────────────

def solve_captcha(image_bytes: bytes) -> str:
    """Send CAPTCHA screenshot bytes to Claude Vision and return solved text."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    image_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=64,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": CAPTCHA_PROMPT},
                ],
            }
        ],
    )
    return msg.content[0].text.strip()


# ── Element finders ──────────────────────────────────────────────────────────

async def find_captcha_image(page):
    """Return locator for the CAPTCHA <img>, or None if not found."""
    candidates = [
        "img[src*='captcha']",
        "img[src*='Captcha']",
        "img[src*='kaptcha']",
        "img[src*='verif']",
        "img[id*='captcha']",
        "img[id*='Captcha']",
        "img[class*='captcha']",
        "img[class*='Captcha']",
        "img[alt*='aptcha']",
        "img[name*='captcha']",
        # Last-resort: any img inside the login form
        "form img",
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                return loc
        except Exception:
            continue
    return None


async def find_captcha_input(page):
    """Return locator for the CAPTCHA text input, or None."""
    candidates = [
        "input[name*='captcha']",
        "input[name*='Captcha']",
        "input[id*='captcha']",
        "input[id*='Captcha']",
        "input[placeholder*='aptcha']",
        "input[placeholder*='APTCHA']",
        "input[placeholder*='code']",
        "input[placeholder*='Code']",
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                return loc
        except Exception:
            continue

    # Fallback: last visible text input on the page (captcha is usually last)
    inputs = page.locator("input[type='text']:visible, input:not([type]):visible")
    count = await inputs.count()
    if count > 0:
        return inputs.nth(count - 1)
    return None


async def find_refresh_button(page):
    """Return locator for the CAPTCHA refresh button, or None."""
    candidates = [
        "img[src*='refresh']",
        "img[src*='reload']",
        "img[onclick*='captcha']",
        "a[onclick*='captcha']",
        "span[onclick*='captcha']",
        "[title*='efresh']",
        "[title*='ew captcha']",
        "[title*='ew Captcha']",
        "a[href*='captcha']",
        # Generic refresh icon near captcha
        ".captcha-refresh",
        "#captchaRefresh",
        "#refreshCaptcha",
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0:
                return loc
        except Exception:
            continue
    return None


async def find_submit_button(page):
    """Return locator for the login submit button, or None."""
    candidates = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Login')",
        "button:has-text('login')",
        "button:has-text('Sign In')",
        "button:has-text('Submit')",
        "button:has-text('LOG IN')",
        "[role='button']:has-text('Login')",
    ]
    for sel in candidates:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible(timeout=1500):
                return loc
        except Exception:
            continue
    return None


# ── Login form ───────────────────────────────────────────────────────────────

async def fill_login_form(page, captcha_text: str):
    """Select Dealer, fill username/password/captcha, and click submit."""
    await page.wait_for_timeout(800)

    # Role dropdown — native <select> or MUI combobox
    try:
        native_sel = page.locator("select").first
        if await native_sel.count() > 0 and await native_sel.is_visible(timeout=2000):
            await native_sel.select_option(label="Dealer")
            print("[OK] Dealer selected via <select>")
        else:
            combo = page.locator("div[role='combobox'], .MuiSelect-select").first
            if await combo.count() > 0 and await combo.is_visible(timeout=2000):
                await combo.click()
                await page.wait_for_timeout(500)
                dealer = page.locator(
                    "li[role='option']:has-text('Dealer'), option:has-text('Dealer')"
                ).first
                await dealer.wait_for(state="visible", timeout=3000)
                await dealer.click()
                print("[OK] Dealer selected via MUI dropdown")
    except Exception as e:
        print(f"[--] Dealer dropdown error: {e}")

    await page.wait_for_timeout(600)

    # Username
    for sel in [
        "input[name='username']", "input[name='userId']",
        "input[placeholder*='Username']", "input[placeholder*='username']",
        "input[placeholder*='User']",
    ]:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0 and await loc.is_visible(timeout=1000):
                await loc.fill(IRAS_USERNAME)
                print(f"[OK] Username filled ({IRAS_USERNAME})")
                break
        except Exception:
            continue

    # Password
    pw = page.locator("input[type='password']").first
    try:
        if await pw.count() > 0 and await pw.is_visible(timeout=2000):
            await pw.fill(IRAS_PASSWORD)
            print("[OK] Password filled")
    except Exception as e:
        print(f"[--] Password fill error: {e}")

    # CAPTCHA input
    cap_input = await find_captcha_input(page)
    if cap_input:
        await cap_input.fill(captcha_text)
        print(f"[OK] CAPTCHA field filled: {captcha_text}")
    else:
        print("[!!] CAPTCHA input not found — skipping fill")

    # Submit
    submit = await find_submit_button(page)
    if submit:
        await submit.click()
        print("[OK] Submit button clicked")
    else:
        print("[--] Submit button not found — pressing Enter")
        await page.keyboard.press("Enter")


# ── Success check ────────────────────────────────────────────────────────────

async def login_succeeded(page) -> bool:
    """Return True if the browser has navigated away from the login page."""
    try:
        await page.wait_for_function(
            "() => !window.location.href.includes('/login')",
            timeout=5000,
        )
        return True
    except PlaywrightTimeout:
        pass
    return "/login" not in page.url


# ── Main ─────────────────────────────────────────────────────────────────────

async def run() -> bool:
    print(f"Target URL : {LOGIN_URL}")
    print(f"Username   : {IRAS_USERNAME}")
    print(f"Claude model: claude-opus-4-5")
    print()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        print(f"Loading login page ...")
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
        print("[OK] Page loaded\n")

        succeeded = False
        last_text = ""

        for attempt in range(1, MAX_ATTEMPTS + 1):
            print(f"── Attempt {attempt}/{MAX_ATTEMPTS} {'─' * 40}")

            if attempt > 1:
                # Refresh CAPTCHA before retrying
                refresh = await find_refresh_button(page)
                if refresh:
                    await refresh.click()
                    print("[OK] CAPTCHA refresh clicked")
                    await page.wait_for_timeout(1000)
                else:
                    print("[--] No refresh button found — reloading page")
                    await page.goto(LOGIN_URL, wait_until="networkidle", timeout=30_000)
                    await page.wait_for_timeout(800)

            # Screenshot CAPTCHA element only
            captcha_img = await find_captcha_image(page)
            if captcha_img is None:
                debug_path = SCRIPT_DIR / f"captcha_debug_attempt{attempt}.png"
                await page.screenshot(path=str(debug_path))
                print(f"[!!] CAPTCHA image not found — full-page screenshot saved: {debug_path.name}")
                print("     Inspect the screenshot to identify the correct selector.")
                continue

            img_bytes = await captcha_img.screenshot()
            debug_path = SCRIPT_DIR / f"captcha_attempt{attempt}.png"
            debug_path.write_bytes(img_bytes)
            print(f"[OK] CAPTCHA screenshot: {len(img_bytes)} bytes → saved {debug_path.name}")

            # Solve with Claude Vision
            print("     Calling Claude Vision API ...")
            last_text = solve_captcha(img_bytes)
            print(f"CAPTCHA solved: {last_text}")

            await fill_login_form(page, last_text)
            await page.wait_for_timeout(3000)

            if await login_succeeded(page):
                print(f"\nLogin SUCCESS")
                succeeded = True
                break
            else:
                print(f"Login FAILED — CAPTCHA was: {last_text}")

        if not succeeded:
            print(f"\nFAILED after {MAX_ATTEMPTS} attempts")

        await browser.close()
    return succeeded


if __name__ == "__main__":
    # Validate required env vars before launching browser
    missing = [v for v in ("IRAS_URL", "IRAS_USERNAME", "IRAS_PASSWORD", "ANTHROPIC_API_KEY")
               if not os.environ.get(v)]
    if missing:
        print(f"ERROR: missing environment variable(s): {', '.join(missing)}")
        print("Add them to .env in the project root and try again.")
        sys.exit(1)

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    result = asyncio.run(run())
    sys.exit(0 if result else 1)
