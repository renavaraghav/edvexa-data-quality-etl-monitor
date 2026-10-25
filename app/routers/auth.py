import secrets
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


class AdminLoginRequest(BaseModel):
    token: str | None = None
    password: str | None = None


@router.post("/login")
def admin_login(body: AdminLoginRequest, request: Request, response: Response):
    """
    Authenticate an administrator and establish a secure, HttpOnly session cookie.
    """
    configured_token = (settings.ADMIN_RESET_TOKEN or "").strip()
    if not configured_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin authentication is not configured on this server."
        )

    supplied = (body.token or body.password or "").strip()
    if not supplied or not secrets.compare_digest(supplied, configured_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid administrative credentials."
        )

    session_token = create_admin_session_token()
    is_prod = settings.ENVIRONMENT == "production"
    is_https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"

    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=session_token,
        max_age=SESSION_MAX_AGE_SECONDS,
        httponly=True,
        samesite="lax",
        secure=(is_prod and is_https),
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
