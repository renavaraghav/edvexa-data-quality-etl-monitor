import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit
from fastapi import HTTPException, Request

from app.config import settings

SESSION_COOKIE_NAME = "edvexa_admin_session"
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours
_SESSION_VERSION = "admin-v2"
_DEV_SIGNING_KEY = secrets.token_urlsafe(32)


def get_secret_key() -> str:
    """
    Retrieve the application secret key strictly for HMAC session signing.
    Under the principle of credential separation, SECRET_KEY never falls back
    to ADMIN_RESET_TOKEN or any other machine credential.
    Fails closed in production if unset.
    """
    key = (os.getenv("SECRET_KEY") or (settings.SECRET_KEY or "")).strip()
    if not key:
        if settings.ENVIRONMENT == "production":
            raise RuntimeError(
                "CRITICAL SECURITY CONFIGURATION ERROR: SECRET_KEY must be set in production for session signing. "
                "Application fails closed to prevent unauthorized access. "
                "Do NOT use ADMIN_RESET_TOKEN for session signing."
            )
        return _DEV_SIGNING_KEY
    return key


def create_admin_session_token() -> str:
    """Generate a cryptographically signed admin session token."""
    now = int(time.time())
    nonce = secrets.token_hex(16)
    payload = f"{_SESSION_VERSION}:{now}:{nonce}"
    signature = hmac.new(
        get_secret_key().encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_admin_session_token(token: str | None) -> bool:
    """Verify HMAC signature and timestamp freshness of a session token."""
    if not token or "." not in token:
        return False

    try:
        payload, signature = token.rsplit(".", 1)
        expected_sig = hmac.new(
            get_secret_key().encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not secrets.compare_digest(signature, expected_sig):
            return False

        parts = payload.split(":")
        if len(parts) != 3 or parts[0] != _SESSION_VERSION:
            return False

        created_at = int(parts[1])
        if not 0 <= time.time() - created_at <= SESSION_MAX_AGE_SECONDS:
            return False

        return True
    except Exception:
        return False


def has_admin_header_session(request: Request) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    return (scheme.lower() == "bearer" and verify_admin_session_token(value.strip())) or verify_admin_session_token(
        request.headers.get("x-admin-session-token", "").strip()
    )


def is_authenticated_admin(request: Request) -> bool:
    """
    Check if the incoming request carries a valid admin session cookie,
    Bearer token, or X-Admin-Session-Token.
    Note: Under the principle of least privilege, machine reset tokens
    (X-Admin-Reset-Token) DO NOT grant broad administrative access to
    general endpoints (see is_authorized_reset_client for reset scoping).
    """
    return has_admin_header_session(request) or verify_admin_session_token(request.cookies.get(SESSION_COOKIE_NAME))


def token_matches(supplied: str | None, expected: str | None) -> bool:
    return bool(supplied and expected and secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8")))


def has_reset_credential(request: Request) -> bool:
    return token_matches(request.headers.get("X-Admin-Reset-Token"), settings.ADMIN_RESET_TOKEN)


def is_authorized_reset_client(request: Request) -> bool:
    """
    Check if the incoming request is authorized specifically for destructive reset operations.
    Accepts:
      1. Full authenticated admin session (cookie, Bearer, session token).
      2. Scoped machine reset token in the X-Admin-Reset-Token header.
    """
    return is_authenticated_admin(request) or has_reset_credential(request)


def _origin(value, *, referer=False):
    if not value or any(c.isspace() for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username is not None:
            return None
        if not referer and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
            return None
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        return parsed.scheme, parsed.hostname.casefold(), port
    except ValueError:
        return None


def enforce_browser_origin(request: Request, *, required=True, use_configured=True):
    """Use ASGI's trusted scheme and Host, or an explicitly configured public origin."""
    label = "Origin" if "origin" in request.headers else "Referer"
    supplied = request.headers.get(label)
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site == "cross-site":
        raise HTTPException(403, "CSRF protection: cross-site request blocked.")
    if supplied is None and not required:
        return
    actual = _origin(supplied, referer=label == "Referer")
    target = _origin(f"{request.url.scheme}://{request.url.netloc}")
    configured_origins = settings.ADMIN_ALLOWED_ORIGINS if use_configured else []
    configured = {_origin(value) for value in configured_origins} - {None}
    allowed = {target} if not configured_origins or target in configured else {
        origin for origin in configured if target and origin[0] == target[0]
    }
    if not actual or actual not in allowed:
        raise HTTPException(403, f"CSRF protection: a trusted {label} is required.")


def enforce_cookie_csrf(request: Request):
    if (request.method in {"POST", "PUT", "PATCH", "DELETE"} and not has_admin_header_session(request)
            and verify_admin_session_token(request.cookies.get(SESSION_COOKIE_NAME))):
        enforce_browser_origin(request)
