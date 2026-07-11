#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: daily completed-shift scrape.
#
# Crontab (VPS clock is UTC):  0 1 * * *   -> 06:30 IST, after the 06:00 IST
# shift boundary. op_date is auto-calculated in IST by run_completed_shift.py
# (always IST today - 1). Extra args (e.g. --date YYYY-MM-DD) are passed through.
#
# Serializes against every other daily_scrape.py run via a shared flock --
# concurrent runs interleave portal sessions and can fail each other.
# Waits up to 25 minutes for an in-flight ATG snapshot to finish.
set -u

REPO="$HOME/pumpvision"
LOCK_DIR="/data/locks"
LOCK="$LOCK_DIR/daily_scrape.lock"
LOG_DIR="/data/logs"
mkdir -p "$LOG_DIR" "$LOCK_DIR"

# Log named by accounting op_date (yesterday in IST), matching the manual-run convention.
OP_DATE="$(TZ=Asia/Kolkata date -d 'yesterday' +%F)"
LOG="$LOG_DIR/completed_shift_${OP_DATE}.log"

{
    echo "[wrapper] start $(date -u +'%F %T') UTC"
    /usr/bin/flock -w 1500 "$LOCK" \
        "$REPO/.venv/bin/python" -X utf8 "$REPO/scripts/run_completed_shift.py" "$@"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "[wrapper] FAILED exit=$rc $(date -u +'%F %T') UTC (rc=1 with no scrape output above means the lock wait timed out)"
    else
        echo "[wrapper] done $(date -u +'%F %T') UTC"
    fi
    exit "$rc"
} >> "$LOG" 2>&1
