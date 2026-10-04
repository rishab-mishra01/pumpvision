#!/usr/bin/env bash
# Pumpvision India VPS cron wrapper: fetch TODAY's IRAS price soon after 06:00 IST.
#
# Prices change at 06:00 IST. The completed-shift run (06:30 IST) only fetches the
# price of the day that just ended, so the new day's price used to reach the DB a
# day late, and attendant credit sales all day used the previous day's rate --
# wrong on a price-change day. IRAS publishes the new price at 06:00 (verified
# 2026-10-04), so fetch it straight away.
#
# Crontab (VPS clock is UTC):
#   40 0 * * *   -> 06:10 IST  first try
#   45 1 * * *   -> 07:15 IST  retry (skips in three SELECTs if 06:10 succeeded)
# The evo raises an owner alert at 07:30 IST if the price is still missing
# (scripts/morning_checks.py price).
set -u

REPO="$HOME/pumpvision"
LOCK="/data/locks/daily_scrape.lock"
LOG_DIR="/data/logs"
mkdir -p "$LOG_DIR" /data/locks
cd "$REPO" || exit 1

TODAY="$(TZ=Asia/Kolkata date +%F)"
LOG="$LOG_DIR/price_today_${TODAY}.log"
find "$LOG_DIR" -name 'price_today_*.log' -mtime +30 -delete 2>/dev/null

{
    echo "[wrapper] start $(date -u +'%F %T') UTC -- price for op_date $TODAY"
    if /usr/bin/flock -w 1500 "$LOCK" \
        "$REPO/.venv/bin/python" -X utf8 "$REPO/scrapers/daily_scrape.py" --price-only --date "$TODAY"; then
        echo "[wrapper] done $(date -u +'%F %T') UTC"
    else
        rc=$?
        echo "[wrapper] FAILED exit=$rc $(date -u +'%F %T') UTC"
        exit "$rc"
    fi
} >> "$LOG" 2>&1
