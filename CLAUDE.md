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
- Payment collection breakdown (cash vs card/UPI vs credit)
- Anomaly and theft detection
- Delivery quality (fuel adulteration checks)
- Credit customer ledger and invoicing

The project is being built with vibe coding (no professional software engineering background).
If successful, it will be sold as a SaaS product to other IndianOil (and potentially HPCL/BPCL) dealers.

---

## Version Control

The project is under Git, hosted at **github.com/rishab-mishra01/pumpvision** (private repo).
The default branch is `main`. The local working directory is the source of truth synced with origin.

`.gitignore` excludes secrets (`.env`), operational data (CSVs, Excel files), Python build artifacts,
the database file, virtual environments, and OS junk. Never commit secrets — see `.gitignore` for the
full list.

The daily workflow after any meaningful change:
```
git add .
git commit -m "Plain-English description of the change"
git push
```

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

**Attendant Nomenclature (locked).** The attendant-facing UI uses HS1/HS2/MS1/MS2 as the primary
nozzle identifiers because that's how they think about the pumps in real life. The actual hardware
mapping (DU 9 / Nozzle 7 etc.) lives behind the scenes — the database stores nozzle numbers, but
the UI shows HS1, HS2, etc. X2 and XG have only one nozzle each, so no suffix.

---

## Data Sources

### Primary Source: IRAS Portal
**URL:** https://iras.iocliras.in
**Credentials:** stored in `.env` file (never hardcode in scripts)
**Automation:** Playwright Python scripts under `scrapers/`

IRAS is a web portal run by IndianOil (IOCL) that records all FCC (Fuel Control Computer)
and ATG data from the outlet. Data is exported as Excel files.

#### Key constraint: ISS tab exports max 30 minutes of data at a time.
All other tabs export the full date range selected.

#### ISS usage strategy
- **Boundary mode** (already built) gives 06:00 totalizer readings per nozzle — sufficient for
  daily reconciliation (litres sold = close − open).
- **Full 48-window scrape** is deferred to Phase 2 (anomaly detection, per-transaction audit).
  Do not build this for reconciliation.

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
| FCC Data > Shift Totalizer | Shift Totalizer Record | Daily nozzle totalizer open/close (midnight boundary). Used for XG pre-check. | Daily |

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

**Boundary mode algorithm (per shift date):**
1. Run the XG Shift Totalizer pre-check (see XG Handling section) — resolves nozzle 11 without ISS search.
2. Start ISS backward search from 05:30–06:00, stepping back in 30-min increments.
3. Search only for the 5 active nozzles: 7, 15, 16, 17, 18. Nozzle 11 is already resolved.
4. Each nozzle tracked independently — once found, removed from the remaining set.
5. Stop when all 5 active nozzles are resolved or 48 windows checked.
6. Returns `{nozzle_no: totalizer_end_value}` for all 6 nozzles (11 from pre-check, 5 from ISS).

The step-0 window (06:00–06:30) is intentionally skipped — it belongs to the new shift.

### XG (Nozzle 11) Handling — Boundary Mode Exemption

XG is effectively dormant (~100L/month). Searching 48 ISS windows backwards for a single
XG transaction is wasteful. The Shift Totalizer is used instead as a pre-check.

**Key facts confirmed from real data:**
- The outlet runs 24x7 but no XG is ever sold between midnight and 06:00 — safe assumption.
- A pump test of exactly 5L per nozzle runs every morning on all 6 nozzles (~08:20).
- ISS transaction type for pump test: "Pump Test (105)" — fuel dispensed back into tank, not a sale.
- Pump test runs every day — treat it as the norm, not the exception.

**XG pre-check algorithm:**

1. Download Shift Totalizer for the operational date.
2. Read nozzle 11 movement: `xg_movement = shift_totalizer_close - shift_totalizer_open`
3. **If xg_movement ≤ 7L:**
   - No meaningful XG sale occurred. Movement is pump test only (5L ± 2L buffer).
   - Do NOT run ISS boundary search for nozzle 11.
   - Carry forward: XG opening totalizer = previous day's stored XG closing totalizer.
   - XG net sales for this shift = 0L.
   - XG pump test litres = xg_movement.
4. **If xg_movement > 7L:**
   - A genuine XG sale occurred (beyond pump test).
   - Run ISS boundary search for nozzle 11 as normal.
   - Deduct 5L as pump test when calculating XG net sales.

**Threshold rationale:** Pump test = 5L (constant), 2L buffer for variation, threshold = 7L.
Misclassification risk (genuine sale ≤2L reported as zero) is acceptable given XG volumes.

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
- They only go up, never reset (closing reading must always be greater than opening)
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
Tank 5 in IRAS is a placeholder — all zeros, status 'U' (Unavailable). Deferred to Phase 2.

---

## Delivery Workflow
1. IOC depot loads tanker, records density per chamber in SAP → appears in RDB SAP Invoice
2. Tanker (truck) arrives at outlet
3. Employee checks each chamber with hydrometer → entered in Receipt Density Records
4. Decanting begins → TT Receipt records ATG levels before and after
5. Post-decant tank density recorded in Density Records

Trucks used: MP17HH4740 (regular), MP53HA2180, MP20ZQ9560 (occasional)
Supply point: Terminal/Depot 3356

**Note:** MP17HH4740 is the supply tanker, NOT a customer vehicle. Do not use this number
as sample data for credit customer vehicles.

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

## Architecture Principles

These principles govern every implementation decision in the project:

### Single Tree, Two Branches
The app serves two distinct user types via a common Flask application:
- **Owner (Executive View)** — full access, financial control, strategic oversight
- **Attendant (Operations View)** — restricted to logging credit sales and entering closing readings

Both branches share:
- Single Flask application (one codebase, one deployment)
- Single SQLAlchemy database (no duplicate schemas)
- Single auth system with `users.role` column (`attendant` or `owner`)
- Shared service functions in `pumpvision/services/` (e.g., price lookup, balance computation)

Build order:
1. Foundation: app factory, blueprint structure, auth, models ✓ Done
2. Attendant branch ✓ Done — all 9 screens wired to real data
3. Owner branch (next — reads data the attendant has been generating)

This means schema stays whole from day one — no migration ever needed when owner views are added.

### Cloud Deployment is the Production Target
- Mobile-first web app, NOT a desktop UI, NOT a native app
- Runs in the phone browser
- Local running is for development only
- Production target: Railway (paid tier) with PostgreSQL
- PWA (Progressive Web App) support to be added at deployment: manifest.json, app icons, theme-color meta tag — enables full-screen home screen install on Android/iOS without Play Store
- Folder structure, config, and dependencies must be deployable from day one

### Local Network Accessible During Dev
- Flask must bind to `0.0.0.0` so the developer can open it on their phone via the laptop's
  local IP (e.g., `192.168.x.x:5000`) while on the same WiFi
- Test on phone, never just laptop

### SQLite Locally, PostgreSQL-Ready
- Use SQLAlchemy ORM exclusively (no raw SQL)
- DATABASE_URL env var switches the backend
- Flask-Migrate / Alembic set up from day one for schema evolution

### Single Configuration via Environment Variables
- `.env` file in dev (gitignored), platform env vars in prod
- All secrets loaded via `python-dotenv`
- Never hardcode

---

## Tech Stack

### Web App
- **Backend:** Python / Flask (app factory pattern, blueprints)
- **ORM:** SQLAlchemy
- **Migrations:** Flask-Migrate / Alembic
- **Database:** SQLite locally → PostgreSQL on cloud
- **Frontend:** Jinja2 templates + Tailwind CSS (mobile-first)
- **Authentication:** Flask-Login (session-based)
- **PDF generation:** ReportLab (WeasyPrint has Windows incompatibility — do not use)
- **Deployment target:** Render or Railway (free tier to start)

### Scraper
- **Language:** Python 3.14
- **Browser automation:** Playwright (async)
- **CAPTCHA:** Claude Vision API (PoC built — see `scrapers/captcha_test.py`)
- **Modes:** boundary (built), full (deferred to Phase 2)
- **Future:** deploy to same cloud server as app, run on cron

### Credentials and Environment Variables
Stored in `.env` file in project root. Never commit. Loaded via `python-dotenv`.
```
IRAS_USERNAME=206858
IRAS_PASSWORD=<see .env>
IRAS_URL=https://iras.iocliras.in
ANTHROPIC_API_KEY=<for CAPTCHA solving>
SECRET_KEY=<random string for Flask sessions>
DATABASE_URL=sqlite:///pumpvision.db  (overridden to postgres:// on cloud)
OUTPUT_FOLDER=C:\IRAS_Data
```

---

## Design System (Locked)

The design system is "Pumpvision Narrative" — sleek dark fintech inspired by Revolut, optimized for
high-glare environments (petrol pump forecourts).

### Colors

**Foundation:**
- Background: `#000000` (pure black)
- Card surface: `rgba(28, 28, 30, 0.7)` with 1px inner-edge border at `rgba(255, 255, 255, 0.1)`
- Glassmorphic blur on cards
- No drop shadows — depth via tonal layering only

**Product Color Coding (strict — never deviate):**
- HS (High Speed Diesel): `#3b82f6` electric blue
- MS (Motor Spirit / Petrol): `#10b981` emerald green
- X2 (Xtra Premium 95): `#a855f7` royal purple
- XG (Xtra Green / Bio Diesel): `#f97316` sunset orange

**Status colors (independent of product):**
- Healthy / OK: white `#ffffff` (default) or `#97C459` for explicit positive
- Warning: amber `#FAC775`
- Critical: red `#F09595`

**Days-Left rule (used on Tanks screen, applies to all products):**
- More than 7 days → white (default)
- 3 to 7 days → amber `#FAC775`
- 2 days or fewer → red `#F09595`

### Typography
- **Headlines:** Manrope, weight 600, tight letter-spacing `-0.01em`
- **Data and body:** Inter, weight 500–600 for numbers, tight letter-spacing for stats
- **Labels:** uppercase, tracked-out (letter-spacing 0.06–0.08em), small (10–11px)

### Shapes
- Card corners: 24px (1.5rem)
- Buttons: 14–16px corner radius
- Status indicators: pill-shaped (fully rounded)
- Iconography: thin geometric outlines, 1.5–2px strokes (Lucide or Material Symbols)

### Layout & Spacing
- 8px rhythmic scale
- Single-column on mobile, 24px horizontal safe-area margins
- Generous whitespace — "islands" of information

### Scroll Behavior (per screen)
- Some screens scroll, some don't. Each screen description below specifies which.
- When scrolling is allowed, primary actions (Submit / Save / Add) stay sticky to the bottom
  above the bottom nav.
- Bottom nav is always pinned.

### Bottom Navigation

**Attendant (3 tabs):** Home · Activity · Profile

**Owner (5 tabs):** Tanks · Credit · Reconcile · Reports · More
*Note: when implementing, the Home tab can either replace or supplement the existing 5 — the
Executive Dashboard mockup shows Home as the leftmost tab. Confirm with the visual reference.*

### Forbidden — Do Not Invent (regression list)

These features have been hallucinated by design tools in earlier rounds and must NOT appear:
- "Efficiency Score", accuracy %, quality metrics, reconciliation cycles as %
- "Auto-settlement", "Automated Payouts", "Settlement batches"
- "Team members reviewing", multi-user collaboration features
- "Live Alerts" branding, "STATION OS", "Diagnostics"
- WhatsApp/SMS confirmation features (deferred until cloud deployment)
- "Fleet Account" or "Corporate Account" customer types — all credit customers are one type
- "Forgot Password", "Remember me", "Contact System Admin" — replaced by "Contact your owner"
- Hardware features that don't exist: temperature, pressure, flow rate
- Any branding other than "Pumpvision" (no FuelFlow Hub, FuelLog Pro, FuelRecon, etc.)
- Hex codes other than the locked palette

---

## Screen Inventory

All visual references live in `docs/screens/` as PNG files. **Read the file before
implementing the corresponding template.** The screenshot is the design contract.

### Auth

#### `01_login.png`
**File:** `docs/screens/01_login.png`
**Route:** `GET/POST /login`
**Purpose:** Sign-in for both attendants and owners (role determines redirect).
**Scroll:** No — single viewport.
**Bottom nav:** None (pre-auth).
**Key elements:**
- Centered Pumpvision logo + wordmark
- Tagline "INDIANOIL · SHREE PETROLEUM"
- Card with "Welcome back", ID/Username field, Password field with show/hide eye toggle
- Primary button "Sign In →"
- Footer: "Need access? Contact your owner."
**Forbidden:** "Forgot Password", "Remember me", "Contact System Admin", any version footer.

---

### Attendant Branch

Bottom nav for all attendant screens: **Home · Activity · Profile** (3 tabs).

#### `02_attendant_home.png`
**File:** `docs/screens/02_attendant_home.png`
**Route:** `GET /` (when role=attendant)
**Scroll:** No — single viewport.
**Active nav:** Home
**Key elements:**
- Top bar: "PUMPVISION" wordmark + "SHREE PETROLEUM RO 206858" identity, bell icon
- Greeting: "Good morning, [first_name]"
- Shift status nudge card (replaces the "Shift active" sub-line):
  - State A (amber) — previous shift not closed: "DD Mon shift not closed · Tap Close Shift to enter your closing readings."
  - State B (green) — previous shift closed: "DD Mon shift closed ✓ · Today's shift is in progress."
  - Logic: checks `previous_op_date` (= `get_operational_date() - 1 day`) for ≥6 locked `ManualTotalizerReading` rows
- Two large action cards:
  - "Log Credit Sale" — fuel pump icon, sub "Record a fuel sale on credit"
  - "Close Shift" — clipboard with checkmark icon, sub "Enter closing nozzle readings"

#### `03_select_customer.png`
**File:** `docs/screens/03_select_customer.png`
**Route:** `GET /attendant/credit/select-customer`
**Scroll:** Yes (customer list scrolls).
**Active nav:** Activity (per design — though logically it's a sub-flow of Home).
**Key elements:**
- Top bar: back arrow, "PUMPVISION" wordmark, profile icon
- Heading "Select customer" + sub-line
- Search bar: placeholder "Search customer name, ID, or vehicle"
- Filter chips: Recent (active default), Frequent, All Accounts
- "CREDIT CUSTOMERS" section with count e.g. "4 found"
- Customer cards: colored avatar (consistent per customer), name, "ACC-XXXX · Credit Active",
  right chevron. Suspended customers shown 50% opacity with red lock icon and "Credit Blocked".

#### `04_log_sale_details.png`
**File:** `docs/screens/04_log_sale_details.png`
**Route:** `GET/POST /attendant/credit/log/<customer_id>`
**Scroll:** Yes — form scrolls, "Confirm & Log Sale" button sticky at bottom.
**Active nav:** Activity
**Key elements:**
- Selected customer card (icon, company name, "ID: ACC-XXXX", "Active" status badge)
- Vehicle Registration Number section: "Unregistered" + "Container" pills + dropdown
  populated from customer's authorized_vehicles. UNREGISTERED and CONTAINER always available.
- Product grid (2x2): HS, MS, X2 (label "Xtra Premium 95"), XG (label "Xtra Green") — each
  card colored with product color, selected card has thick border in product color
- Dispensed Quantity: Amount (₹) | Litres (L) toggle, large entry field, sub-line
  "Current Rate: ₹[rate]/L · Total: ₹[total]"
- Sticky "Confirm & Log Sale" primary button

#### `05_transaction_confirmed.png`
**File:** `docs/screens/05_transaction_confirmed.png`
**Route:** Shown after POST to log sale endpoint
**Scroll:** No — single viewport.
**Active nav:** None highlighted (transient confirmation screen)
**Key elements:**
- Top bar: back arrow, "CREDIT SALE" label, share icon
- Centered blue checkmark with subtle glow
- "Sale logged" heading + "Recorded on credit. Collect parchi from customer."
- Receipt-style card: TOTAL AMOUNT, then 2-column grid (Customer/Vehicle, Product/Volume, Rate/Date+Time)
- Reminder card: "Hand over signed parchi to customer"
- Primary button "Log new sale", secondary ghost "Go to home"
**Forbidden:** Any WhatsApp/SMS confirmation block — paper parchi runs in parallel until cloud deployment.

#### `06_shift_close_product_selection.png`
**File:** `docs/screens/06_shift_close_product_selection.png`
**Route:** `GET /attendant/shift/select-product`
**Scroll:** No — four big tap targets fit in viewport.
**Active nav:** Shift (the design has Shift as a 4th tab when within the close-shift flow —
during MVP the bottom nav is 3-tab Home/Activity/Profile, so adapt this screen accordingly:
either show Activity as active or build a contextual back button instead).
**Key elements:**
- Top bar: back arrow, "Shift Settlement" title, profile icon
- "PUMPVISION · Shree Petroleum, RO 206858" header
- "Select product" heading + sub-line
- 2x2 grid of product cards: HS, MS, X2 ("Xtra Premium 95"), XG ("Xtra Green")
  with correct colors and subtle gradient background per product
- "DONE" badge shown on each product card when all its nozzles have a `ManualTotalizerReading`
  row for the current shift op date. Wired to real data — not a placeholder.

#### `07_shift_close_du_selection.png`
**File:** `docs/screens/07_shift_close_du_selection.png` (compound — shows MS variant + HS variant)
**Route:** `GET /attendant/shift/du/<product>`
**Scroll:** Yes — cards scroll, "Confirm Readings" button sticky at bottom.
**Active nav:** Shift
**Behavior:** This screen is shown only for HS and MS (which have 2 nozzles each). For X2 and XG
(single nozzle), skip this screen entirely and route directly to the numpad.
**Key elements (per variant):**
- Top bar: back arrow, product name (e.g. "MS - Petrol"), profile icon
- Product banner with thin colored bar
- "Select dispensing unit and enter closing reading." sub-line
- Two nozzle cards: HS1/HS2 or MS1/MS2 as primary identifier, "DU [9/14/15]" as supporting context
- Each card: nozzle ID heading, DU sub-text, fuel-pump icon, "OPENING READING" + value,
  "CLOSING READING" + tap-to-enter input field (dashed border when empty)
- Sticky "Confirm Readings" primary button (electric blue, ALWAYS — never green)

#### `08_shift_close_numpad.png`
**File:** `docs/screens/08_shift_close_numpad.png`
**Route:** `GET/POST /attendant/shift/numpad/<nozzle>`
**Scroll:** No — numpad must always be in viewport.
**Active nav:** Shift
**Key elements:**
- Top bar: back arrow, "Shift Settlement" title, profile icon
- "ENTER READING" label + "Nozzle: [HSx/MSx/X2/XG]" badge
- "Opening: [value] L" small reference
- Large input display showing entered value, with computed delta below:
  "LITRES DISPENSED: +[delta] L"
- Validation rules:
  - If delta < 0: red warning "Reading must be higher than opening", Save button disabled
  - If delta = 0: amber warning "Confirm no fuel was dispensed"
  - Otherwise: green delta as shown
- 3x4 numpad: 1-9, decimal, 0, backspace
- Sticky "Save & Confirm" primary button (disabled if delta ≤ 0)

#### `09_shift_close_summary.png`
**File:** `docs/screens/09_shift_close_summary.png`
**Route:** `GET/POST /attendant/shift/summary`
**Scroll:** Yes — content scrolls, "Submit shift" + "Save as draft" buttons sticky at bottom.
**Active nav:** Shift
**Key elements:**
- Top bar: back arrow, "Shift Settlement" title, profile icon
- "Review & submit" heading + sub-line
- Meta row: "SHIFT 06:00 → 06:00" left, "ATTENDANT [name]" right
- Six nozzle cards in order: HS1, HS2, MS1, MS2, X2, XG. Each card:
  - Product-colored left border
  - Nozzle ID badge
  - "OPENING" + value, "CLOSING" + value (small uppercase labels)
  - Computed delta on right (heavy weight) e.g. "+600.00 L"
  - Edit button per card
- "Shift totals by product" card with 2x2 grid:
  - HS = HS1 + HS2 deltas, MS = MS1 + MS2, X2, XG
  - Sub-text "incl. 5L pump test/nozzle"
- Conditional warning card (amber) when any nozzle delta is 0 or below 5L:
  e.g. "[Nozzle] reading flagged. Closing reading equals opening — confirm no fuel was
  dispensed before submitting."
- Sticky buttons: "Submit shift" (electric blue) + "Save as draft" (ghost)

---

### Owner Branch

Bottom nav for all owner screens: **Tanks · Credit · Reconcile · Reports · More** (5 tabs).
Some screens show Home as the leftmost active tab — use the visual reference for each screen
to confirm.

#### `10_executive_dashboard.png`
**File:** `docs/screens/10_executive_dashboard.png`
**Route:** `GET /` (when role=owner)
**Scroll:** No — single viewport.
**Active nav:** Home (or whichever leftmost tab represents this view)
**Key elements:**
- Top bar: outlet identity card "SHREE PETROLEUM · RO 206858" left,
  ACTION CENTER pill with bell icon and unread count right
- Price ticker row, all four products: "HS ₹[rate] [Δ]", "MS ₹[rate] [Δ]",
  "X2 ₹[rate] [Δ]", "XG ₹[rate] [Δ]". Delta arrows are day-over-day vs yesterday's RSP
  (NOT intraday — RSP only changes at 06:00). Use "—" for no change.
- Time selector: Today | Week | Month
- Revenue card: "REVENUE" label, large amount, "+X% vs yesterday" delta,
  7-day BAR chart (NOT a smooth wavy line — must look like data)
- Tank levels strip: 4 compact circular-ring cards with %, product name, current k-litres
- Vehicle delivery teaser card: truck icon + "[size] DELIVERY [In Transit]" + ETA,
  links to Tanks screen
- Sticky bottom nav

#### `11_owner_tanks.png`
**File:** `docs/screens/11_owner_tanks.png`
**Route:** `GET /tanks`
**Scroll:** Yes — tank cards and deliveries scroll.
**Active nav:** Tanks
**Key elements:**
- Top bar: hamburger, "Tanks" title, profile icon
- "AS OF [HH:MM]" timestamp (matches the latest ATG snapshot, 30-min cadence)
- "Stock levels" heading + "4 underground tanks · live ATG readings"
- Four tank cards stacked: HS, MS, X2, XG. Each card:
  - Vertical "test tube" tank visual on left, 36px × 120px, fill color = product color,
    fill height = current %, with thin horizontal guide lines at 20/40/60/80%
  - Right side: product code (Manrope 18px) + full name + large percentage
  - Three stats row: VOLUME, CAPACITY, DAYS LEFT
  - Days-Left color rule applies to the days number (white > 7, amber 3-7, red ≤ 2)
  - When percentage ≤ 25%: card border red-tinted, percentage red, volume red,
    days-left red, plus an "Order soon" pill at the bottom
- "Deliveries" section with delivery cards:
  - In-transit (orange truck filled): title, status sub, ETA (~45 min) right
  - Scheduled (blue empty circle): title, status sub, scheduled time right
**Tank capacities (must match exactly):**
- HS: 20,000 L | MS: 20,000 L | X2: 10,000 L (smaller!) | XG: 20,000 L
**Forbidden:** No "Reorder" CTA button, no temperature/pressure/flow rate, no driver name/phone.

#### `12_credit_customer_list.png`
**File:** `docs/screens/12_credit_customer_list.png`
**Route:** `GET /credit/customers`
**Scroll:** Yes — list scrolls, "+ Add customer" button sticky at bottom.
**Active nav:** Credit
**Key elements:**
- Top bar: hamburger, "Credit" title, profile icon
- "Customers" heading + dynamic sub-line "[N] active · [M] over threshold"
- Two summary cells equal-width: "TOTAL OUTSTANDING" + "OVERDUE" (Overdue tinted amber if > 0)
- Search bar: "Search customer or vehicle"
- Filter chips horizontal: All (default), Over 80%, Overdue, Suspended
- Customer cards as single-row layout (~75-80px tall):
  - Left: 36px colored avatar with initials (consistent per customer)
  - Middle: name on top, "[N] vehicles · ACC-XXXX" sub, thin 3px utilization bar
  - Right: outstanding balance + utilization percentage
  - Card border tinted by utilization: < 70% default, 70-80% amber, > 80% red
- Suspended customers: 50% opacity, "SUSPENDED" red pill inline with name
- Sticky "+ Add customer" primary button

#### `13_credit_customer_detail.png`
**File:** `docs/screens/13_credit_customer_detail.png`
**Route:** `GET /credit/customers/<id>`
**Scroll:** Yes — header, actions, activity feed, invoices all scroll. No sticky CTA on this screen.
**Active nav:** Credit
**Key elements:**
- Top bar: back arrow, "Customer" title, three-dot menu
- Customer header card:
  - Customer name (Manrope 18-20px)
  - Meta line: "ACC-XXXX · [N] vehicles · Net [days]"
  - "OUTSTANDING" label + large balance number (Inter 26-28px)
  - Utilization pill on right: "[X]% of limit" (color shifts based on threshold)
  - Wide 5px utilization progress bar
  - Foot row: balance left, "limit ₹[amount]" right
- Action row: "Record payment" + "Generate invoice" buttons (BOTH ghost-style, equal weight)
- "Recent activity" section — chronological mixed feed:
  - Fuel transactions: product + litres as title, "[Date] · [Vehicle/UNREGISTERED/CONTAINER]" sub,
    rupee amount right, product-colored left border
  - Payments: "Payment received" title, "[Date] · [Mode] · Ref [number]" sub,
    "+ ₹[amount]" green right, green left border, subtle green tint background
- "Invoices" section with "VIEW ALL" link
- Invoice cards: invoice number title, period sub, amount + "PAID" badge if paid

#### `14_credit_customer_add.png`
**File:** `docs/screens/14_credit_customer_add.png`
**Route:** `GET/POST /credit/customers/new` and `/credit/customers/<id>/edit`
**Scroll:** Yes — form scrolls, "Save customer" button sticky at bottom.
**Active nav:** Credit
**Key elements:**
- Top bar: back arrow, "New customer" or "Edit customer" title, profile icon
- "Account details" heading + sub
- Form fields in order:
  1. Company name (full width)
  2. Account ID + GST number (two-column, GST optional, ID format "ACC-XXXX")
  3. Fleet manager name (full width)
  4. Contact (WhatsApp) — placeholder "+91 ..."
  5. Credit limit + Payment terms (two-column: text input + 3-chip selector 15/30/45)
- "Authorized vehicles" section:
  - Existing vehicles as rows (vehicle number top, description below, X to remove)
  - Dashed-border "+ Add vehicle" button below the list
- Sticky "Save customer" primary button
- Below sticky save: "Suspend account" destructive button — **hidden in New mode,
  visible in Edit mode only**

---

### Reconciliation (Owner)

The Reconcile tab has ONE landing screen with multiple states. **Open Items is the default
landing when there are unresolved items; Day View is the default when there aren't.**

#### `17_recon_open_items.png`
**File:** `docs/screens/17_recon_open_items.png`
**Route:** `GET /recon` (default landing when open items exist)
**Scroll:** Yes — list scrolls.
**Active nav:** Reconcile
**Key elements:**
- Top bar: hamburger, "Pumpvision" wordmark, profile icon
- "Open items" heading + "[N] items need attention"
- Action row cards, each with:
  - Date label
  - One-line description (e.g. "Paytm CSV missing · upload to complete")
  - Right-aligned action button: "Upload" / "Review" / "View"
  - Left border tinted by severity (red for variance, amber for pending action)
- When list is empty, screen shows "All caught up" and routes to Day View directly
**Forbidden:** Vague labels like "Awaiting Bank Sync" — be specific about why.

#### `15_recon_day_view.png`
**File:** `docs/screens/15_recon_day_view.png`
**Route:** `GET /recon/<date>` (or default to today)
**Scroll:** Yes — content scrolls, "Mark Reviewed" button sticky at bottom (only when variance flagged).
**Active nav:** Reconcile
**Key elements:**
- Top bar: back arrow, "Pumpvision" wordmark, profile icon
- Date selector "‹ [date] ›" with chevrons (swipe through past days)
- Day | Trend toggle, Day active
- Status pill: BALANCED / VARIANCE / PENDING
- "SALES BY PRODUCT" card — four rows (HS, MS, X2, XG), each:
  - Product code with colored dot
  - "[litres] L · 5L test" sub-line (single row format)
  - Rupee value right
  - Variance flag pill at far right (e.g. "+2L" green, "-48L" red, "—" muted for inactive)
- "Stock variance" / "Inventory Discrepancy" card:
  - Pill showing worst-flagged product (e.g. "MS −48L")
  - One-line diagnostic: "Expected closing − actual closing per product. [product] variance
    exceeds 30L threshold. Review nozzle [N] totalizer or check for unrecorded credit."
- "Total Revenue" + "Collections" card with 3-column grid:
  - Cash: large amount, "derived" sub
  - Paytm: large amount, "UPI + card" sub
  - Credit: large amount, "[N] customers" sub
  - Footnote: "Cash is derived (Revenue − Paytm − Credit). Once daily cash count is logged,
    the gap will appear here as the accountability number."
- Sticky "Mark Reviewed" button when variance flagged
**Forbidden:** "100% Match · 0% Deviation" framing — cash is derived so the math always balances
by construction; that's not an achievement. Be honest about what is and isn't reconciled.

#### `16_recon_trend_view.png`
**File:** `docs/screens/16_recon_trend_view.png`
**Route:** Same as Day View, with `?view=trend` or toggle state
**Scroll:** Yes — cards scroll. No sticky CTA.
**Active nav:** Reconcile
**Key elements:**
- Top bar: back arrow, "Pumpvision" wordmark, profile icon
- Period selector "Last 30 Days ▾" (dropdown)
- Day | Trend toggle, Trend active
- "Total Revenue" card: large amount + delta vs prev. month + 30-day BAR chart (data-looking, not waves)
- "Stock variance patterns" card: 4 small line charts stacked (one per product), each with
  product label and percentage delta
- "Collection mix ratio" card: stacked area chart showing 30-day ratio of Cash / Paytm / Credit
  with subtle band colors and small legend
- **Trend View ENDS after the Collection Mix card. Nothing else.**
**Forbidden:** "Anomalies Detected", "Negative Variance in MS Tank", "FIX" buttons,
"Optimal Collection Efficiency", "Collection Efficiency", "Credit cycle" metrics — these were
all hallucinated in earlier rounds and explicitly removed.

---

## Project Phases

### Phase 1 — See Everything (current)
- Data ingestion from IRAS via automated Playwright scraper (in progress)
- Daily dashboard, shift reconciliation, price tracking
- Credit customer module (built, undergoing UI redesign)
- **Attendant branch app** ✓ Done — all 9 screens wired to real data

### Phase 2 — Trust Engine
- Nozzle variance and totalizer gap detection
- Anomaly alerts
- CNG integration
- Smart anomaly warnings on Shift Close (was deferred — needs 7-day rolling averages)
- Pump test logging via attendant entry (replacing ISS-based detection)

### Phase 3 — Full Operations
- P&L statement
- HR and attendance, daybook, expenses module
- Compliance tracker
- SMS/WhatsApp confirmation for credit transactions

### Phase 4 — Scale and Sell
- Multi-outlet support
- Dealer onboarding, regional benchmarking
- HPCL/BPCL compatibility

---

## Files in This Project

### App package (`pumpvision/`)
- `pumpvision/__init__.py` — app factory (`create_app()`), blueprint registration, DB/login init
- `pumpvision/extensions.py` — `db`, `login_manager`, `migrate` — single source of truth for Flask extensions
- `pumpvision/models.py` — all SQLAlchemy models (imports `db` from `extensions.py`)
- `pumpvision/constants.py` — shared outlet constants: `NOZZLE_LABEL_MAP`, `PRODUCT_LABELS`, `PUMP_TEST_NOZZLES`
- `pumpvision/decorators.py` — `owner_required`, `attendant_required`
- `pumpvision/user.py` — in-memory `User` class (Flask-Login `UserMixin`); **not DB-backed** — see item 18 in "What to Work On Next"
- `pumpvision/services/prices.py` — `get_rsp(product, op_date)` shared price lookup (IrasPrice → LocalPrice fallback)
- `pumpvision/services/operational.py` — `get_operational_date()`: returns the current operational period (yesterday before 06:00, today after 06:00). Used only in `home()` for the nudge card. **Do not use in shift close routes** — those use `_shift_op_date()` = `date.today() - 1` (always yesterday, the shift being closed). Conflating these two caused a critical bug where readings landed under the wrong date.
- `pumpvision/blueprints/auth/routes.py` — login, logout, root redirect
- `pumpvision/blueprints/dashboard/routes.py` — owner dashboard stub
- `pumpvision/blueprints/attendant/routes.py` — all 9 attendant screens: home (nudge), credit sale flow (select customer → log sale → confirmed), shift close flow (select product → DU selection → numpad → summary/submit). Activity and Profile are stubs.
- `pumpvision/blueprints/owner/routes.py` — owner blueprint placeholder (redirects to dashboard)
- `pumpvision/blueprints/credit/owner.py` — credit module owner routes (customers, ledger, invoices, PDF, settings)
- `pumpvision/blueprints/paytm/routes.py` — Paytm CSV upload + day views
- `pumpvision/blueprints/recon/routes.py` — reconciliation engine + scraper trigger
- `pumpvision/blueprints/meters/routes.py` — manual totalizer reading views
- `pumpvision/templates/` — all Jinja2 templates (mirrors blueprint structure)

### Project root
- `wsgi.py` — WSGI entry point (`from pumpvision import create_app; app = create_app()`)
- `migrations/` — Flask-Migrate / Alembic migration files; baseline revision stamped Apr 2026
- `requirements.txt`, `Procfile`, `.env`, `.env.example`
- `start.bat` — Windows quick-launcher

### Scrapers
- `scrapers/iras_iss_exporter.py` — ISS boundary mode scraper
- `scrapers/iras_price_exporter.py` — Price (PRM) scraper
- `scrapers/captcha_test.py` — CAPTCHA solving PoC using Claude Vision
- `scrapers/daily_scrape.py` — orchestration

### Documentation
- `CLAUDE.md` — this file (project briefing + screen inventory)
- `docs/screens/` — all 17 visual references for the UI

### Local-only / Gitignored
- `.env` — credentials
- `instance/` — SQLite database
- All `*.csv` and `*.xlsx` operational data
- `Screenshots/` — local test captures
- `__pycache__/`, `.venv/`

---

## Observed Operating Patterns (from real data)

### Outlet closing hours
The outlet appears to close roughly between ~01:00 and ~06:00. ISS windows in this range
consistently return empty for most or all nozzles. The outlet is technically 24x7 but no XG is
sold between midnight and 06:00 — confirmed safe assumption.

### Nozzle 11 (XG) inactivity
Extremely inactive — last pre-06:00 transaction on 26-Feb 2026 was ~10:00am the previous morning.
Now handled via Shift Totalizer pre-check.

### Nozzle 16 (HS) low volume
Nozzle 16 sells far less diesel than Nozzle 7. On 26-Feb: Nozzle 7 sold 1,860.71 L vs Nozzle 16
only 25.00 L. May reflect customer preference or overflow-only usage.

### Pump Tests
- Run every morning on all 6 nozzles, typically ~08:20
- Always 5L per nozzle (confirmed April 16 data)
- ISS transaction type: "Pump Test (105)"
- Fuel goes back into tank — not a sale
- Deducted from totalizer diff before calculating net sales value

---

---

## Parallel Workstreams

The project now runs three simultaneous workstreams. None blocks the others.

| Stream | What | Who drives it |
|--------|------|---------------|
| **Deployment** | Railway setup, PWA, PostgreSQL migration, real customer data entry | Claude Code + Rishab (1-2 sessions) |
| **Owner branch** | Tanks, Credit Module UI, Executive Dashboard, Recon UI — implemented against existing CLAUDE.md screen specs | Claude Code autonomously on `owner-branch` |
| **Design rework** | Ground-up creative direction, Figma design system, new visual identity — to replace Stitch scaffolding with a real, portfolio-worthy design | Rishab-led in a dedicated design conversation |

**CLAUDE.md is the shared memory across all streams.** Every stream reads from it.
Every significant decision gets written back to it before the next session.

**Git branches keep streams isolated:**
- `main` — always deployable, always stable
- `deployment` — Railway config, PWA, migration scripts
- `owner-branch` — owner screens implementation
- Design work does not touch code until Figma specs are ready to hand off

**Design rework note:** The current design system (Stitch-based, dark fintech) is scaffolding.
A ground-up UI redesign is planned as a parallel workstream with a distinct creative identity.
When the new design system is locked in Figma, CLAUDE.md will be updated with new design tokens
and Claude Code will implement the visual layer. The data layer (routes, models, logic) is
unaffected by the design rework — it stays intact.

## What to Work On Next

1. ~~Verify the ISS scraper~~ ✓ Done
2. ~~Confirm boundary mode boundaries~~ ✓ Done
3. ~~Build credit module standalone~~ ✓ Done — integrated into main app
4. ~~Build Price (PRM) scraper~~ ✓ Done
5. ~~Build Paytm CSV uploader + parser~~ ✓ Done
6. ~~Build reconciliation engine~~ ✓ Done (logic; UI redesign in progress)
7. ~~XG boundary mode exemption~~ ✓ Done
8. ~~CAPTCHA PoC via Claude Vision~~ ✓ Done
9. ~~Lock visual design system + 17 screen mockups~~ ✓ Done
10. ~~Set up Git + GitHub~~ ✓ Done
11. ~~Foundation refactor — app factory, blueprint structure, extensions, Flask-Migrate~~ ✓ Done
12. ~~Implement attendant branch — 9 screens as Jinja templates wired to real data~~ ✓ Done
13. ~~Activity tab and Profile tab for attendant~~ — deferred; deprioritised in favour of deployment
14. **Deploy app to Railway (paid tier) + PWA support** ← CURRENT PRIORITY
    - Platform: Railway (chosen over Render — no cold start, no 90-day DB expiry, usage-based ~$10-15/month)
    - Database: PostgreSQL on Railway (SQLite only for local dev)
    - Customer data migration: Option C — migrate `customers` + `authorized_vehicles` tables only from SQLite; skip test transactions, invoices, Paytm data (all test noise). Enter opening balances via app after migration.
    - PWA: add manifest.json + icon set + theme-color meta during deployment so attendant/owner can install to home screen full-screen
    - After deploy: enter 26 real credit customers from accountant list, brief attendant on the two flows
15. Implement owner branch — Tanks, Credit Module, Reconciliation, Executive Dashboard
16. Integrate autonomous CAPTCHA into main scraper, deploy scraper to cloud cron
17. Phase 2 work: smart anomaly warnings, manual pump test entry, daybook, expenses
18. **Convert User from in-memory to DB-backed model before adding a second attendant** — current `.env`-based auth (one `ATTENDANT_USERNAME`/`ATTENDANT_PASSWORD` pair) does not scale beyond 1–2 users and cannot support per-user roles, display names, or password resets. Deferred deliberately; do this before onboarding a second real attendant account.
