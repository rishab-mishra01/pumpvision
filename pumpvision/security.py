"""Hardening for running on the open internet (added 2026-09-24).

Until September 2026 the app was reachable only over Tailscale, so it had no
CSRF protection, no login throttling and cookies that travelled over plain
http. It is now published through a Cloudflare tunnel (with Cloudflare Access
in front), and this module closes those gaps without touching every template:

- CSRF: every state-changing request must carry an Origin (or, failing that,
  a Referer) naming this same host. Browsers always send Origin on cross-site
  form posts and fetches, so a forged request from another site is refused.
  Session cookies are also SameSite=Lax, a second, independent barrier.
- Login throttling: repeated failures lock the username and the client IP
  for a while. In-memory, which is correct for the single gunicorn worker
  this app runs with; a restart clears it, which is acceptable.
- Cookies are Secure/HttpOnly when PUMPVISION_SECURE_COOKIES=1 (set by
  ~/pumpvision-web.sh). Every real entry point is https (Cloudflare, or
  tailscale serve on :8443), and both proxies are on loopback.
- ProxyFix trusts X-Forwarded-* from one hop, so url_for and the Origin check
  see the public https host. Gunicorn binds 127.0.0.1, so only the local
  proxies can set those headers.
"""
import os
import threading
import time
from urllib.parse import urlsplit

from flask import abort, request
from werkzeug.middleware.proxy_fix import ProxyFix

_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# Login throttling
USER_MAX_FAILS = 5          # per username ...
IP_MAX_FAILS = 20           # ... and per client IP ...
WINDOW_SECONDS = 15 * 60    # ... within this window, then locked for the rest of it

_lock = threading.Lock()
_fails = {}                 # key -> list of failure timestamps


def client_ip():
    # Cloudflare's real client address; only meaningful because gunicorn is
    # loopback-bound, so the header can only come from our own tunnel.
    return request.headers.get("CF-Connecting-IP") or request.remote_addr or "?"


def _recent(key, now):
    stamps = [t for t in _fails.get(key, []) if now - t < WINDOW_SECONDS]
    if stamps:
        _fails[key] = stamps
    else:
        _fails.pop(key, None)
    return stamps


def login_locked(username):
    """Seconds until retry is allowed, or 0 if this attempt may proceed."""
    now = time.time()
    with _lock:
        for key, limit in ((f"u:{username.lower()}", USER_MAX_FAILS), (f"ip:{client_ip()}", IP_MAX_FAILS)):
            stamps = _recent(key, now)
            if len(stamps) >= limit:
                return int(WINDOW_SECONDS - (now - stamps[0])) + 1
    return 0


def record_login_failure(username):
    now = time.time()
    with _lock:
        for key in (f"u:{username.lower()}", f"ip:{client_ip()}"):
            _fails.setdefault(key, []).append(now)


def clear_login_failures(username):
    with _lock:
        _fails.pop(f"u:{username.lower()}", None)


def _same_origin(url):
    parts = urlsplit(url)
    return bool(parts.netloc) and parts.netloc == request.host


def init_security(app):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    secure = os.environ.get("PUMPVISION_SECURE_COOKIES") == "1"
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=secure,
        REMEMBER_COOKIE_HTTPONLY=True,
        REMEMBER_COOKIE_SAMESITE="Lax",
        REMEMBER_COOKIE_SECURE=secure,
        PREFERRED_URL_SCHEME="https" if secure else "http",
    )

    @app.before_request
    def _csrf_origin_check():
        if request.method in _SAFE_METHODS or app.testing:
            return None
        origin = request.headers.get("Origin")
        if origin and origin != "null":
            if _same_origin(origin):
                return None
            abort(403)
        referer = request.headers.get("Referer")
        if referer and _same_origin(referer):
            return None
        abort(403)

    @app.after_request
    def _security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        # same-origin still sends Referer to ourselves, which the check above uses
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if secure:
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp
