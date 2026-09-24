#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: ATG tank stock snapshot (live reading).
#
# Crontab (VPS clock is UTC):  30 0-18 * * *   (hourly at :30, 06:00-00:30 IST)
#
# Shares the daily_scrape lock with the completed-shift wrapper. Non-blocking:
# if a completed-shift run (or another snapshot) is in flight, this snapshot is
# skipped -- the next slot will catch up. ATG is a live reading and the Stock
# window overlaps the cron interval, so a skipped run loses nothing.
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
    # -E 75: flock exits 75 when the lock is held, so a genuine scrape failure
    # (exit 1) is no longer indistinguishable from "another run had the lock".
    if /usr/bin/flock -n -E 75 "$LOCK" \
        "$REPO/.venv/bin/python" -X utf8 "$REPO/scripts/run_atg_snapshot.py"; then
        echo "[wrapper] done $(date -u +'%F %T') UTC"
    else
        rc=$?
        if [ "$rc" -eq 75 ]; then
            echo "[wrapper] SKIPPED $(date -u +'%F %T') UTC -- lock held by another daily_scrape run"
        else
            echo "[wrapper] FAILED exit=$rc $(date -u +'%F %T') UTC -- see output above"
        fi
        exit "$rc"
    fi
} >> "$LOG" 2>&1
