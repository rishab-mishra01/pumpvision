#!/usr/bin/env bash
#
# Pumpvision — VPS reachability probe wrapper.
#
# Prints host facts, then runs scripts/vps_probe.py against the IRAS, Paytm and
# SDMS login pages. Does NOT log in, reads no credentials, writes no DB rows.
#
# Usage (on the VPS):
#     bash scripts/run_vps_probe.sh              # all three sites, direct egress
#     bash scripts/run_vps_probe.sh --only iras  # single site
#     bash scripts/run_vps_probe.sh --proxy      # A/B via IRAS_PROXY_* env vars
#
# Overridable:
#     APP_DIR   repo checkout (default: directory containing this script's parent)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(dirname "$SCRIPT_DIR")}"
VENV_PY="$APP_DIR/.venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "[FATAL] No venv at $VENV_PY. Run scripts/vps_bootstrap.sh first." >&2
    exit 1
fi

echo "================================================================"
echo "  HOST"
echo "================================================================"
echo "  hostname : $(hostname)"
echo "  kernel   : $(uname -srm)"
echo "  os       : $(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo unknown)"
echo "  date-utc : $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "  repo     : $(git -C "$APP_DIR" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')@$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo '?')"
echo "  memory   :"
free -h | sed 's/^/    /'
echo

# The probe reads no credentials, but .env may hold proxy vars we deliberately
# want to ignore on the default (direct) run. Do not source .env here.
cd "$APP_DIR"
exec "$VENV_PY" -X utf8 scripts/vps_probe.py "$@"
