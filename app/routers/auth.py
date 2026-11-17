import time
from collections import defaultdict
from fastapi import APIRouter, Request, Response, HTTPException, status
from pydantic import BaseModel

from app.config import settings
from app.services.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    create_admin_session_token,
    is_authenticated_admin,
    enforce_browser_origin, enforce_cookie_csrf, token_matches, get_secret_key,
)

router = APIRouter(prefix="/api/auth", tags=["Admin Authentication"])

# Login rate limiting: 5 attempts per 60 seconds per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_ATTEMPTS = 5


def check_login_rate_limit(client_ip: str) -> None:
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    _login_attempts[client_ip] = [t for t in _login_attempts.get(client_ip, []) if t > cutoff]
    if len(_login_attempts[client_ip]) >= _LOGIN_MAX_ATTEMPTS:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many failed login attempts from this IP. Please wait {int(_LOGIN_WINDOW_SECONDS)} seconds before retrying.",
            headers={"Retry-After": str(int(_LOGIN_WINDOW_SECONDS))},
        )


def record_failed_login(client_ip: str) -> None:
    _login_attempts[client_ip].append(time.time())


def record_successful_login(client_ip: str) -> None:
    _login_attempts.pop(client_ip, None)


class AdminLoginRequest(BaseModel):
    token: str | None = None
    password: str | None = None


@router.post("/login")
def admin_login(body: AdminLoginRequest, request: Request, response: Response):
    """
    Authenticate an administrator and establish a secure, HttpOnly session cookie.
    Enforces IP-based rate limiting (5 attempts/minute) and fails closed when unconfigured.
    Under least-privilege session security, credentials are NEVER exposed in the JSON response body.
    """
    enforce_browser_origin(request, required=False)
    # Uvicorn only normalizes forwarded addresses from configured, trusted peers.
    client_ip = request.client.host if request.client else "unknown"
    check_login_rate_limit(client_ip)

    password = settings.ADMIN_LOGIN_PASSWORD
    if not password or token_matches(password, settings.ADMIN_RESET_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin authentication is not configured on this server."
        )

    supplied = body.password or body.token  # Legacy JSON field alias for the login password.
    if not token_matches(supplied, password):
        record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrative credentials."
        )

    try:
        get_secret_key()
    except RuntimeError as exc:
        raise HTTPException(403, "Admin authentication is not configured on this server.") from exc
    record_successful_login(client_ip)
    session_token = create_admin_session_token()
    is_prod = settings.ENVIRONMENT == "production"
    is_https = request.url.scheme == "https"

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=is_prod or is_https,
        path="/",
    )

    return {
        "status": "success",
        "message": "Admin session established successfully.",
        "authenticated": True,
    }


@router.post("/logout")
def admin_logout(request: Request, response: Response):
    """Terminate the current admin session and delete the session cookie."""
    enforce_cookie_csrf(request)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", secure=settings.ENVIRONMENT == "production" or request.url.scheme == "https")
    return {
        "status": "success",
        "message": "Admin session terminated.",
        "authenticated": False,
    }


@router.get("/session")
def get_session_status(request: Request):
    """Inspect current session authentication status."""
    return {
        "authenticated": is_authenticated_admin(request),
        "environment": settings.ENVIRONMENT,
    }
