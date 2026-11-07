import secrets
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
)

router = APIRouter(prefix="/api/auth", tags=["Admin Authentication"])

# Login rate limiting: 5 attempts per 60 seconds per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_WINDOW_SECONDS = 60.0
_LOGIN_MAX_ATTEMPTS = 5


def check_login_rate_limit(client_ip: str) -> None:
    now = time.time()
    cutoff = now - _LOGIN_WINDOW_SECONDS
    _login_attempts[client_ip] = [t for t in _login_attempts[client_ip] if t > cutoff]
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
    """
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
    elif request.client and request.client.host:
        client_ip = request.client.host.strip()
    else:
        client_ip = "unknown"
    check_login_rate_limit(client_ip)

    configured_token = (settings.ADMIN_RESET_TOKEN or "").strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin authentication is not configured on this server."
        )

    supplied = (body.token or body.password or "").strip()
    if not supplied or not secrets.compare_digest(supplied, configured_token):
        record_failed_login(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrative credentials."
        )

    record_successful_login(client_ip)
    session_token = create_admin_session_token()
    is_prod = settings.ENVIRONMENT == "production"
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=(is_prod and is_https) or is_prod,
        path="/",
    )

    return {
        "status": "success",
        "message": "Admin session established successfully.",
        "authenticated": True,
        "session_token": session_token,
    }


@router.post("/logout")
def admin_logout(response: Response):
    """Terminate the current admin session and delete the session cookie."""
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
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
