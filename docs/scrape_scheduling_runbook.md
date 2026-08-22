# Pumpvision — Scrape Scheduling Runbook

Last updated: August 2026 (Railway removed; scrapers on the India VPS, web + DB on the evo)

---

## 1. Overview

Production is split across two machines, for one reason: **IRAS, Paytm and SDMS are
India-geo-restricted.** The scrapers must run from an Indian IP; everything else does not.

| Component | Where | How it starts |
|-----------|-------|---------------|
| Scrapers (completed-shift, ATG, SDMS lookback) | AWS Lightsail **Mumbai VPS** — `ubuntu@65.2.38.210` | `crontab` → `scripts/vps_run_*.sh` |
| Flask web app | **evo** (`evo-x3-1`, `100.87.158.40`) — gunicorn on `:8002` | `~/pumpvision-web.sh`, kept alive by `~/start-all.sh` (cron every 5 min) |
| PostgreSQL 17 | **evo**, same box as the web app | system `postgresql@17-main` service |

The VPS reaches the database over **Tailscale**:
`postgresql://pumpvision@100.87.158.40:5432/pumpvision`. There is no public ingress to
either the web app or the database — both are tailnet-only.

> **Railway is gone (August 2026).** `railway.json`, `Procfile` and
> `scripts/railway_entrypoint.py` were deleted with it, along with the
> `PUMPVISION_SERVICE_ROLE` dispatch. Cron wrappers call the job scripts directly.
> `Dockerfile` is kept but nothing deploys from it — it only pins a known-good
> Playwright/Python/Chromium triple.

### The jobs

| Job | Wrapper | Cron (UTC) | IST | What it does |
|-----|---------|------------|-----|-------------|
| Completed-shift | `scripts/vps_run_completed_shift.sh` | `0 1 * * *` | 06:30 | Paytm + Price (PRM) + SDMS for yesterday's op\_date |
| ATG snapshot | `scripts/vps_run_atg_snapshot.sh` | `30 0-18 * * *` | 06:00–24:30 hourly | Current tank stock levels — live snapshot only |
| SDMS CNG lookback | `scripts/vps_run_sdms_lookback.sh` | `0 7`, `0 10`, `35 11` — Mon–Sat | 12:30, 15:30, 17:05 | Picks up the CNG billing row once CGD Rewa posts it |

**Critical rule: ATG is never included in completed-shift.** ATG is a live reading of what
is in the tanks right now. It has no date argument and must not be mixed into accounting
scrapes. The two jobs run on completely separate schedules.

**ATG runs 19×/day, not 48.** `30 0-18 * * *` is hourly at :30 past, for UTC hours 0–18
only — i.e. 06:00 IST through 00:30 IST. The outlet is not transacting overnight IST, so
the small hours are deliberately skipped. Expect ~17–19 snapshots per day in
`tank_readings`; a day with far fewer is a signal worth investigating.

**op\_date convention:** The completed-shift job covers the shift that just closed. The
outlet shift runs 06:00 IST → 05:59 IST the next calendar day. The cron runs at 01:00 UTC
= 06:30 IST, after the shift boundary. op\_date = IST calendar date − 1.

Example:
- Cron fires on 23 May 2026 at 01:00 UTC (06:30 IST)
- op\_date = 22 May 2026
- Covers: 2026-05-22 06:00 IST → 2026-05-23 05:59 IST

---

## 2. VPS setup

### Layout

| Path | Purpose |
|------|---------|
| `~/pumpvision` | Git checkout |
| `~/pumpvision/.venv` | Python venv — **always invoke via this**, never system `python3` |
| `~/pumpvision/.env` | **The only copy of the scraper secrets.** Mode 0600. There is no other source to restore it from — back it up before editing. |
| `/data/logs/` | Per-job, per-IST-day logs (`atg_2026-08-22.log`, …), 30-day retention |
| `/data/locks/daily_scrape.lock` | `flock` target shared by completed-shift and ATG |
| `/data/state/` | Persisted Paytm + SDMS browser sessions |
| `/data/iras_data/`, `/data/paytm/`, `/data/sdms/` | Downloaded artefacts |

`/data` is on the Lightsail instance disk and **persists across reboots** — unlike the
Railway container filesystem it replaced. Session state, logs and downloaded CSVs all
survive, so a restart no longer forces a re-login.

### Cron

The VPS clock is **UTC**. Install with `crontab -e`:

```cron
0  1     * * *    /home/ubuntu/pumpvision/scripts/vps_run_completed_shift.sh
30 0-18  * * *    /home/ubuntu/pumpvision/scripts/vps_run_atg_snapshot.sh
0  7     * * 1-6  /home/ubuntu/pumpvision/scripts/vps_run_sdms_lookback.sh
0  10    * * 1-6  /home/ubuntu/pumpvision/scripts/vps_run_sdms_lookback.sh
35 11    * * 1-6  /home/ubuntu/pumpvision/scripts/vps_run_sdms_lookback.sh
```

The wrappers set their own logging and locking, so no redirection belongs in the crontab.

**Locking.** Completed-shift and ATG share `/data/locks/daily_scrape.lock` via
`flock -n` (non-blocking). If a completed-shift run is still going when an ATG slot
arrives, that ATG snapshot is *skipped*, not queued — and the wrapper logs `SKIPPED`,
exiting 1. This is correct: ATG is a live reading, so a skipped snapshot loses nothing
that the next hour will not supply. `--completed-shift` can take 15–30 minutes.

**SDMS lookback timing.** CGD Rewa posts the CNG billing row for op\_date D on D+1
between roughly 11:00 and 16:00 IST, and never on Sunday — Saturday's and Sunday's rows
both post on Monday. Three probes catch the number near its earliest posting instead of
at the end of the window. The 17:05 IST probe is deliberately offset past the 11:30 UTC
ATG slot to avoid contending for the lock.

### Tailscale

The VPS joins the tailnet as `pumpvision-vps` (`100.96.147.27`). `tailscaled` is
enabled at boot, so the DB path survives a reboot on either end.

```bash
tailscale status          # confirm the node is Running
tailscale ping 100.87.158.40
```

The connection currently relays via DERP rather than going direct, because the evo's
Postgres sits behind WSL2's NAT. That costs ~50 ms per round trip, which is irrelevant
for a job that writes a few dozen rows an hour.

---

## 3. Environment variables

All variables live in `~/pumpvision/.env` on the VPS, loaded by `python-dotenv`.

> **Never hardcode a secret in a script or in this document.**

> `load_dotenv()` does **not** override variables already present in the environment.
> That is deliberate and load-bearing: a wrapper script can export `DATABASE_URL` and win
> over the `.env` value without editing the file.

| Variable | Used by |
|----------|---------|
| `DATABASE_URL` | All DB writes — points at the evo over Tailscale |
| `IRAS_USERNAME` / `IRAS_PASSWORD` | IRAS login (Price, boundaries, ATG) |
| `IRAS_URL` | IRAS portal base URL |
| `ANTHROPIC_API_KEY` | CAPTCHA solving — IRAS image CAPTCHA + SDMS e-Mitra arithmetic |
| `PAYTM_EMAIL` / `PAYTM_PASSWORD` | Paytm scraper |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Paytm OTP via Gmail IMAP |
| `SDMS_USERNAME` / `SDMS_PASSWORD` | SDMS PAD scraper |
| `CNG_RSP_PER_KG` | CNG RSP fallback (default: 93.40) |
| `OUTPUT_FOLDER` | `/data/iras_data` |
| `PAYTM_STATE_PATH` / `SDMS_STATE_PATH` | `/data/state/*.json` — persisted browser sessions |
| `PAYTM_HEADLESS` | `true` on the VPS (no display) |

`SECRET_KEY`, `OWNER_*`, `MANAGER_*` and `ATTENDANT_*` are also present because the
`.env` is shared with the web app's expectations; the scrapers do not read them.

---

## 4. When the database is unreachable

This is the failure that cost 40 hours of data in August 2026, so it has dedicated
machinery. Before the fix, a failed DB write was caught, logged, and **the run still
reported SUCCESS** — the data was simply discarded.

**Now:** `scrapers/db_spool.py` writes any failed payload to a spool directory on disk
instead of dropping it. `scrapers/replay_spool.py` applies the spool once the database is
back. Both are idempotent — `tank_readings` has a unique constraint on
`(scraped_at, tank_id)`, so replaying twice is harmless.

```bash
# After an outage, apply anything the scrapers spooled while the DB was down
~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scrapers/replay_spool.py
```

**If data was lost before spooling existed** (or the spool itself was lost), ATG readings
can be reconstructed from the cron logs, because `run_atg()` prints every parsed reading
before saving it:

```bash
# See what is recoverable without writing anything
~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scripts/recover_atg_from_logs.py \
    --log-dir /data/logs --dry-run

# Recover a specific window, including blocks whose save *reported* success
~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scripts/recover_atg_from_logs.py \
    --log-dir /data/logs --since 2026-08-18 --all
~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scrapers/replay_spool.py
```

By default the recovery tool only re-emits blocks whose DB save failed. **`--all` is
needed when the writes succeeded against a database you no longer have** — the log says
SUCCESS, but the rows live nowhere you can reach. Recovered rows are exact for
`tank_id`, `product`, `capacity_litres`, `is_reliable` and `scraped_at`; `volume_litres`
is accurate to 0.5 L (the log rounds to whole litres) and `level_mm` is not printed at
all, so it stays NULL.

Only ATG needs this. Every other source is date-parameterised and the portals keep
history, so a missed window is simply re-scraped (see section 7).

---

## 5. Manual test — run before scheduling

Always test on a date that already has data in the DB before activating a live schedule.

```bash
# On the VPS — completed-shift with an explicit date
~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scripts/run_completed_shift.py --date 2026-08-21

# ATG snapshot (no date argument — it is a live reading)
~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scripts/run_atg_snapshot.py

# Or exercise the exact path cron uses, including lock + logging
bash ~/pumpvision/scripts/vps_run_atg_snapshot.sh
tail -40 /data/logs/atg_$(TZ=Asia/Kolkata date +%F).log
```

Expected output: a header showing op\_date, IST timestamp, paytm wait and mode; the full
`daily_scrape.py` output; then `RESULT: SUCCESS` or `FAILED`.

**A log line saying `SUCCESS` is not proof the data landed.** That was exactly the August
2026 failure mode. Confirm against the database:

```bash
psql "$DATABASE_URL" -c \
  "select count(*), max(scraped_at) from tank_readings
   where created_at > now() - interval '10 minutes';"
```

To override op\_date for a one-off backfill, set `PUMPVISION_COMPLETED_SHIFT_DATE` for
that invocation only — never in `.env`, or every future run scrapes the same fixed date:

```bash
PUMPVISION_COMPLETED_SHIFT_DATE=2026-08-20 \
  ~/pumpvision/.venv/bin/python -X utf8 ~/pumpvision/scripts/run_completed_shift.py
```

`PUMPVISION_PAYTM_WAIT_SECONDS=1800` works the same way for a slow Paytm run.

---

## 6. Windows local fallback (not production)

The `.ps1` wrapper scripts are for local manual use and Windows Task Scheduler only. They
are **not** the production scheduling path — use them only from a machine on the tailnet,
pointed at the evo's `DATABASE_URL`. Note that IRAS/Paytm/SDMS will refuse a non-Indian
IP, so these are of limited use outside India.

| Script | Purpose |
|--------|---------|
| `scripts/run_completed_shift.ps1` | Windows wrapper for completed-shift scrape |
| `scripts/run_atg_snapshot.ps1` | Windows wrapper for ATG snapshot |

Both scripts are ASCII-safe and parse correctly under Windows PowerShell 5
(`powershell.exe`).

### Windows Task Scheduler — Completed-shift

1. Task Scheduler → Create Task
2. **General:** Name `Pumpvision - Completed Shift`; run whether user is logged on or not
3. **Triggers → New:** Daily → 06:15 (or 06:30 for extra margin)
4. **Actions → New:**
   - Program: `powershell.exe`
   - Arguments: `-NonInteractive -ExecutionPolicy Bypass -File "C:\Users\Rishab 2\Desktop\Pumpvision\scripts\run_completed_shift.ps1"`
   - Start in: `C:\Users\Rishab 2\Desktop\Pumpvision`
5. **Settings:** Do not start a new instance if already running. Stop after 2 hours.

### Windows Task Scheduler — ATG snapshot

1. Create Task → Name `Pumpvision - ATG Snapshot`
2. **Triggers → New:** Daily → 06:00, repeat every **30 minutes** for **1 day**
3. **Actions → New:** as above but `run_atg_snapshot.ps1`
4. **Settings:** Do not start a new instance if already running.

**Windows environment variables** (`DATABASE_URL` and all scraper credentials) must be set
as user environment variables for the account that runs the tasks:
`Win + R → sysdm.cpl → Advanced → Environment Variables → User variables → New`

---

## 7. Recovery commands

If a completed-shift run exits nonzero, check the ACCOUNTING SOURCE SUMMARY in the output
to see which source failed. Retry only that source — do not re-run the full
completed-shift.

```bash
cd ~/pumpvision
.venv/bin/python -X utf8 scrapers/daily_scrape.py --paytm-only  --date 2026-08-21
.venv/bin/python -X utf8 scrapers/daily_scrape.py --price-only  --date 2026-08-21
.venv/bin/python -X utf8 scrapers/daily_scrape.py --sdms-only   --date 2026-08-21
```

**If IRAS autonomous CAPTCHA keeps failing — manual fallback only (never for scheduled
runs):**

```bash
.venv/bin/python -X utf8 scrapers/daily_scrape.py --price-only --date 2026-08-21 --iras-manual-captcha
```

A fresh CAPTCHA image is saved to `data/iras/debug/login_<ts>/manual_captcha.png`. Open
the image, type the characters, press Enter. Blocks until input — never use in a cron job.

**If the Paytm CSV already exists on disk:**

```bash
.venv/bin/python -X utf8 scrapers/import_paytm_csv.py /data/paytm/paytm_2026-08-21.csv
```

**Checking recent job outcomes:**

```bash
tail -60 /data/logs/atg_$(TZ=Asia/Kolkata date +%F).log
grep -c 'RESULT      : SUCCESS' /data/logs/atg_$(TZ=Asia/Kolkata date +%F).log
```

---

## 8. Current caveats

| Issue | Behaviour | Recovery |
|-------|-----------|---------|
| IRAS CAPTCHA fails | Price and boundaries are skipped; SDMS still runs; run exits nonzero | Retry with `--price-only`; if autonomous keeps failing, use `--iras-manual-captcha` once manually |
| Paytm download is slow | Default wait is 900s (15 min); use `--paytm-wait-seconds 1800` for slow runs | Retry with `--paytm-only`; if CSV is on disk, import with `import_paytm_csv.py` |
| Paytm session expired | OTP is sent; Gmail IMAP reads it automatically if Gmail env vars are set | Ensure `GMAIL_ADDRESS`/`GMAIL_APP_PASSWORD` are set; OTP is auto-handled |
| SDMS session expired | SDMS solves the e-Mitra arithmetic challenge; usually auto-recovers | Retry with `--sdms-only` |
| Source already in DB | Automatic skip — not a failure; ACCOUNTING SOURCE SUMMARY shows SKIPPED | No action needed |
| ATG data for XG is unreliable | Stored with `is_reliable = False`; XG probe known hardware issue | Data stored; UI warning deferred to Stage 2 |
| **Database unreachable** | Payload is spooled to disk; the run reports the failure honestly | `replay_spool.py` once the DB is back — see section 4 |
| ATG snapshot skipped by lock | Completed-shift was still running; wrapper logs `SKIPPED`, exits 1 | None needed — the next hourly slot catches up |
| evo host resets | The evo has bugchecked repeatedly; the web app is restored within 5 min by the `start-all.sh` watchdog, and scraper writes spool meanwhile | `replay_spool.py` after the box is back |

**Cron jobs must never block waiting for manual input.** Do not add `--iras-manual-captcha`
or `--paytm-debug` to any cron command.

---

## 9. File locations summary

| File | Purpose |
|------|---------|
| `scripts/vps_bootstrap.sh` | One-time VPS provisioning |
| `scripts/vps_run_completed_shift.sh` | Cron wrapper — completed-shift; `flock` + per-day log |
| `scripts/vps_run_atg_snapshot.sh` | Cron wrapper — ATG; shares the lock, non-blocking |
| `scripts/vps_run_sdms_lookback.sh` | Cron wrapper — CNG lookback probes, Mon–Sat |
| `scripts/run_completed_shift.py` | Completed-shift logic (IST op\_date auto-calc) |
| `scripts/run_atg_snapshot.py` | ATG snapshot logic |
| `scripts/recover_atg_from_logs.py` | Rebuilds ATG readings from cron logs when writes were lost |
| `scripts/run_iras_probe.py` | IRAS login-page diagnostic probe; no login, exits 0 always |
| `scripts/run_completed_shift.ps1` | Windows local/manual fallback — completed-shift |
| `scripts/run_atg_snapshot.ps1` | Windows local/manual fallback — ATG snapshot |
| `scrapers/daily_scrape.py` | Underlying Python orchestrator — all scraper logic |
| `scrapers/db_spool.py` | Spools failed DB payloads to disk instead of discarding them |
| `scrapers/replay_spool.py` | Applies the spool once the database is reachable again |
| `Dockerfile` | Kept only to pin a known-good Playwright/Python/Chromium triple — **nothing deploys from it** |
| `.dockerignore` | Excludes secrets, local data, session state, and venvs from the build context |
| `~/pumpvision-web.sh` *(evo, not in repo)* | gunicorn line serving the Flask app on `:8002` |
| `~/start-all.sh` *(evo, not in repo)* | Watchdog; restarts the web app if dead, cron every 5 min |
| `~/pg_backup.sh` *(evo, not in repo)* | Nightly `pg_dump -Fc`; pumpvision retained 90 days |
| `/data/logs/` *(VPS)* | Per-job, per-IST-day cron logs, 30-day retention |
| `/data/state/` *(VPS)* | Persisted Paytm + SDMS browser sessions |
