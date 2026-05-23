> Always read this entire file before starting any task in this project.
> Visual references for every UI screen live in `docs/screens/`. Read them before
> implementing any template. For owner screens 10 and 15, the canonical visual
> reference is `docs/design/Owner_Screens.html` — not the design system PNG files.

# Pumpvision — Project Briefing

## What This Project Is

Pumpvision is a mobile-first management and operations platform for an IndianOil retail outlet (RO).
The outlet is **Shree Petroleum**, RO code **206858**, located in Rewa, Madhya Pradesh, India.
It is operated by the dealer's family (owner: father + son Rishab).

The goal is to give the owners a 360-degree real-time view of:
- Daily fuel sales and revenue (by product, by nozzle)
- Stock levels and delivery tracking
- Payment collection breakdown (cash vs card/UPI vs credit vs fleet card)
- Anomaly and theft detection
- Credit customer ledger and invoicing

The project is built with vibe coding (no professional software engineering background).
If successful, it will be sold as SaaS to other IndianOil (and HPCL/BPCL) dealers.

**Competitive context:** PetroByte is the main mobile-capable competitor (~₹3,990/year).
Pumpvision's differentiation: automated IRAS data ingestion, owner-first design, IOC-native
integration, distinct visual identity. Market: ~100,000 petrol pumps in India.

---

## Version Control

Hosted at **github.com/rishab-mishra01/pumpvision** (private). Default branch: `main`.
Every push to `main` auto-deploys to Railway.

Daily workflow:
```
git add .
git commit -m "Plain-English description"
git push
```

---

## Deployment (Live — May 2026)

| Item | Value |
|------|-------|
| Platform | Railway (paid tier) |
| Live URL | `web-production-a1322.up.railway.app` |
| Database | PostgreSQL on Railway (SQLite locally) |
| Auto-deploy | Every push to `main` |
| PWA | manifest.json + icons at `pumpvision/static/` |
| Owner login | `admin` / `shreeadmin2026` |
| Attendant login | `operations` / `shreeoperations2026` |

29 customers + 66 vehicles migrated to production PostgreSQL (May 2026).

---

## Products Sold at This Outlet

| Code | Full Name | Type | Data Source |
|------|-----------|------|-------------|
| HS | High Speed Diesel (HSD) | Diesel | IRAS ISS + nozzle map |
| MS | Motor Spirit | Petrol | IRAS ISS + nozzle map |
| X2 | Xtra Premium 95 (XP95) | Premium Petrol | IRAS ISS + nozzle map |
| XG | Xtra Green | Bio Diesel | IRAS ISS + nozzle map |
| CNG | Compressed Natural Gas | Gas | SDMS PAD scraper (CGD Rewa billing row, kg) — display source |

**CNG is active — not deferred.** CNG does not appear in IRAS nozzle or ISS tables.
**Display source:** `_cng_sdms()` in dashboard routes — reads `sdms_summaries` DB table first (Railway
production source of truth); falls back to local `data/sdms/sdms_pad_{date}_summary.json` for
local/debug compatibility. SDMS JSON files are local/debug artifacts only, not the production source.
**Attendant entries** (`cng_shift_readings`) are still collected at shift close and stored — kept for
future cross-checks — but are NOT used for dashboard or summary display.

---

## Payment Modes

- **Cash** — derived remainder; never recorded directly
- **Card + UPI** — Paytm POS machines; data from Paytm scraper
- **Credit** — fleet/institutional customers
- **Fleet card** — IOCL depot account; data from SDMS PAD scraper — **no manager entry needed**

### Payment Reconciliation Formula
```
Gross fuel sales (liquid fuel litres × RSP  +  CNG kg × RSP/kg)
+ Lube sales (cash component)
= Total revenue

Total revenue
= Cash (derived)
+ Paytm UPI + Card
+ Credit extended
+ Fleet card (SDMS PAD data)

Cash = Total revenue − Paytm − Credit − Fleet card
```

Fleet card swipes settle to the IOCL depot account (separate ledger from bank account).
Depot account reconciliation deferred to Stage 3.

---

## Three-User Model

One Flask app, one DB, one deployment. Role set at login via `users.role`.

### Owner (father / Rishab)
Dashboard, Daily Summary, Tanks, Credit management.
Reviews daily P&L, browses historical summaries, manages credit customers,
confirms bank transfer payments.

### Manager
Shift-contextual daily task checklist. Prescriptive — app tells him what to do.
**Stage 1:** Log expenses · Record payments received.
**Stage 2:** Log lube sales · Generate invoices.
**Does not log fleet card swipes** — fleet data comes from SDMS PAD scraper.

### Attendant
Credit sale flow · Shift close flow (including CNG totalizer reading).

---

## Hardware at the Outlet

### Underground Tanks
| Tank | Product | Capacity |
|------|---------|----------|
| 1 | HS | 20,000 L |
| 2 | MS | 20,000 L |
| 3 | X2 | 10,000 L |
| 4 | XG | 20,000 L |

All tanks: GVR MAG PLUS ATG probes. ATG refreshes every 30 minutes.
Tank 4 (XG): probe historically unreliable — data stored; UI warning not currently shown.
CNG has no underground tank.

### Liquid Fuel Nozzles
| DU | Pump | Nozzle | Product | Tank | Label |
|----|------|--------|---------|------|-------|
| 9 (MIDCO) | 1 | 7 | HS | 1 | HS1 |
| 9 (MIDCO) | 2 | 11 | XG | 4 | XG |
| 14 (MIDCO) | 3 | 17 | X2 | 3 | X2 |
| 14 (MIDCO) | 4 | 18 | MS | 2 | MS1 |
| 15 (GILBARCO) | 5 | 15 | MS | 2 | MS2 |
| 15 (GILBARCO) | 6 | 16 | HS | 1 | HS2 |

Only DU 9 has receipt printers. NPND interlock disabled on all pumps.

### CNG Dispenser
One nozzle. Not in IRAS. No ISS records. Unit: kg. RSP: static (`cng_rsp_per_kg` in `app_settings`).
Attendant label: **CNG** (no suffix — single nozzle).
No pump test deduction for CNG.

---

## CNG — Full Implementation Spec

### Data Sources (two separate streams)

**Display (dashboard + summary):** `_cng_sdms(op_date)` in `blueprints/dashboard/routes.py`.
Queries `sdms_summaries` DB table first (Railway production source of truth).
Falls back to `data/sdms/sdms_pad_{date}_summary.json` for local/debug compatibility.
Returns a `SimpleNamespace(kg_sold, rsp_per_kg, revenue)` so templates need no changes.
Returns `None` if no SDMS data for the date or `cng_kg_total ≤ 0`.
RSP source: `CNG_RSP_PER_KG` env var (default `93.40`) — stored in `sdms_summaries` row at scrape time.

**Attendant entry (stored, not displayed):** `cng_shift_readings` table. Collected at shift close.
Kept for future cross-checks and variance analysis. Not used by any dashboard route.

### How attendant entry works
The attendant enters the CNG meter reading (in kg) at shift close, exactly like liquid fuel.
Opening reading for day N = closing reading from day N−1. First-ever entry: manual opening.

### Schema: `cng_shift_readings` ✓ built (migration `2fc50a7d52a6`)
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| op_date | Date NOT NULL | Operational date |
| opening_reading | Float NOT NULL | Meter kg at 06:00 |
| closing_reading | Float NOT NULL | Meter kg at shift close |
| kg_sold | Float NOT NULL | closing − opening (computed) |
| rsp_per_kg | Float NOT NULL | Snapshot of `cng_rsp_per_kg` at entry time |
| revenue | Float NOT NULL | kg_sold × rsp_per_kg |
| submitted_by | Integer FK → users | |
| created_at | DateTime | |

### RSP
Static value: `CNG_RSP_PER_KG` env var (default `93.40`). Stored in SDMS JSON at scrape time.
Also mirrored to `app_settings` key `cng_rsp_per_kg` for attendant shift close RSP snapshot.
No IRAS price lookup. CNG RSP does not appear in the Price (PRM) table.

### Shift Close Flow
CNG appears as a 5th product tile on the product selection screen.
Selecting CNG → numpad directly (no DU selection step, same pattern as X2/XG).
Numpad unit label: **kg** (not L). Delta label: **kg sold**.
CNG row appears in shift close summary. Invalid if closing < opening → warn-100 block.

### Daily Summary
CNG row in FUEL SALES section:
`CNG  [kg_sold] kg  ×  ₹[rsp]/kg  =  ₹[revenue]`
Included in GROSS FUEL SALES subtotal.

---

## Data Sources

### Primary: IRAS Portal
**URL:** https://iras.iocliras.in · **Creds:** `.env` · **Automation:** Playwright (`scrapers/`)

ISS tab: 30-min export limit. Boundary mode (built) gives 06:00 totalizer readings.
Full 48-window scrape deferred to Stage 3.

#### IRAS Tables Used
| Tab | Sheet | Contents | Freq |
|-----|-------|----------|------|
| FCC > Product(PDM) | Product(PDM) | Code → name | Static |
| FCC > Tank(TKM) | Tank | Capacity, ATG details | Static |
| FCC > Pump | Pump | DU config | Static |
| FCC > Nozzle | Nozzle | Nozzle → pump → tank → product | Static |
| FCC > Price(PRM) | Price(PDM) | RSP per liquid fuel product | Daily 06:00 |
| FCC > Issue(ISS) | Issue(ISS) | Every liquid fuel transaction | Per transaction |
| FCC > Stock | Stock | ATG tank level snapshots | Every 30 min |
| FCC > Shift Totalizer | Shift Totalizer Record | Nozzle open/close totalizers | Daily |
| RDB > Invoice | RDB SAP Invoice | Depot invoice + density | Per delivery |
| FCC > SAP Invoice | SAP Invoice | Chamber breakdown, truck no. | Per delivery |
| FCC > Receipt | TT Receipt | Decanting: ATG pre/post | Per delivery |
| FCC > Receipt Density | Receipt Density Records | Hydrometer per chamber | Per delivery |
| FCC > Density Records | Density Records | Post-decant ATG density | Per delivery |

### ATG Scraper ✓ Built
`scrapers/iras_atg_exporter.py`. Scrapes IRAS Stock tab. Stores snapshots in `tank_readings`
table every 30 minutes. Integrated into `daily_scrape.py` as Job 5.
XG data: stored with `is_reliable = False`.
**Production data backfill pending** — `tank_readings` has 0 rows on Railway until scraper
is run locally against the Railway PostgreSQL `DATABASE_URL`.

### Paytm for Business
`scrapers/paytm_exporter.py` — headless Playwright, stealth. Job 0 in `daily_scrape.py`.
Downloads previous operational day's transaction CSV from `dashboard.paytm.com`.
Session: `scrapers/paytm_state.json`.

**OTP handling:** Paytm sends login OTP via SMS and email. The scraper reads it automatically
from Gmail using IMAP (`imaplib.IMAP4_SSL`, `GMAIL_ADDRESS` + `GMAIL_APP_PASSWORD` env vars).
Searches for `UNSEEN FROM "care@paytm.com" SUBJECT "One Time Password"`, skips emails > 600s old,
marks as Seen after extraction. If the saved session is still valid, OTP step is skipped entirely.

**Auto-import to DB:** After a successful CSV download, `_job_paytm()` in `daily_scrape.py`
automatically imports the CSV into the `paytm_transactions` table (insert-or-skip by `paytm_txn_id`).
No manual upload step needed.

**"Files to Download" panel:** The panel on the Paytm dashboard is collapsed by default.
Selectors `a[download]` find nothing until it is expanded. The scraper expands it via click
(step 6b), re-expands every 10 s during the download poll, and uses broad fallback selectors
(`a[href*="s3.amazonaws.com"]`, `a[href*="s3-ap-southeast"]`, `a[href*="s3-us-east"]`) in addition
to `a[download]` to detect the download link.

**Paytm download is not fully reliable.** Report generation or download can fail silently.
If the CSV already exists on disk, import it directly without re-downloading:
```
python -X utf8 scrapers/import_paytm_csv.py data/paytm/paytm_YYYY-MM-DD.csv
```
`scrapers/import_paytm_csv.py` reuses the existing parser (`_parse_paytm_csv`), skips rows with
duplicate `paytm_txn_id`, and is idempotent — safe to run multiple times on the same file.
Does not attempt any Paytm login or download.

### SDMS PAD Portal
`scrapers/sdms_pad_exporter.py` — headless Playwright, Claude Vision CAPTCHA. Job 4.
Fleet card posting totals + CSV. Session: `scrapers/sdms_state.json`.

**CNG extraction:** The scraper also extracts CNG sales from CGD Rewa billing rows.
Detection: `plant.upper().startswith("CGD")` AND `unit.upper() == "KG"`.
`compute_cng_summary(rows)` returns `(kg_total, revenue, count)`.
Output JSON includes: `cng_kg_total`, `cng_revenue`, `cng_rsp_per_kg`, `cng_count`.
RSP used: `CNG_RSP_PER_KG` env var (default `93.40`).

**DB persistence:** After each successful run, `save_summary_to_db()` upserts a `SdmsSummary`
row (idempotent by `op_date`). DB write is skipped if `DATABASE_URL` is not set or `--dry-run`
is active. SDMS JSON files are local/debug artifacts — `sdms_summaries` is the Railway
production source. `_fleet_total()` and `_cng_sdms()` in `dashboard/routes.py` read DB first.

---

## Scraper Orchestration Modes

### `--completed-shift` (production daily use)

```
python -X utf8 scrapers/daily_scrape.py --completed-shift --date YYYY-MM-DD
```

`--date` means **accounting op_date** (shift start date).
Example: `--date 2026-05-20` covers the completed shift `2026-05-20 06:00 → 2026-05-21 05:59`.

What it coordinates:
1. Opening boundary date = `--date` (e.g. 2026-05-20)
2. Closing boundary date = `--date + 1` (e.g. 2026-05-21)
3. Paytm for the shift window `--date 06:00 → (--date + 1) 05:59`
4. Price (PRM) for `op_date = --date`
5. SDMS PAD for `op_date = --date`
6. ATG: **not run** — see ATG rule below

One IRAS login covers Price + any needed boundary scrapes (no second CAPTCHA solve).
Add `--dry-run` to preview what would run/skip without writing to DB.

**Accounting source skips:** Paytm, Price, and SDMS are each checked against the DB before
running. Sources already present are skipped automatically. See *Accounting Source Existence
Checks* below.

**IRAS failure isolation:** If IRAS login fails (CAPTCHA failure), Price and boundary scrapes
are skipped but SDMS still runs independently. The run returns a non-zero exit if any source
failed.

### Boundary Completeness Check

Before scraping each boundary, `--completed-shift` checks `nozzle_totalizers`.
A boundary is **complete** only if all six expected nozzle numbers are present:
`{7, 11, 15, 16, 17, 18}` — all liquid-fuel nozzles per hardware spec.

| Status | Meaning | Action |
|--------|---------|--------|
| COMPLETE | All 6 nozzles present | Skip — do not re-scrape |
| INCOMPLETE | Some nozzles present, some missing | Scrape — partial is not sufficient |
| MISSING | No rows for this date | Scrape |

The check uses SQLAlchemy Core directly (`create_engine` + `select`). It does **not** call
`create_app()`, so it never triggers `db.create_all()`, Alembic migrations, or seed logic.
Safe to call with `--dry-run`.

### Accounting Source Existence Checks

Before running any accounting source (Paytm / Price / SDMS), `--completed-shift`,
`--accounting-only`, and all `--*-only` modes check the DB using SQLAlchemy Core
(`create_engine` + `select`). No `create_app()`, no migrations, no seed logic. Safe
with `--dry-run`.

| Source | Table checked | Completeness rule |
|--------|--------------|-------------------|
| Paytm | `paytm_transactions` | `COUNT(*) WHERE operational_date = D > 0` |
| Price | `iras_prices` | `SELECT DISTINCT product` set = `{HS, MS, X2, XG}` |
| SDMS | `sdms_summaries` | `COUNT(*) WHERE op_date = D > 0` |

**Price completeness uses set logic, not row count.** A date with only HS and MS rows
is `INCOMPLETE`, not `COMPLETE`. Duplicate rows do not cause a false pass.
Status values: `COMPLETE` (skip) · `INCOMPLETE` (re-scrape) · `MISSING` (scrape).
Only `COMPLETE` is treated as a skip — any other status triggers re-scraping.

CNG is not in the IRAS Price table and is not part of the Price existence check.

**Skipped = successful.** A source that is already in DB does not degrade the final
exit status. Only actively-failed sources cause a non-zero exit.

### Source-Specific Retry Modes

Single-source retry without touching unrelated portals. Use when one source failed in a
`--completed-shift` run and the others already succeeded.

```
# Retry Paytm only
python -X utf8 scrapers/daily_scrape.py --paytm-only --date 2026-05-20

# Retry Price only (requires IRAS login)
python -X utf8 scrapers/daily_scrape.py --price-only --date 2026-05-20

# Retry SDMS only
python -X utf8 scrapers/daily_scrape.py --sdms-only --date 2026-05-20
```

For all three modes, `--date` is the **accounting op_date** (shift start date), not the
shift boundary date. `--date 2026-05-20` covers the shift window `2026-05-20 06:00 →
2026-05-21 05:59`.

Each mode runs its existence check first and skips silently if the data is already in DB.
No IRAS login is attempted by `--paytm-only` or `--sdms-only`. `--price-only` defers the
IRAS credential check to runtime — if all four products are already present, no login occurs.

**Rationale:** IRAS CAPTCHA failures are an operational reality. If SDMS succeeds and IRAS
fails, retry with `--price-only` (or `--boundary-only`) without re-running Paytm or SDMS.
Minimises unnecessary portal hits and avoids redundant data duplication.

### Failure Isolation

Each source runs in its own browser context. A failure in one source does not abort others.

| Failure | Behaviour |
|---------|-----------|
| Paytm download fails | Warning printed; Price and SDMS still run |
| IRAS login fails (CAPTCHA) | Price and boundaries marked failed; SDMS still runs |
| SDMS download fails | Warning printed; final summary shows failed |
| DB save fails (any source) | Source marked failed; other sources unaffected |

**Final summary** is printed after all sources complete:

```
=======================================================
  ACCOUNTING SOURCE SUMMARY
=======================================================
  op_date 2026-05-20:
    paytm   : SUCCEEDED
    price   : SUCCEEDED
    sdms    : SKIPPED (already in DB)
=======================================================
```

`run()` returns `False` (non-zero exit) if any source shows `FAILED`. `SKIPPED` is not
a failure. If the process exits non-zero, check the summary above to see which source failed,
then use the corresponding `--*-only` flag to retry that source alone.

### Paytm Wait and Debug Options

| Flag | Behaviour |
|------|-----------|
| _(not set)_ | Paytm download waits up to the scraper's built-in timeout |
| `--paytm-wait-seconds N` (N > 0) | Poll for download link for up to N seconds |
| `--paytm-wait-seconds 0` | Poll indefinitely (use with caution) |
| `--paytm-wait-seconds N` (N < 0) | Rejected — script exits with error |

```
python -X utf8 scrapers/daily_scrape.py --completed-shift --date 2026-05-20 --paytm-wait-seconds 120
```

`--paytm-debug` saves diagnostic artifacts to the working directory:
- `paytm_page.html` — full page HTML at the time of the download attempt
- `paytm_anchors.txt` — all anchor hrefs found on the page
- `paytm_candidates.txt` — candidate download link(s) evaluated
- `network.log` — network requests observed (URLs sanitized — query strings stripped)
- `paytm_debug.png` — screenshot at the time of the download attempt

**Security note:** Page HTML, anchor lists, and candidate-link files may contain signed
S3 URLs or other sensitive query parameters in their raw (pre-sanitization) form.
These artifacts are **local-only** and must **not** be committed to git or shared externally.
Add them to `.gitignore` if you run `--paytm-debug` regularly.

### ATG Rule

ATG/tank stock is a **live/current snapshot** of what is in the tanks right now.
It is not historical completed-shift accounting data.

- `--completed-shift` intentionally excludes ATG.
- Run ATG on its own schedule:
  ```
  python -X utf8 scrapers/daily_scrape.py --atg-only
  ```
- Ideal: every 30 minutes, or another multiple of 30 minutes, depending on operational need.
- Automated scheduling is not live on Railway — runs are currently manual.

### Mode Summary

| Mode | Flag | `--date` meaning | Jobs |
|------|------|-----------------|------|
| Completed-shift | `--completed-shift` | Accounting op_date | Paytm + Price + missing boundaries + SDMS. ATG excluded. |
| Boundary only | `--boundary-only` | Shift boundary date | Price + ST + ISS. No Paytm/SDMS/ATG. |
| Accounting only | `--accounting-only` | Accounting op_date | Paytm + Price + SDMS. IRAS for Price only. |
| ATG snapshot | `--atg-only` | Ignored | Current tank levels only. |
| All (default) | _(none)_ | Shift boundary date | All 6 jobs in order (0 → 5). |
| Paytm only | `--paytm-only` | Accounting op_date | Paytm only. Skips if rows already in DB. No IRAS login. |
| Price only | `--price-only` | Accounting op_date | IRAS Price (PRM) only. Skips if all 4 products in DB. |
| SDMS only | `--sdms-only` | Accounting op_date | SDMS PAD only. Skips if row already in DB. No IRAS login. |

### Recommended Operational Runbook

**Automated scheduling is not live on Railway.** All runs are currently manual from a local
machine using the Railway `DATABASE_URL` env var. The intent when scheduling is implemented:

| Schedule | Command | Notes |
|----------|---------|-------|
| Once daily, after 06:10 | `--completed-shift --date YESTERDAY` | After the shift closes; YESTERDAY = calendar date − 1 |
| Every 30 minutes | `--atg-only` | Tank levels; separate from completed-shift |

**Standard daily workflow (manual until scheduling is live):**

```bash
# Run after 06:10 on YYYY-MM-DD for the shift that just closed (op_date = yesterday)
python -X utf8 scrapers/daily_scrape.py --completed-shift --date YYYY-MM-DD

# Separately, to refresh tank levels
python -X utf8 scrapers/daily_scrape.py --atg-only
```

**If a source fails in the completed-shift run:**

```bash
# Retry Paytm only (if Paytm download failed or timed out)
python -X utf8 scrapers/daily_scrape.py --paytm-only --date YYYY-MM-DD

# Retry Paytm with extended wait (if report generation is slow)
python -X utf8 scrapers/daily_scrape.py --paytm-only --date YYYY-MM-DD --paytm-wait-seconds 180

# Retry Price only (if IRAS CAPTCHA failed)
python -X utf8 scrapers/daily_scrape.py --price-only --date YYYY-MM-DD

# Retry SDMS only (if SDMS failed)
python -X utf8 scrapers/daily_scrape.py --sdms-only --date YYYY-MM-DD
```

**Dry-run (preview without writing to DB):**

```bash
python -X utf8 scrapers/daily_scrape.py --completed-shift --date YYYY-MM-DD --dry-run
```

**Paytm manual import (if CSV already exists locally):**

```bash
python -X utf8 scrapers/import_paytm_csv.py data/paytm/paytm_YYYY-MM-DD.csv
```

---

## Critical Business Logic

### Operational Day: 06:00 to 05:59
RSPs effective 06:00:00 → 05:59:59 next day. Shift Totalizer is midnight-to-midnight.
Boundary mode resolves the mismatch.

### Boundary Mode Algorithm
1. XG pre-check: nozzle 11 movement ≤ 7L → carry forward, skip ISS. > 7L → ISS search.
2. ISS backward search 05:30–06:00 for nozzles 7, 15, 16, 17, 18.
3. Stop when all 5 resolved or 48 windows checked.
XG threshold: 5L pump test + 2L buffer = 7L.

**`daily_scrape.py --date YYYY-MM-DD` semantics:**
`--date 2026-05-22` resolves the 06:00 boundary for `operational_date = 2026-05-22`.
It writes a `NozzleTotalizer` row with `operational_date = 2026-05-22` (the 06:00 reading).
The first backward ISS search window is 2026-05-22 05:30 → 06:00.
To display fuel sales for 21 May on the owner dashboard, you need **two consecutive runs**:
`--date 2026-05-21` (opening boundary) and `--date 2026-05-22` (closing boundary).

### Price Lookup (liquid fuel only)
Never hardcode. Join transaction datetime to Price table:
match Product Code + datetime in [Effective From 06:00, Effective To 05:59:59].
CNG: read from `app_settings.cng_rsp_per_kg` — not from Price table.

### Totalizer Facts
Hardware odometers, cumulative since installation. Only increase, never reset.
Closing > opening always. Gap = unreported dispensing (tamper signal).

### Pump Tests (liquid fuel only, not CNG)
5L/nozzle every ~08:20, all 6 liquid nozzles. ISS type "Pump Test (105)".
Fuel returns to tank — not a sale. Deducted from totalizer diff before net sales.

### Fuel Adulteration Check
Depot density (RDB Invoice) → Tanker hydrometer (Receipt Density) → Post-decant (Density Records).
Alert if depot density vs hydrometer delta ≥ 5%.

### `get_operational_date()`
`services/operational.py`. Before 06:00 → yesterday. At/after 06:00 → today.
Used **only** in attendant home nudge.
Shift close uses `_shift_op_date() = date.today() − 1` (always yesterday). Do not conflate.

### Owner Dashboard Accounting Date Semantics

**The owner dashboard does not show "today so far." It shows the last completed operational
shift.** Do not describe it as live intraday accounting.

The outlet operational day runs 06:00 → 05:59/06:00 next calendar day.
When the owner opens the dashboard on calendar day D, the accounting view is:

> **D−1 06:00 → D 06:00** (i.e., `op_date = D − 1`)

**Example:** If today is 22 May 2026, the dashboard shows accounting data for
21 May 2026 06:00 → 22 May 2026 06:00. In DB terms: `op_date = 2026-05-21`.

**NozzleTotalizer requirement:** Fuel sales for `op_date = 2026-05-21` require two rows:

| `operational_date` | Role |
|--------------------|------|
| 2026-05-21 | Opening boundary (06:00 totalizer reading) |
| 2026-05-22 | Closing boundary (06:00 totalizer reading) |

Fuel litres = totalizer at 2026-05-22 06:00 − totalizer at 2026-05-21 06:00 − pump tests.
A single scraper run produces one boundary row. Two consecutive dates must both be scraped
before any fuel volume can appear on the dashboard.

In code: `dashboard_bp.index()` hardcodes `op_date = date.today() - timedelta(days=1)`.
`_product_sales(op_date)` queries `NozzleTotalizer` for `op_date` (opening) and `op_date + 1`
(closing). If either row is missing, that product shows 0 L.

### Stock Watch Calculation (owner dashboard)
Rolling 7-day avg daily consumption per product from ISS data.
Days remaining = current ATG volume ÷ avg daily consumption.
Order-by date = today + days_remaining − 2 (2-day depot lead time).
Show stock watch card only if any product ≤ 7 days. Hidden if all > 7 days.

---

## Lube Products (44 SKUs)

Seeded in `lube_products`. Cash or credit sales. Credit adds to customer balance.
Source: pump_stock_10_04_2026.pdf + godam_stock_10_04_2026.pdf.
Sale rates in `lube_products.sale_rate`.

**Stage 1:** Lube manager flow NOT built yet. Show "—" / "Logging not active" in
Daily Summary lube row until Stage 2 ships.

---

## Schema

### `users`
username · password_hash (bcrypt) · role (owner/manager/attendant) · first_name ·
is_active · created_at. Seed via upsert at startup from `.env`.

### `cng_shift_readings` ✓ built (migration `2fc50a7d52a6`)
Full spec in CNG section above. **Not used for dashboard/summary display** — SDMS is the display
source. Table is retained for future cross-checks.

### `tank_readings` ✓ built (migration `2fc50a7d52a6`)
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| scraped_at | DateTime NOT NULL | ATG snapshot timestamp |
| tank_id | Integer NOT NULL | 1–4 |
| product | String NOT NULL | HS / MS / X2 / XG |
| level_mm | Float | Raw ATG level |
| volume_litres | Float | Computed volume |
| capacity_litres | Float | Tank capacity |
| pct_full | Float | volume / capacity × 100 |
| is_reliable | Boolean DEFAULT True | False for XG |
| created_at | DateTime | |

### `sdms_summaries` ✓ built (migration `a1b2c3d4e5f6`)
One row per operational date. Upserted by `save_summary_to_db()` in `sdms_pad_exporter.py`.
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| op_date | Date UNIQUE NOT NULL | Calendar date scraped (yesterday) |
| fleet_card_total | Float | Sum of Fleet-Card Posting amounts |
| fleet_card_count | Integer | Number of fleet card transactions |
| cng_kg_total | Float | CNG kg from CGD Rewa billing rows |
| cng_revenue | Float | cng_kg_total × cng_rsp_per_kg |
| cng_rsp_per_kg | Float | `CNG_RSP_PER_KG` env var snapshot at scrape time |
| cng_count | Integer | Number of CGD billing rows |
| opening_balance | Float | PAD statement opening balance |
| closing_balance | Float | PAD statement closing balance |
| created_at | DateTime | |
| updated_at | DateTime | |

### `lube_products` (seeded)
name · pack_size · unit · sale_rate · is_active

### `lube_transactions`
product_id · quantity · unit_price · amount · payment_mode · customer_id (nullable) ·
op_date · transaction_time · logged_by · created_at

### `expenses`
amount · category · description · op_date · logged_by · created_at
Categories: Staff / Maintenance / Utilities / Supplies / Misc (configurable via `app_settings`)

### `fleet_card_transactions`
card_identifier · amount · op_date · transaction_time · logged_by · notes · created_at
**Note:** Originally built for manager manual entry. Fleet data now comes from SDMS PAD
scraper. Do not build a manager UI for fleet card entry. Table may be repurposed Stage 2.

### `payments_received` (extended)
Added: status (confirmed / pending_verification / flagged) · verified_by (FK users, nullable)
· verified_at (nullable).
Cash/cheque → confirmed immediately. Bank transfer → pending_verification → owner confirms.

---

## Manager Workflows

### Stage 1 — Pending (not yet built)

**Manager home (daily checklist):**
Resets every operational day. Prescriptive — not a nav menu.
Items: Log expenses (warn if not done, ok if done) · Record payments per customer
· Overdue items from previous day.

**Log expense:**
Amount · category (dropdown) · description · date (defaults today) → `expenses`

**Record payment received:**
Customer picker · amount · mode (Cash / Cheque / Bank Transfer) · reference number
Cash/Cheque → confirmed, balance updated immediately
Bank Transfer → pending_verification, owner must confirm

### Stage 2 — After Live Testing

**Log lube sale:**
Catalogue picker · quantity · unit price (pre-filled, editable) · Cash or Credit
If Credit → customer picker → `lube_transaction`, balance updated

**Generate invoice:**
Customer picker → show uninvoiced credit transactions → confirm → ReportLab PDF
→ mark transactions as invoiced

### Intelligence Signals (owner receives on dashboard)
- "X bank transfers pending verification"
- "[Customer] payment not recorded in N days"
- "No expenses logged today"
- Credit utilisation alerts

---

## Production Data Status (Railway PostgreSQL)

Last updated: 23 May 2026.

### op_date 2026-05-21 — fully verified

Owner dashboard proof-of-life succeeded for accounting op_date **2026-05-21**
(shift window 2026-05-21 06:00 → 2026-05-22 06:00). All major data streams verified:

| Table | Status |
|-------|--------|
| `nozzle_totalizers` | ✓ Opening boundary 2026-05-21 + closing boundary 2026-05-22 present |
| `iras_prices` | ✓ Price rows covering 2026-05-21 present |
| `paytm_transactions` | ✓ Rows for 2026-05-21 present |
| `tank_readings` | ✓ ATG snapshot populated |
| `sdms_summaries` | ✓ CNG + fleet data for 2026-05-21 present |

### op_date 2026-05-20 — fully complete

Completed-shift live test for op_date **2026-05-20** (shift window 2026-05-20 06:00 → 2026-05-21 05:59):

| Item | Status |
|------|--------|
| Opening boundary 2026-05-20 | ✓ Scraped and saved |
| Closing boundary 2026-05-21 | ✓ Already present (COMPLETE) — skipped |
| Price for 2026-05-20 | ✓ Downloaded and saved |
| SDMS PAD for 2026-05-20 | ✓ Downloaded and saved |
| Paytm for 2026-05-20 | ✓ 520 rows imported via `import_paytm_csv.py` |

Owner dashboard shows data for 2026-05-20. All streams complete.

### Known IRAS Reliability Issue (May 2026)

In recent runs, Paytm succeeded but the subsequent IRAS session failed after 3 CAPTCHA
attempts. This is **not necessarily a credentials problem** — IRAS CAPTCHA is intermittently
difficult, and repeated attempts sometimes fail even with correct login details.

**If IRAS fails after Paytm succeeds:**

1. Check the failure screenshot (if `--paytm-debug` was active) for clues.
2. Retry with `--price-only` — Paytm is already in DB and will be skipped.
3. If failure recurs, consider saving the failed CAPTCHA image and the predicted text
   to diagnose whether the Claude Vision response is incorrect, or whether the IRAS
   login page itself changed.

**Recommended diagnostics when IRAS keeps failing:**
- Save the CAPTCHA image sent to Claude Vision alongside the predicted text
- Save a screenshot immediately after the failed login attempt
- Compare the predicted text with the actual CAPTCHA characters if visible

This has not been automated yet. If IRAS login proves persistently unreliable, a
manual CAPTCHA entry fallback may be warranted (out of scope for current stage).

### Backfill command reference

To populate any op_date from scratch (boundaries + Price + Paytm + SDMS):
```
python -X utf8 scrapers/daily_scrape.py --completed-shift --date YYYY-MM-DD
```

To populate ATG (run separately — tank stock is a live snapshot, not historical):
```
python -X utf8 scrapers/daily_scrape.py --atg-only
```

**Automated scheduled scraping is not live on Railway.** All scraper runs are currently
manual (local machine → Railway DB via `DATABASE_URL` env var).

---

## Build Stages

Sprint 1/2/3 naming retired. Use Stage 1/2/3.

### Stage 1 — Live Testing (current priority)

| Task | Status |
|------|--------|
| ATG scraper (`iras_atg_exporter.py`, `tank_readings` table, Job 5) | ✓ Built + data populated for 2026-05-21; run `--atg-only` on separate schedule |
| `--completed-shift` orchestration mode (`daily_scrape.py`) | ✓ Built — boundary completeness check, single IRAS session for Price + boundaries |
| `import_paytm_csv.py` — Paytm CSV import fallback | ✓ Built |
| Source-specific retry modes (`--paytm-only`, `--price-only`, `--sdms-only`) | ✓ Built — existence checks, failure isolation, final summary |
| Owner dashboard (screen 10, wire to real data) | ✓ Done |
| Owner daily summary (screen 15, wire to real data) | ✓ Done |
| Tanks screen (screen 11, wire to `tank_readings`) | ✓ Done |
| SDMS DB persistence (`sdms_summaries`, DB-first reads) | ✓ Done |
| CNG shift close (`cng_shift_readings` table, attendant flow) | ✓ Done |
| Credit screens polish (12, 13, 14) | ✓ Substantially done |
| Production data — op_date 2026-05-21 (dashboard proof-of-life) | ✓ All streams verified on Railway |
| Production data — op_date 2026-05-20 (all streams) | ✓ Complete — 520 Paytm rows imported via `import_paytm_csv.py` |
| Decide scraper schedule (completed-shift once daily after 06:00; ATG every 30 min) | Pending — not automated yet |
| IRAS CAPTCHA diagnostics (save failed image + prediction for debugging) | Pending — recurring failure observed May 2026 |
| Manager home checklist | Pending |
| Manager log expense | Pending |
| Manager record payment | **Next priority** |

### Stage 2 — Complete Operational Layer

| Task | Notes |
|------|-------|
| Manager lube sale | Full catalogue, cash or credit |
| Manager generate invoice | ReportLab PDF |
| Owner bank transfer verification | Confirm / flag pending transfers |
| Automated Paytm ingestion | Email watcher replaces scraper |

### Stage 3 — Infrastructure and Advanced

| Task | Notes |
|------|-------|
| Full ISS 48-window scrape | Intraday sales data |
| Delivery receipt scraper | TT Receipt, SAP Invoice, density chain |
| Stock variance screen | Requires ATG + delivery data |
| Anomaly detection | Tamper signals, adulteration flags |
| Depot account reconciliation | Fleet card vs fuel purchase ledger |
| CNG RSP edit UI | Owner settings screen |
| Phase 2 features | P&L, HR, compliance, daybook |

---

## Architecture

### Single Tree, Three Branches
One Flask app, one DB, one deployment. Three roles via `users.role`.

### Design System Phases (historical reference)
- Phase 1 ✓ — design-system.css + macros/ui.html + base.html
- Phase 2 ✓ — login drum-roll animation
- Phase 3 ✓ — all 9 attendant screens reskinned
- Phase 4 — manager screens (new design from start)
- Phase 5 — owner screens (`Owner_Screens.html` as visual reference)

### Cloud Deployment
Railway (paid tier), PostgreSQL. Mobile-first PWA. Bind `0.0.0.0` in dev.
Auto-deploys on push to `main`.

### Local Dev
`start.bat` uses full Python path: `C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe`
(Windows Store `python` stub does not work.) Local login: `rishab` / `changeme`.

**Stale process warning (Windows):** Flask `--debug` mode spawns a stat-reloader parent + worker
child. Multiple `start` calls accumulate stale processes, all listening on port 5000. Symptoms:
intermittent 500 errors or old code being served. Fix: use PowerShell `Stop-Process` to kill all
Python PIDs before restarting — `taskkill` from Bash silently fails on Windows.
Never run the Flask dev server via `run_in_background` in a Claude session; always start it in a
visible terminal so you can kill it cleanly.

### Database
SQLAlchemy ORM only — no raw SQL. `DATABASE_URL` env var switches SQLite ↔ PostgreSQL.
Flask-Migrate / Alembic for all schema changes.

**Connection pool hardening (Railway PostgreSQL):** `SQLALCHEMY_ENGINE_OPTIONS` is set in
`create_app()` for PostgreSQL only (`pool_pre_ping=True`, `pool_recycle=300`). This prevents
`SSL error: decryption failed or bad record mac` errors caused by stale pooled connections
after Railway idle-connection timeouts. SQLite is unaffected.

---

## Tech Stack

- **Backend:** Python / Flask (app factory, blueprints)
- **ORM:** SQLAlchemy + Flask-Migrate
- **Frontend:** Jinja2 + Tailwind CSS (mobile-first)
- **Auth:** Flask-Login (session-based)
- **PDF:** ReportLab (NOT WeasyPrint — Windows incompatibility)
- **Scraping:** Playwright async + Claude Vision API (CAPTCHA)
- **Deployment:** Railway

### Environment Variables
```
IRAS_USERNAME=206858
IRAS_PASSWORD=<see .env>
IRAS_URL=https://iras.iocliras.in
ANTHROPIC_API_KEY=<CAPTCHA solving>
SECRET_KEY=<random string>
DATABASE_URL=sqlite:///pumpvision.db
OUTPUT_FOLDER=C:\IRAS_Data
OWNER_USERNAME=admin
OWNER_PASSWORD=shreeadmin2026
ATTENDANT_USERNAME=operations
ATTENDANT_PASSWORD=shreeoperations2026
MANAGER_USERNAME=<see .env>
MANAGER_PASSWORD=<see .env>
PAYTM_EMAIL=<see .env>
PAYTM_PASSWORD=<see .env>
PAYTM_STATE_PATH=scrapers/paytm_state.json
PAYTM_HEADLESS=false
GMAIL_ADDRESS=<see .env>
GMAIL_APP_PASSWORD=<Google App Password — see .env>
SDMS_USERNAME=<see .env>
SDMS_PASSWORD=<see .env>
SDMS_STATE_PATH=scrapers/sdms_state.json
CNG_RSP_PER_KG=93.40
```

**Railway env vars to add:** `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `CNG_RSP_PER_KG`

---

## Design System

### Active Implementation
Full spec: `docs/design/Pumpvision_Design_System.html`
CSS: `pumpvision/static/css/design-system.css`
Macros: `pumpvision/templates/macros/ui.html`

**Owner screens 10 and 15 deviate from the v0.1 design system.**
Use `docs/design/Owner_Screens.html` as the sole visual and CSS reference for those
two screens. Extract all tokens, colors, and component styles from that file directly.
Login and attendant screens retain the original design system.

### Design Tokens (design-system.css, used for login + attendant)
- `--paper-*` (50–500): warm substrate
- `--ink-*` (500–900): navy
- `--saffron-*` (100–700): energy accent — one CTA per screen max
- `--ok-*` / `--warn-*` / `--error-*`: status states
- Product layer: `--hsd-*` (green) · `--ms-*` (brick red) · `--x2-*` (purple) · `--xg-*` (teal)

### Typography
- **Newsreader** — serif headings (login + attendant)
- **IBM Plex Sans** — UI labels, body, nav
- **JetBrains Mono** — all operational numbers (`font-variant-numeric: tabular-nums`)
- **Major Mono Display** — wordmark only

### Macros (`{% from 'macros/ui.html' import … %}`)
totalizer · product_chip · status_chip · field · select_field · textarea_field ·
totalizer_field · card · section_rule · receipt_row · back_btn · screen_topbar

### Login Animation
10 drum cells → PUMPVISION. `document.fonts.ready` gate required.
680ms–2335ms left→right, `cubic-bezier(0.04,0.06,0,1)`. Hero exits → form fades 80ms later.

### Forbidden — Do Not Invent
- Efficiency scores, accuracy %, quality metrics
- "Auto-settlement", "Automated Payouts", "Settlement batches"
- Multi-user collaboration features
- "Live Alerts", "STATION OS", "Diagnostics"
- WhatsApp/SMS (deferred)
- "Fleet Account" or "Corporate Account" types
- Any branding other than Pumpvision
- Hardware features that don't exist (temperature, pressure, flow rate)
- Time selector (Today/Week/Month) on owner dashboard
- 7-day revenue bar chart on owner dashboard
- Circular tank rings — horizontal fill bars only
- "Reconcile" as nav label or screen
- Reconciliation open items workflow
- "Variance" screen — needs delivery scraper (Stage 3)
- Aggregate total volume across all products
- Manager fleet card entry UI

---

## Screen Inventory

PNG refs in `docs/screens/`. **Owner screens 10 + 15: use `docs/design/Owner_Screens.html`.**

---

### Auth

#### `01_login.png` ✓
**Route:** `GET/POST /login`
Drum-roll animation → form. Newsreader italic heading, saffron left-border card, saffron CTA.

---

### Attendant Branch ✓ All implemented

Nav: Home · Activity · Profile

#### `02_attendant_home.png` ✓
**Route:** `GET /` (role=attendant)
Shift status nudge (ok/warn). Two action cards: Log Credit Sale · Close Shift.

#### `03_select_customer.png` ✓
**Route:** `GET /attendant/credit/select-customer`
Search, filter chips, customer rows. Suspended: 50% opacity + error-600 lock.

#### `04_log_sale_details.png` ✓
**Route:** `GET/POST /attendant/credit/log/<customer_id>`
Customer card · vehicle dropdown · 2×2 product grid · amount/litres toggle · sticky saffron CTA.

#### `05_transaction_confirmed.png` ✓
**Route:** post-submit redirect
ok-100 checkmark · receipt rows · warn-100 parchi reminder · saffron + ghost CTAs.

#### `06_shift_close_product_selection.png` ✓
**Route:** `GET /attendant/shift/select-product`
2×2 grid (HS / MS / X2 / XG) + full-width CNG tile. HS/MS → DU selection. X2/XG/CNG → numpad directly.
Chip-ok DONE badge when reading submitted.

#### `07_shift_close_du_selection.png` ✓ (HS/MS only — no change)
**Route:** `GET /attendant/shift/du/<product>`
Not used for X2, XG, or CNG.

#### `08_shift_close_numpad.png` ✓
**Route:** `GET/POST /attendant/shift/numpad/<nozzle>`
CNG: unit label = **kg**, delta label = **kg sold**. Liquid fuel: unchanged.

#### `09_shift_close_summary.png` ✓
**Route:** `GET/POST /attendant/shift/summary`
CNG card after nozzle rows: kg opening · kg closing · kg sold · ₹ revenue.
Revenue = kg_sold × `cng_rsp_per_kg`. CNG optional — does not block 6-nozzle submit.

---

### Manager Branch — Stage 1

Nav: Home · Expenses · Payments · More
(Fleet tab removed — no manual fleet entry.)

#### Manager home
**Route:** `GET /manager/`
Daily checklist. Resets each operational day.
Items: Expenses (warn/ok) · Pending payment recordings · Overdue items.

#### Log expense
**Route:** `GET/POST /manager/expense`
Amount · category · description · date → `expenses`

#### Record payment received
**Route:** `GET/POST /manager/payment`
Customer picker · amount · mode (Cash/Cheque/Bank Transfer) · reference.
Cash/Cheque → confirmed. Bank Transfer → pending_verification.

---

### Owner Branch — Stage 1

Nav: Home · Tanks · Credit · Summary · More

#### `10_owner_dashboard.png` ✓
**Route:** `GET /` (role=owner)
**Design ref: `docs/design/Owner_Screens.html` screen 10 — implemented.**

Data wiring:
- Revenue: ISS (litres × RSP per product) + SDMS CNG (`_cng_sdms()`, kg × rsp/kg)
- Cash in hand: Revenue − Paytm − Credit − Fleet card (derived)
- Per-product breakdown: ISS per product code + CNG from SDMS DB via `_cng_sdms()` (JSON fallback)
- Stock watch: rolling 7-day consumption from ISS → days remaining → order-by date.
  Card only appears when any product ≤ 7 days. Hidden otherwise.
- Price ticker: RSP from Price table (liquid) + `cng_rsp_per_kg` (CNG)

#### `11_owner_tanks.png` — ✓ Done
**Route:** `GET /tanks`
Latest `tank_readings` per tank. "AS OF HH:MM" timestamp.
Fill bar (product color) · % · volume · capacity · days left.
Days left: > 7 default · 3–7 warn-600 · ≤ 2 error-600.
No data state: "No ATG reading" shown per card.
Note: "Order soon" chip and XG probe warning are deferred — not currently shown.

#### Credit home ✓ Done
**Route:** `GET /credit/home`
Cross-customer credit activity view. Recent transactions across all accounts.

#### `12_credit_customer_list.png` ✓ Done
**Route:** `GET /credit/customers`
Summary: Total Outstanding + Overdue. Filter chips: All / Over 80% / Overdue / Suspended.
Customer cards: avatar · name · vehicles · utilisation bar · balance.
Border: < 70% default · 70–80% warn-600 · > 80% error-600. Sticky "+ Add customer".

#### `13_credit_customer_detail.png` — ✓ Done
**Route:** `GET /credit/customers/<id>`
Header: name · balance · utilisation pill. Tabs: Activity · Invoices · Receipts.
Owner view is read-only. Payment recording is manager-side (manager branch, Stage 1).
Activity feed: fuel + payments (chronological). Invoices and receipts shown in separate tabs.

#### `14_credit_customer_add.png` ✓ Done
**Route:** `GET/POST /credit/customers/new` and `/<id>/edit`
Company name · account ID + GST · fleet manager · contact · credit limit · payment terms (15/30/45).
Vehicles: "+ Add vehicle" dashed button. "Suspend account" destructive: hidden in New, visible in Edit.

#### `15_owner_daily_summary.png` ✓
**Route:** `GET /summary` and `GET /summary/<date_str>`
**Design ref: `docs/design/Owner_Screens.html` screen 15 — implemented.**

Data wiring (full calculation chain):

1. **FUEL SALES** — one row per product (HS / MS / X2 / XG / CNG):
   Liquid: ISS litres × RSP from Price table.
   CNG: kg_sold × rsp_per_kg from `_cng_sdms(op_date)` — reads SDMS DB first, JSON fallback.
   → Subtotal: GROSS FUEL SALES

2. **LUBE SALES** — cash lube from `lube_transactions` for the day.
   Show "—" + "Logging not active" until Stage 2 manager flow is live.

3. **GROSS REVENUE** — fuel + lube (totalizer per Owner_Screens.html)

4. **DEDUCTIONS:**
   - Credit extended: sum of credit fuel + credit lube for the day
   - POS (UPI + Card): Paytm data for the day, combined
   - Fleet card: SDMS PAD data for the day
   - Expenses: `expenses` sum for the day ("—" if none logged)

5. **CASH IN HAND** — derived. Totalizer at bottom of receipt card.

Date nav: ← [date] → via URL param. Default: today's operational date.
No data → "No data for [date]" with nav still present.
PDF via ReportLab matching on-screen layout. "Share" (ghost) + "Print / Save PDF" (saffron CTA).

---

## Delivery Workflow

1. IOC depot loads tanker → RDB SAP Invoice
2. Tanker arrives → hydrometer readings per chamber → Receipt Density Records
3. Decanting → TT Receipt (ATG before/after)
4. Post-decant → Density Records

Trucks: MP17HH4740 (regular) · MP53HA2180 · MP20ZQ9560. Supply point: Depot 3356.
**MP17HH4740 is the supply tanker — NOT a customer vehicle.**

---

## Known Anomalies
- Receipt 1107 (25 Mar 2026): 463L MS, no invoice/truck/density. Suspicious.
- Product XP (legacy — predates X2): ignore in all reporting.

---

## Files

### App (`pumpvision/`)
- `__init__.py` — app factory, blueprint registration
- `extensions.py` — db, login_manager, migrate
- `models.py` — all models
- `constants.py` — NOZZLE_LABEL_MAP, PRODUCT_LABELS, PUMP_TEST_NOZZLES
- `decorators.py` — owner_required, attendant_required
- `user.py` — DB-backed User
- `services/prices.py` — get_rsp()
- `services/operational.py` — get_operational_date()
- `blueprints/auth/routes.py`
- `blueprints/attendant/routes.py` — all 9 screens + CNG shift close (`/shift/cng`)
- `blueprints/owner/routes.py` — stub redirect to dashboard
- `blueprints/credit/owner.py`
- `blueprints/dashboard/routes.py` — owner dashboard (`/`) + daily summary (`/summary`, `/summary/<date_str>`)
- `blueprints/paytm/routes.py`
- `blueprints/recon/routes.py` — data logic retained, UI retired
- `blueprints/meters/routes.py`

### Static
- `pumpvision/static/css/design-system.css`
- `pumpvision/static/css/owner.css` — owner dashboard + summary styles (separate from design-system)
- `pumpvision/static/manifest.json`
- `pumpvision/static/icons/icon-192.png` + `icon-512.png`

### Templates
- `pumpvision/templates/macros/ui.html`
- `pumpvision/templates/owner/summary.html` — daily summary (screen 15)

### Scrapers
- `scrapers/iras_iss_exporter.py` — ISS boundary mode
- `scrapers/iras_price_exporter.py` — Price (PRM)
- `scrapers/iras_atg_exporter.py` — ATG Stock tab (✓ built, Job 5)
- `scrapers/paytm_exporter.py`
- `scrapers/sdms_pad_exporter.py`
- `scrapers/daily_scrape.py` — Job 0: Paytm · Job 1: Price · Job 2: ST · Job 3: ISS · Job 4: SDMS · Job 5: ATG
- `scrapers/import_paytm_csv.py` — manual import of existing Paytm CSV into DB (fallback when download fails)
- `scrapers/captcha_test.py`

### Documentation
- `CLAUDE.md` — this file
- `docs/screens/` — PNG refs (01–15)
- `docs/design/Pumpvision_Design_System.html` — design system v0.1
- `docs/design/Owner_Screens.html` — **canonical visual ref for screens 10 and 15**

---

## Observed Operating Patterns
- Outlet closes ~01:00–06:00. No XG sold midnight–06:00.
- Nozzle 16 (HS2): very low volume (~25L/day) — possible overflow-only usage.
- Pump tests: ~08:20, 5L/nozzle, all 6 liquid nozzles, every day.

---

## Parallel Workstreams

| Stream | Status |
|--------|--------|
| Deployment | ✓ Live — Railway, PostgreSQL, PWA |
| Attendant branch | ✓ Complete — 9 screens, live data, reskinned |
| Three-user foundation | ✓ Complete |
| Paytm scraper | ✓ Complete — Gmail IMAP OTP, auto-import to DB, OTP not logged |
| SDMS PAD scraper | ✓ Complete — fleet + CNG extraction + DB persistence (`sdms_summaries`) |
| ATG scraper | ✓ Built — data populated for 2026-05-21; run `--atg-only` separately (not in completed-shift) |
| `--completed-shift` orchestration mode | ✓ Built — boundary completeness check, single IRAS session |
| `import_paytm_csv.py` Paytm CSV import fallback | ✓ Built |
| Source-specific retry modes (`--paytm-only`, `--price-only`, `--sdms-only`) | ✓ Built — existence checks, failure isolation, final summary per source |
| Owner dashboard (screen 10) | ✓ Complete |
| Owner daily summary (screen 15) | ✓ Complete |
| Owner tanks screen (screen 11) | ✓ Complete |
| Credit screens (12, 13, 14 + `/credit/home`) | ✓ Substantially done |
| SDMS DB persistence + dashboard DB-first reads | ✓ Done |
| Railway PostgreSQL connection pool hardening | ✓ Done — `pool_pre_ping=True`, `pool_recycle=300` |
| CNG RSP default/fallback consistently 93.40 | ✓ Done |
| dry-run DB-skip for all scraper jobs (Paytm, Price, ATG, SDMS) | ✓ Done |
| Production debug traceback handler removed | ✓ Done |
| Production data — op_date 2026-05-21 (dashboard proof-of-life) | ✓ All streams verified on Railway |
| Production data — op_date 2026-05-20 (all streams) | ✓ Complete — 520 Paytm rows imported |
| Scraper scheduling (completed-shift daily + ATG 30-min) | Pending — not automated yet |
| IRAS CAPTCHA diagnostics | Pending — recurring failures observed May 2026; see *Known IRAS Reliability Issue* |
| Manager core (checklist, expenses, payments) | Stage 1 — **next priority** |
| CNG shift close + `cng_shift_readings` | ✓ Complete |
| Manager lube sales + invoicing | Stage 2 |
| Bank transfer verification UI | Stage 2 |
| Automated Paytm ingestion | Stage 2 |
| Full ISS + delivery scraper + variance | Stage 3 |
| Anomaly detection, P&L, Phase 2 features | Stage 3 |
