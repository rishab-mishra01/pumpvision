> Always read this entire file before starting any task in this project.

# Pumpvision — Project Briefing

## What This Project Is

Pumpvision is a mobile-first management and operations platform for an IndianOil retail outlet (RO).
The outlet is **Shree Petroleum**, RO code **206858**, located in Rewa, Madhya Pradesh, India.
It is operated by the dealer's family (owner: father + son Rishab).

The goal is to give the owners a 360-degree real-time view of:
- Daily fuel sales and revenue (by product, by nozzle)
- Stock levels and delivery reconciliation
- Payment collection breakdown (cash vs card/UPI vs credit)
- Anomaly and theft detection
- Delivery quality (fuel adulteration checks)
- Credit customer ledger and invoicing

The project is being built with vibe coding (no professional software engineering background).
If successful, it will be sold as a SaaS product to other IndianOil (and potentially HPCL/BPCL) dealers.

---

## Products Sold at This Outlet

| Code | Full Name | Type |
|------|-----------|------|
| HS | High Speed Diesel (HSD) | Diesel |
| MS | Motor Spirit | Petrol |
| X2 | Xtra Premium 95 (XP95) | Premium Petrol |
| XG | Xtra Green | Bio Diesel |
| CNG | Compressed Natural Gas | Gas (deferred to Phase 2) |

---

## Payment Modes

- **Cash** — not recorded digitally; derived as a remainder
- **Card + UPI** — processed via Paytm POS machines; data from Paytm for Business app
- **Credit** — institutional/fleet customers; currently paper-based (parchis), being digitised

---

## Hardware at the Outlet (Physical Layout)

### Underground Tanks
| Tank | Product | Capacity |
|------|---------|----------|
| 1 | HS | 20,000 L |
| 2 | MS | 20,000 L |
| 3 | X2 | 10,000 L |
| 4 | XG | 20,000 L |

All tanks have GVR MAG PLUS ATG probes. CNG has no tank in this system.
Tank 4 (XG) ATG data is unreliable — probe appears uncalibrated for XG.

### Dispensing Units (DUs) and Nozzles
| DU | Pump | Nozzle | Product | Tank |
|----|------|--------|---------|------|
| 9 (MIDCO) | 1 | 7 | HS | 1 |
| 9 (MIDCO) | 2 | 11 | XG | 4 |
| 14 (MIDCO) | 3 | 17 | X2 | 3 |
| 14 (MIDCO) | 4 | 18 | MS | 2 |
| 15 (GILBARCO) | 5 | 15 | MS | 2 |
| 15 (GILBARCO) | 6 | 16 | HS | 1 |

Only DU 9 (Pumps 1 & 2) has receipt printers. NPND interlock is disabled on all pumps.

---

## Data Sources

### Primary Source: IRAS Portal
**URL:** https://iras.iocliras.in  
**Credentials:** stored in `.env` file (never hardcode in scripts)  
**Automation:** Playwright Python script (`iras_iss_exporter.py`)

IRAS is a web portal run by IndianOil (IOCL) that records all FCC (Fuel Control Computer)
and ATG data from the outlet. Data is exported as Excel files.

#### Key constraint: ISS tab exports max 30 minutes of data at a time.
All other tabs export the full date range selected.

#### ISS usage strategy
- **Boundary mode** (already built) gives 06:00 totalizer readings per nozzle — sufficient for daily reconciliation (litres sold = close − open).
- **Full 48-window scrape** is deferred to Phase 2 (anomaly detection, per-transaction audit). Do not build this for reconciliation.

### IRAS Tables We Use

#### Static Reference Tables (range-independent)
| Tab | Sheet | Contents |
|-----|-------|----------|
| FCC Data > Product(PDM) | Product(PDM) | Product code → name mapping |
| FCC Data > Tank(TKM) | Tank | Tank config, capacity, ATG probe details |
| FCC Data > Pump | Pump | DU make/model, pump config |
| FCC Data > Nozzle | Nozzle | Nozzle → pump → tank → product mapping |

#### Daily Operations Tables (range-dependent)
| Tab | Sheet | Contents | Frequency |
|-----|-------|----------|-----------|
| FCC Data > Price(PRM) | Price(PDM) | Daily RSP per product (IOC-pushed at 06:00) | Daily |
| FCC Data > Issue(ISS) | Issue(ISS) | Every fuel transaction (30-min export limit) | Per transaction |
| FCC Data > Stock | Stock | ATG tank level snapshots | Every 30 min |
| FCC Data > Shift Totalizer | Shift Totalizer Record | Daily nozzle totalizer open/close (midnight boundary). Now actively used for XG pre-check — see XG Handling section. | Daily |

#### Delivery Tables (per-delivery event)
| Tab | Sheet | Contents |
|-----|-------|----------|
| RDB Data > Invoice | RDB SAP Invoice | Depot invoice: purchase amount + depot density |
| FCC Data > SAP Invoice | SAP Invoice | Delivery summary: chamber breakdown, truck no |
| FCC Data > Receipt | TT Receipt | Actual decanting: ATG-measured pre/post tank levels |
| FCC Data > Receipt Density | Receipt Density Records | Employee hydrometer readings per chamber |
| FCC Data > Density Records | Density Records | Post-decant ATG density in tank |

### Secondary Source: Paytm for Business
Card and UPI transactions are processed via Paytm POS machines.
Transaction reports are exported from the Paytm for Business mobile app via email in CSV format.
Two report types are used:
- **Transaction report** — individual UPI and card transactions per operational day
- **Settlement report** — actual amounts settled into bank account (used to catch Paytm fees and failed settlements)

#### Paytm report date range — IMPORTANT
Paytm's date picker end date is **exclusive** (the end date itself is not included in the export).
To get a complete operational day X (06:00 X → 05:59 X+1), select **From: X, To: X+2** in Paytm.
Example: operational day Apr 18 → select Apr 18 to Apr 20.
Selecting X to X+1 misses the X+1 calendar day entirely, leaving the 00:00–05:59 window empty.

---

## Critical Business Logic

### The Operational Day is 06:00 to 05:59 (not midnight to midnight)
- RSP (price) records are effective from 06:00:00 to 05:59:59 the next day
- Employees perform manual reconciliation on this 06:00-to-06:00 boundary
- The Shift Totalizer records midnight-to-midnight — this mismatch must be handled

### How We Resolve the 6AM Boundary Problem
The ISS table records `Totalizer Start` and `Totalizer End` on every transaction.
To get a 6AM totalizer reading: find the last transaction before 06:00 on each nozzle.
Its `Totalizer End` = the 6AM opening reading for the operational day.
The scraper's "boundary" mode exists specifically for this purpose.

**Boundary mode algorithm (per shift date):**
1. Run the XG Shift Totalizer pre-check (see XG Handling section) — resolves nozzle 11 without ISS search.
2. Run ST pre-check for all 5 active nozzles (7, 15, 16, 17, 18) — if movement ≤ 7L, nozzle was OOO or pump-test-only; use ST close as boundary and skip ISS search for that nozzle.
3. If all 6 nozzles resolved by ST pre-checks, skip ISS entirely.
4. For remaining nozzles: ISS backward search from 05:30–06:00, stepping back in 30-min increments. Each nozzle tracked independently — once found, removed from the remaining set.
5. Returns `{nozzle_no: totalizer_end_value}` for all 6 nozzles.

The step-0 window (06:00–06:30) is intentionally skipped — it belongs to the new shift.

**OOO (Out of Order) nozzle handling:**
Active nozzles (7, 15, 16, 17, 18) that show ≤ 7L ST movement for a calendar day were either broken or completely idle. Same 7L threshold as XG. ST close is used as the 06:00 boundary — valid because the outlet is closed 01:00–06:00, so no meaningful sales occur in that window. This prevents 48-window exhaustion when a nozzle has zero ISS activity (confirmed: nozzle 15 had zero transactions on Mar 31).

### XG (Nozzle 11) Handling — Boundary Mode Exemption

XG is effectively dormant (~100L/month). Searching 48 ISS windows backwards for a single
XG transaction is wasteful. The Shift Totalizer is used instead as a pre-check.

**Key facts confirmed from real data:**
- The outlet runs 24x7 but no XG is ever sold between midnight and 06:00 — safe assumption.
- A pump test of exactly 5L per nozzle runs every morning on all 6 nozzles (~08:20).
- ISS transaction type for pump test: "Pump Test (105)" — fuel dispensed back into tank, not a sale.
- Pump test runs every day — treat it as the norm, not the exception.

**XG pre-check algorithm (runs before ISS boundary scraper):**

1. Download Shift Totalizer for the operational date (already being downloaded for other reasons).
2. Read nozzle 11 movement: `xg_movement = shift_totalizer_close - shift_totalizer_open`
3. **If xg_movement ≤ 7L:**
   - No meaningful XG sale occurred. Movement is pump test only (5L ± 2L buffer).
   - Do NOT run ISS boundary search for nozzle 11.
   - Use Shift Totalizer open value as the 6AM opening totalizer for this operational day.
     Valid because no XG is ever sold between midnight and 06:00, so midnight open = 06:00 open.
   - XG net sales for this shift = 0L.
   - XG pump test litres = xg_movement (store in NozzleTotalizer.pump_test_litres).
4. **If xg_movement > 7L:**
   - A genuine XG sale occurred (beyond pump test).
   - Run ISS boundary search for nozzle 11 as normal.
   - Deduct 5L as pump test when calculating XG net sales.

**Threshold rationale:**
- Pump test = 5L (constant, confirmed)
- Buffer = 2L (handles minor variation)
- Threshold = 7L
- Genuine XG sales of >2L correctly trigger boundary search
- Misclassification risk (genuine sale ≤2L reported as zero) is acceptable given XG volumes and owner supervision

**Pump test for active nozzles (HS/MS/X2):**
- The ISS backward search for active nozzles naturally passes through the ~08:20 morning window
- Pump test transactions for active nozzles are detected from downloaded ISS files as before
- Future plan: Replace ISS-based pump test detection with manual attendant entry in app (deferred)

### The Reconciliation Formula (per operational day, per product)
```
Opening Stock (ATG at 06:00)
+ Deliveries Received (TT Receipt, Net Qty Decanted)
- Sales Volume (Totalizer Close at 06:00 next day - Totalizer Open at 06:00)
= Expected Closing Stock

Actual Closing Stock (ATG at 06:00 next day)
vs Expected Closing Stock = Stock Variance (flag if > threshold)
```

### The Payment Reconciliation Formula
```
Sales Value (litres sold × RSP) 
= Paytm Settlements (card + UPI)
+ Cash Collected
+ Credit Extended

Cash = Sales Value - Paytm - Credit  (derived, not measured)
Any shortfall in physical cash vs derived cash = accountability gap
```

### Price Lookup Logic
Never use hardcoded prices. Always join transaction datetime to the Price table:
- Match Product Code
- Match where Transaction DateTime falls between Effective Date From (06:00)
  and Effective Date To (05:59:59 next day)

### Totalizer Facts
- Totalizers are hardware odometers on each nozzle — cumulative litres since installation
- They only go up, never reset
- Tamper-evident: a gap in the sequence = unreported dispensing
- Nozzle 7 (HS), 11 (XG), 16 (HS) show Last Totalizer = 0 in master table
  but do appear in ISS data — the master table value is stale, not the transactions

### Fuel Adulteration Check
Three-stage density chain per delivery:
1. **Depot density** → RDB SAP Invoice (`Density` column) — measured at loading
2. **Tanker chamber density** → Receipt Density Records — employee hydrometer at unloading
3. **Post-decant tank density** → Density Records + TT Receipt Pre/Post — ATG in tank

Alert rule: if delta between depot density and employee hydrometer reading ≥ 5% → flag as potential adulteration.

### CNG
CNG is a completely separate infrastructure not tracked by IRAS FCC system.
Tank 5 in IRAS is a placeholder — all zeros, status 'U' (Unavailable).
CNG is deferred to Phase 2.

---

## Delivery Workflow
1. IOC depot loads tanker, records density per chamber in SAP → appears in RDB SAP Invoice
2. Tanker (truck) arrives at outlet
3. Employee checks each chamber with hydrometer → entered in Receipt Density Records
4. Decanting begins → TT Receipt records ATG levels before and after
5. Post-decant tank density recorded in Density Records

Trucks used: MP17HH4740 (regular), MP53HA2180, MP20ZQ9560 (occasional)
Supply point: Terminal/Depot 3356

---

## Known Anomalies to Be Aware Of

### Receipt 1107 (25 Mar 2026, MS, 463L)
A TT Receipt record exists for 463L of MS decanted into Tank 2 on 25-Mar with:
- No Supply Point
- No Invoice Number  
- No Truck Number
- No corresponding Receipt Density Record
- No Density Record
This is unresolved and suspicious. Flagged for investigation.

### Product XP (legacy)
Product code XP (Xtra Premium) appears in the Product master with a different CS ID
and no recent activity. It predates X2. Ignore in all reporting — use X2 only.

---

## Tech Stack

### Scraper
- **Language:** Python 3.14
- **Browser automation:** Playwright (async)
- **Mode:** Currently headless=False (manual CAPTCHA). Autonomous CAPTCHA solving via Claude Vision API in progress — see Autonomous Scraper section below.
- **Output:** Excel files (.xlsx) organised by tab type and date
- **Host:** Currently owner's home PC (Windows). Target: cloud server with cron schedule.

### Scraper Operating Modes
- `boundary` — ST pre-check for all 6 nozzles first; ISS backward search only for nozzles with movement > 7L; returns `{nozzle_no: totalizer_end_value}` for all 6 nozzles
- `pump_test_scan` — downloads ISS 08:00–11:30 windows (7 × 30-min slots) for a list of dates in `SHIFT_DATES`; prints a summary table of detected "Pump Test (105)" transactions per nozzle per date. Used for ad-hoc analysis only — reset `RUN_MODE` to `boundary` and clear `SHIFT_DATES` after use.
- `full` — exports all 48 half-hour windows of a full shift day (deferred to Phase 2)

### Scraper Archive Toggle Behaviour
- IRAS ISS tab has a "Last 7 days Report" toggle. Toggle ON = last 7 days only; toggle OFF = full archive.
- Toggle state is preserved when changing date/time within a session — no need to re-toggle per window.
- `ensure_iss_archive_mode()` called once per shift date before ISS search: turns toggle OFF for dates >7 days old, turns it back ON for dates ≤7 days old (handles batch runs going old→recent).

### Running the Scraper

**Daily autonomous run (current):**
```
python -X utf8 scrapers\daily_scrape.py
```
Defaults to today as shift_date (cron fires at 07:00 after 06:00 boundary passes).
Single date: `--date 2026-04-23`. Batch: `--dates 2026-04-20 2026-04-21 2026-04-22`.
Dry run (fresh downloads, no DB writes): add `--dry-run`.
Use `-u` flag (unbuffered) when output needs to appear in a log file in real time — the Flask UI trigger does this automatically.

**Manual ISS-only (legacy, rarely needed):**
```
python -X utf8 scrapers\iras_iss_exporter.py --dates <dates> --mode boundary
```
Browser opens for manual CAPTCHA login; scraper handles everything after.

### Autonomous Scraper — Architecture (built and confirmed)

`scrapers/daily_scrape.py` — one login covers all three daily jobs:

**Job order (Price → ST → ISS):**
1. **Price (PRM)** — RSP for the exact op day(s) being reconciled. `from_date = to_date = shift_date - 1`. RSP only changes at 06:00; never use a rolling lookback.
2. **Shift Totalizer** — must come before ISS; XG pre-check and OOO nozzle detection both read the ST file from disk.
3. **ISS boundary** — backward search from 06:00; writes results to NozzleTotalizer DB.

**CAPTCHA solving (`scrapers/captcha_test.py` + inline in `daily_scrape.py`):**
- Playwright headless=True loads IRAS login page
- Screenshot CAPTCHA image element only (locator screenshot)
- Send to `claude-opus-4-5` as base64; prompt: reply with characters only, ignore strikethrough
- Type returned text into CAPTCHA field and submit
- Check success: URL no longer contains `/login`
- If failed: retry up to 3 times, refresh CAPTCHA between each
- If all 3 exhausted: log failure and exit (alert hook TBD)
- Confirmed reliability: 4/5 first-attempt solves; retry logic handles the rest; 5/5 overall

**Key implementation details (important for future edits):**
- `importlib.util.spec_from_file_location` used to load `iras_iss_exporter` and `iras_price_exporter` by explicit path — bypasses sys.path entirely, avoids loading stale project-root copies
- `os.dup(1)` captured before imports; `sys.stdout` restored after both modules load — prevents stdout being closed by the double-wrapper left behind when both scrapers wrap stdout at import time
- `--dry-run` redirects all file output to `C:\IRAS_Data\_dry_run\{ISS,ShiftTotalizer,Price}` so skip-if-exists never triggers and existing production files are untouched; DB writes are skipped

**Cloud deployment (next step after local validation):**
- Deploy scraper to same cloud VM as Flask app (Render/Railway or DigitalOcean)
- Playwright runs headless on Linux — no display needed
- Cron job fires at 07:00 daily
- Scraper writes results directly to production database
- No human input required

### Recon UI — Totalizer Step Behaviour

The "Get Totalizer" button in `pumpvision/blueprints/recon/routes.py` (`run_scraper` route) has the following states:

| Condition | Message shown | Button |
|---|---|---|
| Both opening + closing in DB (6/6 nozzles each) | Ready — nozzles … | ✓ green tick |
| Current time < 06:00 on next_date (shift still in progress) | Day closing unavailable — shift still in progress | Greyed out, disabled |
| Current time ≥ 06:00 on next_date, closing not yet fetched | Ready to fetch day closing | Active blue button |
| Any other missing state | No data for this date | Active blue button |

When triggered, the route checks which of the two boundaries (op_date, next_date) are incomplete in the DB and only passes those to `daily_scrape.py`. If the opening boundary is already complete (6/6 nozzles), only the closing date is passed — no double work. Output is logged to `instance/scraper.log`.

**Totalizer Sales table** shows per-nozzle rows (not aggregated by product):
HS 1 (N7), HS 2 (N16), MS 1 (N18), MS 2 (N15), X2 (N17), XG (N11).
This matches the manual accounting sheet layout for easy cross-checking during the handholding/testing period. Product aggregation will be done at the dashboard level.

### Starting the Dev Server
Use `run_server.bat` on the Desktop: `flask --app wsgi run --host 0.0.0.0 --port 5000 --debug`
Note: `wsgi.py` has no `app.run()` — it is a WSGI entry point only. Do not run it with `python wsgi.py`.

### Web App — Architecture Principles
Every module and the final product must follow these principles without exception:

- **Mobile-first web app** — not a desktop UI, not a native app. Runs in the phone browser.
  All screens designed for mobile viewport from the start. Large touch targets, simple forms.
- **Cloud deployment is the production target** — never a locally-run server as a permanent solution.
  Local running is only for development and testing by the owner before deployment.
- **Local network accessible during development** — the app must bind to `0.0.0.0` so the
  developer can open it on their phone browser via the laptop's local IP (e.g. `192.168.x.x:5000`)
  while on the same WiFi. This is how all testing is done — never on a laptop screen.
- **Cloud-deployable from day one** — folder structure, config, and dependencies must be
  compatible with deployment to Render or Railway with minimal changes. No hardcoded local paths.
  Use environment variables for all config. Include a `Procfile` and `requirements.txt`.
- **Single codebase** — owner and attendant roles are served by the same app behind role-based login.
  No separate apps, no separate deployments.
- **SQLite for development, PostgreSQL-ready for production** — use SQLAlchemy ORM so the
  database backend can be switched by changing one environment variable.

### Web App — Tech Stack
- **Backend:** Python / Flask
- **Database ORM:** SQLAlchemy
- **Database:** SQLite locally → PostgreSQL on cloud
- **Frontend:** Jinja2 templates + Tailwind CSS (mobile-first utility classes)
- **Authentication:** Flask-Login (session-based, simple username/password)
- **PDF generation:** ReportLab (WeasyPrint has Windows incompatibility — do not use)
- **Deployment target:** Render or Railway (free tier to start)

### Credentials and Environment Variables
Stored in `.env` file in project root. Never commit to version control.
Use `python-dotenv` to load in development. On cloud, set via the platform's environment config.
```
IRAS_USERNAME=206858
IRAS_PASSWORD=<see .env>
IRAS_URL=https://iras.iocliras.in
OUTPUT_FOLDER=C:\IRAS_Data
SECRET_KEY=<random string for Flask sessions>
DATABASE_URL=sqlite:///pumpvision.db  (overridden to postgres:// on cloud)
```

---

## Project Phases

### Phase 1 — See Everything (current)
- Data ingestion from IRAS via automated Playwright scraper ← IN PROGRESS
- Daily dashboard: sales by product, stock levels, delivery log
- Shift reconciliation: expected vs actual collections
- Price tracking

### Phase 2 — Trust Engine
- Nozzle variance and totalizer gap detection
- Employee shift performance tracking
- Anomaly alerts (push notifications)
- CNG integration
- Dip stock reconciliation
- **Settlement reconciliation** — match Paytm settlements, bank credit alerts (SMS/email), and cash deposit records against expected collections to build a live "cash at pump" number and flag shortfalls in real time

### Phase 3 — Full Operations
- **Full accounting picture** — P&L with all revenue streams (fuel, lubricants, other) and all expense categories; settlement sources (Paytm, bank statements, credit receipts, cash deposits) unified into a single ledger
- Credit customer digital ledger (replacing paper parchi system) ← MODULE BUILT
- HR and attendance
- Compliance tracker

### Phase 4 — Scale and Sell
- Multi-outlet support
- Dealer onboarding
- Regional benchmarking
- HPCL/BPCL compatibility

---

## Credit Module

Built and integrated into the main Pumpvision app.
See `pumpvision/blueprints/credit/` and `pumpvision/blueprints/attendant/`.
Paper parchi system continues in parallel for legal authorization until SMS confirmation is built.

### Roles
- **Owner:** Full access — dashboard, customer management, ledger, invoices
- **Attendant:** Single screen — log a credit transaction

### Key Business Rules
- Vehicle dropdown always includes UNREGISTERED (automobile dealers) and CONTAINER
- Rate per litre auto-populated from IrasPrice table; never entered by attendant
- outstanding_balance updated atomically on every transaction and payment
- Credit alert threshold: 80% of credit limit (global, configurable)
- Alert is in-app only — no SMS at this stage
- Opening balance field on customer create/edit form directly sets `outstanding_balance` — used for migrating from paper ledger. Negative values allowed (pump owes customer).

### Current Data State (as of 24 Apr 2026)
- 29 credit customers in DB, all active
- Opening balances all entered — verified in DB, no zeros
- Notable balances: SONU TIWARI ₹32.5L, BP MISHRA ₹9.2L, CMR ₹9.3L, GODAHA CASH ₹6.8L, KHANIJ RH DGM ₹6.7L
- 3 customers with negative balances (pump owes customer): ARUN CONSTRUCTION, CENTRAL ACADEMY, TANKER — valid per business rules
- 0 credit transactions recorded digitally so far (all outstanding balances are opening migration values)

### Known Backlog
- Full mobile UI review pass not yet done

---

## Files in This Project
- `CLAUDE.md` — this file
- `.env` — credentials (never commit)
- `.claude/settings.local.json` — Claude Code permissions
- `credit_module/` — original standalone credit app (superseded by main app, kept for reference)
- `pumpvision/` — main Flask app (app factory pattern, blueprints)
  - `__init__.py` — app factory, blueprint registration, DB init + seed
  - `models.py` — all models: credit, IRAS (IrasPrice), Paytm (PaytmTransaction), NozzleTotalizer
  - `blueprints/auth/` — login/logout
  - `blueprints/dashboard/` — owner dashboard
  - `blueprints/credit/` — credit module owner routes, url_prefix `/credit`
  - `blueprints/attendant/` — attendant log transaction, url_prefix `/attendant`
  - `blueprints/paytm/` — Paytm CSV upload + day views, url_prefix `/paytm`
  - `blueprints/recon/` — reconciliation engine, url_prefix `/recon`
  - `templates/` — all Jinja2 templates, mobile-first Tailwind CSS
- `wsgi.py` — WSGI entry point (`from pumpvision import create_app; app = create_app()`)
- `scrapers/`
  - `iras_iss_exporter.py` — ISS scraper; boundary mode + XG exemption fully implemented; Shift Totalizer download + parser built; pump_test_scan mode added for ad-hoc analysis
  - `iras_price_exporter.py` — Price (PRM) scraper; navigate_to_price() has overflow "..." menu fallback for when Price(PRM) tab is not directly visible
  - `captcha_test.py` — standalone CAPTCHA PoC; confirmed 5/5 runs successful; saves debug screenshots as `captcha_attempt<N>.png`
  - `daily_scrape.py` — daily orchestrator: one autonomous login → Price → ST → ISS; supports `--date`, `--dates`, `--dry-run`; logs to `instance/scraper.log` when UI-triggered
- `requirements.txt`, `Procfile`, `.env`, `.env.example`

---

## Observed Operating Patterns (from real data)

### Outlet closing hours
The outlet appears to close roughly between ~01:00 and ~06:00. ISS windows in this range
consistently return empty for most or all nozzles. Confirmed on 2026-02-26 and 2026-02-27.
Outlet is technically 24x7 but no XG is sold between midnight and 06:00 — confirmed safe assumption.

### Nozzle 11 (XG) transaction frequency
Extremely inactive — last pre-06:00 transaction on 26-Feb was ~10:00am the previous morning.
Now handled via Shift Totalizer pre-check rather than ISS backward search. See XG Handling section.

### Nozzle 16 (HS) low volume
Nozzle 16 (HS, DU 15 Gilbarco) sells far less diesel than Nozzle 7 (HS, DU 9 MIDCO).
On 26-Feb: Nozzle 7 sold 1,860.71 L vs Nozzle 16 only 25.00 L.
May reflect customer preference or Nozzle 16 used only for overflow. Watch across more days.

### Pump Tests
- Run every morning on all 6 nozzles, typically ~08:20
- Always 5L per nozzle (confirmed April 16 data)
- ISS transaction type: "Pump Test (105)" — fuel goes back into tank, not a sale
- Deducted from totalizer diff before calculating net sales value

---

## What to Work On Next
1. ~~Verify the scraper successfully downloads ISS Excel files for test dates~~ ✓ Done
2. ~~Confirm boundary mode correctly identifies the last pre-6AM transaction window~~ ✓ Done
3. ~~Build the credit module standalone web app~~ ✓ Done
4. ~~Test and iterate on the credit module~~ ✓ Done — integrated into main app
5. ~~Build Price (PRM) scraper~~ ✓ Done — `scrapers/iras_price_exporter.py`
6. ~~Build Paytm CSV uploader + parser~~ ✓ Done — `pumpvision/blueprints/paytm/`
7. ~~Build reconciliation engine~~ ✓ Done — `pumpvision/blueprints/recon/`
8. ~~Implement XG boundary mode exemption~~ ✓ Done
9. ~~Extend ST pre-check to all active nozzles~~ ✓ Done — OOO nozzles (movement ≤ 7L) use ST close as boundary, skip ISS search; archive toggle now bidirectional
10. ~~Run batch scraper for Apr 1–22~~ ✓ Done — NozzleTotalizer DB complete: Apr 1–23 all 6/6 nozzles
11. ~~UI: show open and close totalizer values as separate columns~~ ✓ Done — recon day view now shows Open, Close, Pump Test, Net L, RSP, Value
12. ~~Recon day auto-calculates on load~~ ✓ Done — no Calculate button; result shown automatically when data ready; date picker added
13. ~~Manual totalizer entry screen~~ ✓ Done — `pumpvision/blueprints/meters/` (owner) + shift-close routes in `pumpvision/blueprints/credit/attendant.py`
    - Employee flow: `/attendant/` home → `/attendant/shift-close` product list → `/attendant/shift-close/<product>` entry → `/attendant/shift-close/submit` locks + notifies
    - Owner view: `/meters/` index + `/meters/<date_str>` day view with toggle (Totalizer nozzle-level | Litres Sold product-level)
    - **Pending: employee flow needs real-device testing**
14. ~~Pump test hardcoded at 5L per nozzle~~ ✓ Done — AppSetting `pump_test_nozzle_<N>` default 5.0; editable in Settings tab
15. ~~Autonomous CAPTCHA PoC~~ ✓ Done — `scrapers/captcha_test.py`; confirmed 5/5 runs; 4/5 first-attempt solves, retry handles the rest
16. ~~Build daily scraper orchestrator~~ ✓ Done — `scrapers/daily_scrape.py`; autonomous login; job order Price → ST → ISS; 1-day price range (op day only); `--dry-run` confirmed working on Apr 23; UI-triggered via "Get Totalizer" button in recon page → writes to `instance/scraper.log`
17. ~~Enter opening balances for 26 credit customers~~ ✓ Done — 29 customers, all balances verified in DB
18. **Upload Paytm CSVs for Apr 20–22** — currently missing; recon for those days will show Paytm not ready
19. **Paytm completeness check** — flag in recon UI when an operational day's Paytm data has no next-calendar-day transactions (incomplete upload warning)
20. **Expenses and lubricant sales module** — owner logs daily expenses (any category) and non-fuel sales. Feeds into P&L. Attendant not involved.
21. **Build main Pumpvision dashboard UI** — ground-up redesign conversation before building; data layer must be complete first
22. Future: Replace ISS-based pump test detection with manual attendant entry in app
23. Future: Deploy scraper to cloud server with cron schedule — run `daily_scrape.py` at 07:00 via cron; headless mode already set
