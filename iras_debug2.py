"""Debug: click ... overflow tab and find ISS."""
import asyncio, json, sys, io
from pathlib import Path
from playwright.async_api import async_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
OUT = Path(r"C:\IRAS_Data\ISS\debug")

FIND_MENU_ITEMS = (
    "() => {"
    "  const results = [];"
    "  const sel = \"[role='menuitem'], [role='option'], .MuiMenuItem-root, li\";"
    "  document.querySelectorAll(sel).forEach(el => {"
    "    const t = (el.innerText || '').trim().split('\\n')[0];"
    "    if (t && t.length < 100)"
    "      results.push({tag: el.tagName, text: t, cls: el.className, role: el.getAttribute('role')});"
    "  });"
    "  return results;"
    "}"
)

FIND_ISS = (
    "() => {"
    "  const results = [];"
    "  document.querySelectorAll('*').forEach(el => {"
    "    const t = (el.innerText || '').trim();"
    "    if (t && (t.includes('ISS') || t.includes('Issue')) && t.length < 200 && el.children.length <= 2)"
    "      results.push({tag: el.tagName, text: t.slice(0,100), cls: el.className, role: el.getAttribute('role')});"
    "  });"
    "  return results.slice(0,30);"
    "}"
)

FIND_ALL_VISIBLE = (
    "() => {"
    "  const results = [];"
    "  document.querySelectorAll(\"button, [role='tab'], [role='menuitem'], a\").forEach(el => {"
    "    const rect = el.getBoundingClientRect();"
    "    const t = (el.innerText || '').trim().split('\\n')[0];"
    "    if (t && t.length < 100 && rect.width > 0 && rect.height > 0)"
    "      results.push({tag: el.tagName, text: t, cls: el.className.slice(0,60), role: el.getAttribute('role')});"
    "  });"
    "  return results;"
    "}"
)


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()

        await page.goto("https://iras.iocliras.in/login", wait_until="networkidle")
        await page.wait_for_timeout(1500)
        try:
            await page.fill("input[type='text']", "206858")
            await page.fill("input[type='password']", "Shree@26")
        except Exception:
            pass

        print("Waiting for login...")
        await page.wait_for_function(
            "() => !window.location.href.includes('/login')", timeout=120_000
        )
        print(f"Logged in: {page.url}")
        await page.wait_for_timeout(2000)

        # Navigate to FCC Data
        await page.locator("text=FCC Data").click()
        await page.wait_for_timeout(2000)
        print(f"FCC Data page: {page.url}")

        # Click the ... overflow tab
        overflow_btn = page.locator("button[role='tab']:has-text('...')")
        cnt = await overflow_btn.count()
        print(f"Overflow '...' button count: {cnt}")

        if cnt > 0:
            await overflow_btn.click()
            await page.wait_for_timeout(1500)
            print("Clicked ... overflow")
            await page.screenshot(path=str(OUT / "07_after_overflow.png"), full_page=False)

            items = await page.evaluate(FIND_MENU_ITEMS)
            print(f"\nMenu items after overflow ({len(items)}):")
            for item in items:
                print(f"  {item}")

            iss = await page.evaluate(FIND_ISS)
            print(f"\nElements with ISS/Issue ({len(iss)}):")
            for e in iss:
                print(f"  {e}")

            all_vis = await page.evaluate(FIND_ALL_VISIBLE)
            print(f"\nAll visible interactive elements ({len(all_vis)}):")
            for e in all_vis:
                print(f"  {e}")

            # Save HTML after overflow click
            html = await page.content()
            (OUT / "08_after_overflow.html").write_text(html, encoding="utf-8")

            # Try to click ISS if found
            for sel in ["[role='menuitem']:has-text('ISS')",
                        "[role='option']:has-text('ISS')",
                        ".MuiMenuItem-root:has-text('ISS')",
                        "li:has-text('Issue')",
                        "text=Issue(ISS)"]:
                c = await page.locator(sel).count()
                if c > 0:
                    print(f"\nFound ISS with selector: {sel!r}")
                    await page.locator(sel).first.click()
                    await page.wait_for_timeout(2000)
                    await page.screenshot(path=str(OUT / "09_iss_page.png"), full_page=False)
                    html2 = await page.content()
                    (OUT / "09_iss_page.html").write_text(html2, encoding="utf-8")
                    print(f"ISS page URL: {page.url}")

                    # Look for form fields and buttons
                    form_els = await page.evaluate("""() => {
                        const r = [];
                        document.querySelectorAll("input, button, select, label").forEach(el => {
                            const t = (el.innerText || el.value || el.placeholder || el.textContent || "").trim();
                            if (t && t.length < 100)
                                r.push({tag: el.tagName, text: t.slice(0,80), type: el.type, cls: el.className.slice(0,60)});
                        });
                        return r;
                    }""")
                    print(f"\nForm elements on ISS page ({len(form_els)}):")
                    for fe in form_els:
                        print(f"  {fe}")
                    break
        else:
            print("No ... overflow button. Listing all tabs:")
            all_tabs = await page.evaluate("""() => {
                const r = [];
                document.querySelectorAll("[role='tab']").forEach(el => r.push({text: el.innerText, cls: el.className}));
                return r;
            }""")
            for t in all_tabs:
                print(f"  {t}")

        print("\nDone. Closing in 60s...")
        await page.wait_for_timeout(60_000)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
