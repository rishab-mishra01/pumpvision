#!/bin/bash
# 4-hourly review (cron on the evo). The DB URL is the one the web app uses.
set -u
cd /home/dhruv/pumpvision || exit 1
export $(grep -o 'DATABASE_URL="[^"]*"' /home/dhruv/pumpvision-web.sh | head -1 | tr -d '"')
exec /home/dhruv/pumpvision/.venv/bin/python scripts/run_review.py
