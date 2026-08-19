# Pumpvision — New Machine Setup

Step-by-step guide to get this project running on a fresh computer. Written for
someone who has git access to the repo but nothing else set up yet.

Read this file first. Once the app runs locally, read `CLAUDE.md` at the repo
root — it's the full project brief (business logic, data model, screens,
scraper architecture) and is considered required reading before making changes.

---

## 1. Prerequisites to install

| Tool | Why | Check with |
|---|---|---|
| **Git** | get the code | `git --version` |
| **Python 3.12** | app + scrapers run on this version (matches production) | `python3 --version` or `python --version` |
| **pip** | Python package installer | `pip --version` (comes with Python) |

No Node.js, no npm, no build step — the frontend is server-rendered Jinja2
templates with a hand-written CSS file (`pumpvision/static/css/design-system.css`),
not a compiled Tailwind pipeline. Nothing to `npm install`.

If you'll ever run the scrapers locally (Playwright-driven browser automation
for IRAS/Paytm/SDMS), you'll also need:
```
pip install playwright
python -m playwright install --with-deps chromium
```
Skip this if you're only doing app/UI work — not needed to run the Flask app itself.

---

## 2. Get the code

```
git clone https://github.com/rishab-mishra01/pumpvision.git
cd pumpvision
```

(If you were sent a `.git` archive/bundle instead of cloning directly, extract
it and treat the resulting folder the same as a normal clone from this point on.)

---

## 3. Set up a Python virtual environment

From the repo root:

```
python -m venv .venv
```

Activate it:
- **Windows (PowerShell):** `.venv\Scripts\Activate.ps1`
- **Windows (Git Bash):** `source .venv/Scripts/activate`
- **Mac/Linux:** `source .venv/bin/activate`

Install dependencies:
```
pip install -r requirements.txt
```

---

## 4. Set up your `.env` file

Copy the template:
```
cp .env.example .env
```
(Windows PowerShell: `Copy-Item .env.example .env`)

Open `.env` and fill in values. **Important — your local `.env` is separate
from Railway's environment variables and the VPS's `.env`.** They do not sync
automatically; each machine/environment keeps its own copy.

### Minimum needed to just run the app and click around locally:

```
SECRET_KEY=anything-you-want-locally
DATABASE_URL=sqlite:///pumpvision.db
OWNER_USERNAME=owner
OWNER_PASSWORD=pick-any-password
ATTENDANT_USERNAME=attendant
ATTENDANT_PASSWORD=pick-any-password
MANAGER_USERNAME=manager
MANAGER_PASSWORD=pick-any-password
```
These four users get seeded into your **local** database automatically on
first run — they don't need to match production credentials at all.

### Only needed if you're working on scrapers (IRAS / Paytm / SDMS):

```
IRAS_USERNAME=206858
IRAS_PASSWORD=<real value — ask Rishab>
IRAS_URL=https://iras.iocliras.in
ANTHROPIC_API_KEY=<real value — for CAPTCHA solving>
PAYTM_EMAIL=<real value>
PAYTM_PASSWORD=<real value>
PAYTM_HEADLESS=false
GMAIL_ADDRESS=<real value — OTP auto-read>
GMAIL_APP_PASSWORD=<real value>
SDMS_USERNAME=<real value>
SDMS_PASSWORD=<real value>
CNG_RSP_PER_KG=93.40
```
These are real operator credentials for live IndianOil portals — don't request
them unless you're actually touching scraper code. Ask Rishab directly and
out-of-band (not committed anywhere, ever — `.env` is gitignored).

---

## 5. Set up the database

Local dev uses SQLite by default (no separate DB server needed). Apply the
existing migrations:

```
flask db upgrade
```

This creates `instance/pumpvision.db` and brings it up to the current schema
(6 migrations as of this writing — users/lube/expense/fleet tables, CNG +
tank_readings, sdms_summaries, nullable invoice_id fix).

If you want to see **populated** dashboards/summary screens instead of empty
"no data" states, ask Rishab for a copy of his local `instance/pumpvision.db`
(or a Railway data export) rather than trying to regenerate real fuel/payment
data yourself — that data only exists via the scrapers hitting live portals.

---

## 6. Run the app locally

```
python wsgi.py
```
or, on Windows, use the existing helper:
```
start.bat
```
(`start.bat` calls the full Python path directly — the Windows Store `python`
stub does not work for this project. If you're not on the same Windows setup
as Rishab, just run `python wsgi.py` or `flask run` directly instead.)

App should now be reachable at `http://localhost:5000` (or whatever `PORT`
your environment implies — check `wsgi.py`/Flask defaults if unsure).

Log in with the `OWNER_USERNAME`/`OWNER_PASSWORD` (etc.) you set in your `.env`.

### Windows-specific gotcha
Flask's `--debug` reloader spawns a parent + worker process. Running the dev
server multiple times without killing the old one leaves stale processes on
port 5000, causing weird intermittent errors or stale code being served. Kill
all Python processes via PowerShell `Stop-Process` before restarting (plain
`taskkill` from Git Bash doesn't reliably work here). Always run the dev
server in a visible terminal you can kill cleanly — never detached/background.

---

## 7. Project orientation — where things live

| Area | Path |
|---|---|
| Flask app factory, blueprints registration | `pumpvision/__init__.py` |
| DB models | `pumpvision/models.py` |
| Business logic constants (nozzle map, product labels) | `pumpvision/constants.py` |
| Routes, by role | `pumpvision/blueprints/{auth,attendant,manager,owner,dashboard,credit,...}/routes.py` |
| Templates | `pumpvision/templates/` |
| CSS / design system | `pumpvision/static/css/design-system.css`, `pumpvision/static/css/owner.css` |
| DB migrations | `migrations/versions/` |
| Scrapers (IRAS, Paytm, SDMS) | `scrapers/` |
| Scheduling / cron entrypoints | `scripts/` |
| Screen visual references (PNGs) | `docs/screens/` |
| Owner screens 10 & 15 canonical design ref | `docs/design/Owner_Screens.html` |
| Full project brief (read this next) | `CLAUDE.md` |
| Scraper scheduling deep-dive | `docs/scrape_scheduling_runbook.md` |

---

## 8. Production / infrastructure access (not required to start developing)

You don't need any of this to write code and test locally. Only relevant once
you're deploying or debugging live data:

- **Railway** — hosts the live app + PostgreSQL DB, auto-deploys on push to `main`. Ask Rishab for a Railway team invite if you'll be deploying/checking production.
- **India VPS (AWS Lightsail, Mumbai)** — runs the scraper cron jobs (IRAS/Paytm/SDMS/ATG are India-geo-restricted, so they run from here, not Railway). SSH access is separate — ask Rishab.
- **GitHub** — you already have this since you're reading this file via git.

---

## 9. What to read next, in order

1. `CLAUDE.md` (repo root) — the complete project brief. Long, but everything
   about the business (fuel products, payment reconciliation, operational-day
   logic, scraper orchestration, schema, screens, forbidden UI patterns) lives
   here. Treat it as the source of truth over any assumption you'd otherwise make.
2. `docs/scrape_scheduling_runbook.md` — only if you're touching scraper/cron code.
3. `docs/design/Owner_Screens.html` — only if you're touching owner dashboard (screen 10) or daily summary (screen 15) UI — these two screens deliberately deviate from the main design system and use this file as their sole visual reference.

---

## 10. If something doesn't match this doc

This file was written to reflect the repo as of the last handover cleanup. If
you hit a mismatch (a file that's moved, an env var that's not read anymore,
etc.), trust the actual code/`git log` over this document, and please update
this file in the same PR — it should stay accurate for whoever comes after you.
