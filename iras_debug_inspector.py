"""
IRAS Debug Inspector
====================
Logs in, then dumps page structure to help fix navigation selectors.
Run this, solve the CAPTCHA, then inspect the output files.
"""

import asyncio
import json
import sys
import io
from pathlib import Path
from playwright.async_api import async_playwright

# Force UTF-8 output on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

IRAS_URL    = "https://iras.iocliras.in/login"
USERNAME    = "206858"
PASSWORD    = "Shree@26"
OUT         = Path(r"C:\IRAS_Data\ISS\debug")

async def main():
    OUT.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page    = await context.new_page()

        await page.goto(IRAS_URL, wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # Pre-fill credentials
        try:
            await page.fill("input[type='text']", USERNAME)
            await page.fill("input[type='password']", PASSWORD)
            print("✓ Credentials pre-filled — solve CAPTCHA and click Login")
        except Exception as e:
            print(f"Could not pre-fill: {e}")

        # Wait for login (any URL that is NOT the login page)
        print("\nWaiting for you to complete login...")
        try:
            await page.wait_for_function(
                "() => !window.location.href.includes('/login')",
                timeout=120_000
            )
        except Exception:
            print("Trying dashboard URL pattern...")
            await page.wait_for_url("**/*", timeout=120_000)

        print(f"✓ Login done — URL: {page.url}")
        await page.wait_for_timeout(2000)

        # Screenshot 1: post-login dashboard
        sc1 = str(OUT / "01_after_login.png")
        await page.screenshot(path=sc1, full_page=True)
        print(f"Screenshot saved: {sc1}")

        # Dump all visible text links / nav items
        nav_items = await page.evaluate("""() => {
            const results = [];
            // All anchor tags
            document.querySelectorAll('a').forEach(el => {
                const t = el.innerText.trim();
                if (t) results.push({tag: 'a', text: t, href: el.href, class: el.className});
            });
            // All buttons
            document.querySelectorAll('button').forEach(el => {
                const t = el.innerText.trim();
                if (t) results.push({tag: 'button', text: t, class: el.className});
            });
            // All li items (menu items)
            document.querySelectorAll('li').forEach(el => {
                const t = el.innerText.trim().split('\\n')[0];
                if (t && t.length < 80) results.push({tag: 'li', text: t, class: el.className, id: el.id});
            });
            // All span with text
            document.querySelectorAll('span').forEach(el => {
                const t = el.innerText.trim();
                if (t && t.length < 60 && el.children.length === 0)
                    results.push({tag: 'span', text: t, class: el.className});
            });
            return results;
        }""")

        with open(OUT / "02_nav_items.json", "w") as f:
            json.dump(nav_items, f, indent=2)
        print(f"Nav items dumped: {OUT / '02_nav_items.json'} ({len(nav_items)} items)")

        # Print anything that looks like a menu/nav item
        print("\n--- Potentially relevant nav elements ---")
        keywords = ["fcc", "iss", "issue", "data", "report", "menu", "nav"]
        for item in nav_items:
            t = item.get("text", "").lower()
            if any(k in t for k in keywords):
                print(f"  {item}")

        # Dump full page HTML
        html = await page.content()
        html_path = OUT / "03_page.html"
        html_path.write_text(html, encoding="utf-8")
        print(f"\nFull HTML saved: {html_path}")

        # Try clicking FCC Data with different strategies
        print("\n--- Trying to find FCC Data ---")
        strategies = [
            "text=FCC Data",
            "text=FCC",
            "[aria-label*='FCC']",
            "a:has-text('FCC')",
            "li:has-text('FCC Data')",
            ".p-menuitem:has-text('FCC')",
            "span:has-text('FCC Data')",
        ]
        for sel in strategies:
            try:
                count = await page.locator(sel).count()
                print(f"  {sel!r:45s} → {count} match(es)")
            except Exception as e:
                print(f"  {sel!r:45s} → ERROR: {e}")

        # Try the most promising one
        print("\n--- Attempting navigation ---")
        clicked = False
        for sel in ["text=FCC Data", "a:has-text('FCC Data')", "span:has-text('FCC Data')",
                    "li:has-text('FCC Data') a", ".p-menuitem:has-text('FCC Data')"]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    print(f"  Clicking: {sel!r}")
                    await el.click()
                    clicked = True
                    await page.wait_for_timeout(1000)
                    break
            except Exception as e:
                print(f"  {sel!r} not found: {e}")

        if clicked:
            sc2 = str(OUT / "04_after_fcc_click.png")
            await page.screenshot(path=sc2, full_page=True)
            print(f"Screenshot after FCC click: {sc2}")

            # Now look for Issue(ISS)
            print("\n--- Looking for Issue(ISS) ---")
            for sel in ["text=Issue(ISS)", "text=ISS", "a:has-text('ISS')",
                        "li:has-text('ISS')", "span:has-text('ISS')",
                        ".p-menuitem:has-text('ISS')"]:
                try:
                    count = await page.locator(sel).count()
                    print(f"  {sel!r:45s} → {count} match(es)")
                except Exception as e:
                    print(f"  {sel!r:45s} → ERROR: {e}")

            # Dump items visible after FCC click
            items_after = await page.evaluate("""() => {
                const results = [];
                document.querySelectorAll('a, li, span, .p-menuitem').forEach(el => {
                    const t = el.innerText.trim().split('\\n')[0];
                    if (t && t.length < 80)
                        results.push({tag: el.tagName, text: t, class: el.className});
                });
                return results;
            }""")
            with open(OUT / "05_after_fcc_items.json", "w") as f:
                json.dump(items_after, f, indent=2)
            print(f"Items after FCC click: {OUT / '05_after_fcc_items.json'}")

        print(f"\n✓ Debug complete. Check files in: {OUT}")
        print("Press Ctrl+C to close, or wait 60s...")
        await page.wait_for_timeout(60_000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
