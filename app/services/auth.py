import hashlib
import hmac
import os
import secrets
import time
from fastapi import Request

from app.config import settings

SESSION_COOKIE_NAME = "edvexa_admin_session"
SESSION_MAX_AGE_SECONDS = 86400  # 24 hours


def get_secret_key() -> str:
    """Retrieve or derive the application secret key for signing sessions."""
    return (
        os.getenv("SECRET_KEY")
        or settings.ADMIN_RESET_TOKEN
        or "edvexa-secure-session-signing-key-default"
    )


def create_admin_session_token() -> str:
    """Generate a cryptographically signed admin session token."""
    now = int(time.time())
    nonce = secrets.token_hex(16)
    payload = f"admin:{now}:{nonce}"
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
        if len(parts) != 3 or parts[0] != "admin":
            return False

        created_at = int(parts[1])
        if time.time() - created_at > SESSION_MAX_AGE_SECONDS:
            return False

        return True
    except Exception:
        return False


def is_authenticated_admin(request: Request) -> bool:
    """
    Check if the incoming request carries a valid admin session cookie,
    Bearer token, or verified administrative credential.
    """
    # 1. Check HttpOnly session cookie
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie_token and verify_admin_session_token(cookie_token):
        return True

    # 2. Check Authorization: Bearer <session_token>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        bearer_token = auth_header[len("Bearer "):].strip()
        if verify_admin_session_token(bearer_token):
            return True

    # 3. Check X-Admin-Session-Token
    session_header = request.headers.get("X-Admin-Session-Token", "").strip()
    if session_header and verify_admin_session_token(session_header):
        return True

    # 4. Check direct admin secret token for machine-to-machine admin automation
    configured_token = (settings.ADMIN_RESET_TOKEN or "").strip()
    if configured_token:
        direct_token = (
            request.headers.get("X-Admin-Reset-Token")
            or request.query_params.get("token")
            or ""
        ).strip()
        if direct_token and secrets.compare_digest(direct_token, configured_token):
            return True

    return False
