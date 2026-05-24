"""
IRAS proxy config helper — scrapers/iras_proxy.py

Reads IRAS_PROXY_SERVER / IRAS_PROXY_USERNAME / IRAS_PROXY_PASSWORD from the
environment at import time and builds a Playwright-compatible proxy dict.

Env vars:
    IRAS_PROXY_SERVER    e.g. http://brd.superproxy.io:33335
    IRAS_PROXY_USERNAME  optional — included only if set
    IRAS_PROXY_PASSWORD  optional — included only if set

Credentials are NEVER printed or logged anywhere in this module.
The only public interface for status reporting is IRAS_PROXY_ENABLED (bool).

Usage:
    from iras_proxy import iras_proxy_cfg, IRAS_PROXY_ENABLED, safe_exc_name

    # Log proxy status (prints yes/no only — never server URL or credentials):
    print(f"  [IRAS] proxy : {'yes' if IRAS_PROXY_ENABLED else 'no'}")

    # Inject into browser.new_context():
    ctx_kw: dict = {
        "accept_downloads": True,
        "viewport": {"width": 1400, "height": 900},
        "user_agent": "...",
    }
    _proxy = iras_proxy_cfg()
    if _proxy is not None:
        ctx_kw["proxy"] = _proxy
    context = await browser.new_context(**ctx_kw)

    # Log exceptions safely (never str(exc) — may contain proxy host/port):
    except Exception as exc:
        print(f"  [IRAS] Setup failed: {safe_exc_name(exc)}")
"""

import os

_SERVER   = os.environ.get("IRAS_PROXY_SERVER",   "").strip()
_USERNAME = os.environ.get("IRAS_PROXY_USERNAME", "").strip()
_PASSWORD = os.environ.get("IRAS_PROXY_PASSWORD", "").strip()

# Build the Playwright proxy dict once at import time.
# _cfg is None when IRAS_PROXY_SERVER is absent — preserving existing behaviour.
_cfg: dict | None = None
if _SERVER:
    _cfg = {"server": _SERVER}
    if _USERNAME:
        _cfg["username"] = _USERNAME
    if _PASSWORD:
        _cfg["password"] = _PASSWORD

# Public flag — safe to print/compare; never contains server or credentials.
IRAS_PROXY_ENABLED: bool = _cfg is not None


def iras_proxy_cfg() -> dict | None:
    """Return the cached Playwright proxy config dict, or None if not configured.

    Pass the return value directly to browser.new_context(proxy=...) only.
    Never call str() on the dict — it contains credentials.
    """
    return _cfg


def safe_exc_name(exc: BaseException) -> str:
    """Return only the exception class name — never str(exc).

    Playwright exception messages for proxy/connection failures can embed the
    proxy server host, port, or full URL (e.g. net::ERR_TUNNEL_CONNECTION_FAILED
    at http://proxy:33335).  Always use this helper instead of str(exc) or
    f'{exc}' whenever an exception may originate from proxy/browser/network
    setup code.
    """
    return exc.__class__.__name__
