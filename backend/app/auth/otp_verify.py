"""
Phase 11 item 1 -- PRD.md S4.10's public verify flow: phone number -> OTP
(fixed/mocked for the demo, documented pre-production placeholder per
Rules.md, same pattern already used for X-User-Id and password auth) ->
basic details (name + address) -> unlocks the location-lookup endpoint.

Phase 12 bug fix (user-flagged practicality issue): this used to also
collect an "official_id" alongside the phone number. Dropped entirely --
there was no real identity system behind it to check against, so it was
security theatre (an unauthenticated visitor could type anything) while
adding friction. Phone number alone, format-validated as an Indian mobile
number, is the actual verification anchor (an OTP genuinely proves control
of that number); "basic details" (name/address) is collected AFTER OTP
success as a simple registration step, not a substitute for verification.

Storage choice flagged: kept in-process memory (three plain dicts), not a
Postgres table. A verification is short-lived (10 min to enter the OTP,
1 hour of "verified" standing afterward) and carries no audit-trail value
(Rules.md's "never delete" rule is about Flag/Review/CollusionAlert --
durable accountability records; this is ephemeral, closer to
auth/sessions.py's Session rows, which already delete themselves on
expiry). Same honest-placeholder framing as the mock OTP itself: fine for
a demo/single-process deployment, would need a shared store (Postgres or
Redis) behind a real SMS provider in production -- not invented here per
Rules.md ("do not add new third-party services ... without asking").
"""
import re
import secrets
from datetime import datetime, timedelta

# Fixed mock OTP for the demo -- documented here and in Demo-Credentials.md,
# never a real SMS send (Rules.md: no new paid third-party services without
# asking).
MOCK_OTP = "123456"

# Enough time to type in a 6-digit code; not a security boundary for a
# mocked OTP, just a sane expiry so stale entries don't accumulate forever.
REQUEST_TTL = timedelta(minutes=10)
# How long a successful verify stays usable for area lookups afterward --
# long enough for one public-dashboard visit.
VERIFIED_TTL = timedelta(hours=1)

# Indian mobile numbers: 10 digits, first digit 6-9 (TRAI numbering plan).
# _normalize_indian_phone strips a leading "+91"/"91"/"0" country/trunk
# prefix before checking, so "+91 98765 43210", "919876543210", and
# "09876543210" all normalize to the same 10-digit form as "9876543210".
_INDIAN_MOBILE_RE = re.compile(r"^[6-9]\d{9}$")

_pending: dict[str, dict] = {}   # verification_id -> {phone_number, expires_at}
_verified: dict[str, dict] = {}  # verify_token -> {phone_number, verified_at, name, address, details_submitted}


def normalize_indian_phone(raw: str) -> str | None:
    """Strips punctuation/spaces and an optional country/trunk prefix, then
    checks the result against the Indian mobile number pattern. Returns the
    normalized 10-digit number, or None if it's not a valid Indian mobile
    number -- callers (routers/public.py) turn None into a 400."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    return digits if _INDIAN_MOBILE_RE.match(digits) else None


def request_otp(phone_number: str) -> str:
    """Starts a verification attempt for an already-normalized 10-digit
    Indian mobile number (routers/public.py validates/normalizes before
    calling this)."""
    verification_id = secrets.token_urlsafe(16)
    _pending[verification_id] = {
        "phone_number": phone_number,
        "expires_at": datetime.utcnow() + REQUEST_TTL,
    }
    return verification_id


def confirm_otp(verification_id: str, otp: str) -> str | None:
    """Checks the submitted OTP against the fixed mock value. Returns a
    verify_token on success, None on a wrong/missing/expired attempt --
    callers should treat None as a plain 401, same generic-error pattern
    as auth/routers/auth.py's login (don't reveal which part was wrong)."""
    entry = _pending.get(verification_id)
    if entry is None:
        return None
    if entry["expires_at"] < datetime.utcnow():
        del _pending[verification_id]
        return None
    if otp != MOCK_OTP:
        return None
    del _pending[verification_id]  # one-time use, like a real OTP
    verify_token = secrets.token_urlsafe(24)
    _verified[verify_token] = {
        "phone_number": entry["phone_number"],
        "verified_at": datetime.utcnow(),
        "name": None,
        "address": None,
        "details_submitted": False,
    }
    return verify_token


def submit_details(verify_token: str, name: str, address: str) -> bool:
    """Phase 12 -- records the basic-details step (name + address) a
    verified visitor must complete before an area lookup is allowed.
    Returns False if the token doesn't exist or has expired (caller turns
    that into a 401, same as an invalid/expired OTP verify)."""
    entry = _verified.get(verify_token)
    if entry is None:
        return False
    if entry["verified_at"] + VERIFIED_TTL < datetime.utcnow():
        del _verified[verify_token]
        return False
    entry["name"] = name
    entry["address"] = address
    entry["details_submitted"] = True
    return True


def check_verified(verify_token: str, require_details: bool = False) -> bool:
    """True if this token came from a genuinely successful confirm_otp()
    call within the last VERIFIED_TTL. Used to gate GET /public/area-projects
    so the location lookup can't be reached by skipping the OTP step.

    require_details=True (Phase 12) additionally requires submit_details()
    to have been called on this token -- GET /public/area-projects passes
    this, so basic details are a real gate, not just a UI step the visitor
    could skip by calling the API directly."""
    entry = _verified.get(verify_token)
    if entry is None:
        return False
    if entry["verified_at"] + VERIFIED_TTL < datetime.utcnow():
        del _verified[verify_token]
        return False
    if require_details and not entry.get("details_submitted"):
        return False
    return True
