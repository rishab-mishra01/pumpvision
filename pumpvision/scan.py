"""On-demand ATG snapshot ("scan now").

The scrapers do not run on this host. IRAS is India-geo-restricted, so every
scrape runs on the Mumbai VPS and writes back to this database over Tailscale.
A manual scan therefore means asking the VPS to run the *same* wrapper cron
uses -- scripts/vps_run_atg_snapshot.sh -- rather than reimplementing it here.

That wrapper already takes /data/locks/daily_scrape.lock with `flock -n -E 75`,
so a manual scan can never interleave with a cron scrape or a completed-shift
run: it exits 75 instead, and we surface that as "a scrape is already running".

A run takes roughly two minutes (IRAS login with CAPTCHA, then an Excel export),
so this is a background thread with polled status, never a blocking request.
There is one gunicorn worker, so module-level state is the whole picture; a
lock still guards it because that worker is threaded.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime

VPS_HOST = "ubuntu@pumpvision-vps"
WRAPPER = "/home/ubuntu/pumpvision/scripts/vps_run_atg_snapshot.sh"

# A scrape is ~2 min; well past that means something hung, and we would rather
# report a timeout than leave the UI spinning forever.
TIMEOUT_S = 360

# flock's -E: the wrapper exits 75 when another daily_scrape holds the lock.
EXIT_LOCK_HELD = 75

_lock = threading.Lock()
_state: dict = {"status": "idle", "message": "", "started_at": None, "finished_at": None}


def _snapshot() -> dict:
    return dict(_state)


def status() -> dict:
    with _lock:
        return _snapshot()


def _run() -> None:
    try:
        proc = subprocess.run(
            [
                "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
                "-o", "StrictHostKeyChecking=accept-new",
                VPS_HOST, WRAPPER,
            ],
            capture_output=True, text=True, timeout=TIMEOUT_S,
        )
        rc = proc.returncode
        if rc == 0:
            result = ("done", "Tank levels updated.")
        elif rc == EXIT_LOCK_HELD:
            result = ("busy", "A scheduled scrape is already running. Try again shortly.")
        else:
            # The wrapper logs the detail on the VPS; the phone gets the summary.
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            hint = tail[-1][:160] if tail else f"exit {rc}"
            result = ("failed", f"Scan failed ({hint}).")
    except subprocess.TimeoutExpired:
        result = ("failed", f"Scan timed out after {TIMEOUT_S // 60} minutes.")
    except FileNotFoundError:
        result = ("failed", "ssh is unavailable on this host.")
    except Exception as exc:                     # noqa: BLE001 - must never kill the thread
        result = ("failed", f"Scan error: {type(exc).__name__}")

    with _lock:
        _state["status"], _state["message"] = result
        _state["finished_at"] = datetime.now().isoformat(timespec="seconds")


def start() -> dict:
    """Kick off a scan unless one is already in flight. Returns the new state."""
    with _lock:
        if _state["status"] == "running":
            return _snapshot()
        _state.update(
            status="running",
            message="Contacting the pump…",
            started_at=datetime.now().isoformat(timespec="seconds"),
            finished_at=None,
        )
        snap = _snapshot()

    threading.Thread(target=_run, name="atg-scan", daemon=True).start()
    return snap
