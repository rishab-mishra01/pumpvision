#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: SDMS CNG lookback probes.
#
# CGD Rewa posts the CNG billing row for op_date D on D+1 between ~11:00 and
# 16:00 IST (never on Sunday -- billing offices closed; Saturday's and Sunday's
# rows both post on Monday). Probe three times so the CNG number lands at the
# earliest posting rather than at the end of the window:
#
# Crontab (VPS clock is UTC), Mon-Sat only:
#   0  7  * * 1-6    -> 12:30 IST
#   0  10 * * 1-6    -> 15:30 IST
#   35 11 * * 1-6    -> 17:05 IST (offset past the 11:30 UTC ATG slot)
#
# Each probe covers the last 3 op_dates via --sdms-only. The DB existence check
# treats a row with a CNG figure (or an aged-out zero-CNG row) as COMPLETE, so
# once the bill has landed -- or on fully-complete days -- a probe costs three
# SELECTs and never launches a browser. No retry loop: the next scheduled probe
# is the retry.
set -u

REPO="$HOME/pumpvision"
LOCK_DIR="/data/locks"
LOCK="$LOCK_DIR/daily_scrape.lock"
LOG_DIR="/data/logs"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

# The SDMS DB-save step resolves the migrations/ folder relative to the cwd;
# cron starts in $HOME, so run from the repo root.
cd "$REPO" || exit 1

# One log file per IST calendar day; the day's probes append to it.
LOG="$LOG_DIR/sdms_lookback_$(TZ=Asia/Kolkata date +%F).log"
find "$LOG_DIR" -name 'sdms_lookback_*.log' -mtime +30 -delete 2>/dev/null

D1="$(TZ=Asia/Kolkata date -d 'yesterday' +%F)"
D2="$(TZ=Asia/Kolkata date -d '2 days ago' +%F)"
D3="$(TZ=Asia/Kolkata date -d '3 days ago' +%F)"

{
    echo "[wrapper] start $(date -u +'%F %T') UTC -- probing op_dates $D3 $D2 $D1"
    if /usr/bin/flock -w 600 "$LOCK" \
        "$REPO/.venv/bin/python" -X utf8 "$REPO/scrapers/daily_scrape.py" \
        --sdms-only --dates "$D1" "$D2" "$D3"; then
        echo "[wrapper] done $(date -u +'%F %T') UTC"
    else
        rc=$?
        echo "[wrapper] FAILED exit=$rc $(date -u +'%F %T') UTC -- next scheduled probe will retry (rc=1 with no scrape output above means the lock wait timed out)"
        exit "$rc"
    fi
} >> "$LOG" 2>&1
