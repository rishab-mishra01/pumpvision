# Pumpvision — Scrape Scheduling Runbook

Last updated: May 2026

---

## 1. Overview

The scraper pipeline runs two independent jobs. They must never be merged into one.

| Job | Entrypoint (Railway) | Cron schedule (UTC) | What it does |
|-----|---------------------|---------------------|-------------|
| Completed-shift | `scripts/run_completed_shift.py` | `0 1 * * *` | Paytm + Price (PRM) + SDMS for yesterday's op\_date |
| ATG snapshot | `scripts/run_atg_snapshot.py` | `*/30 * * * *` | Current tank stock levels — live snapshot only |

**Critical rule: ATG is never included in completed-shift.** ATG is a live reading of what
is in the tanks right now. It has no date argument and must not be mixed into accounting
scrapes. The two jobs run on completely separate schedules.

**op\_date convention:** The completed-shift job covers the shift that just closed. The outlet
shift runs 06:00 IST → 05:59 IST the next calendar day. The cron runs at 01:00 UTC = 06:30
IST, after the shift boundary. op\_date = IST calendar date − 1.

Example:
- Cron fires on 23 May 2026 at 01:00 UTC (06:30 IST)
- op\_date = 22 May 2026
- Covers: 2026-05-22 06:00 IST → 2026-05-23 05:59 IST

**Production target: Railway cron services.** The Windows PowerShell `.ps1` scripts are
local/manual fallback only. See section 6.

---

## 2. Railway cron setup

Railway supports a dedicated cron service type: a service that runs a command on a crontab
schedule, then exits. Create two cron services in the same Railway project as the web service.

### Service 1: Completed-shift — once daily

| Setting | Value |
|---------|-------|
| Service type | Cron |
| Start command | `python -X utf8 scripts/run_completed_shift.py` |
| Cron schedule | `0 1 * * *` |
| Root directory | *(same repo root as web service)* |

**Schedule explanation:**
- `0 1 * * *` fires at 01:00 UTC every day.
- 01:00 UTC = 06:30 IST (UTC+05:30).
- This is 30 minutes after the outlet's 06:00 IST shift boundary — enough margin.
- op\_date is calculated automatically by `run_completed_shift.py` as IST today − 1 day.

To override op\_date for a backfill run, change the start command temporarily:
```
python -X utf8 scripts/run_completed_shift.py --date 2026-05-20
```

### Service 2: ATG snapshot — every 30 minutes

| Setting | Value |
|---------|-------|
| Service type | Cron |
| Start command | `python -X utf8 scripts/run_atg_snapshot.py` |
| Cron schedule | `*/30 * * * *` |
| Root directory | *(same repo root as web service)* |

**Schedule explanation:**
- `*/30 * * * *` fires at :00 and :30 every hour, every day, all UTC.
- ATG is a live/current tank level snapshot with no date argument.
- 60-minute schedule (`0 * * * *`) is also acceptable if 30 minutes is too frequent.

> **Concurrency note:** If a previous cron execution is still running when the next fire
> time arrives, Railway may skip the new instance. `daily_scrape.py --atg-only` typically
> completes well within 30 minutes, but if IRAS is slow, a run may overlap.
> `daily_scrape.py --completed-shift` can take 15–30 minutes; the once-daily schedule
> avoids overlap in normal operation.

---

## 3. Railway environment variables

Every Railway cron service needs its own copy of the environment variables. The easiest
approach is to use Railway's variable reference feature to share variables from the web
service, or to set them directly on each cron service.

**DATABASE_URL** should use Railway's internal reference variable so it points to the
Railway-internal PostgreSQL connection, not the public connection string. In Railway this
looks like `${{Postgres.DATABASE_URL}}` in the variable definition.

**Never hardcode any secret in a script or in this document.** Set variables in the Railway
dashboard under Service → Variables.

### Required variables for completed-shift cron service

| Variable | Used by |
|----------|---------|
| `DATABASE_URL` | All DB writes |
| `IRAS_USERNAME` | IRAS login (Price, boundaries) |
| `IRAS_PASSWORD` | IRAS login |
| `ANTHROPIC_API_KEY` | CAPTCHA solving (IRAS + SDMS) |
| `PAYTM_EMAIL` | Paytm scraper |
| `PAYTM_PASSWORD` | Paytm scraper |
| `GMAIL_ADDRESS` | Paytm OTP via Gmail IMAP |
| `GMAIL_APP_PASSWORD` | Paytm OTP via Gmail IMAP |
| `SDMS_USERNAME` | SDMS PAD scraper |
| `SDMS_PASSWORD` | SDMS PAD scraper |
| `CNG_RSP_PER_KG` | CNG RSP fallback (default: 93.40) |

### Required variables for ATG cron service

| Variable | Used by |
|----------|---------|
| `DATABASE_URL` | DB writes for tank readings |
| `IRAS_USERNAME` | IRAS login |
| `IRAS_PASSWORD` | IRAS login |
| `ANTHROPIC_API_KEY` | CAPTCHA solving |

---

## 4. Railway filesystem and state

Railway container filesystems are **ephemeral**. Local files written inside a container are
lost when the container restarts or a new deploy is pushed.

This affects the following files used by the scrapers:

| File | Effect of loss | Severity |
|------|---------------|----------|
| `scrapers/paytm_state.json` | Saved Paytm browser session (cookies). Lost = re-login with OTP on next run. OTP is auto-handled via Gmail IMAP. | Low — auto-recovers |
| `scrapers/sdms_state.json` | Saved SDMS browser session. Lost = re-login with CAPTCHA on next run. CAPTCHA is auto-handled via Claude Vision. | Low — auto-recovers |
| `data/iras/debug/login_*/` | IRAS CAPTCHA diagnostics. Lost on restart. | Low — debug only |
| `data/logs/scheduler/` | Log files from `.ps1` wrappers. Not written by Python cron entrypoints; Railway captures stdout/stderr natively. | None — not used on Railway |
| `data/paytm/paytm_YYYY-MM-DD.csv` | Downloaded Paytm CSV. Imported to DB immediately after download; loss after import is harmless. | Low — import happens in-run |

**DB persistence is unaffected** — all accounting data writes to Railway PostgreSQL, which
is a separate persistent service.

If you want session files to survive restarts (to avoid re-login overhead on every cron run),
attach a Railway volume to the cron services and set `PAYTM_STATE_PATH` /
`SDMS_STATE_PATH` to paths on the mounted volume.

---

## 5. Manual test — run before scheduling

Always test with an explicit `--date` on a date that already has data in DB before configuring
a live cron schedule.

### Test completed-shift Python entrypoint

```bash
python -X utf8 scripts/run_completed_shift.py --date 2026-05-21
```

Expected output:
- Header showing op\_date, IST timestamp, paytm wait, mode
- Python script output (boundary status, source status, final ACCOUNTING SOURCE SUMMARY)
- Footer showing RESULT: SUCCESS or FAILED

If DATABASE\_URL is not set, the script exits immediately with a clear error before touching
any portal.

### Test ATG Python entrypoint

```bash
python -X utf8 scripts/run_atg_snapshot.py
```

Expected output:
- Header showing mode, IST timestamp
- Python output for `--atg-only`
- Footer showing RESULT

---

## 6. Windows local fallback (not production)

The `.ps1` wrapper scripts are for local manual use and Windows Task Scheduler only.
They are **not** the production scheduling path. Use them when running scrapers from a local
machine against the Railway `DATABASE_URL`, or when Railway cron is not available.

| Script | Purpose |
|--------|---------|
| `scripts/run_completed_shift.ps1` | Windows wrapper for completed-shift scrape |
| `scripts/run_atg_snapshot.ps1` | Windows wrapper for ATG snapshot |

Both scripts are ASCII-safe and parse correctly under Windows PowerShell 5 (`powershell.exe`).

### Windows Task Scheduler — Completed-shift

**Via Task Scheduler GUI:**

1. Open Task Scheduler → Create Task
2. **General tab:**
   - Name: `Pumpvision - Completed Shift`
   - Run whether user is logged on or not: ✓
3. **Triggers tab → New:**
   - Begin the task: On a schedule → Daily → 06:15:00 (or 06:30:00 for extra margin)
4. **Actions tab → New:**
   - Program: `powershell.exe`
   - Arguments:
     ```
     -NonInteractive -ExecutionPolicy Bypass -File "C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_completed_shift.ps1"
     ```
   - Start in: `C:\Users\Rishab 2\Desktop\Pumpvision`
5. **Settings tab:** Do not start a new instance if already running. Stop after 2 hours.

**Via schtasks.exe (elevated prompt):**

```cmd
schtasks /Create /TN "Pumpvision\CompletedShift" ^
  /SC DAILY /ST 06:15 ^
  /TR "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File \"C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_completed_shift.ps1\"" ^
  /SD 01/01/2026 /F
```

### Windows Task Scheduler — ATG snapshot

**Via Task Scheduler GUI:**

1. Create Task → Name: `Pumpvision - ATG Snapshot`
2. **Triggers tab → New:**
   - On a schedule → Daily → 06:00:00
   - ✓ Repeat task every: **30 minutes** for a duration of: **1 day**
3. **Actions tab → New:**
   - Program: `powershell.exe`
   - Arguments:
     ```
     -NonInteractive -ExecutionPolicy Bypass -File "C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_atg_snapshot.ps1"
     ```
   - Start in: `C:\Users\Rishab 2\Desktop\Pumpvision`
4. **Settings tab:** Do not start a new instance if already running.

**Via schtasks.exe:**

```cmd
schtasks /Create /TN "Pumpvision\ATGSnapshot" ^
  /SC MINUTE /MO 30 ^
  /TR "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File \"C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_atg_snapshot.ps1\"" ^
  /F
```

**Windows environment variables** (`DATABASE_URL` and all scraper credentials) must be set
as user environment variables for the account that runs the tasks:
`Win + R → sysdm.cpl → Advanced → Environment Variables → User variables → New`

---

## 7. Recovery commands

If a completed-shift run exits nonzero, check the ACCOUNTING SOURCE SUMMARY in the output
to see which source failed. Retry only that source — do not re-run the full completed-shift.

**From a local machine (PowerShell), substituting the correct op\_date:**

```powershell
# Retry Paytm only
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --paytm-only --date 2026-05-22

# Retry Price only (IRAS CAPTCHA)
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --price-only --date 2026-05-22

# Retry SDMS only
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --sdms-only --date 2026-05-22
```

**From any platform (bash / Railway shell):**

```bash
python -X utf8 scrapers/daily_scrape.py --paytm-only --date 2026-05-22
python -X utf8 scrapers/daily_scrape.py --price-only --date 2026-05-22
python -X utf8 scrapers/daily_scrape.py --sdms-only --date 2026-05-22
```

**If IRAS autonomous CAPTCHA keeps failing — manual fallback only (never for scheduled runs):**

```bash
python -X utf8 scrapers/daily_scrape.py --price-only --date 2026-05-22 --iras-manual-captcha
```

A fresh CAPTCHA image is saved to `data/iras/debug/login_<ts>/manual_captcha.png`. Open the
image, type the characters, press Enter. Blocks until input — never use in a cron job.

**If Paytm CSV already exists on disk:**

```bash
python -X utf8 scrapers/import_paytm_csv.py data/paytm/paytm_2026-05-22.csv
```

---

## 8. Current caveats

| Issue | Behaviour | Recovery |
|-------|-----------|---------|
| IRAS CAPTCHA fails | Price and boundaries are skipped; SDMS still runs; run exits nonzero | Retry with `--price-only`; if autonomous keeps failing, use `--iras-manual-captcha` once locally |
| Paytm download is slow | Default wait is 900s (15 min); use `--paytm-wait-seconds 1800` for slow runs | Retry with `--paytm-only`; if CSV is on disk, import with `import_paytm_csv.py` |
| Paytm session expired | OTP is sent; Gmail IMAP reads it automatically if Gmail env vars are set | Ensure `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` are set; OTP is auto-handled |
| SDMS session expired | SDMS uses Claude Vision CAPTCHA; usually auto-recovers | Retry with `--sdms-only` |
| Source already in DB | Automatic skip — not a failure; ACCOUNTING SOURCE SUMMARY shows SKIPPED | No action needed |
| ATG data for XG is unreliable | Stored with `is_reliable = False`; XG probe known hardware issue | Data stored; UI warning deferred to Stage 2 |
| Railway ephemeral filesystem | Session files lost on restart; re-login triggered automatically | Attach a volume to persist session files if re-login overhead is unacceptable |

**Cron jobs must never block waiting for manual input.** Do not add `--iras-manual-captcha`
or `--paytm-debug` to any cron start command.

---

## 9. File locations summary

| File | Purpose |
|------|---------|
| `scripts/run_completed_shift.py` | **Railway cron entrypoint** — completed-shift (cross-platform) |
| `scripts/run_atg_snapshot.py` | **Railway cron entrypoint** — ATG snapshot (cross-platform) |
| `scripts/run_completed_shift.ps1` | Windows local/manual fallback — completed-shift |
| `scripts/run_atg_snapshot.ps1` | Windows local/manual fallback — ATG snapshot |
| `scrapers/daily_scrape.py` | Underlying Python orchestrator — all scraper logic |
| `data/logs/scheduler/` | Log output from .ps1 wrappers (gitignored, local only) |
| `data/iras/debug/login_<ts>/` | IRAS CAPTCHA diagnostics (auto-saved on failure, local only) |
| `data/paytm/debug/` | Paytm diagnostics (saved when `--paytm-debug` is passed, local only) |
