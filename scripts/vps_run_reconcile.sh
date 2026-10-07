#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: daily totalizer reconciliation.
#
# Compares the nozzle_totalizers chain and the attendants' shift-close readings
# against IRAS's own Shift Totalizer files for the last few op dates, logs every
# disagreement and raises an owner alert (app_notifications 'recon_alert').
# Read-only on totalizer data -- it never corrects a value.
#
# Crontab (VPS clock is UTC):  30 2 * * *  -> 08:00 IST, after the 06:30 IST
# completed-shift run and its retries have finished.
set -u

REPO="$HOME/pumpvision"
LOG_DIR="/data/logs"
mkdir -p "$LOG_DIR"
cd "$REPO" || exit 1

LOG="$LOG_DIR/reconcile_$(TZ=Asia/Kolkata date +%F).log"
find "$LOG_DIR" -name 'reconcile_*.log' -mtime +30 -delete 2>/dev/null

{
    echo "[wrapper] start $(date -u +'%F %T') UTC"
    "$REPO/.venv/bin/python" -X utf8 "$REPO/scripts/reconcile_totalizers.py" "$@"
    rc=$?
    echo "[wrapper] exit=$rc $(date -u +'%F %T') UTC (0 = all agree, 2 = problems reported)"
    exit "$rc"
} >> "$LOG" 2>&1
