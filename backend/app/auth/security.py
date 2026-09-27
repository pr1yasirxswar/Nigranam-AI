"""
Phase 8 bug fix (Implementation-Guide.md Phase 8 item 6) -- password
hashing for the new demo login. Salted SHA-256 via stdlib hashlib, not
bcrypt/argon2/passlib -- those aren't in requirements.txt and Rules.md
says not to add new dependencies without asking first. Documented,
honest pre-production placeholder, same pattern as the X-User-Id scheme
and the Phase 11 mock OTP -- do not present this as production-grade
password storage.
"""
import hashlib
import hmac
import os

# Static pepper is fine for a hackathon demo secret, not a production
# secret-management story -- flagged here, not hidden.
_PEPPER = os.environ.get("SENTINEL_PASSWORD_PEPPER", "sentinel-demo-pepper-2026")
_ITERATIONS = 100_000


def hash_password(plain: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", plain.encode() + _PEPPER.encode(), salt, _ITERATIONS)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(plain: str, stored: str | None) -> bool:
    if not stored or "$" not in stored:
        return False
    salt_hex, digest_hex = stored.split("$", 1)
    try:
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", plain.encode() + _PEPPER.encode(), salt, _ITERATIONS)
    return hmac.compare_digest(candidate, expected)
