"""
Debug: Click Show on ISS tab and inspect what loads.
Uses today's date/time to maximise chance of real data.
"""
import asyncio, sys, io, json
from pathlib import Path
from datetime import datetime, timedelta
from playwright.async_api import async_playwright

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = Path(r"C:\IRAS_Data\ISS\debug")

FIND_TABLE = (
    "() => {"
    "  const r = [];"
    "  const sels = ["
    "    '[class*=ag-row]', '[class*=ag-cell]', '[class*=ag-grid]',"
    "    '[class*=MuiDataGrid]', '[class*=DataGrid]',"
    "    'table', 'tbody', 'tr', '[role=row]', '[role=grid]',"
    "    '[class*=table]', '[class*=Table]'"
    "  ];"
    "  sels.forEach(sel => {"
    "    const cnt = document.querySelectorAll(sel).length;"
    "    if (cnt > 0) r.push({sel, cnt});"
    "  });"
    "  return r;"
    "}"
)

FIND_NODATA = (
    "() => {"
    "  const r = [];"
    "  document.querySelectorAll('*').forEach(el => {"
    "    const t = (el.innerText || '').trim();"
    "    if (t && t.length < 120 && el.children.length === 0"
    "        && (t.toLowerCase().includes('no data') || t.toLowerCase().includes('no rows')"
    "            || t.toLowerCase().includes('no record') || t.toLowerCase().includes('loading')))"
    "      r.push({tag: el.tagName, text: t, cls: el.className.slice(0,60)});"
    "  });"
    "  return r.slice(0,10);"
    "}"
)

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await ctx.new_page()

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

        # Click ... overflow tab
        overflow = page.locator("button[role='tab']:has-text('...')")
        await overflow.wait_for(state="visible", timeout=10_000)
        await overflow.click()
        await page.wait_for_timeout(1000)

        # Click Issue(ISS)
        iss = page.locator("li.app-tab-list:has-text('Issue(ISS)')")
        await iss.wait_for(state="visible", timeout=5_000)
        await iss.click()
        await page.wait_for_timeout(2000)
        print("On ISS tab")

        # Set As Per
        as_per = page.locator("div[role='combobox'][aria-labelledby*='As Per']")
        await as_per.wait_for(state="visible", timeout=5_000)
        await as_per.click()
        await page.wait_for_timeout(600)
        opt = page.locator("li[role='option']").filter(has_text="Actual")
        await opt.first.wait_for(state="visible", timeout=5_000)
        await opt.first.click()
        await page.wait_for_timeout(400)

        # Screenshot BEFORE setting dates (to see current state)
        await page.screenshot(path=str(OUT / "10_iss_before_dates.png"), full_page=False)

        # Use today minus 1 hour for a window likely to have data
        now = datetime.now()
        one_hr_ago = now - timedelta(hours=1)
        from_val = one_hr_ago.strftime("%d-%m-%Y %I:%M:00 %p").lower()
        to_val   = now.strftime("%d-%m-%Y %I:%M:00 %p").lower()
        print(f"Setting From: {from_val}")
        print(f"Setting To  : {to_val}")

        # Set From Date
        from_field = page.get_by_label("From Date")
        await from_field.click()
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(100)
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(100)
        await from_field.type(from_val, delay=40)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(500)

        # Read back what the field shows now
        from_actual = await from_field.input_value()
        print(f"From field actual value: {from_actual!r}")

        # Set To Date
        to_field = page.get_by_label("To Date")
        await to_field.click()
        await page.keyboard.press("Control+a")
        await page.wait_for_timeout(100)
        await page.keyboard.press("Delete")
        await page.wait_for_timeout(100)
        await to_field.type(to_val, delay=40)
        await page.keyboard.press("Tab")
        await page.wait_for_timeout(500)

        to_actual = await to_field.input_value()
        print(f"To field actual value  : {to_actual!r}")

        # Screenshot AFTER setting dates
        await page.screenshot(path=str(OUT / "11_iss_dates_set.png"), full_page=False)

        # Click Show
        show_btn = page.locator("button:has-text('Show')").first
        print("Clicking Show...")
        await show_btn.click()

        # Wait 8 seconds and then inspect what loaded
        for i in range(8):
            await page.wait_for_timeout(1000)
            tbl = await page.evaluate(FIND_TABLE)
            if tbl:
                print(f"  t+{i+1}s: {tbl}")

        await page.screenshot(path=str(OUT / "12_iss_after_show.png"), full_page=False)
        print("Screenshot saved: 12_iss_after_show.png")

        # Dump table structure
        tbl = await page.evaluate(FIND_TABLE)
        print(f"\nTable elements found: {json.dumps(tbl, indent=2)}")

        nodata = await page.evaluate(FIND_NODATA)
        print(f"\nNo-data / loading messages: {json.dumps(nodata, indent=2)}")

        # Also dump all buttons visible (to find correct Excel/export button)
        btns = await page.evaluate(
            "() => { const r = []; document.querySelectorAll('button').forEach(b => {"
            "  const t = (b.innerText||'').trim(); const rect = b.getBoundingClientRect();"
            "  if (t && rect.width > 0) r.push({text:t, cls:b.className.slice(0,70)});"
            "}); return r; }"
        )
        print(f"\nVisible buttons: {json.dumps(btns, indent=2)}")

        # Save page HTML
        html = await page.content()
        (OUT / "13_iss_after_show.html").write_text(html, encoding="utf-8")
        print(f"\nHTML saved: {OUT / '13_iss_after_show.html'}")

        print("\nDone. Closing in 60s...")
        await page.wait_for_timeout(60_000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())
