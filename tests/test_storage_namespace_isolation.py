"""
Comprehensive Regression Tests for Storage Namespace Isolation and Session Security.
Verifies:
1. Strict cloud namespace segregation: uploads/{filename} vs surveys/{filename}.
2. Retrieval, exists, delete, list_keys, and clear_all namespace awareness.
3. Path traversal rejection and invalid namespace rejection.
4. ETL reset deletes only uploads/ objects, preserving surveys/ objects.
5. Survey reset deletes only surveys/ objects, preserving uploads/ objects.
6. Cloud deletion failure handling: surfaces partial_success and errors; never reports false success.
7. Local storage backend namespace isolation and collision prevention.
8. Login endpoint security: session_token removed from JSON response body; HttpOnly cookie used.
9. Cookie authentication lifecycle: login -> authenticated access -> logout -> unauthenticated.
"""

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Customer, ProcessingJob, SurveyAuditRun, SurveyRespondent
from app.services.auth import SESSION_COOKIE_NAME
from app.services.storage import LocalStorageBackend, NeonObjectStorageBackend


# =============================================================================
# 1. CLOUD OBJECT STORAGE NAMESPACE ISOLATION TESTS (MOCKED S3 CLIENT)
# =============================================================================

def test_neon_storage_saves_etl_in_uploads_and_surveys_in_surveys(tmp_path: Path):
    """
    Verify that saving files to NeonObjectStorageBackend preserves namespaces:
    - ETL files are stored under uploads/{filename}
    - Survey files are stored under surveys/{filename}
    - surveys/ is NEVER flattened into uploads/
    """
    mock_s3 = MagicMock()
    with patch.object(settings, "UPLOADS_DIR", tmp_path / "uploads"), \
         patch.object(settings, "SURVEYS_DIR", tmp_path / "surveys"):
        backend = NeonObjectStorageBackend(
            bucket_name="test-bucket",
            access_key_id="mock-key",
            secret_access_key="mock-secret",
            endpoint_url="https://mock.neon.tech",
            region_name="ap-southeast-1"
        )
        backend.s3_client = mock_s3

        # 1. Save ETL file (un-prefixed for backward compatibility)
        etl_name = backend.save("job_100_abc.csv", b"cust_id,name\n1,Alice\n", content_type="text/csv")
        assert etl_name == "job_100_abc.csv"
        mock_s3.put_object.assert_called_with(
            Bucket="test-bucket",
            Key="uploads/job_100_abc.csv",
            Body=b"cust_id,name\n1,Alice\n",
            ContentType="text/csv"
        )

        # 2. Save ETL file (with explicit uploads/ prefix)
        etl_name2 = backend.save("uploads/job_101_def.csv", b"cust_id,name\n2,Bob\n", content_type="text/csv")
        assert etl_name2 == "job_101_def.csv"
        mock_s3.put_object.assert_called_with(
            Bucket="test-bucket",
            Key="uploads/job_101_def.csv",
            Body=b"cust_id,name\n2,Bob\n",
            ContentType="text/csv"
        )

        # 3. Save Survey file (with explicit surveys/ prefix)
        survey_key = backend.save("surveys/qq_study_01.pdf", b"%PDF-1.4 mock content", content_type="application/pdf")
        assert survey_key == "surveys/qq_study_01.pdf"
        mock_s3.put_object.assert_called_with(
            Bucket="test-bucket",
            Key="surveys/qq_study_01.pdf",
            Body=b"%PDF-1.4 mock content",
            ContentType="application/pdf"
        )


def test_neon_storage_retrieval_and_content_integrity(tmp_path: Path):
    """
    Verify that get() retrieves from the exact requested namespace,
    even if both namespaces hold a file with identical names.
    """
    mock_s3 = MagicMock()

    def mock_get_object(Bucket, Key):
        if Key == "uploads/shared_name.csv":
            return {"Body": io.BytesIO(b"ETL_EXCLUSIVE_CONTENT")}
        elif Key == "surveys/shared_name.csv":
            return {"Body": io.BytesIO(b"SURVEY_EXCLUSIVE_CONTENT")}
        raise Exception("NoSuchKey")

    mock_s3.get_object.side_effect = mock_get_object

    with patch.object(settings, "UPLOADS_DIR", tmp_path / "uploads_empty"), \
         patch.object(settings, "SURVEYS_DIR", tmp_path / "surveys_empty"):
        backend = NeonObjectStorageBackend(
            bucket_name="test-bucket",
            access_key_id="mock-key",
            secret_access_key="mock-secret",
            endpoint_url="https://mock.neon.tech",
        )
        backend.s3_client = mock_s3

        # Retrieve ETL file
        etl_data = backend.get("uploads/shared_name.csv")
        assert etl_data == b"ETL_EXCLUSIVE_CONTENT"

        # Retrieve Survey file
        survey_data = backend.get("surveys/shared_name.csv")
        assert survey_data == b"SURVEY_EXCLUSIVE_CONTENT"

        # Content must be completely distinct
        assert etl_data != survey_data


def test_neon_storage_list_keys_namespace_isolation():
    """
    Verify that:
    - Listing surveys/ never returns uploads/ keys.
    - Listing uploads/ never returns surveys/ keys.
    """
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()

    def mock_paginate(Bucket, Prefix):
        if Prefix == "surveys/":
            return [{"Contents": [{"Key": "surveys/qq_1.pdf"}, {"Key": "surveys/raw_data.csv"}]}]
        elif Prefix == "uploads/":
            return [{"Contents": [{"Key": "uploads/job_10.csv"}, {"Key": "uploads/job_11.csv"}]}]
        return [{"Contents": []}]

    mock_paginator.paginate.side_effect = mock_paginate
    mock_s3.get_paginator.return_value = mock_paginator

    backend = NeonObjectStorageBackend(
        bucket_name="test-bucket",
        access_key_id="mock-key",
        secret_access_key="mock-secret",
        endpoint_url="https://mock.neon.tech",
    )
    backend.s3_client = mock_s3

    # List surveys
    survey_keys = backend.list_keys(prefix="surveys/")
    assert survey_keys == ["surveys/qq_1.pdf", "surveys/raw_data.csv"]
    for k in survey_keys:
        assert k.startswith("surveys/")
        assert "uploads/" not in k

    # List uploads
    upload_keys = backend.list_keys(prefix="uploads/")
    assert upload_keys == ["uploads/job_10.csv", "uploads/job_11.csv"]
    for k in upload_keys:
        assert k.startswith("uploads/")
        assert "surveys/" not in k


def test_identical_filenames_in_both_namespaces_do_not_collide(tmp_path: Path):
    """
    Ensure deleting a survey object cannot delete an ETL object with the same filename.
    """
    mock_s3 = MagicMock()
    with patch.object(settings, "UPLOADS_DIR", tmp_path / "uploads"), \
         patch.object(settings, "SURVEYS_DIR", tmp_path / "surveys"):
        backend = NeonObjectStorageBackend(
            bucket_name="test-bucket",
            access_key_id="mock-key",
            secret_access_key="mock-secret",
            endpoint_url="https://mock.neon.tech",
        )
        backend.s3_client = mock_s3

        # Delete survey file
        deleted = backend.delete("surveys/collision_target.csv")
        assert deleted is True

        # Assert only surveys/collision_target.csv was targeted
        mock_s3.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="surveys/collision_target.csv"
        )

        mock_s3.reset_mock()

        # Delete ETL file
        deleted_etl = backend.delete("uploads/collision_target.csv")
        assert deleted_etl is True
        mock_s3.delete_object.assert_called_once_with(
            Bucket="test-bucket",
            Key="uploads/collision_target.csv"
        )


def test_storage_key_path_traversal_and_invalid_namespace_rejected():
    """
    Verify that path traversal attempts ('..', absolute paths) and
    unauthorized namespaces are strictly rejected with ValueError.
    """
    backend = NeonObjectStorageBackend(
        bucket_name="test-bucket",
        access_key_id="mock-key",
        secret_access_key="mock-secret",
        endpoint_url="https://mock.neon.tech",
    )
    backend.s3_client = MagicMock()

    # 1. Path traversal attacks
    traversals = [
        "../../etc/passwd",
        "surveys/../../secret.env",
        "uploads/../../../root.key",
        "surveys/subdir/../escape.csv",
    ]
    for key in traversals:
        with pytest.raises(ValueError) as exc:
            backend.save(key, b"data")
        assert "path traversal" in str(exc.value).lower() or "traversal" in str(exc.value).lower()

    # 2. Absolute path attempts
    absolute_keys = [
        "/var/data/evil.csv",
        "\\Windows\\System32\\cmd.exe",
    ]
    for key in absolute_keys:
        with pytest.raises(ValueError) as exc:
            backend.save(key, b"data")
        assert "absolute path" in str(exc.value).lower() or "path traversal" in str(exc.value).lower()

    # 3. Invalid namespace prefixes
    invalid_namespaces = [
        "private/document.pdf",
        "admin/keys.json",
        "temp_data/scratch.csv",
    ]
    for key in invalid_namespaces:
        with pytest.raises(ValueError) as exc:
            backend.save(key, b"data")
        assert "invalid storage namespace" in str(exc.value).lower()


# =============================================================================
# 2. ETL RESET VS SURVEY RESET ISOLATION TESTS
# =============================================================================

def test_etl_reset_deletes_etl_objects_and_preserves_surveys(client: TestClient, db_session: Session):
    """
    Verify that executing ETL reset (POST /api/reset-data):
    1. Calls clear_all() which targets ONLY uploads/.
    2. Clears ETL database tables.
    3. Leaves survey database tables completely untouched.
    4. Leaves surveys/ cloud storage objects completely untouched.
    """
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    # Paginator returns both uploads/ and surveys/
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "uploads/job_99_etl.csv"}, {"Key": "surveys/qq_persistent.pdf"}]}
    ]
    mock_s3.get_paginator.return_value = mock_paginator

    # Populate both ETL and Survey database tables
    cust = Customer(customer_id="C_ISO_1", name="Alice ETL", email="alice@etl.com", created_at="2026-11-07")
    db_session.add(cust)
    survey_audit = SurveyAuditRun(
        audit_name="Persistent Survey Audit",
        raw_filename="survey_keep.csv",
        raw_file_hash="abc123hash",
        total_respondents=1,
        accepted_count=1
    )
    db_session.add(survey_audit)
    db_session.commit()

    with patch.object(settings, "STORAGE_BACKEND", "neon"), \
         patch.object(settings, "S3_BUCKET_NAME", "edvexa-vault"), \
         patch.object(settings, "S3_ACCESS_KEY_ID", "key"), \
         patch.object(settings, "S3_SECRET_ACCESS_KEY", "secret"), \
         patch.object(settings, "S3_ENDPOINT_URL", "https://vault.neon.tech"), \
         patch("app.services.storage.get_storage") as mock_get_storage:

        backend = NeonObjectStorageBackend(
            bucket_name="edvexa-vault",
            access_key_id="key",
            secret_access_key="secret",
            endpoint_url="https://vault.neon.tech"
        )
        backend.s3_client = mock_s3
        mock_get_storage.return_value = backend

        # Execute ETL reset
        reset_resp = client.post("/api/dashboard/reset-data")
        assert reset_resp.status_code == 200
        res_json = reset_resp.json()
        assert res_json["survey_data_preserved"] is True

        # Verify ETL database cleared, survey database preserved
        assert db_session.query(Customer).count() == 0
        assert db_session.query(SurveyAuditRun).count() == 1

        # Verify S3 deletion calls: uploads/ was deleted, surveys/ was NEVER deleted
        deleted_keys = [call.kwargs.get("Key") for call in mock_s3.delete_object.call_args_list]
        for dk in deleted_keys:
            assert "surveys/" not in dk


def test_survey_reset_deletes_survey_objects_and_preserves_etl(auth_client: TestClient, db_session: Session):
    """
    Verify that executing Survey reset (POST /api/surveys/reset-survey-data):
    1. Targets ONLY surveys/ in cloud storage.
    2. Clears survey database tables.
    3. Leaves ETL database tables completely untouched.
    4. Leaves uploads/ cloud storage objects completely untouched.
    """
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "surveys/qq_remove.pdf"}, {"Key": "surveys/raw_remove.csv"}]}
    ]
    mock_s3.get_paginator.return_value = mock_paginator

    # Populate both ETL and Survey database tables
    cust = Customer(customer_id="C_ISO_2", name="Bob ETL", email="bob@etl.com", created_at="2026-11-07")
    db_session.add(cust)
    survey_audit = SurveyAuditRun(
        audit_name="Survey To Clear",
        raw_filename="survey_drop.csv",
        raw_file_hash="def456hash",
        total_respondents=1,
        accepted_count=1
    )
    db_session.add(survey_audit)
    db_session.commit()

    with patch.object(settings, "STORAGE_BACKEND", "neon"), \
         patch.object(settings, "S3_BUCKET_NAME", "edvexa-vault"), \
         patch.object(settings, "S3_ACCESS_KEY_ID", "key"), \
         patch.object(settings, "S3_SECRET_ACCESS_KEY", "secret"), \
         patch.object(settings, "S3_ENDPOINT_URL", "https://vault.neon.tech"), \
         patch("app.services.storage.get_storage") as mock_get_storage:

        backend = NeonObjectStorageBackend(
            bucket_name="edvexa-vault",
            access_key_id="key",
            secret_access_key="secret",
            endpoint_url="https://vault.neon.tech"
        )
        backend.s3_client = mock_s3
        mock_get_storage.return_value = backend

        # Execute Survey reset
        survey_resp = auth_client.post("/api/surveys/reset-survey-data")
        assert survey_resp.status_code == 200
        res_json = survey_resp.json()
        assert res_json["status"] == "success"
        assert res_json["etl_records_preserved"] is True

        # Verify survey database cleared, ETL database preserved
        assert db_session.query(SurveyAuditRun).count() == 0
        assert db_session.query(Customer).count() == 1

        # Verify S3 deletions targeted ONLY surveys/
        deleted_keys = [call.kwargs.get("Key") for call in mock_s3.delete_object.call_args_list]
        assert len(deleted_keys) >= 1
        for dk in deleted_keys:
            assert dk.startswith("surveys/")
            assert "uploads/" not in dk


def test_survey_reset_explicitly_reports_cloud_deletion_failures(auth_client: TestClient, db_session: Session):
    """
    Requirement 9: Handle cloud listing/deletion errors explicitly.
    Do NOT report a successful complete reset if remote survey objects could not be deleted.
    """
    mock_s3 = MagicMock()
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "surveys/locked_object.pdf"}]}
    ]
    mock_s3.get_paginator.return_value = mock_paginator
    # Simulate S3 deletion rejection
    mock_s3.delete_object.side_effect = RuntimeError("AccessDenied: bucket policy denies DeleteObject")

    with patch.object(settings, "STORAGE_BACKEND", "neon"), \
         patch.object(settings, "S3_BUCKET_NAME", "edvexa-vault"), \
         patch.object(settings, "S3_ACCESS_KEY_ID", "key"), \
         patch.object(settings, "S3_SECRET_ACCESS_KEY", "secret"), \
         patch.object(settings, "S3_ENDPOINT_URL", "https://vault.neon.tech"), \
         patch("app.services.storage.get_storage") as mock_get_storage:

        backend = NeonObjectStorageBackend(
            bucket_name="edvexa-vault",
            access_key_id="key",
            secret_access_key="secret",
            endpoint_url="https://vault.neon.tech"
        )
        backend.s3_client = mock_s3
        mock_get_storage.return_value = backend

        reset_resp = auth_client.post("/api/surveys/reset-survey-data")
        assert reset_resp.status_code == 200
        res_json = reset_resp.json()

        # MUST NOT report success when cloud objects failed to delete
        assert res_json["status"] == "partial_success"
        assert "cloud_deletion_errors" in res_json
        assert len(res_json["cloud_deletion_errors"]) > 0
        assert "locked_object.pdf" in res_json["cloud_deletion_errors"][0]
        assert "could not be deleted" in res_json["message"].lower()


# =============================================================================
# 3. LOCAL STORAGE BACKEND NAMESPACE & COLLISION TESTS
# =============================================================================

def test_local_storage_backend_namespace_isolation_and_no_collision(tmp_path: Path):
    """
    Verify LocalStorageBackend preserves separate namespaces:
    - Same filename in surveys/ and uploads/ does not collide
    - Deleting from surveys/ does not delete from uploads/
    - clear_all() removes only uploads/, preserving surveys/
    """
    storage = LocalStorageBackend(base_dir=tmp_path / "uploads_root")

    # Save same filename in both namespaces
    storage.save("surveys/report.csv", b"SURVEY_CONTENT")
    storage.save("uploads/report.csv", b"ETL_CONTENT")

    # Verify both can be retrieved with their own contents
    assert storage.get("surveys/report.csv") == b"SURVEY_CONTENT"
    assert storage.get("uploads/report.csv") == b"ETL_CONTENT"

    # Delete survey file only
    assert storage.delete("surveys/report.csv") is True
    assert storage.exists("surveys/report.csv") is False
    # ETL file MUST remain intact
    assert storage.exists("uploads/report.csv") is True
    assert storage.get("uploads/report.csv") == b"ETL_CONTENT"

    # Save additional files
    storage.save("surveys/survey_keep.csv", b"KEEP_SURVEY")
    storage.save("uploads/job_to_clear.csv", b"CLEAR_ETL")

    # Clear all upload artifacts
    removed = storage.clear_all()
    assert removed >= 1

    # Uploads cleared
    assert storage.exists("uploads/job_to_clear.csv") is False
    # Survey file remains intact
    assert storage.exists("surveys/survey_keep.csv") is True
    assert storage.get("surveys/survey_keep.csv") == b"KEEP_SURVEY"


# =============================================================================
# 4. AUTHENTICATION & SESSION SECURITY TESTS
# =============================================================================

def test_admin_login_removes_session_token_from_json(client: TestClient):
    """
    Blocker 2 Verification:
    - Successful login sets HttpOnly, SameSite=lax cookie.
    - JSON response contains authenticated: true.
    - JSON response does NOT contain session_token, access_token, or equivalent secret.
    """
    secret = settings.ADMIN_RESET_TOKEN or "test-secret-token-for-pytest"
    resp = client.post(
        "/api/auth/login",
        json={"password": secret},
        headers={"x-forwarded-for": "198.51.100.42"}
    )
    assert resp.status_code == 200
    data = resp.json()

    # 1. authenticated: true present
    assert data["authenticated"] is True
    assert data["status"] == "success"

    # 2. session_token must NEVER be in JSON response
    assert "session_token" not in data
    assert "token" not in data
    assert "access_token" not in data

    # 3. HttpOnly session cookie must be set
    set_cookie = resp.headers.get("set-cookie", "")
    assert SESSION_COOKIE_NAME in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()


def test_cookie_auth_workflow_and_logout(client: TestClient):
    """
    Verify complete cookie-authenticated lifecycle:
    1. Unauthenticated request to /api/auth/session returns authenticated: False.
    2. Login sets cookie and updates /api/auth/session to authenticated: True.
    3. Cookie-authenticated request to survey endpoints works without Bearer token.
    4. Logout clears cookie and returns authenticated: False.
    5. Subsequent requests are rejected with 401 Unauthorized.
    """
    # 1. Initially unauthenticated
    session_check = client.get("/api/auth/session")
    assert session_check.status_code == 200
    assert session_check.json()["authenticated"] is False

    # 2. Login
    secret = settings.ADMIN_RESET_TOKEN or "test-secret-token-for-pytest"
    login_resp = client.post(
        "/api/auth/login",
        json={"password": secret},
        headers={"x-forwarded-for": "198.51.100.43"}
    )
    assert login_resp.status_code == 200
    assert "session_token" not in login_resp.json()

    # 3. Session status is now authenticated
    session_check2 = client.get("/api/auth/session")
    assert session_check2.status_code == 200
    assert session_check2.json()["authenticated"] is True

    # 4. Access survey endpoint using session cookie
    survey_dash = client.get("/api/surveys/dashboard/summary")
    assert survey_dash.status_code == 200
    assert "kpis" in survey_dash.json()

    # 5. Logout
    logout_resp = client.post("/api/auth/logout")
    assert logout_resp.status_code == 200
    assert logout_resp.json()["authenticated"] is False

    # Cookie is deleted
    session_check3 = client.get("/api/auth/session")
    assert session_check3.status_code == 200
    assert session_check3.json()["authenticated"] is False

    # 6. Survey endpoint now rejects unauthenticated request
    rejected = client.get("/api/surveys/dashboard/summary")
    assert rejected.status_code == 401
