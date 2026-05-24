#!/usr/bin/env python3
"""
Shared Railway entrypoint. All Railway services from this repo run this script.
The service role is controlled by the PUMPVISION_SERVICE_ROLE environment variable.

Supported roles:
  web              Flask app via gunicorn (default if variable is not set)
  completed-shift  Daily accounting scrape cron job
  atg              ATG tank snapshot cron job
  iras-probe       Diagnostic: opens IRAS login page, prints DOM/network report,
                   exits 0.  Use to verify browser + IRAS reachability without
                   logging in or using credentials.

Set PUMPVISION_SERVICE_ROLE in each Railway service's Variables panel.
Never set it to a secret value -- it is printed to logs at startup.

railway.json sets this as the single start command for all services:
    python -X utf8 scripts/railway_entrypoint.py
"""

import os
import subprocess
import sys

_VALID_ROLES = frozenset({"web", "completed-shift", "atg", "iras-probe"})


def _role() -> str:
    """Return the normalised service role. Defaults to 'web'."""
    return os.environ.get("PUMPVISION_SERVICE_ROLE", "web").strip().lower()


def _scripts_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _repo_root() -> str:
    return os.path.dirname(_scripts_dir())


# ---------------------------------------------------------------------------
# Role: web
# ---------------------------------------------------------------------------

def _dispatch_web() -> None:
    """Start gunicorn.

    Uses os.execvp so gunicorn replaces this Python process entirely.
    Railway manages the gunicorn PID directly: health checks, signals, and
    graceful shutdown all work without a Python wrapper sitting in between.
    """
    port = os.environ.get("PORT", "").strip()
    if not port:
        print(
            "[ERROR] PORT is not set. Railway injects PORT automatically for web services.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("[railway_entrypoint] role=web")
    print(f"  port    : {port}")
    print(f"  command : gunicorn wsgi:app --bind 0.0.0.0:{port} --workers 2 --preload")
    sys.stdout.flush()

    # Replace this process with gunicorn. Does not return on success.
    os.execvp("gunicorn", [
        "gunicorn", "wsgi:app",
        "--bind", f"0.0.0.0:{port}",
        "--workers", "2",
        "--preload",
    ])

    # If execvp returns, the exec failed.
    print("[ERROR] os.execvp(gunicorn) failed -- is gunicorn installed?", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Role: completed-shift
# ---------------------------------------------------------------------------

def _dispatch_completed_shift() -> int:
    """Delegate to scripts/run_completed_shift.py.

    Optional env var overrides (set in Railway service Variables for testing):
      PUMPVISION_COMPLETED_SHIFT_DATE   YYYY-MM-DD  Pass as --date. Remove after test.
      PUMPVISION_PAYTM_WAIT_SECONDS     integer     Pass as --paytm-wait-seconds.
    """
    script = os.path.join(_scripts_dir(), "run_completed_shift.py")
    cmd = [sys.executable, "-X", "utf8", script]

    date_override = os.environ.get("PUMPVISION_COMPLETED_SHIFT_DATE", "").strip()
    if date_override:
        cmd += ["--date", date_override]

    wait_override = os.environ.get("PUMPVISION_PAYTM_WAIT_SECONDS", "").strip()
    if wait_override:
        cmd += ["--paytm-wait-seconds", wait_override]

    print("[railway_entrypoint] role=completed-shift")
    if date_override:
        print(f"  date override : {date_override}  (PUMPVISION_COMPLETED_SHIFT_DATE)")
    if wait_override:
        print(f"  paytm wait    : {wait_override}s  (PUMPVISION_PAYTM_WAIT_SECONDS)")
    sys.stdout.flush()

    result = subprocess.run(cmd, cwd=_repo_root())
    return result.returncode


# ---------------------------------------------------------------------------
# Role: atg
# ---------------------------------------------------------------------------

def _dispatch_atg() -> int:
    """Delegate to scripts/run_atg_snapshot.py."""
    script = os.path.join(_scripts_dir(), "run_atg_snapshot.py")
    cmd = [sys.executable, "-X", "utf8", script]

    print("[railway_entrypoint] role=atg")
    sys.stdout.flush()

    result = subprocess.run(cmd, cwd=_repo_root())
    return result.returncode


# ---------------------------------------------------------------------------
# Role: iras-probe
# ---------------------------------------------------------------------------

def _dispatch_iras_probe() -> int:
    """Delegate to scripts/run_iras_probe.py.

    Launches Chromium, navigates to the IRAS login page, prints a diagnostic
    report (URL, title, HTML length, DOM counts, script srcs, failed requests),
    and exits 0 regardless of whether the page rendered.

    Use this role to verify that the Docker/browser environment can reach and
    render iras.iocliras.in before attempting a real scraper run.

    Does NOT log in.  Does NOT use IRAS credentials.  Does NOT save cookies.
    """
    script = os.path.join(_scripts_dir(), "run_iras_probe.py")
    cmd = [sys.executable, "-X", "utf8", script]

    print("[railway_entrypoint] role=iras-probe")
    sys.stdout.flush()

    result = subprocess.run(cmd, cwd=_repo_root())
    return result.returncode


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def main() -> int:
    role = _role()

    if role not in _VALID_ROLES:
        print(
            f"[ERROR] Unknown PUMPVISION_SERVICE_ROLE: '{role}'. "
            f"Valid values: {', '.join(sorted(_VALID_ROLES))}.",
            file=sys.stderr,
        )
        return 1

    if role == "web":
        _dispatch_web()   # os.execvp -- replaces this process, does not return
        return 1          # unreachable

    if role == "completed-shift":
        return _dispatch_completed_shift()

    if role == "atg":
        return _dispatch_atg()

    if role == "iras-probe":
        return _dispatch_iras_probe()

    return 1  # unreachable


if __name__ == "__main__":
    sys.exit(main())
