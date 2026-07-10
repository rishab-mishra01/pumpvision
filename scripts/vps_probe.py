#!/usr/bin/env python3
"""
VPS reachability probe — IRAS + Paytm + SDMS.

Answers one question: from this machine's IP, do the three portals actually
render, or are we being geo/ASN-filtered?

This probe does NOT log in.  It reads no credentials, solves no CAPTCHA,
saves no cookies or session state, and writes nothing to the database.
It always exits 0 — a blocked site is a *finding*, not a crash.

By default it runs with NO PROXY, even if IRAS_PROXY_SERVER etc. are set in
the environment.  That is the entire point: we want to know whether the raw
VPS egress IP can reach these sites.  Pass --proxy to opt in to the configured
proxy for an A/B comparison.

Two things this probe gets right that a naive "does the page load" check does not:

  1. Paytm's login form is injected into an accounts.paytm.com IFRAME after JS
     runs (see _login() in scrapers/paytm_exporter.py).  dashboard.paytm.com can
     serve a perfectly good 200 while accounts.paytm.com is blocked — and login
     would still be impossible.  We poll the frame tree, not just the document.

  2. Only SAME-ORIGIN script assets count toward the verdict.  These pages pull
     in Google Tag Manager, DoubleClick and analytics beacons that routinely
     fail or 400 for reasons that have nothing to do with geo-blocking.  Those
     are reported separately and never affect the verdict.

Usage:
    python -X utf8 scripts/vps_probe.py             # direct, all three sites
    python -X utf8 scripts/vps_probe.py --proxy     # via IRAS_PROXY_* env vars
    python -X utf8 scripts/vps_probe.py --only iras # single site

Optional env vars (read-only, non-credential):
    IRAS_URL   Base URL of the IRAS portal (default: https://iras.iocliras.in)
"""

import argparse
import asyncio
import os
import sys
import urllib.parse

_NAV_TIMEOUT_MS   = 30_000
_SETTLE_MS        = 15_000   # best-effort networkidle so SPA/iframe injection happens
_FORM_TIMEOUT_MS  = 20_000
_FRAME_TIMEOUT_S  = 45       # how long to wait for a cross-origin login iframe
_ASSET_TIMEOUT_MS = 10_000
_MAX_ASSETS       = 4

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Mirrors the selector list in _autonomous_login() in scrapers/daily_scrape.py
_GENERIC_FORM = (
    "input[type='password'], "
    "input[name='username'], input[name='userId'], input[name='email'], "
    "form, "
    "img[src*='captcha'], img[id*='captcha'], img[class*='captcha'], "
    "canvas"
)

# Mirrors the selectors used by _login() in scrapers/paytm_exporter.py
_PAYTM_FORM = (
    "input[placeholder='Enter your Mobile Number or Email'], "
    "input[type='password'], "
    "input:visible"
)

_IRAS_BASE = os.environ.get("IRAS_URL", "https://iras.iocliras.in").rstrip("/")


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower()
    except Exception:
        return ""


TARGETS: dict[str, dict] = {
    "iras": {
        "url": _IRAS_BASE if _IRAS_BASE.endswith("/login") else _IRAS_BASE + "/login",
        # Same-origin hosts whose assets genuinely matter for this portal.
        "asset_hosts": {_host(_IRAS_BASE)},
        "form_frame_host": None,
        "form_selectors": _GENERIC_FORM,
        "extra_origins": [],
    },
    "paytm": {
        "url": "https://dashboard.paytm.com/login/",
        "asset_hosts": {"dashboard.paytm.com", "accounts.paytm.com", "webappsstatic.paytm.com"},
        # The real login form lives here, in a cross-origin iframe.
        "form_frame_host": "accounts.paytm.com",
        "form_selectors": _PAYTM_FORM,
        # If the iframe never appears we still want to know: is this host reachable?
        "extra_origins": ["https://accounts.paytm.com/"],
    },
    "sdms": {
        "url": "https://sdms.indianoil.in/sdmspro/auth/login",
        "asset_hosts": {"sdms.indianoil.in"},
        "form_frame_host": None,
        "form_selectors": _GENERIC_FORM,
        "extra_origins": [],
    },
}


def _exc_name(exc: BaseException) -> str:
    """Return only the exception class name — never str(exc).

    Playwright/HTTPX exception messages can embed the proxy host, port, or
    full proxy URL (e.g. net::ERR_TUNNEL_CONNECTION_FAILED at http://host:33335).
    All exception reporting in this probe goes through this helper.
    """
    return exc.__class__.__name__


def _strip(url: str) -> str:
    """Drop query string and fragment so signed URLs / tokens are never logged."""
    return url.split("?")[0].split("#")[0]


def _proxy_cfg_from_env() -> dict | None:
    """Build a Playwright proxy dict from IRAS_PROXY_* env vars, or None."""
    server = os.environ.get("IRAS_PROXY_SERVER", "").strip()
    if not server:
        return None
    cfg: dict = {"server": server}
    username = os.environ.get("IRAS_PROXY_USERNAME", "").strip()
    password = os.environ.get("IRAS_PROXY_PASSWORD", "").strip()
    if username:
        cfg["username"] = username
    if password:
        cfg["password"] = password
    return cfg


def _classify(r: dict) -> tuple[str, str]:
    """Map a probe result to (verdict, one-line reason).

    Only same-origin evidence feeds the verdict. Third-party analytics noise
    (GTM, DoubleClick, google-analytics) is deliberately excluded.
    """
    status = r["nav_status"]
    if r["nav_error"] and status is None:
        return "NETWORK_FAIL", f"navigation raised {r['nav_error']}"
    if isinstance(status, int) and status >= 400:
        return "HTTP_BLOCKED", f"document returned HTTP {status}"
    if r["html_len"] is not None and r["html_len"] < 500:
        return "NETWORK_FAIL", f"document only {r['html_len']} bytes"

    # Cross-origin login iframe (Paytm): its absence is decisive.
    if r["form_frame_host"]:
        if not r["frame_found"]:
            extra = "; ".join(
                f"{o['url']}→{o['status']}" for o in r["extra_origins"]
            ) or "no reachability data"
            return "HTTP_BLOCKED", f"login iframe {r['form_frame_host']} never appeared ({extra})"
        if not r["form_found"]:
            return "RENDERED_NO_FORM", f"iframe {r['form_frame_host']} loaded but no input appeared"
        return "RENDERED", f"login form present inside {r['form_frame_host']} iframe"

    if r["form_found"]:
        return "RENDERED", "login form element present"

    bad = [a for a in r["assets"] if a["status"] != 200]
    if bad:
        return "RENDERED_NO_FORM", f"{len(bad)} same-origin script asset(s) did not return 200"
    return "RENDERED_NO_FORM", "HTML served but no form element appeared"


async def _egress_ip(context) -> None:
    """Print the egress IP + geo + ASN as this machine appears to the internet.

    Sends nothing but a plain GET. Confirms the exit node is really in India,
    and shows which ASN the portals will judge us by (datacenter vs residential).
    """
    print("\n" + "=" * 64)
    print("  EGRESS IDENTITY")
    print("=" * 64)
    try:
        resp = await context.request.get("https://ipinfo.io/json", timeout=_ASSET_TIMEOUT_MS)
        if resp.status != 200:
            print(f"  lookup failed : HTTP {resp.status}")
            return
        data = await resp.json()
        for key in ("ip", "city", "region", "country", "org", "timezone"):
            if key in data:
                print(f"  {key:<13} : {data[key]}")
        if data.get("country") != "IN":
            print("  >> WARNING: egress country is not IN. India-geo portals will block this.")
    except Exception as exc:
        print(f"  lookup failed : {_exc_name(exc)}")
    sys.stdout.flush()


async def _find_login_frame(page, frame_host: str):
    """Poll the frame tree for a frame served from `frame_host`.

    Paytm injects its login iframe only after page JS has run, so this can take
    several seconds. Mirrors the 60x1s poll in scrapers/paytm_exporter.py.
    """
    for _ in range(_FRAME_TIMEOUT_S):
        for frame in page.frames:
            if frame_host in (frame.url or ""):
                return frame
        await page.wait_for_timeout(1_000)
    return None


async def _probe_site(context, name: str, cfg: dict) -> dict:
    """Navigate to one login page and collect diagnostics. Never logs in."""
    url = cfg["url"]
    asset_hosts: set[str] = cfg["asset_hosts"]
    frame_host: str | None = cfg["form_frame_host"]

    print("\n" + "=" * 64)
    print(f"  PROBE: {name.upper()}")
    print("=" * 64)
    print(f"  target : {url}")
    if frame_host:
        print(f"  iframe : login form expected inside {frame_host}")
    sys.stdout.flush()

    r: dict = {
        "name": name, "url": url, "form_frame_host": frame_host,
        "nav_status": None, "nav_error": None,
        "frame_found": frame_host is None, "form_found": False,
        "final_url": None, "title": None, "html_len": None, "body_excerpt": None,
        "input_count": -1, "frames": [], "assets": [], "third_party_assets": [],
        "extra_origins": [], "failed_same_origin": [], "failed_third_party": [],
        "console_errors": [],
    }

    page = await context.new_page()

    def _on_request_failed(req) -> None:
        entry = f"{req.method} {_strip(req.url)}"
        if _host(req.url) in asset_hosts:
            r["failed_same_origin"].append(entry)
        else:
            r["failed_third_party"].append(entry)

    def _on_console(msg) -> None:
        if msg.type == "error":
            r["console_errors"].append(msg.text[:200])

    page.on("requestfailed", _on_request_failed)
    page.on("console", _on_console)

    # ── Navigate ─────────────────────────────────────────────────────────────
    # domcontentloaded, not networkidle: one blocked third-party beacon can hold
    # networkidle open for the full timeout and mask a successful page load.
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        r["nav_status"] = resp.status if resp is not None else None
    except Exception as exc:
        r["nav_error"] = _exc_name(exc)
        print(f"  [nav] failed: {r['nav_error']}")

    # Give SPA hydration / iframe injection a chance. Best-effort — never fatal.
    try:
        await page.wait_for_load_state("networkidle", timeout=_SETTLE_MS)
    except Exception:
        pass

    # ── Locate the form, in the right frame ──────────────────────────────────
    search_ctx = page
    if frame_host:
        frame = await _find_login_frame(page, frame_host)
        if frame is not None:
            r["frame_found"] = True
            search_ctx = frame
            print(f"  [frame] found: {_strip(frame.url)}")
        else:
            print(f"  [frame] {frame_host} iframe never appeared after {_FRAME_TIMEOUT_S}s")

    if r["frame_found"]:
        try:
            await search_ctx.wait_for_selector(cfg["form_selectors"], timeout=_FORM_TIMEOUT_MS)
            r["form_found"] = True
        except Exception:
            pass

    # ── Collect page facts ───────────────────────────────────────────────────
    try:
        r["final_url"] = page.url
    except Exception:
        pass
    try:
        r["title"] = await page.title()
    except Exception:
        r["title"] = "(unavailable)"
    try:
        r["html_len"] = len(await page.content())
    except Exception:
        pass
    try:
        body = (await page.locator("body").inner_text(timeout=2000)).strip()
        r["body_excerpt"] = " ".join(body.split())[:250]
    except Exception:
        r["body_excerpt"] = "(unavailable)"
    try:
        r["input_count"] = await search_ctx.locator("input").count()
    except Exception:
        pass
    try:
        r["frames"] = [_strip(f.url) for f in page.frames if f.url]
    except Exception:
        pass

    # ── Fetch same-origin script assets ──────────────────────────────────────
    # HTML often comes off a CDN edge while the JS bundle is served from origin.
    # A 200 on the document plus a 403 on the bundle is the signature of an
    # origin-side geo/ASN filter, invisible if you only check the document.
    try:
        scripts = page.locator("script[src]")
        total = await scripts.count()
        checked = 0
        for i in range(total):
            if checked >= _MAX_ASSETS:
                break
            raw = await scripts.nth(i).get_attribute("src") or ""
            abs_url = _strip(urllib.parse.urljoin(r["final_url"] or url, raw))
            if _host(abs_url) not in asset_hosts:
                r["third_party_assets"].append(abs_url)
                continue
            checked += 1
            asset: dict = {"url": abs_url, "status": None, "content_type": None, "error": None}
            try:
                aresp = await context.request.get(abs_url, timeout=_ASSET_TIMEOUT_MS)
                asset["status"] = aresp.status
                asset["content_type"] = aresp.headers.get("content-type", "(none)")
            except Exception as exc:
                asset["status"] = "fetch_failed"
                asset["error"] = _exc_name(exc)
            r["assets"].append(asset)
    except Exception:
        pass

    # ── Extra origin reachability (e.g. accounts.paytm.com) ──────────────────
    for origin in cfg["extra_origins"]:
        entry: dict = {"url": origin, "status": None, "error": None}
        try:
            oresp = await context.request.get(origin, timeout=_ASSET_TIMEOUT_MS)
            entry["status"] = oresp.status
        except Exception as exc:
            entry["status"] = "fetch_failed"
            entry["error"] = _exc_name(exc)
        r["extra_origins"].append(entry)

    await page.close()

    # ── Report ───────────────────────────────────────────────────────────────
    print(f"  nav status    : {r['nav_status']}")
    print(f"  nav error     : {r['nav_error'] or 'none'}")
    if frame_host:
        print(f"  iframe found  : {r['frame_found']}")
    print(f"  form found    : {r['form_found']}")
    print(f"  final URL     : {r['final_url']}")
    print(f"  page title    : {r['title']!r}")
    print(f"  HTML length   : {r['html_len']} bytes")
    print(f"  input count   : {r['input_count']}  (in {'iframe' if frame_host else 'document'})")
    print(f"  body excerpt  : {r['body_excerpt']!r}")
    if r["frames"]:
        print(f"  frames        : {len(r['frames'])}")
        for f in r["frames"][:8]:
            print(f"    {f}")
    if r["extra_origins"]:
        print("  extra origins :")
        for o in r["extra_origins"]:
            line = f"    [{o['status']}] {o['url']}"
            if o["error"]:
                line += f"  error={o['error']}"
            print(line)
    if r["assets"]:
        print("  same-origin script assets (these count):")
        for a in r["assets"]:
            line = f"    [{a['status']}] {a['url']}"
            if a["content_type"]:
                line += f"  ({a['content_type']})"
            if a["error"]:
                line += f"  error={a['error']}"
            print(line)
    else:
        print("  same-origin script assets: none found")
    if r["third_party_assets"]:
        print(f"  third-party assets ignored: {len(r['third_party_assets'])}")
    if r["failed_same_origin"]:
        print(f"  FAILED same-origin requests: {len(r['failed_same_origin'])}")
        for f in r["failed_same_origin"][:10]:
            print(f"    {f}")
    else:
        print("  failed same-origin requests: none")
    if r["failed_third_party"]:
        print(f"  failed third-party requests: {len(r['failed_third_party'])} (ignored — analytics/ads)")
    if r["console_errors"]:
        print(f"  console errors: {len(r['console_errors'])}")
        for e in r["console_errors"][:6]:
            print(f"    {e}")
    sys.stdout.flush()

    return r


async def _run(sites: list[str], use_proxy: bool) -> None:
    from playwright.async_api import async_playwright

    proxy_cfg = _proxy_cfg_from_env() if use_proxy else None

    print("=" * 64)
    print("  Pumpvision — VPS Reachability Probe")
    print("=" * 64)
    print(f"  sites   : {', '.join(sites)}")
    print(f"  proxy   : {'yes' if proxy_cfg else 'no (direct egress)'}")
    if use_proxy and proxy_cfg is None:
        print("  >> --proxy passed but IRAS_PROXY_SERVER is unset; running direct.")
    print("  login   : NO — this probe never authenticates")
    print("=" * 64)
    sys.stdout.flush()

    results: list[dict] = []

    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx_kw: dict = {
                "accept_downloads": False,
                "viewport": {"width": 1400, "height": 900},
                "user_agent": _UA,
            }
            if proxy_cfg is not None:
                ctx_kw["proxy"] = proxy_cfg
            context = await browser.new_context(**ctx_kw)
        except Exception as exc:
            # Message suppressed — may contain the proxy URL or credentials.
            print(f"\n[probe] Browser/context setup failed ({_exc_name(exc)}); cannot continue.")
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            return

        await _egress_ip(context)

        for name in sites:
            try:
                results.append(await _probe_site(context, name, TARGETS[name]))
            except Exception as exc:
                print(f"  [probe] {name} raised {_exc_name(exc)}")
                results.append({
                    "name": name, "url": TARGETS[name]["url"],
                    "form_frame_host": TARGETS[name]["form_frame_host"],
                    "nav_status": None, "nav_error": _exc_name(exc),
                    "frame_found": False, "form_found": False,
                    "html_len": None, "assets": [], "extra_origins": [],
                })

        await browser.close()

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print("  VERDICT")
    print("=" * 64)
    for r in results:
        verdict, reason = _classify(r)
        print(f"  {r['name']:<6} : {verdict:<16} — {reason}")
    print("=" * 64)
    print("  RENDERED         → portal is reachable from this IP. Good.")
    print("  RENDERED_NO_FORM → HTML served but form/assets missing. Partial filter.")
    print("  HTTP_BLOCKED     → server actively refused. Geo/ASN filter likely.")
    print("  NETWORK_FAIL     → no usable connection. DNS, firewall, or drop.")
    print("=" * 64)
    print("  Third-party analytics failures are excluded from the verdict by design.")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe IRAS/Paytm/SDMS reachability. Never logs in.")
    parser.add_argument(
        "--only",
        choices=sorted(TARGETS),
        action="append",
        help="Probe only this site (repeatable). Default: all three.",
    )
    parser.add_argument(
        "--proxy",
        action="store_true",
        help="Route through IRAS_PROXY_* env vars instead of direct egress.",
    )
    args = parser.parse_args()

    sites = args.only or list(TARGETS)

    # Always exit 0 — a blocked portal is a finding to report, not a crash.
    try:
        asyncio.run(_run(sites, args.proxy))
    except Exception as exc:
        print(f"[probe] Unexpected error ({_exc_name(exc)}); probe could not complete.")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
