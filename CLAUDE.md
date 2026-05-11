> Always read this entire file before starting any task in this project.
> Visual references for every UI screen live in `docs/screens/` — read them
> before implementing any template.

# Pumpvision — Project Briefing

## What This Project Is

Pumpvision is a mobile-first management and operations platform for an IndianOil retail outlet (RO).
The outlet is **Shree Petroleum**, RO code **206858**, located in Rewa, Madhya Pradesh, India.
It is operated by the dealer's family (owner: father + son Rishab).

The goal is to give the owners a 360-degree real-time view of:
- Daily fuel sales and revenue (by product, by nozzle)
- Stock levels and delivery reconciliation
- Payment collection breakdown (cash vs card/UPI vs credit vs fleet card)
- Anomaly and theft detection
- Delivery quality (fuel adulteration checks)
- Credit customer ledger and invoicing

The project is being built with vibe coding (no professional software engineering background).
If successful, it will be sold as a SaaS product to other IndianOil (and potentially HPCL/BPCL) dealers.

**Competitive context:** PetroByte is the main mobile-capable competitor (cloud-based, Android app,
~₹3,990/year). Pumpvision's differentiation: automated IRAS data ingestion (no manual entry),
owner-first design (not operator-first), IOC/IRAS-native integration, and a distinct visual identity.
The market has ~100,000 petrol pumps in India (90%+ public sector — IOC, BPCL, HPCL).

---

## Version Control

The project is under Git, hosted at **github.com/rishab-mishra01/pumpvision** (private repo).
The default branch is `main`. Every push to `main` auto-deploys to Railway.

`.gitignore` excludes secrets (`.env`), operational data (CSVs, Excel files), Python build artifacts,
the database file, virtual environments, and OS junk.

Daily workflow:
```
git add .
git commit -m "Plain-English description of the change"
git push
```

---

## Deployment (Live — May 2026)

| Item | Value |
|------|-------|
| Platform | Railway (paid tier, usage-based ~$10–15/month) |
| Live URL | `web-production-a1322.up.railway.app` |
| Database | PostgreSQL on Railway (SQLite only for local dev) |
| Auto-deploy | Every push to `main` triggers a redeploy |
| PWA | Installed on phone — manifest.json + icons at `pumpvision/static/` |
| Owner login | `admin` / `shreeadmin2026` |
| Attendant login | `operations` / `shreeoperations2026` |

**Customer data:** 29 customers + 66 vehicles migrated from local SQLite into production PostgreSQL (May 2026).

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
- **Credit** — institutional/fleet customers; paper parchis being digitised
- **Fleet card** — fourth payment mode; customer swipes fleet card (prepaid), value credited to IOCL depot account (not bank account). Settles monthly against fuel purchases from IOCL.

### Revised Payment Reconciliation Formula
```
Gross fuel sales (litres × RSP)
+ Lube sales (cash component)
= Total revenue

Total revenue
= Cash collected (derived)
+ Paytm (UPI + card)
+ Credit extended to customers
+ Fleet card swipes (settles to IOCL depot account)

Cash = Total revenue − Paytm − Credit − Fleet card   (derived, not measured)
```

The IOCL depot account is a separate ledger:
- Credits: fleet card swipes processed at the outlet
- Debits: fuel purchases from IOCL depot
- Balance: what IOCL owes you or you owe IOCL
Depot account reconciliation is deferred to Phase 2 pending exploration of settlement report format.

---

## Three-User Model

The app serves three distinct user roles via a common Flask application. All share one codebase,
one database, one deployment. Role is determined at login.

### Owner (Rishab / father)
**Primary goal:** Financial control and strategic oversight.
**Interface:** Executive dashboard, reconciliation, credit management, intelligence layer.
**Actions:** View daily P&L, review reconciliation, manage credit customers and limits,
confirm bank transfer payments, view all alerts, generate invoices (via owner copy).

### Manager (outlet manager)
**Primary goal:** Daily operational accountability and cash management.
**Interface:** Shift-contextual task checklist — tells him exactly what's pending each day.
**Actions:** Log lube sales (cash or credit), log expenses, record fleet card swipes,
record payments received from credit customers, generate invoices.

The manager is typically an unskilled operator who's been at the outlet long enough to be
promoted. The interface must be prescriptive — it tells him what to do, not the other way around.

### Attendant (pump attendant)
**Primary goal:** Field operations — logging credit fuel sales and shift close readings.
**Interface:** Two-card home, credit sale flow, shift close flow.
**Actions:** Log credit fuel sales, enter shift close totalizer readings.

### Auth Architecture Note
Currently User is in-memory (env-var-based auth). This does NOT scale beyond 1–2 users.
**Sprint 0 must convert User to a DB-backed model** before a second attendant or the manager
account is created. See item 18 in What to Work On Next.

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
ATG data refreshes every 30 minutes — that is the freshest the Stock screen can be.

### Dispensing Units (DUs) and Nozzles
| DU | Pump | Nozzle | Product | Tank | Attendant Name |
|----|------|--------|---------|------|---------------|
| 9 (MIDCO) | 1 | 7 | HS | 1 | HS1 |
| 9 (MIDCO) | 2 | 11 | XG | 4 | XG (only nozzle) |
| 14 (MIDCO) | 3 | 17 | X2 | 3 | X2 (only nozzle) |
| 14 (MIDCO) | 4 | 18 | MS | 2 | MS1 |
| 15 (GILBARCO) | 5 | 15 | MS | 2 | MS2 |
| 15 (GILBARCO) | 6 | 16 | HS | 1 | HS2 |

Only DU 9 (Pumps 1 & 2) has receipt printers. NPND interlock is disabled on all pumps.

**Attendant Nomenclature (locked).** Attendant UI uses HS1/HS2/MS1/MS2 as primary nozzle
identifiers. DB stores nozzle numbers. X2 and XG have only one nozzle each, so no suffix.

---

## Data Sources

### Primary Source: IRAS Portal
**URL:** https://iras.iocliras.in
**Credentials:** stored in `.env` file (never hardcode in scripts)
**Automation:** Playwright Python scripts under `scrapers/`

#### Key constraint: ISS tab exports max 30 minutes of data at a time.

#### ISS usage strategy
- **Boundary mode** (built) gives 06:00 totalizer readings per nozzle — sufficient for reconciliation.
- **Full 48-window scrape** deferred to Phase 2.

### IRAS Tables We Use

#### Static Reference Tables
| Tab | Sheet | Contents |
|-----|-------|----------|
| FCC Data > Product(PDM) | Product(PDM) | Product code → name mapping |
| FCC Data > Tank(TKM) | Tank | Tank config, capacity, ATG probe details |
| FCC Data > Pump | Pump | DU make/model, pump config |
| FCC Data > Nozzle | Nozzle | Nozzle → pump → tank → product mapping |

#### Daily Operations Tables
| Tab | Sheet | Contents | Frequency |
|-----|-------|----------|-----------| 
| FCC Data > Price(PRM) | Price(PDM) | Daily RSP per product (IOC-pushed at 06:00) | Daily |
| FCC Data > Issue(ISS) | Issue(ISS) | Every fuel transaction (30-min export limit) | Per transaction |
| FCC Data > Stock | Stock | ATG tank level snapshots | Every 30 min |
| FCC Data > Shift Totalizer | Shift Totalizer Record | Daily nozzle totalizer open/close (midnight boundary) | Daily |

#### Delivery Tables
| Tab | Sheet | Contents |
|-----|-------|----------|
| RDB Data > Invoice | RDB SAP Invoice | Depot invoice: purchase amount + depot density |
| FCC Data > SAP Invoice | SAP Invoice | Delivery summary: chamber breakdown, truck no |
| FCC Data > Receipt | TT Receipt | Actual decanting: ATG-measured pre/post tank levels |
| FCC Data > Receipt Density | Receipt Density Records | Employee hydrometer readings per chamber |
| FCC Data > Density Records | Density Records | Post-decant ATG density in tank |

### Secondary Source: Paytm for Business
- **Transaction report** — individual UPI and card transactions per operational day
- **Settlement report** — actual amounts settled into bank account

**Current (Sprint 1):** `scrapers/paytm_exporter.py` — Playwright scraper that downloads the
previous operational day's transaction CSV (06:00 yesterday → 05:59 today) from
`dashboard.paytm.com`. Runs headless with stealth mode. Integrated into `daily_scrape.py`
as Job 0. Session persisted via `scrapers/paytm_state.json` (JSON cookie store).

**Future (Sprint 2):** Automated ingestion via email watcher — Paytm exports CSVs to email,
cloud server watches inbox, detects Paytm emails, ingests automatically. Eliminates manual
scraping.

---

## Critical Business Logic

### The Operational Day is 06:00 to 05:59 (not midnight to midnight)
RSP records are effective from 06:00:00 to 05:59:59 the next day. Manual reconciliation happens
on this 06:00-to-06:00 boundary. Shift Totalizer records midnight-to-midnight — this mismatch
must be handled.

### How We Resolve the 6AM Boundary Problem
Find the last ISS transaction before 06:00 per nozzle. Its `Totalizer End` = the 06:00 opening
reading for the operational day.

**Boundary mode algorithm:**
1. Run XG Shift Totalizer pre-check (resolves nozzle 11 without ISS search).
2. ISS backward search from 05:30–06:00 for 5 active nozzles: 7, 15, 16, 17, 18.
3. Stop when all 5 resolved or 48 windows checked.

The step-0 window (06:00–06:30) is skipped — it belongs to the new shift.

### XG (Nozzle 11) Handling — Boundary Mode Exemption
**Pre-check algorithm:**
1. Read nozzle 11 movement from Shift Totalizer: `xg_movement = close - open`
2. If xg_movement ≤ 7L → carry forward previous totalizer, XG net sales = 0, no ISS search.
3. If xg_movement > 7L → run ISS search, deduct 5L pump test from net sales.

Threshold: 5L pump test + 2L buffer = 7L. Outlet runs 24x7 but no XG sold midnight–06:00.

### Reconciliation Formulas

**Stock reconciliation (per product per day):**
```
Opening Stock (ATG at 06:00)
+ Deliveries Received (TT Receipt, Net Qty Decanted)
- Sales Volume (Totalizer Close − Totalizer Open at 06:00)
= Expected Closing Stock

Actual Closing Stock (ATG at 06:00 next day)
vs Expected = Stock Variance (flag if > threshold)
```
Requires ATG scraper (not yet built) + delivery scraper (not yet built).

**Payment reconciliation (per day):**
See revised formula in Payment Modes section above.

### Price Lookup Logic
Never hardcode prices. Join transaction datetime to Price table:
- Match Product Code
- Match where DateTime falls between Effective Date From (06:00) and To (05:59:59 next day)

### Totalizer Facts
- Hardware odometers — cumulative litres since installation. Only go up, never reset.
- Tamper-evident: gap in sequence = unreported dispensing.
- Closing reading must always be greater than opening.

### Fuel Adulteration Check
Three-stage density chain per delivery:
1. Depot density → RDB SAP Invoice
2. Tanker chamber density → Receipt Density Records (employee hydrometer)
3. Post-decant tank density → Density Records + TT Receipt

Alert rule: delta between depot density and employee hydrometer ≥ 5% → flag.

### Pump Tests
- 5L per nozzle every morning, all 6 nozzles, typically ~08:20
- ISS type: "Pump Test (105)" — fuel goes back into tank, not a sale
- Deducted from totalizer diff before net sales calculation
- Totals show "incl. 5L pump test/nozzle" — all figures are net

### Operational Date Function
`get_operational_date()` in `services/operational.py`:
- Before 06:00 → returns yesterday (shift still open)
- At/after 06:00 → returns today (new shift started)
Used only in attendant home nudge. **Do not use in shift close routes** — those use
`_shift_op_date() = date.today() - 1` (always yesterday, the shift being closed).
Conflating these caused a critical bug.

---

## Lube Products Module

Lubricants are sold at the outlet counter. They can be sold for cash (dominant) or on credit.
Credit lube sales add to the customer's outstanding balance like credit fuel sales.

### The Catalogue (44 SKUs — seeded in `lube_products` table)

**Engine Oils**
2T Supreme 1L · 2T Tractor Oil MG 20W40 7.5L · 4T Green Oil 1L · 4T Green Oil 900ml ·
4T Oil 1L · Honda Josh 900ml · Kool Plus 1L · Premium 15W40 CF4 1L · Premium 15W40 10L ·
Premium 15W40 15L · Premium 15W40 20L · Pride TC 15W40 7.5L · Pride TC 15W40 10L ·
Pride XL Plus 15W40 10L · Pride XL Plus 15W40 15L · Pride XL Plus 15W40 20L ·
Servo FLT CF4 15W40 1L · Servo FLT CF4 15W40 7.5L · Servo FLT CF4 15W40 15L ·
Servo SMG 20W40 5L · Servo SMG 20W40 7.5L · Super 20W40 MG 500ml · Super 20W40 MG 1L ·
Super 20W40 MG 10L · Super 20W40 MG 20L · Fleet CF4 15W40 1L · Fleet CF4 15W40 5L ·
Fleet Supreme CF4 Plus 15W40 15L

**Gear Oils & Hydraulic**
Gear HP 90 1L · Gear HP 90 5L · Gear HP 90 20L · Hydra Shakti 68 26L ·
System 46 26L · System 46 20L · System 68 20L (bucket) · System 68 20L (hydraulic) · System 68 26L

**Transmission**
Transfluid A 1L

**Brake Fluid**
Brake Oil 250ml · Brake Oil 500ml

**Grease**
Grease MP3 1kg · Grease MP3 2kg

**Urea / AdBlue**
IOC ClearBlue 20L · Servo Clear Blue 20L

Source: pump_stock_10_04_2026.pdf + godam_stock_10_04_2026.pdf (deduplicated).
Sale rates per SKU are in those files and should be seeded into `lube_products.sale_rate`.

---

## Schema — New Tables (Sprint 0 additions)

### `users` (DB-backed — replaces in-memory User)
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| username | String NOT NULL UNIQUE | |
| password_hash | String NOT NULL | bcrypt |
| role | String NOT NULL | 'owner' / 'manager' / 'attendant' |
| first_name | String | For greeting on home screen |
| is_active | Boolean DEFAULT True | |
| created_at | DateTime | |

Seed at startup: owner (`admin`), attendant (`operations`), and manager account.
Passwords from `.env` env vars — never hardcode.
Seed uses upsert pattern — checks if username exists before inserting. Safe to
run on non-empty DB. Adding new users to the seed block will always work on redeploy.

### `lube_products` (seeded catalogue)
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| name | String NOT NULL | e.g. "Servo FLT CF4 15W40" |
| pack_size | String NOT NULL | e.g. "1L", "7.5L", "900ml" |
| unit | String DEFAULT 'unit' | |
| sale_rate | Float NOT NULL | From stock file |
| is_active | Boolean DEFAULT True | |

### `lube_transactions`
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| product_id | Integer FK → lube_products | |
| quantity | Float NOT NULL | |
| unit_price | Float NOT NULL | Sale rate at time of transaction |
| amount | Float NOT NULL | quantity × unit_price |
| payment_mode | String NOT NULL | 'cash' / 'credit' |
| customer_id | Integer FK → customers NULLABLE | Only if credit |
| op_date | Date NOT NULL | Operational date |
| transaction_time | DateTime NOT NULL | |
| logged_by | Integer FK → users | Manager who logged it |
| created_at | DateTime | |

### `expenses`
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| amount | Float NOT NULL | |
| category | String NOT NULL | 'Staff' / 'Maintenance' / 'Utilities' / 'Supplies' / 'Misc' |
| description | String | Free text |
| op_date | Date NOT NULL | |
| logged_by | Integer FK → users | |
| created_at | DateTime | |

Categories are configurable via `app_settings` table (existing).

### `fleet_card_transactions`
| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | |
| card_identifier | String NOT NULL | Free text — card number or customer name |
| amount | Float NOT NULL | Value of the swipe |
| op_date | Date NOT NULL | |
| transaction_time | DateTime | |
| logged_by | Integer FK → users | Manager |
| notes | String | |
| created_at | DateTime | |

Fleet card swipes credit the IOCL depot account, not the bank account.
Depot account reconciliation deferred to Phase 2.

### `payments_received` — update existing table
Add columns:
- `status` String DEFAULT 'confirmed': 'confirmed' / 'pending_verification' / 'flagged'
- `verified_by` Integer FK → users NULLABLE (owner who confirmed)
- `verified_at` DateTime NULLABLE

**Logic:** Cash and cheque payments → status = 'confirmed' immediately (manager has physical evidence).
Bank transfers → status = 'pending_verification' until owner confirms against bank statement.
Owner sees pending bank transfers in their Action Center / dashboard. On confirm → status = 'confirmed',
customer balance updates. On flag → status = 'flagged', manager is notified.

---

## Manager Workflows (Sprint 1)

### Manager Home — Shift-Contextual Checklist
The manager home screen is a **daily task checklist** that resets every operational day.
It tells the manager exactly what's pending, not what's possible.

Checklist items populate dynamically based on events and completion state:

**Always present:**
- Log today's expenses (amber if not yet logged, green if done)
- Upload Paytm report (until automated ingestion is built)

**Triggered by events:**
- Fleet card swipe — if a fleet customer came in (manager logs it immediately)
- Record payment received — per customer who said they'd pay today

**Periodic (end of month):**
- Generate invoices for customers with uninvoiced transactions

**Carried from previous day if not done:**
- Any unpaid / un-invoiced items from yesterday surface as overdue

Each row is tappable, routes directly to the relevant form. The manager cannot miss
something because the app tells them explicitly what is incomplete.

### Manager Actions

**Log lube sale:**
- Select product from `lube_products` catalogue
- Enter quantity
- Unit price pre-filled from catalogue (editable)
- Toggle: Cash or Credit
- If Credit → select customer (same customer picker as attendant credit flow)
- Submit → creates `lube_transaction`, updates `customers.outstanding_balance` if credit

**Log expense:**
- Amount, category (dropdown from seeded categories), description (free text), date (defaults today)
- Submit → creates `expenses` record

**Log fleet card swipe:**
- Card identifier (free text), amount, optional notes
- Submit → creates `fleet_card_transactions` record

**Record payment received:**
- Select customer
- Amount, payment mode (Cash / Cheque / Bank Transfer)
- Reference number (for cheque/bank transfer)
- Cash/Cheque → status = confirmed immediately
- Bank Transfer → status = pending_verification (owner must confirm)
- Confirmed payments → update customer outstanding_balance atomically

**Generate invoice:**
- Select customer
- System shows uninvoiced credit transactions for the period
- Confirm total → generate PDF → mark transactions as invoiced
- Owner gets an alert in their Action Center: "Invoice generated for [customer] — review"

### Intelligence Layer (Owner receives)
The owner's Action Center / dashboard surfaces accountability signals:
- "X bank transfers pending your verification" (with date and customer details)
- "Invoice generated for [customer] by [manager] — [date]" (informational)
- "[Customer] payment not recorded in [N] days — follow up" (based on payment terms)
- "No expenses logged today" (if manager hasn't logged by a configurable time)
- Credit alerts: customers over utilization threshold

---

## Architecture Principles

### Single Tree, Three Branches
One Flask app, one database, one deployment. Three roles via `users.role`.

Build order:
1. Foundation ✓ Done
2. Attendant branch ✓ Done
3. Sprint 0: three-user foundation (convert User to DB, add manager blueprint) ← NEXT
4. Sprint 1: manager operational flows
5. Sprint 2: ATG scraper + automated Paytm ingestion
6. Sprint 3: owner branch UI (new design system applied)

### Cloud Deployment
- Production: Railway (paid tier), PostgreSQL
- Mobile-first PWA, runs in phone browser
- Bind to `0.0.0.0` in dev for local network phone testing
- Auto-deploys on every push to `main`

### SQLite Locally, PostgreSQL-Ready
SQLAlchemy ORM only (no raw SQL). `DATABASE_URL` env var switches backend.
Flask-Migrate / Alembic for all schema changes.

---

## Tech Stack

### Web App
- **Backend:** Python / Flask (app factory, blueprints)
- **ORM:** SQLAlchemy + Flask-Migrate
- **Database:** SQLite locally → PostgreSQL on Railway
- **Frontend:** Jinja2 + Tailwind CSS (mobile-first)
- **Auth:** Flask-Login (session-based)
- **PDF:** ReportLab (NOT WeasyPrint — Windows incompatibility)
- **Deployment:** Railway (paid tier)

### Scraper
- **Language:** Python 3.14
- **Automation:** Playwright (async)
- **CAPTCHA:** Claude Vision API (PoC at `scrapers/captcha_test.py`)
- **Modes:** boundary (built), full (Phase 2)

### Environment Variables
```
IRAS_USERNAME=206858
IRAS_PASSWORD=<see .env>
IRAS_URL=https://iras.iocliras.in
ANTHROPIC_API_KEY=<for CAPTCHA solving>
SECRET_KEY=<random string>
DATABASE_URL=sqlite:///pumpvision.db  (overridden to postgres:// on Railway)
OUTPUT_FOLDER=C:\IRAS_Data
OWNER_USERNAME=admin
OWNER_PASSWORD=shreeadmin2026
ATTENDANT_USERNAME=operations
ATTENDANT_PASSWORD=shreeoperations2026
MANAGER_USERNAME=<see .env>
MANAGER_PASSWORD=<see .env>
PAYTM_EMAIL=<email registered with Paytm Business>
PAYTM_PASSWORD=<see .env>
PAYTM_STATE_PATH=scrapers/paytm_state.json  (optional, default as shown)
PAYTM_HEADLESS=false  (omit or set true for headless; false for headed/debug)
SDMS_USERNAME=<see .env>
SDMS_PASSWORD=<see .env>
SDMS_STATE_PATH=scrapers/sdms_state.json  (optional, default as shown)
```

---

## Design System

### Current (Scaffolding — being replaced)
The current templates use a dark glassmorphic fintech aesthetic (Manrope + Inter,
black background, electric blue primary, product color coding). This is scaffolding built
during the Stitch iteration phase. It is functional but not the final visual identity.

### New Design System — Pumpvision Narrative (locked, being implemented)
A ground-up redesign with a distinct identity. Built in Figma. See `docs/design_system/`.

**Brand philosophy:** "Pulling something stuck in the past into modernity, without the clean break."
The interface borrows the language of the totalizer, the receipt, and the shift register.
Light, warm, precise. Made to be read in sunlight, used with one hand, trusted at the end of every shift.

**Wordmark:** Option A — compressed display lowercase "pumpvision" with saffron status-LED square.

**Color palette:**
- Paper (warm neutral substrate): background, card fill
- Ink (navy): structure, numbers, rules
- Saffron (energy accent, used with restraint): primary CTA, precision digit, status markers
- Product layer (secondary, fuel grades only):
  - HS: Electric Blue
  - MS: Emerald Green
  - X2: Royal Purple
  - XG: Sunset Orange

**Typography (three families, distinct registers):**
- **Newsreader** — serif, voice and editorial copy. Weights: Light, Regular, Medium only. **Never italic.**
- **IBM Plex Sans** — grotesk, UI labels, interface text, body copy
- **JetBrains Mono** — monospaced, all operational numbers (totalizer readings, litres, rupee amounts). Tabular figures, contextual ligatures off.
- **Major Mono Display** — single purpose: wordmark and totalizer display component only

**The Totalizer Component:**
Whenever a number carries operational weight (shift total, tank reading, cumulative dispense),
it renders as a totalizer: each digit in its own dark navy cell, monospaced, mechanical.
Sizes: XL (64px) · L (44px) · M (32px) · S (22px).
Last cell may render in saffron (precision digit, or mid-update state).

**Motion:** Numbers roll into place mechanically.
Duration 1800ms · cubic-bezier(0.16, 1, 0.3, 1).
Stagger: digit_index × 90ms (right-to-left settle).
Final snap: cubic-bezier(0.6, 0, 0.4, 1.4) — slight overshoot.

**Implementation status:** Design system locked in Figma. Implementation deferred until
Sprint 3 (owner UI). Attendant screens will also be updated in Sprint 3 in the same pass.
Do not apply the new design system to any templates until explicitly instructed.

### Forbidden — Do Not Invent
- "Efficiency Score", accuracy %, quality metrics, reconciliation cycles as %
- "Auto-settlement", "Automated Payouts", "Settlement batches"
- "Team members reviewing", multi-user collaboration features
- "Live Alerts" branding, "STATION OS", "Diagnostics", "Run Diagnostics"
- WhatsApp/SMS confirmation (deferred until cloud deployment)
- "Fleet Account" or "Corporate Account" customer types — all one type
- Any branding other than "Pumpvision"
- Hardware features that don't exist: temperature, pressure, flow rate

---

## Screen Inventory

All visual references live in `docs/screens/`. Read before implementing any template.

### Auth

#### `01_login.png`
**Route:** `GET/POST /login` · **Scroll:** No · **Bottom nav:** None
Both roles use same login page; role determines redirect after auth.
Elements: Pumpvision logo + wordmark, "INDIANOIL · SHREE PETROLEUM", Welcome back card,
ID/Username, Password with eye toggle, "Sign In →" button, "Need access? Contact your owner."
Forbidden: "Forgot Password", "Remember me", "Contact System Admin", version footer.

---

### Attendant Branch (3-tab nav: Home · Activity · Profile)

#### `02_attendant_home.png`
**Route:** `GET /` (role=attendant) · **Scroll:** No
Shift status nudge card between greeting and action cards:
- State A (amber): "[DD Mon] shift not closed · Tap Close Shift to enter readings."
- State B (green): "[DD Mon] shift closed ✓ · Today's shift is in progress."
Two action cards: "Log Credit Sale" (fuel pump icon) · "Close Shift" (clipboard + checkmark)

#### `03_select_customer.png`
**Route:** `GET /attendant/credit/select-customer` · **Scroll:** Yes
Search bar, filter chips (Recent/Frequent/All Accounts), customer cards with colored avatars.
Suspended customers: 50% opacity, red lock icon, "Credit Blocked".

#### `04_log_sale_details.png`
**Route:** `GET/POST /attendant/credit/log/<customer_id>` · **Scroll:** Yes (sticky CTA)
Customer card, vehicle dropdown (UNREGISTERED + CONTAINER always at top), 2x2 product grid,
Amount/Litres toggle, current rate auto-filled from IrasPrice, "Confirm & Log Sale" sticky.

#### `05_transaction_confirmed.png`
**Route:** Post-submit redirect · **Scroll:** No
"Sale logged" + receipt card + "Hand over signed parchi to customer" reminder.
NO WhatsApp block — paper parchi continues until cloud SMS is built.

#### `06_shift_close_product_selection.png`
**Route:** `GET /attendant/shift/select-product` · **Scroll:** No
2x2 product grid. DONE badge on products with all nozzle readings submitted for current op date.
HS/MS tap → DU Selection. X2/XG tap → Numpad directly (single nozzle, skip DU selection).

#### `07_shift_close_du_selection.png`
**Route:** `GET /attendant/shift/du/<product>` (HS or MS only) · **Scroll:** Yes (sticky CTA)
Two nozzle cards per product (HS1/HS2 or MS1/MS2 + DU number).
Dashed border = empty, solid = has draft reading. "Confirm Readings" always electric blue.

#### `08_shift_close_numpad.png`
**Route:** `GET/POST /attendant/shift/numpad/<nozzle>` · **Scroll:** No
Large input display + computed delta. Validation: delta < 0 → red warning + disabled button;
delta = 0 → amber warning + enabled; delta > 0 → green delta. 3x4 numpad.

#### `09_shift_close_summary.png`
**Route:** `GET/POST /attendant/shift/summary` · **Scroll:** Yes (sticky CTAs)
Six nozzle cards (HS1/HS2/MS1/MS2/X2/XG) with opening/closing/delta + Edit buttons.
Shift totals 2x2 grid (net after 5L pump test per nozzle).
Amber warning if any nozzle delta = 0 or < 5L.
Sticky: "Submit shift" (electric blue) + "Save as draft" (ghost).

---

### Manager Branch (to be designed — screens TBD)

Bottom nav: TBD (3-4 tabs). Screens to design and build in Sprint 1.

Manager home is a shift-contextual daily checklist. See Manager Workflows section above.
Manager screens needed: Home (checklist) · Log Lube Sale · Log Expense · Log Fleet Card ·
Record Payment · Generate Invoice.

Visual design for manager screens: use current dark scaffolding system for now.
New design system applied in Sprint 3 across all roles simultaneously.

---

### Owner Branch (to be designed — screens in docs/screens/)

Bottom nav: Home · Tanks · Credit · Reconcile · More (5 tabs)

#### `10_executive_dashboard.png`
**Route:** `GET /` (role=owner) · **Scroll:** No
Outlet identity + Action Center pill (with unread count) in topbar.
Price ticker (all 4 products, day-over-day delta, "—" for no change).
Time selector: Today/Week/Month.
Revenue card with 7-day BAR chart (not smooth waves).
Tank levels strip (4 compact circular rings with %, product name, k-litres).
Vehicle delivery teaser card → links to Tanks screen.

#### `11_owner_tanks.png`
**Route:** `GET /tanks` · **Scroll:** Yes
"AS OF [HH:MM]" timestamp (30-min ATG cadence).
Four tank cards: vertical test-tube fill visual (product color), %, Volume, Capacity, Days Left.
Days-Left color: > 7 days white, 3-7 amber, ≤ 2 red. ≤ 25% → card border red + "Order soon" pill.
Tank capacities: HS 20kL · MS 20kL · X2 10kL (smaller!) · XG 20kL.
Deliveries section: in-transit (orange) + scheduled (blue).

#### `12_credit_customer_list.png`
**Route:** `GET /credit/customers` · **Scroll:** Yes (sticky "+ Add customer")
Summary cells: Total Outstanding + Overdue.
Filter chips: All / Over 80% / Overdue / Suspended.
Customer cards: avatar + name + vehicle count + utilization bar + balance + %.
Border tint: < 70% default, 70-80% amber, > 80% red.

#### `13_credit_customer_detail.png`
**Route:** `GET /credit/customers/<id>` · **Scroll:** Yes
Customer header: name, meta, outstanding balance, utilization pill + progress bar.
Action row: "Record payment" + "Generate invoice" (both ghost, equal weight).
Activity feed: fuel transactions + lube transactions + payments received (chronological).
Invoices section at bottom.

#### `14_credit_customer_add.png`
**Route:** `GET/POST /credit/customers/new` and `/<id>/edit` · **Scroll:** Yes (sticky save)
Form: company name, account ID + GST, fleet manager, contact, credit limit + payment terms (15/30/45).
Authorized vehicles list with "+ Add vehicle" dashed button.
"Suspend account" destructive button — hidden in New mode, visible in Edit mode only.

#### Reconciliation screens (17, 15, 16)
See detailed specs in previous CLAUDE.md versions. Summary:
- `17_recon_open_items.png`: action rows with date, description, action button. Open items landing.
- `15_recon_day_view.png`: date selector + Day/Trend toggle, sales by product, stock variance card, collections grid.
- `16_recon_trend_view.png`: 30-day bar chart revenue, 4 product variance sparklines, collection mix stacked area. ENDS after collection mix — no invented metrics.

---

## Delivery Workflow
1. IOC depot loads tanker → appears in RDB SAP Invoice
2. Tanker arrives → employee hydrometers each chamber → Receipt Density Records
3. Decanting → TT Receipt records ATG before/after
4. Post-decant density → Density Records

Trucks: MP17HH4740 (regular), MP53HA2180, MP20ZQ9560 (occasional). Supply point: Depot 3356.
**MP17HH4740 is the supply tanker, NOT a customer vehicle.**

---

## Known Anomalies
- **Receipt 1107 (25 Mar 2026):** 463L MS with no invoice/truck/density records. Suspicious.
- **Product XP (legacy):** Predates X2. Ignore in all reporting.

---

## Files in This Project

### App package (`pumpvision/`)
- `__init__.py` — app factory, blueprint registration
- `extensions.py` — db, login_manager, migrate
- `models.py` — all SQLAlchemy models
- `constants.py` — NOZZLE_LABEL_MAP, PRODUCT_LABELS, PUMP_TEST_NOZZLES
- `decorators.py` — owner_required, attendant_required
- `user.py` — **in-memory User (NOT DB-backed) — convert in Sprint 0**
- `services/prices.py` — get_rsp()
- `services/operational.py` — get_operational_date()
- `blueprints/auth/routes.py` — login, logout, redirect
- `blueprints/attendant/routes.py` — all 9 attendant screens
- `blueprints/owner/routes.py` — placeholder (redirects to dashboard)
- `blueprints/credit/owner.py` — credit owner routes
- `blueprints/dashboard/routes.py` — owner dashboard stub
- `blueprints/paytm/routes.py` — Paytm CSV upload + day views
- `blueprints/recon/routes.py` — reconciliation engine + scraper trigger
- `blueprints/meters/routes.py` — totalizer reading views

### Project root
- `wsgi.py` — WSGI entry point
- `migrations/` — Flask-Migrate baseline (Apr 2026)
- `requirements.txt`, `Procfile`, `railway.json`
- `start.bat` — Windows launcher

### Static
- `pumpvision/static/manifest.json` — PWA manifest
- `pumpvision/static/icons/icon-192.png` + `icon-512.png` — PWA icons

### Scrapers
- `scrapers/iras_iss_exporter.py` — ISS boundary mode (XG exemption implemented)
- `scrapers/iras_price_exporter.py` — Price (PRM) scraper
- `scrapers/paytm_exporter.py` — Paytm transaction CSV downloader (headless, stealth mode)
- `scrapers/paytm_state.json` — persisted Paytm session cookies (gitignored)
- `scrapers/sdms_pad_exporter.py` — SDMS PAD Statement scraper; fleet card posting total + CSV (headless, stealth, Claude Vision CAPTCHA)
- `scrapers/sdms_state.json` — persisted SDMS session cookies (gitignored)
- `scrapers/captcha_test.py` — Claude Vision CAPTCHA PoC
- `scrapers/daily_scrape.py` — orchestration (Job 0: Paytm, Job 1: Price, Job 2: ST, Job 3: ISS, Job 4: SDMS PAD)

### Documentation
- `CLAUDE.md` — this file
- `docs/screens/` — 17 visual references (01_login.png → 17_recon_open_items.png)

---

## Observed Operating Patterns

- Outlet closes ~01:00–06:00. No XG sold midnight–06:00.
- Nozzle 11 (XG): last pre-06:00 transaction on 26-Feb was ~10:00am previous morning.
- Nozzle 16 (HS): 25L on 26-Feb vs Nozzle 7's 1,860L. Possible overflow-only usage.
- Pump tests: ~08:20, 5L/nozzle, every day.

---

## Parallel Workstreams

| Stream | Status |
|--------|--------|
| **Deployment** | ✓ Complete — Railway live, PWA, PostgreSQL, 29 customers migrated |
| **Attendant branch** | ✓ Complete — 9 screens, all wired to real data |
| **Sprint 0: three-user foundation** | ✓ Complete — DB-backed users, manager role, lube/expense/fleet schemas |
| **Sprint 1: manager flows** | ← CURRENT — stubs wired, need full implementation |
| **Paytm scraper** | ✓ Complete — headless, stealth mode, integrated into daily_scrape.py |
| **SDMS PAD scraper** | Branch `sdms-pad-scraper` — tested ✓, ready to merge to main |
| **Sprint 2: ATG scraper + Paytm automation** | After Sprint 1 |
| **Sprint 3: owner UI + design system** | After Sprint 2 (or in parallel) |
| **Design rework (Pumpvision Narrative)** | In progress in separate design conversation |

---

## What to Work On Next

1. ~~ISS scraper verify~~ ✓
2. ~~Boundary mode confirm~~ ✓
3. ~~Credit module~~ ✓
4. ~~Price scraper~~ ✓
5. ~~Paytm CSV uploader~~ ✓
6. ~~Reconciliation engine~~ ✓
7. ~~XG boundary exemption~~ ✓
8. ~~CAPTCHA PoC~~ ✓
9. ~~Design system + 17 screen mockups~~ ✓
10. ~~Git + GitHub~~ ✓
11. ~~Foundation refactor~~ ✓
12. ~~Attendant branch (9 screens)~~ ✓
13. ~~Activity + Profile tabs for attendant~~ — deprioritised
14. ~~Deploy to Railway + PWA~~ ✓ — live at `web-production-a1322.up.railway.app`
15. ~~Sprint 0: three-user foundation~~ ✓ — DB-backed users, manager role, all new tables, 44 lube SKUs seeded
16. ~~Paytm scraper~~ ✓ — `scrapers/paytm_exporter.py`, headless + stealth, integrated into `daily_scrape.py`
    - Session cookie reuse, auto-login fallback, set-based new-link detection
    - `PAYTM_HEADLESS=false` for debug; headless works reliably with stealth mode
16b. **SDMS PAD scraper** — `scrapers/sdms_pad_exporter.py`, branch `sdms-pad-scraper`
    - Logs into SDMS portal (Retail role, Claude Vision CAPTCHA), navigates to PAD Statement
    - Extracts full table to CSV + fleet card posting summary JSON
    - Integrated into `daily_scrape.py` as Job 4; skips if credentials not set
    - All `page.goto()` calls use `timeout=0` — SDMS portal is very slow (60s+ to load)
    - SPA render: after initial load waits for `networkidle` + nav element visible before clicking
    - Date inputs (`id="fromdate"` / `id="todate"`) set via JS injection + dispatchEvent to
      bypass datepicker widget interception; portal defaults to today if skipped (wrong data)
    - Session persisted to `sdms_state.json`; auto-login fallback if session expires
    - Verified working: 09-05-2026 — 11 rows, Rs. 14,500.93 fleet card total (3 txns)
    - ✓ Test run complete — ready to merge to main
17. **Sprint 1: manager operational flows** ← CURRENT PRIORITY
    - Manager home (shift-contextual checklist)
    - Log lube sale (cash or credit)
    - Log expense
    - Log fleet card swipe
    - Record payment received (cash/cheque instant confirm; bank transfer → pending)
    - Owner: bank transfer verification workflow
    - Owner: intelligence/action center for accountability nudges
    - Generate invoice (moves from owner to manager; owner gets alert)
18. **Sprint 2: ATG scraper + automated Paytm ingestion** — after Sprint 1
    - ATG stock scraper (tank levels, enables Tanks screen)
    - Delivery receipt scraper (TT Receipt, SAP Invoice, density chain)
    - Email watcher for automated Paytm CSV ingestion (replaces scraper)
19. **Sprint 3: owner UI + new design system** — after Sprint 2 (or in parallel)
    - Build all owner screens (Tanks, Credit Module, Executive Dashboard, Recon)
    - Apply Pumpvision Narrative design system across all roles
20. ~~Convert User to DB-backed model~~ ✓ — completed in Sprint 0.
    Current `.env`-based auth does not scale beyond 1–2 users.
20. Integrate autonomous CAPTCHA into main scraper + deploy to cloud cron
21. Phase 2: smart anomaly warnings, manual pump test entry
22. Phase 3: P&L, HR/attendance, compliance tracker, daybook
