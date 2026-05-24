#!/usr/bin/env python3
"""
IRAS login-page diagnostic probe.

Launches Chromium headless, navigates to the IRAS login page, waits up to
20 seconds for any form element, then prints a structured diagnostic report.

Purpose: verify that the Docker + Railway environment can reach and render
iras.iocliras.in before attempting a real scraper run.

This probe does NOT log in.  It does NOT read or print credentials.
It does NOT save cookies, session state, or screenshots.
It always exits 0 — failure to render is reported in the log, not raised.

Usage:
    Set in Railway service Variables:
        PUMPVISION_SERVICE_ROLE=iras-probe
    Then trigger/redeploy the service and check Railway logs.

Optional env var (read-only — not a credential):
    IRAS_URL   Base URL of the IRAS portal (default: https://iras.iocliras.in)
"""

import asyncio
import os
import sys

# Read only the base URL — not credentials, not API keys.
_IRAS_BASE = os.environ.get("IRAS_URL", "https://iras.iocliras.in").rstrip("/")
_LOGIN_URL = _IRAS_BASE if _IRAS_BASE.endswith("/login") else _IRAS_BASE + "/login"
_WAIT_MS   = 20_000   # seconds to wait for any form element to appear


async def _run_probe() -> None:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

    print("=" * 57)
    print("  Pumpvision - IRAS Login Page Probe")
    print("=" * 57)
    print(f"  target  : {_LOGIN_URL}")
    print(f"  wait    : {_WAIT_MS // 1000}s for login form elements")
    print("=" * 57)
    sys.stdout.flush()

    # Collect failed network request paths (query strings + fragments stripped — no tokens)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    _nav_status: int | None = None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            # Match the exact launch args used by daily_scrape.py scrapers
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            accept_downloads=False,
            viewport={"width": 1400, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        # Track failed requests — strip query strings so no signed URLs are logged
        def _on_request_failed(req) -> None:
            url_path = req.url.split("?")[0].split("#")[0]
            failed_requests.append(f"{req.method} {url_path}")

        page.on("requestfailed", _on_request_failed)

        # Collect browser console errors (message text only — no secrets or headers)
        def _on_console(msg) -> None:
            if msg.type == "error":
                console_errors.append(msg.text)

        page.on("console", _on_console)

        # ── Navigate ──────────────────────────────────────────────────────────
        print(f"\n[probe] Navigating to {_LOGIN_URL} ...")
        sys.stdout.flush()
        try:
            _nav_resp = await page.goto(_LOGIN_URL, wait_until="networkidle", timeout=30_000)
            _nav_status = _nav_resp.status if _nav_resp is not None else None
        except PlaywrightTimeout:
            print("[probe] Navigation timed out (networkidle not reached — continuing)")
        except Exception as nav_exc:
            print(f"[probe] Navigation error: {nav_exc}")

        # ── Wait for any sign of a login form ─────────────────────────────────
        # Same selector list as _autonomous_login() in daily_scrape.py
        _FORM_SELECTORS = (
            "input[type='password'], "
            "input[name='username'], input[name='userId'], "
            "form, "
            "img[src*='captcha'], img[id*='captcha'], img[class*='captcha'], "
            "canvas"
        )
        form_found = False
        try:
            await page.wait_for_selector(_FORM_SELECTORS, timeout=_WAIT_MS)
            form_found = True
            print("[probe] Form element detected.")
        except PlaywrightTimeout:
            print(f"[probe] No form element appeared within {_WAIT_MS // 1000}s.")
        except Exception as wait_exc:
            print(f"[probe] Wait error: {wait_exc}")

        # ── Collect diagnostics ───────────────────────────────────────────────
        current_url = page.url

        try:
            title = await page.title()
        except Exception:
            title = "(unavailable)"

        try:
            html_len = len(await page.content())
        except Exception:
            html_len = -1

        try:
            body_text = (await page.locator("body").inner_text(timeout=2000)).strip()
            body_excerpt = body_text[:500]
        except Exception:
            body_excerpt = "(unavailable)"

        try:
            img_count = await page.locator("img").count()
        except Exception:
            img_count = -1

        try:
            form_count = await page.locator("form").count()
        except Exception:
            form_count = -1

        try:
            input_count = await page.locator("input").count()
        except Exception:
            input_count = -1

        try:
            pw_count = await page.locator("input[type='password']").count()
        except Exception:
            pw_count = -1

        # Script src values — strip query strings to avoid logging signed URLs
        script_srcs: list[str] = []
        script_total = -1
        try:
            all_scripts = page.locator("script[src]")
            script_total = await all_scripts.count()
            for i in range(min(script_total, 10)):
                raw_src = await all_scripts.nth(i).get_attribute("src") or ""
                script_srcs.append(raw_src.split("?")[0].split("#")[0])
        except Exception:
            pass

        await browser.close()

    # ── Print report ──────────────────────────────────────────────────────────
    print()
    print("=" * 57)
    print("  PROBE RESULTS")
    print("=" * 57)
    print(f"  nav status     : {_nav_status}")
    print(f"  form found     : {form_found}")
    print(f"  current URL    : {current_url}")
    print(f"  page title     : {title!r}")
    print(f"  HTML length    : {html_len} bytes")
    print(f"  body text      : {body_excerpt!r}")
    print(f"  img count      : {img_count}")
    print(f"  form count     : {form_count}")
    print(f"  input count    : {input_count}")
    print(f"  password input : {pw_count}")
    print(f"  script[src]    : {script_total} total")
    for src in script_srcs:
        print(f"    {src}")
    if failed_requests:
        print(f"  failed requests: {len(failed_requests)}")
        for req in failed_requests[:20]:
            print(f"    {req}")
    else:
        print(f"  failed requests: none")
    if console_errors:
        print(f"  console errors : {len(console_errors)}")
        for err in console_errors[:20]:
            print(f"    {err}")
    else:
        print(f"  console errors : none")
    print("=" * 57)
    sys.stdout.flush()


def main() -> int:
    asyncio.run(_run_probe())
    return 0   # Always 0 — this is a diagnostic tool, not a pass/fail test


if __name__ == "__main__":
    sys.exit(main())
