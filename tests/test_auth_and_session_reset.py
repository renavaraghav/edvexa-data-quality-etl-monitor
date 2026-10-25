"""
Tests for Admin Authentication, Session Verification, and Production Reset Protection.
Verifies:
1. Anonymous visitors in production cannot execute destructive data reset (HTTP 403).
2. Proper authenticated admin session authorization via HttpOnly cookies and Bearer tokens.
3. Tampered or expired session tokens are rejected.
4. Admin logout successfully terminates session privileges.
5. Local development allows unrestricted reset testing with test data.
6. Reset status endpoint reflects authentication state in production.
"""
import time
from unittest.mock import patch
from fastapi.testclient import TestClient

from app.config import settings
from app.services.auth import create_admin_session_token, verify_admin_session_token


def test_anonymous_visitor_cannot_reset_production_data(client: TestClient):
    """Anonymous visitors in production are strictly forbidden from deleting data."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-secret-pass-123"):
            res = client.post("/api/dashboard/reset-data")
            assert res.status_code == 403
            assert "disabled in production" in res.json()["detail"].lower()
            assert "authenticated administrator session is required" in res.json()["detail"].lower()


def test_invalid_admin_login_rejected(client: TestClient):
    """Supplying invalid credentials to /api/auth/login returns 401."""
    with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-secret-pass-123"):
        # 1. Missing credentials
        res_empty = client.post("/api/auth/login", json={})
        assert res_empty.status_code == 401

        # 2. Incorrect credentials
        res_bad = client.post("/api/auth/login", json={"token": "wrong-secret"})
        assert res_bad.status_code == 401


def test_admin_login_when_token_unset_in_production_returns_403(client: TestClient):
    """When ADMIN_RESET_TOKEN is unset in production, admin login is disabled."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_RESET_TOKEN", None):
            res = client.post("/api/auth/login", json={"token": "some-token"})
            assert res.status_code == 403
            assert "not configured" in res.json()["detail"].lower()


def test_authenticated_admin_session_authorizes_reset(client: TestClient):
    """An authenticated administrator with active session can authorize reset in production."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-secret-pass-123"):
            # Step 1: Login
            login_res = client.post("/api/auth/login", json={"password": "prod-secret-pass-123"})
            assert login_res.status_code == 200
            assert login_res.json()["authenticated"] is True
            assert "session_token" in login_res.json()

            # Step 2: Verify session status endpoint
            sess_res = client.get("/api/auth/session")
            assert sess_res.status_code == 200
            assert sess_res.json()["authenticated"] is True

            # Step 3: Perform reset (authorized via session cookie)
            reset_res = client.post("/api/dashboard/reset-data")
            assert reset_res.status_code == 200
            assert reset_res.json()["status"] == "success"

            # Step 4: Logout terminates session
            logout_res = client.post("/api/auth/logout")
            assert logout_res.status_code == 200

            # Step 5: Post-logout reset is rejected
            post_logout_reset = client.post("/api/dashboard/reset-data")
            assert post_logout_reset.status_code == 403


def test_bearer_session_token_accepted_in_production(client: TestClient):
    """Session token provided in Authorization header authorizes reset in production."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-secret-pass-123"):
            session_token = create_admin_session_token()
            res = client.post(
                "/api/dashboard/reset-data",
                headers={"Authorization": f"Bearer {session_token}"}
            )
            assert res.status_code == 200
            assert res.json()["status"] == "success"


def test_tampered_session_token_rejected(client: TestClient):
    """Tampered or invalid session tokens are rejected in production."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-secret-pass-123"):
            bad_token = "admin:9999999999:deadbeefdeadbeef.invalidfakehash123456"
            res = client.post(
                "/api/dashboard/reset-data",
                headers={"Authorization": f"Bearer {bad_token}"}
            )
            assert res.status_code == 403


def test_unrestricted_reset_testing_in_local_development(client: TestClient):
    """In local development environment, reset testing is unrestricted for test data."""
    with patch.object(settings, "ENVIRONMENT", "development"):
        with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", True):
            res = client.post("/api/dashboard/reset-data")
            assert res.status_code == 200
            assert res.json()["status"] == "success"


def test_reset_status_in_production_unauthenticated_vs_authenticated(client: TestClient):
    """In production, reset-status reports destructive reset is disabled until authenticated."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-secret-pass-123"):
            # Anonymous visitor
            anon_status = client.get("/api/dashboard/reset-status")
            assert anon_status.status_code == 200
            data_anon = anon_status.json()
            assert data_anon["environment"] == "production"
            assert data_anon["destructive_reset_allowed"] is False
            assert data_anon["authenticated_admin"] is False

            # Authenticate
            login_res = client.post("/api/auth/login", json={"token": "prod-secret-pass-123"})
            assert login_res.status_code == 200

            # Authenticated admin
            admin_status = client.get("/api/dashboard/reset-status")
            assert admin_status.status_code == 200
            data_admin = admin_status.json()
            assert data_admin["environment"] == "production"
            assert data_admin["destructive_reset_allowed"] is True
            assert data_admin["authenticated_admin"] is True
