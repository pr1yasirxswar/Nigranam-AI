"""Production guards for endpoints that must not be open on a public deploy.

APP_ENV=production (set in render.yaml) turns these on. In development
(default) nothing changes, so local workflows keep working.

Bug context: /debug/* was completely unauthenticated -- anyone could
re-trigger the full 19.5k-work scoring pass (CPU/DoS), and /debug/score +
/debug/risk return `seeded_anomaly_type`, the dataset's hidden ground-truth
label. /auth/demo-accounts listed every valid login id; its own docstring
says a real deployment must remove or gate it.
"""
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request, status


def is_production() -> bool:
    return os.environ.get("APP_ENV", "development").lower() == "production"


def require_debug_access(x_debug_token: str | None = Header(default=None)):
    """Dev: open. Production: requires header X-Debug-Token == DEBUG_TOKEN;
    if DEBUG_TOKEN is unset in production the endpoints behave as if they
    don't exist (404)."""
    if not is_production():
        return
    expected = os.environ.get("DEBUG_TOKEN", "")
    if not expected:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")
    if not x_debug_token or not hmac.compare_digest(x_debug_token, expected):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


def require_not_production():
    if is_production():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not Found")


# --- login brute-force throttle (in-memory, single process) ---------------
_MAX_FAILS = 8
_WINDOW_SECONDS = 300
_fails: dict[str, deque] = defaultdict(deque)


def _client_key(request: Request, account: str) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")
    return f"{ip}|{account.lower()}"


def check_login_allowed(request: Request, account: str) -> None:
    key = _client_key(request, account)
    q = _fails[key]
    cutoff = time.monotonic() - _WINDOW_SECONDS
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= _MAX_FAILS:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            "Too many failed attempts. Try again in a few minutes.")


def record_login_failure(request: Request, account: str) -> None:
    _fails[_client_key(request, account)].append(time.monotonic())


def clear_login_failures(request: Request, account: str) -> None:
    _fails.pop(_client_key(request, account), None)
