#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: ATG tank stock snapshot (live reading).
#
# Crontab (VPS clock is UTC):  30 0-18 * * *   (hourly at :30, 06:00-00:30 IST)
#
# Shares the daily_scrape lock with the completed-shift wrapper. Waits up to 15 min
# for it (it used to skip, which left the Stock screen hours stale); the Stock
# window overlaps the cron interval, so a later run still back-fills any gap.
set -u

REPO="$HOME/pumpvision"
LOCK_DIR="/data/locks"
LOCK="$LOCK_DIR/daily_scrape.lock"
LOG_DIR="/data/logs"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

# One log file per IST calendar day; ~48 runs append to it.
LOG="$LOG_DIR/atg_$(TZ=Asia/Kolkata date +%F).log"

# Keep a month of ATG logs.
find "$LOG_DIR" -name 'atg_*.log' -mtime +30 -delete 2>/dev/null

{
    echo "[wrapper] start $(date -u +'%F %T') UTC"
    # Wait up to 15 min for the lock instead of skipping: the completed-shift run
    # (06:30 IST, longer when its IRAS login needs a retry) used to hold it past
    # 07:00 and the 07:00 reading was lost. -E 75: flock exits 75 when the wait
    # times out, so that stays distinguishable from a genuine scrape failure.
    # The IRAS login's CAPTCHA fails now and then (3 of 3 at 08:00 on 2026-10-05),
    # so one failed run is retried once, 3 min later, before giving up.
    rc=0
    for attempt in 1 2; do
        /usr/bin/flock -w 900 -E 75 "$LOCK" \
            "$REPO/.venv/bin/python" -X utf8 "$REPO/scripts/run_atg_snapshot.py"
        rc=$?
        [ "$rc" -eq 0 ] && break
        [ "$rc" -eq 75 ] && break
        [ "$attempt" -eq 1 ] && { echo "[wrapper] attempt 1 failed (exit=$rc) -- retrying in 3 min"; sleep 180; }
    done
    if [ "$rc" -eq 0 ]; then
        echo "[wrapper] done $(date -u +'%F %T') UTC"
    elif [ "$rc" -eq 75 ]; then
        echo "[wrapper] SKIPPED $(date -u +'%F %T') UTC -- lock still held after 15 min"
        exit 75
    else
        echo "[wrapper] FAILED exit=$rc $(date -u +'%F %T') UTC -- see output above"
        exit "$rc"
    fi
} >> "$LOG" 2>&1
