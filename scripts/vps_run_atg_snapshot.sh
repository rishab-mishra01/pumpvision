#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: ATG tank stock snapshot (live reading).
#
# Crontab (VPS clock is UTC):  */30 * * * *
#
# Shares the daily_scrape lock with the completed-shift wrapper. Non-blocking:
# if a completed-shift run (or another snapshot) is in flight, this snapshot is
# skipped -- the next 30-minute slot will catch up. ATG is a live reading, so a
# skipped run loses nothing that matters.
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
    if /usr/bin/flock -n "$LOCK" \
        "$REPO/.venv/bin/python" -X utf8 "$REPO/scripts/run_atg_snapshot.py"; then
        echo "[wrapper] done $(date -u +'%F %T') UTC"
    else
        rc=$?
        if [ "$rc" -eq 1 ]; then
            echo "[wrapper] SKIPPED $(date -u +'%F %T') UTC -- lock held by another daily_scrape run (or the snapshot exited 1; see output above)"
        else
            echo "[wrapper] FAILED exit=$rc $(date -u +'%F %T') UTC"
        fi
        exit "$rc"
    fi
} >> "$LOG" 2>&1
