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
import urllib.parse

# Read only the base URL — not credentials, not API keys.
_IRAS_BASE = os.environ.get("IRAS_URL", "https://iras.iocliras.in").rstrip("/")
_LOGIN_URL = _IRAS_BASE if _IRAS_BASE.endswith("/login") else _IRAS_BASE + "/login"
_WAIT_MS   = 20_000   # milliseconds to wait for any form element to appear

# Optional proxy — values read here, never printed.
_PROXY_SERVER   = os.environ.get("IRAS_PROXY_SERVER",   "").strip()
_PROXY_USERNAME = os.environ.get("IRAS_PROXY_USERNAME", "").strip()
_PROXY_PASSWORD = os.environ.get("IRAS_PROXY_PASSWORD", "").strip()


def _exc_name(exc: BaseException) -> str:
    """Return only the exception class name — never str(exc).

    Exception messages from Playwright/HTTPX can contain proxy host, port,
    or connection details that must not appear in logs.  All exception
    reporting in this probe uses this helper instead of str(exc) or {exc}.
    """
    return exc.__class__.__name__


async def _run_probe() -> None:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

    print("=" * 57)
    print("  Pumpvision - IRAS Login Page Probe")
    print("=" * 57)
    print(f"  target  : {_LOGIN_URL}")
    print(f"  wait    : {_WAIT_MS // 1000}s for login form elements")
    print(f"  proxy   : {'yes' if _PROXY_SERVER else 'no'}")
    print("=" * 57)
    sys.stdout.flush()

    # Proxy config — assembled from env vars; server/credentials never logged.
    _proxy_cfg: dict | None = None
    if _PROXY_SERVER:
        _proxy_cfg = {"server": _PROXY_SERVER}
        if _PROXY_USERNAME:
            _proxy_cfg["username"] = _PROXY_USERNAME
        if _PROXY_PASSWORD:
            _proxy_cfg["password"] = _PROXY_PASSWORD

    # Collect failed network request paths (query strings + fragments stripped — no tokens)
    failed_requests: list[str] = []
    console_errors: list[str] = []
    _nav_status: int | None = None

    async with async_playwright() as p:
        # ── Browser / context / page setup ──────────────────────────────────
        # Proxy setup errors are intentionally sanitized — the exception message
        # is NOT printed because it may contain the proxy server URL or credentials.
        # If any step fails, a safe diagnostic is logged and the probe exits 0.
        # The browser is closed cleanly if it was opened before the failure.
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                # Match the exact launch args used by daily_scrape.py scrapers
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            _ctx_kw: dict = {
                "accept_downloads": False,
                "viewport": {"width": 1400, "height": 900},
                "user_agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            }
            if _proxy_cfg is not None:
                _ctx_kw["proxy"] = _proxy_cfg  # absent entirely when no proxy is configured
            context = await browser.new_context(**_ctx_kw)
            page = await context.new_page()
        except Exception as _setup_exc:
            # Exception message intentionally suppressed — may contain proxy URL/credentials.
            print(
                f"[probe] Browser/context setup failed"
                f" ({type(_setup_exc).__name__})"
                "; probe could not continue."
            )
            sys.stdout.flush()
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            return  # _run_probe() returns None; main() still returns 0

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
            print(f"[probe] Navigation failed: {_exc_name(nav_exc)}")

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
            print(f"[probe] Wait error: {_exc_name(wait_exc)}")

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

        # Script src values — strip query strings + fragments for display;
        # keep raw values separately for absolute-URL resolution below.
        script_srcs: list[str] = []        # display-safe (stripped)
        _script_raw_srcs: list[str] = []   # original values for urljoin
        script_total = -1
        try:
            all_scripts = page.locator("script[src]")
            script_total = await all_scripts.count()
            for i in range(min(script_total, 10)):
                raw_src = await all_scripts.nth(i).get_attribute("src") or ""
                _script_raw_srcs.append(raw_src)
                script_srcs.append(raw_src.split("?")[0].split("#")[0])
        except Exception:
            pass

        # ── Fetch script asset responses ─────────────────────────────────────
        # Resolve each script src to an absolute URL and make a GET request.
        # Logs: HTTP status · content-type · content-length · body excerpt.
        # Does NOT log cookies, auth headers, or any other headers.
        script_assets: list[dict] = []
        for _raw in _script_raw_srcs:
            # Resolve to absolute URL first; strip query + fragment for display and fetching.
            _abs_url = urllib.parse.urljoin(current_url, _raw)
            _display_url = _abs_url.split("?")[0].split("#")[0]
            _asset: dict = {
                "display_url": _display_url,
                "status": None,
                "content_type": None,
                "content_length": None,
                "body_excerpt": None,
                "error": None,
            }
            try:
                _resp = await context.request.get(_display_url, timeout=10_000)
                _asset["status"] = _resp.status
                _asset["content_type"] = _resp.headers.get("content-type", "(none)")
                _asset["content_length"] = _resp.headers.get("content-length")
                try:
                    _body_bytes = await _resp.body()
                    _body_text = _body_bytes[:300].decode("utf-8", errors="replace")
                    # Collapse whitespace → single line so log stays readable
                    _body_line = " ".join(_body_text.split())
                    _asset["body_excerpt"] = _body_line[:120]
                except Exception as _be:
                    _asset["body_excerpt"] = f"(body read error: {_exc_name(_be)})"
            except Exception as _fe:
                _asset["status"] = "fetch_failed"
                _asset["error"] = _exc_name(_fe)
            script_assets.append(_asset)

        # ── Main.js header variant probes ────────────────────────────────────
        # Fetch /main.js three times with different header sets to determine
        # whether the server distinguishes requests by browser-like headers
        # (User-Agent, Accept, Sec-Fetch-*) or blocks at IP/egress level.
        # Request headers are NOT printed — only the variant name is logged.
        # Does NOT log cookies, auth headers, or any other headers.
        _MAIN_JS_URL = _IRAS_BASE + "/main.js"
        _UA_CHROME = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        # Each entry: name + optional headers dict (None = Playwright defaults only)
        _VARIANTS: list[dict] = [
            {
                "name": "1-minimal",
                "headers": None,
            },
            {
                "name": "2-browser-ish",
                "headers": {
                    "User-Agent": _UA_CHROME,
                    "Accept": (
                        "text/javascript, application/javascript, "
                        "application/ecmascript, */*;q=0.8"
                    ),
                    "Referer": "https://iras.iocliras.in/login",
                    "Origin": "https://iras.iocliras.in",
                    "Sec-Fetch-Dest": "script",
                    "Sec-Fetch-Mode": "no-cors",
                    "Sec-Fetch-Site": "same-origin",
                },
            },
            {
                "name": "3-curl-ish",
                "headers": {
                    "Accept": "application/javascript, */*",
                    "Referer": "https://iras.iocliras.in/login",
                },
            },
        ]
        mainjs_variants: list[dict] = []
        for _var in _VARIANTS:
            _vr: dict = {
                "name": _var["name"],
                "url": _MAIN_JS_URL,
                "status": None,
                "content_type": None,
                "content_length": None,
                "body_excerpt": None,
                "error": None,
            }
            try:
                # Only pass headers kwarg when the variant specifies custom headers;
                # omitting it lets Playwright use its context defaults (variant 1).
                _fetch_kw: dict = {"timeout": 10_000}
                if _var["headers"] is not None:
                    _fetch_kw["headers"] = _var["headers"]
                _vresp = await context.request.get(_MAIN_JS_URL, **_fetch_kw)
                _vr["status"] = _vresp.status
                _vr["content_type"] = _vresp.headers.get("content-type", "(none)")
                _vr["content_length"] = _vresp.headers.get("content-length")
                try:
                    _vbody = await _vresp.body()
                    _vtext = _vbody[:300].decode("utf-8", errors="replace")
                    _vline = " ".join(_vtext.split())
                    _vr["body_excerpt"] = _vline[:120]
                except Exception as _vbe:
                    _vr["body_excerpt"] = f"(body read error: {_exc_name(_vbe)})"
            except Exception as _vfe:
                _vr["status"] = "fetch_failed"
                _vr["error"] = _exc_name(_vfe)
            mainjs_variants.append(_vr)

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
    if script_assets:
        print()
        print("  SCRIPT ASSET RESPONSES")
        print("  " + "-" * 40)
        for _a in script_assets:
            print(f"    url      : {_a['display_url']}")
            print(f"    status   : {_a['status']}")
            print(f"    c-type   : {_a['content_type']}")
            if _a["content_length"] is not None:
                print(f"    c-len    : {_a['content_length']}")
            if _a["body_excerpt"] is not None:
                print(f"    body     : {_a['body_excerpt']!r}")
            if _a["error"] is not None:
                print(f"    error    : {_a['error']}")
        print()
    if mainjs_variants:
        print()
        print("  MAIN.JS HEADER VARIANT PROBES")
        print("  " + "-" * 40)
        for _v in mainjs_variants:
            print(f"    variant  : {_v['name']}")
            print(f"    url      : {_v['url']}")
            print(f"    status   : {_v['status']}")
            print(f"    c-type   : {_v['content_type']}")
            if _v["content_length"] is not None:
                print(f"    c-len    : {_v['content_length']}")
            if _v["body_excerpt"] is not None:
                print(f"    body     : {_v['body_excerpt']!r}")
            if _v["error"] is not None:
                print(f"    error    : {_v['error']}")
        print()
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
    # Catch-all: prevents any unhandled exception (e.g. Playwright startup failure)
    # from reaching sys.exit with a nonzero code. Exception messages are suppressed
    # to avoid accidentally leaking proxy credentials or other sensitive config.
    try:
        asyncio.run(_run_probe())
    except Exception as _exc:
        print(
            f"[probe] Unexpected error ({type(_exc).__name__})"
            "; probe could not complete."
        )
        sys.stdout.flush()
    return 0   # Always 0 — this is a diagnostic tool, not a pass/fail test


if __name__ == "__main__":
    sys.exit(main())
