# Pumpvision — Scrape Scheduling Runbook

Last updated: May 2026

> **Compatibility note:** Both wrapper scripts (`run_completed_shift.ps1` and
> `run_atg_snapshot.ps1`) are ASCII-safe and parse correctly under Windows
> PowerShell 5 (`powershell.exe`). No BOM required.

---

## 1. Operating model

The scraper pipeline has two independent schedules:

| Job | Script | Trigger | What it does |
|-----|--------|---------|-------------|
| Completed-shift | `run_completed_shift.ps1` | Once daily, **after 06:10** | Paytm + Price (PRM) + SDMS for yesterday's op_date |
| ATG snapshot | `run_atg_snapshot.ps1` | Every 30 or 60 min | Current tank stock levels — live/current snapshot only |

**Critical rule: ATG is never included in completed-shift.** ATG is a live/current reading of what is in the tanks right now. It has no date argument and must not be mixed into accounting scrapes. The two jobs run on completely separate schedules.

**op_date convention:** The completed-shift job covers the shift that just closed. The outlet shift runs 06:00 → 05:59 the next calendar day. Running after 06:10 on day D means op_date = D − 1.

Example:
- Script runs on 23 May 2026 at 06:15
- op_date = 22 May 2026
- Covers: 2026-05-22 06:00 → 2026-05-23 05:59

---

## 2. Environment setup

### DATABASE_URL

`DATABASE_URL` must be set in the Windows user or system environment **before** running or scheduling any script. The scripts check for it at startup and exit immediately if it is absent.

**Do not paste DATABASE_URL into any script file.** Set it via:

```
Win + R → sysdm.cpl → Advanced → Environment Variables
→ User variables → New
  Name:  DATABASE_URL
  Value: postgresql://...  (Railway connection string)
```

Or in PowerShell (sets for current session only — not persistent):
```powershell
$env:DATABASE_URL = "postgresql://..."
```

To persist across sessions, use the System Properties dialog above.

### Python path

Current project Python:
```
C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe
```

Both scripts default to this path. Override with `-PythonExe` if needed.

### Other env vars

The following must also be set for a complete completed-shift run:

| Variable | Used by |
|----------|---------|
| `IRAS_USERNAME` | IRAS login (Price, boundaries) |
| `IRAS_PASSWORD` | IRAS login |
| `ANTHROPIC_API_KEY` | CAPTCHA solving |
| `PAYTM_EMAIL` | Paytm scraper |
| `PAYTM_PASSWORD` | Paytm scraper |
| `GMAIL_ADDRESS` | Paytm OTP via Gmail IMAP |
| `GMAIL_APP_PASSWORD` | Paytm OTP via Gmail IMAP |
| `SDMS_USERNAME` | SDMS PAD scraper |
| `SDMS_PASSWORD` | SDMS PAD scraper |
| `CNG_RSP_PER_KG` | CNG RSP fallback (default: 93.40) |

These are typically in `.env` for local development. For scheduled tasks, set them as user environment variables (same method as DATABASE_URL) or via a launcher script that loads `.env` before invoking the PS1.

---

## 3. Manual test — run before scheduling

Always test with an explicit `-Date` first to verify the output is correct before setting up an automated schedule.

### Test completed-shift wrapper

```powershell
# From repo root. Replace date with a date that already has data.
.\scripts\run_completed_shift.ps1 -Date 2026-05-21
```

Expected output:
- Header showing op_date, log file path
- Python script output (boundary status, source status, final ACCOUNTING SOURCE SUMMARY)
- Footer showing RESULT: SUCCESS or FAILED

If DATABASE_URL is not set, the script exits immediately with a clear error before touching any portal.

### Test ATG wrapper

```powershell
.\scripts\run_atg_snapshot.ps1
```

Expected output:
- Header showing mode, log file path
- Python output for `--atg-only`
- Footer showing RESULT

---

## 4. Windows Task Scheduler setup

### Prerequisites

- DATABASE_URL (and other env vars above) set as **user environment variables** for the account that runs the tasks.
- PowerShell execution policy allows running scripts. Use `-ExecutionPolicy Bypass` in the task action.
- Tasks must **Start In** the repo root so relative paths (e.g. `scrapers\`) resolve correctly.

### Task 1: Completed-shift — once daily

**Via Task Scheduler GUI:**

1. Open Task Scheduler → Create Task
2. **General tab:**
   - Name: `Pumpvision - Completed Shift`
   - Run whether user is logged on or not: ✓
   - Run with highest privileges: (optional, usually not needed)
3. **Triggers tab → New:**
   - Begin the task: On a schedule
   - Daily, recur every 1 day
   - Start: 06:15:00 (or 06:30:00 for extra margin)
4. **Actions tab → New:**
   - Action: Start a program
   - Program: `powershell.exe`
   - Arguments:
     ```
     -NonInteractive -ExecutionPolicy Bypass -File "C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_completed_shift.ps1"
     ```
   - Start in: `C:\Users\Rishab 2\Desktop\Pumpvision`
5. **Settings tab:**
   - If the task is already running, do not start a new instance
   - Stop the task if it runs longer than: 2 hours (safety limit)

**Via schtasks.exe (run in an elevated prompt):**

```cmd
schtasks /Create /TN "Pumpvision\CompletedShift" ^
  /SC DAILY /ST 06:15 ^
  /TR "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File \"C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_completed_shift.ps1\"" ^
  /SD 01/01/2026 ^
  /F
```

Note: `/RU` defaults to the current user. The task inherits that user's environment variables, including DATABASE_URL.

### Task 2: ATG snapshot — every 30 or 60 minutes

**Via Task Scheduler GUI (recommended for repeating interval):**

1. Create Task → Name: `Pumpvision - ATG Snapshot`
2. **Triggers tab → New:**
   - Begin the task: On a schedule
   - Daily, recur every 1 day, start time 06:00:00
   - ✓ Repeat task every: **30 minutes** (or 60 minutes) for a duration of: **1 day**
3. **Actions tab → New:**
   - Program: `powershell.exe`
   - Arguments:
     ```
     -NonInteractive -ExecutionPolicy Bypass -File "C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_atg_snapshot.ps1"
     ```
   - Start in: `C:\Users\Rishab 2\Desktop\Pumpvision`
4. **Settings tab:**
   - If the task is already running, do not start a new instance

**Via schtasks.exe (creates the trigger; use GUI to add repetition interval):**

```cmd
schtasks /Create /TN "Pumpvision\ATGSnapshot" ^
  /SC MINUTE /MO 30 ^
  /TR "powershell.exe -NonInteractive -ExecutionPolicy Bypass -File \"C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_atg_snapshot.ps1\"" ^
  /F
```

> Note: `schtasks /SC MINUTE /MO 30` runs every 30 minutes from task creation time with no time-of-day bound. Use the GUI to set a start time and duration if you want the ATG scraper to run only during outlet operating hours.

---

## 5. Recovery commands

If `run_completed_shift.ps1` exits nonzero, check the ACCOUNTING SOURCE SUMMARY at the bottom of the log to see which source failed. Then retry only that source — do not re-run the full completed-shift.

**Run these directly in PowerShell, not via the scheduler scripts:**

```powershell
# Retry Paytm only
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --paytm-only --date 2026-05-22

# Retry Price only (IRAS CAPTCHA)
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --price-only --date 2026-05-22

# Retry SDMS only
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --sdms-only --date 2026-05-22
```

**If IRAS autonomous CAPTCHA keeps failing — manual fallback (never for scheduled runs):**

```powershell
# Adds interactive terminal CAPTCHA prompt after autonomous attempts fail.
# A fresh CAPTCHA image is saved to data/iras/debug/login_<ts>/manual_captcha.png
# You open the image, type the characters, press Enter.
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\daily_scrape.py --price-only --date 2026-05-22 --iras-manual-captcha
```

`--iras-manual-captcha` blocks until you type input. Never add it to a scheduled task.

**If Paytm CSV already exists on disk:**

```powershell
& "C:\Users\Rishab 2\AppData\Local\Python\bin\python.exe" -X utf8 scrapers\import_paytm_csv.py data\paytm\paytm_2026-05-22.csv
```

---

## 6. Logs

All logs are written to:
```
data\logs\scheduler\
```

Log file naming:
| Job | Filename pattern |
|-----|-----------------|
| Completed-shift | `completed_shift_YYYYMMDD_HHMMSS.log` |
| ATG snapshot | `atg_snapshot_YYYYMMDD_HHMMSS.log` |

Each log file contains:
- Run header (op_date, mode, started timestamp)
- Full stdout from `daily_scrape.py`
- Any stderr lines (in a `[stderr]` section)
- Final RESULT / op_date / finished timestamp

**`data/` is in `.gitignore` — logs are never committed.**

Logs contain operational data (dates, row counts, IRAS navigation steps, CAPTCHA predictions). They do not contain DATABASE_URL, passwords, cookies, or session tokens.

To view the most recent completed-shift log:
```powershell
Get-Content (Get-ChildItem data\logs\scheduler\completed_shift_*.log | Sort-Object LastWriteTime | Select-Object -Last 1).FullName
```

---

## 7. Current caveats

| Issue | Behaviour | Recovery |
|-------|-----------|---------|
| IRAS CAPTCHA fails | Price and boundaries are skipped; SDMS still runs; run exits nonzero | Retry with `--price-only`; if autonomous solving keeps failing, use `--iras-manual-captcha` once |
| Paytm download is slow | `--paytm-wait-seconds 900` gives 15 min; increase with `-PaytmWaitSeconds 1800` | Retry with `--paytm-only`; if CSV is on disk, import with `import_paytm_csv.py` |
| Paytm session expired | OTP is sent; Gmail IMAP reads it automatically if `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` are set | Ensure Gmail env vars are set; OTP is auto-handled |
| SDMS session expired | SDMS uses Claude Vision CAPTCHA same as IRAS; usually auto-recovers | Retry with `--sdms-only` |
| Source already in DB | Automatic skip — not a failure; ACCOUNTING SOURCE SUMMARY shows SKIPPED | No action needed |
| ATG data for XG is unreliable | Stored with `is_reliable = False`; XG probe known hardware issue | Data stored; UI warning deferred to Stage 2 |

**Scheduled tasks should never block waiting for manual input.** If a source keeps failing automatically, investigate manually with the source-specific retry commands above before concluding it needs automated escalation.

---

## 8. File locations summary

| File | Purpose |
|------|---------|
| `scripts/run_completed_shift.ps1` | Scheduled wrapper for daily completed-shift |
| `scripts/run_atg_snapshot.ps1` | Scheduled wrapper for repeating ATG snapshots |
| `scrapers/daily_scrape.py` | Underlying Python orchestrator — all scraper logic |
| `data/logs/scheduler/` | Log output (gitignored, local only) |
| `data/iras/debug/login_<ts>/` | IRAS CAPTCHA diagnostics (auto-saved on failure) |
| `data/paytm/debug/` | Paytm diagnostics (saved when `--paytm-debug` is passed) |
