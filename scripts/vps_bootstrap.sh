#!/usr/bin/env bash
#
# Pumpvision — India VPS bootstrap (Ubuntu 24.04 LTS).
#
# Idempotent. Safe to re-run: every step checks before it acts.
# Installs Python + git, clones/updates the repo, creates a venv, installs
# requirements, installs Chromium for Playwright, and creates the data tree.
#
# CONTAINS NO SECRETS. It never writes .env — you place that file yourself.
#
# Usage (on the VPS, as the `ubuntu` user):
#     bash scripts/vps_bootstrap.sh
#
# Or, for a first-time run before the repo exists:
#     curl -fsSL <raw-url-of-this-file> -o /tmp/vps_bootstrap.sh && bash /tmp/vps_bootstrap.sh
#
# Overridable via environment:
#     REPO_URL   git remote to clone      (default: SSH form, needs a deploy key)
#     BRANCH     branch to check out      (default: fix/india-runner)
#     APP_DIR    checkout location        (default: $HOME/pumpvision)
#     DATA_ROOT  persistent data root     (default: /data)
#     SWAP_GB    swapfile size in GiB     (default: 2; set 0 to skip)

set -euo pipefail

REPO_URL="${REPO_URL:-git@github.com:rishab-mishra01/pumpvision.git}"
BRANCH="${BRANCH:-fix/india-runner}"
APP_DIR="${APP_DIR:-$HOME/pumpvision}"
DATA_ROOT="${DATA_ROOT:-/data}"
SWAP_GB="${SWAP_GB:-2}"
VENV_DIR="$APP_DIR/.venv"

log()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
ok()   { printf '    \033[0;32m[ok]\033[0m %s\n' "$*"; }
skip() { printf '    \033[0;33m[skip]\033[0m %s\n' "$*"; }
die()  { printf '\n\033[0;31m[FATAL]\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] && die "Do not run as root. Run as the 'ubuntu' user; sudo is used where needed."
command -v sudo >/dev/null 2>&1 || die "sudo not found."

# ── 1. System packages ───────────────────────────────────────────────────────
log "System packages"
PKGS=(python3 python3-venv python3-pip git curl ca-certificates)
MISSING=()
for pkg in "${PKGS[@]}"; do
    dpkg -s "$pkg" >/dev/null 2>&1 || MISSING+=("$pkg")
done
if [ ${#MISSING[@]} -eq 0 ]; then
    skip "all present: ${PKGS[*]}"
else
    sudo apt-get update -qq
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${MISSING[@]}"
    ok "installed: ${MISSING[*]}"
fi
PY_VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
ok "python3 = $PY_VER"
case "$PY_VER" in
    3.1[2-9]) ;;
    *) printf '    \033[0;33m[warn]\033[0m expected Python 3.12+ on Ubuntu 24.04, got %s\n' "$PY_VER" ;;
esac

# ── 2. Swap ──────────────────────────────────────────────────────────────────
# Headless Chromium peaks well above 1 GiB. On a 1 GB instance the kernel OOM
# killer takes the browser mid-scrape and the failure looks like a portal error.
log "Swap (${SWAP_GB}G)"
if [ "$SWAP_GB" = "0" ]; then
    skip "SWAP_GB=0, not creating swap"
elif [ -f /swapfile ]; then
    skip "/swapfile already exists"
else
    sudo fallocate -l "${SWAP_GB}G" /swapfile
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
    sudo swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
    ok "created and enabled ${SWAP_GB}G swapfile (persisted in /etc/fstab)"
fi
free -h | sed 's/^/    /'

# ── 3. SSH deploy key (private repo) ─────────────────────────────────────────
log "Git access"
if [ -d "$APP_DIR/.git" ]; then
    skip "repo already cloned; deploy key assumed working"
elif [[ "$REPO_URL" == https://* ]]; then
    skip "HTTPS remote; no deploy key needed"
elif [ -f "$HOME/.ssh/id_ed25519" ]; then
    skip "SSH key already exists at ~/.ssh/id_ed25519"
else
    ssh-keygen -t ed25519 -N "" -f "$HOME/.ssh/id_ed25519" -C "pumpvision-vps" >/dev/null
    ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null
    printf '\n\033[1;33m    ACTION REQUIRED\033[0m\n'
    printf '    Add this public key as a GitHub deploy key (read-only is enough):\n'
    printf '    https://github.com/rishab-mishra01/pumpvision/settings/keys/new\n\n'
    sed 's/^/      /' "$HOME/.ssh/id_ed25519.pub"
    printf '\n    Then re-run this script.\n'
    exit 0
fi
grep -q '^github.com' "$HOME/.ssh/known_hosts" 2>/dev/null || \
    ssh-keyscan -t ed25519 github.com >> "$HOME/.ssh/known_hosts" 2>/dev/null || true

# ── 4. Clone or update ───────────────────────────────────────────────────────
log "Repository → $APP_DIR (branch: $BRANCH)"
if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --quiet origin
    git -C "$APP_DIR" checkout --quiet "$BRANCH"
    git -C "$APP_DIR" pull --quiet --ff-only origin "$BRANCH"
    ok "updated to $(git -C "$APP_DIR" rev-parse --short HEAD)"
else
    git clone --quiet --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
    ok "cloned at $(git -C "$APP_DIR" rev-parse --short HEAD)"
fi

# ── 5. Virtualenv + Python dependencies ──────────────────────────────────────
log "Python virtualenv"
if [ -x "$VENV_DIR/bin/python" ]; then
    skip "venv exists at $VENV_DIR"
else
    python3 -m venv "$VENV_DIR"
    ok "created $VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip
"$VENV_DIR/bin/python" -m pip install --quiet -r "$APP_DIR/requirements.txt"
ok "requirements installed"

# ── 6. Playwright Chromium ───────────────────────────────────────────────────
# install-deps needs root (apt); the browser download itself must run as the
# same user that will later launch it, so it lands in ~/.cache/ms-playwright.
log "Playwright Chromium"
if "$VENV_DIR/bin/python" -c "
import os, sys
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    sys.exit(0 if os.path.exists(p.chromium.executable_path) else 1)
" >/dev/null 2>&1; then
    skip "chromium binary already present"
else
    sudo "$VENV_DIR/bin/python" -m playwright install-deps chromium
    "$VENV_DIR/bin/python" -m playwright install chromium
    ok "chromium installed"
fi

# ── 7. Data tree ─────────────────────────────────────────────────────────────
# OUTPUT_FOLDER points at $DATA_ROOT/iras_data; the scrapers create the
# ISS/ShiftTotalizer/Price/ATG subfolders themselves, but we pre-create them
# so a permissions problem surfaces here rather than mid-scrape.
log "Data tree → $DATA_ROOT"
DIRS=(
    "$DATA_ROOT"
    "$DATA_ROOT/iras_data" "$DATA_ROOT/iras_data/ISS" "$DATA_ROOT/iras_data/ShiftTotalizer"
    "$DATA_ROOT/iras_data/Price" "$DATA_ROOT/iras_data/ATG"
    "$DATA_ROOT/state" "$DATA_ROOT/paytm" "$DATA_ROOT/sdms"
    "$DATA_ROOT/logs" "$DATA_ROOT/debug"
)
sudo mkdir -p "${DIRS[@]}"
sudo chown -R "$(id -u):$(id -g)" "$DATA_ROOT"
chmod 700 "$DATA_ROOT/state"   # session cookies live here
ok "created ${#DIRS[@]} directories, owned by $(id -un)"

TESTFILE="$DATA_ROOT/.write_test"
if touch "$TESTFILE" 2>/dev/null; then
    rm -f "$TESTFILE"
    ok "write test passed"
else
    die "cannot write to $DATA_ROOT"
fi

# ── 8. Verify ────────────────────────────────────────────────────────────────
log "Verification"
"$VENV_DIR/bin/python" - <<'PY'
import sys
print(f"    python      : {sys.version.split()[0]}")
import playwright; print(f"    playwright  : {playwright.__version__ if hasattr(playwright,'__version__') else 'installed'}")
import flask, sqlalchemy, anthropic  # noqa: F401
print("    flask/sqlalchemy/anthropic: import ok")
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    print(f"    chromium    : {b.version}")
    b.close()
PY

# ── 9. .env status (never created by this script) ────────────────────────────
log "Environment file"
if [ -f "$APP_DIR/.env" ]; then
    ok ".env present (contents not inspected)"
else
    printf '    \033[0;33m[warn]\033[0m %s missing. Create it manually before running any scraper.\n' "$APP_DIR/.env"
    printf '            See docs/scrape_scheduling_runbook.md for the variable list.\n'
fi

log "Bootstrap complete"
cat <<EOF
    APP_DIR   : $APP_DIR
    VENV      : $VENV_DIR
    DATA_ROOT : $DATA_ROOT

    Next: run the reachability probe (no login, no credentials needed):
        bash $APP_DIR/scripts/run_vps_probe.sh
EOF
