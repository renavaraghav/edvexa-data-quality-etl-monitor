"""
Tests for Cloud Deployment, Storage Abstraction, and Reset Security.
Verifies:
1. normalize_database_url and sanitize_database_url for Neon PostgreSQL and SQLite.
2. LocalStorageBackend operations and traversal resistance.
3. R2StorageBackend operations with S3 mock.
4. Storage singleton factory and helper functions.
5. GET /api/dashboard/reset-status endpoint.
6. POST /api/dashboard/reset-data authorization guards in production and development.
"""
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import (
    Settings,
    normalize_database_url,
    sanitize_database_url,
    settings,
)
from app.services.storage import (
    LocalStorageBackend,
    NeonObjectStorageBackend,
    R2StorageBackend,
    S3StorageBackend,
    generate_artifact_key,
    get_storage,
    reset_storage_instance,
    save_upload_artifact,
    get_upload_artifact,
    delete_upload_artifact,
    clear_all_upload_artifacts,
)


# =====================================================================
# 1. DATABASE URL NORMALIZATION & SANITIZATION TESTS
# =====================================================================

def test_normalize_database_url_converts_postgres_scheme():
    """Verify legacy postgres:// scheme is transformed to postgresql://."""
    raw = "postgres://user:pass@ep-cool-fog-123456.us-east-2.aws.neon.tech/neondb"
    normalized = normalize_database_url(raw)
    assert normalized.startswith("postgresql://")
    assert "sslmode=require" in normalized


def test_normalize_database_url_preserves_postgresql_and_adds_ssl():
    """Verify postgresql:// without sslmode gains sslmode=require for Neon."""
    raw = "postgresql://user:pass@ep-cool-fog-123456.us-east-2.aws.neon.tech/neondb"
    normalized = normalize_database_url(raw)
    assert normalized.startswith("postgresql://")
    assert normalized.endswith("?sslmode=require")


def test_normalize_database_url_leaves_existing_sslmode():
    """Verify explicit sslmode=disable or verify-full is not duplicated or overwritten."""
    raw = "postgresql://user:pass@localhost:5432/testdb?sslmode=disable"
    normalized = normalize_database_url(raw)
    assert normalized.count("sslmode=") == 1
    assert "sslmode=disable" in normalized


def test_normalize_database_url_preserves_sqlite():
    """Verify SQLite URLs are untouched and empty URLs fall back to default SQLite."""
    raw = "sqlite:///./edvexa_data.db"
    assert normalize_database_url(raw) == raw
    assert normalize_database_url("").startswith("sqlite:///")
    assert normalize_database_url(None).startswith("sqlite:///")


def test_sanitize_database_url_redacts_credentials():
    """Verify passwords in connection strings are hidden in logs."""
    raw = "postgresql://neon_admin:SuperSecretP@ssword123@ep-sample-999.neon.tech/neondb?sslmode=require"
    sanitized = sanitize_database_url(raw)
    assert "SuperSecretP@ssword123" not in sanitized
    assert "neon_admin:****@" in sanitized
    assert "ep-sample-999.neon.tech/neondb" in sanitized


def test_sanitize_database_url_handles_sqlite():
    """Verify sqlite paths are returned intact."""
    sqlite_url = "sqlite:///C:/test/path/edvexa.db"
    assert sanitize_database_url(sqlite_url) == sqlite_url


# =====================================================================
# 2. LOCAL STORAGE BACKEND TESTS
# =====================================================================

def test_local_storage_backend_crud(tmp_path: Path):
    """Verify save, exists, get, list_keys, delete, and clear_all on LocalStorageBackend."""
    storage = LocalStorageBackend(base_dir=tmp_path)
    content = b"customer_id,name,email\nC1,Alice,alice@example.com\n"
    key = "job_42_abcdef123456.csv"

    # Save
    saved_key = storage.save(key, content)
    assert saved_key == key
    assert (tmp_path / key).exists()

    # Exists & Get
    assert storage.exists(key) is True
    assert storage.get(key) == content

    # List keys
    keys = storage.list_keys()
    assert key in keys

    # Delete
    assert storage.delete(key) is True
    assert storage.exists(key) is False

    # Get non-existent raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        storage.get("non_existent_file.csv")


def test_local_storage_path_traversal_protection(tmp_path: Path):
    """Verify malicious filenames with path traversal cannot escape base_dir."""
    storage = LocalStorageBackend(base_dir=tmp_path)
    content = b"malicious content"

    # Attempt directory traversal
    traversal_key = "../../evil_outside.csv"
    saved_key = storage.save(traversal_key, content)

    # Saved key must be sanitized to basename only
    assert saved_key == "evil_outside.csv"
    assert (tmp_path / "evil_outside.csv").exists()
    # Parent directory must NOT contain evil_outside.csv
    assert not (tmp_path.parent / "evil_outside.csv").exists()


def test_local_storage_clear_all(tmp_path: Path):
    """Verify clear_all deletes all files in directory and returns accurate count."""
    storage = LocalStorageBackend(base_dir=tmp_path)
    storage.save("f1.csv", b"1")
    storage.save("f2.csv", b"2")
    storage.save("f3.csv", b"3")

    assert len(storage.list_keys()) == 3
    deleted_count = storage.clear_all()
    assert deleted_count == 3
    assert len(storage.list_keys()) == 0


# =====================================================================
# 3. NEON OBJECT STORAGE & S3 BACKEND TESTS (MOCKED BOTO3 S3 CLIENT)
# =====================================================================

def test_neon_object_storage_backend_mocked(tmp_path: Path):
    """Verify Neon Object Storage interactions with mocked boto3 S3 client."""
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": BytesIO(b"customer_id,name\nC1,Alice\n")
    }
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "uploads/job_10_abc.csv"}, {"Key": "uploads/job_11_def.csv"}]}
    ]
    mock_s3.get_paginator.return_value = mock_paginator

    with patch.object(settings, "UPLOADS_DIR", tmp_path):
        backend = NeonObjectStorageBackend(
            bucket_name="edvexa-uploads",
            access_key_id="neon-access-key-123",
            secret_access_key="neon-secret-key-456",
            endpoint_url="https://br-cool-branch.storage.ap-southeast-1.aws.neon.tech",
            region_name="ap-southeast-1"
        )
        backend.s3_client = mock_s3

        # Test save
        saved_name = backend.save("job_10_abc.csv", b"sample csv content", content_type="text/csv")
        assert saved_name == "job_10_abc.csv"
        mock_s3.put_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_10_abc.csv",
            Body=b"sample csv content",
            ContentType="text/csv"
        )

        # Test exists
        assert backend.exists("job_10_abc.csv") is True
        mock_s3.head_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_10_abc.csv"
        )

        # Test list_keys
        keys = backend.list_keys()
        assert keys == ["job_10_abc.csv", "job_11_def.csv"]

        # Test get (cached hit from save)
        retrieved_cached = backend.get("job_10_abc.csv")
        assert retrieved_cached == b"sample csv content"

        # Test get (remote download when uncached)
        retrieved_remote = backend.get("job_11_def.csv")
        assert retrieved_remote == b"customer_id,name\nC1,Alice\n"
        mock_s3.get_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_11_def.csv"
        )

        # Test delete
        assert backend.delete("job_10_abc.csv") is True
        mock_s3.delete_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_10_abc.csv"
        )


def test_neon_storage_factory_resolution(tmp_path: Path):
    """Verify get_storage() properly resolves NeonObjectStorageBackend when configured."""
    reset_storage_instance()
    with patch.object(settings, "STORAGE_BACKEND", "neon"):
        with patch.object(settings, "S3_BUCKET_NAME", "edvexa-uploads"):
            with patch.object(settings, "S3_ACCESS_KEY_ID", "key123"):
                with patch.object(settings, "S3_SECRET_ACCESS_KEY", "secret123"):
                    with patch.object(settings, "S3_ENDPOINT_URL", "https://storage.neon.tech"):
                        with patch("boto3.client") as mock_boto:
                            storage = get_storage()
                            assert isinstance(storage, NeonObjectStorageBackend)
                            assert storage.bucket_name == "edvexa-uploads"
    reset_storage_instance()


def test_neon_storage_fallback_on_incomplete_credentials(tmp_path: Path):
    """Verify get_storage() gracefully falls back to LocalStorageBackend if credentials missing."""
    reset_storage_instance()
    with patch.object(settings, "STORAGE_BACKEND", "neon"):
        with patch.object(settings, "S3_ACCESS_KEY_ID", None):
            with patch.object(settings, "UPLOADS_DIR", tmp_path):
                storage = get_storage()
                assert isinstance(storage, LocalStorageBackend)
    reset_storage_instance()


def test_r2_storage_backend_mocked(tmp_path: Path):
    """Verify backwards-compatible R2 storage interactions with mocked boto3 S3 client."""
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": BytesIO(b"id,val\n1,test\n")
    }
    mock_paginator = MagicMock()
    mock_paginator.paginate.return_value = [
        {"Contents": [{"Key": "uploads/job_1_abc.csv"}, {"Key": "uploads/job_2_def.csv"}]}
    ]
    mock_s3.get_paginator.return_value = mock_paginator

    with patch.object(settings, "UPLOADS_DIR", tmp_path):
        backend = R2StorageBackend(
            account_id="acc123",
            bucket_name="edvexa-uploads",
            access_key_id="test-key",
            secret_access_key="test-secret",
            endpoint_url="https://acc123.r2.cloudflarestorage.com"
        )
        backend.s3_client = mock_s3

        # Test save
        saved_name = backend.save("job_1_abc.csv", b"sample content", content_type="text/csv")
        assert saved_name == "job_1_abc.csv"
        mock_s3.put_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_1_abc.csv",
            Body=b"sample content",
            ContentType="text/csv"
        )

        # Test exists
        backend.exists("job_1_abc.csv")
        mock_s3.head_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_1_abc.csv"
        )

        # Test list_keys
        keys = backend.list_keys()
        assert keys == ["job_1_abc.csv", "job_2_def.csv"]

        # Test delete
        backend.delete("job_1_abc.csv")
        mock_s3.delete_object.assert_called_once_with(
            Bucket="edvexa-uploads",
            Key="uploads/job_1_abc.csv"
        )


def test_storage_singleton_and_helpers(tmp_path: Path):
    """Verify singleton factory and top-level helper functions."""
    reset_storage_instance()
    with patch.object(settings, "STORAGE_BACKEND", "local"):
        with patch.object(settings, "UPLOADS_DIR", tmp_path):
            storage = get_storage()
            assert isinstance(storage, LocalStorageBackend)

            key = generate_artifact_key(101, "a1b2c3d4e5f67890")
            assert key == "job_101_a1b2c3d4e5f6.csv"

            # Helper functions
            saved_key = save_upload_artifact(101, "a1b2c3d4e5f67890", b"col1\nval1\n")
            assert saved_key == key
            retrieved = get_upload_artifact(saved_key)
            assert retrieved == b"col1\nval1\n"
            assert delete_upload_artifact(saved_key) is True
    reset_storage_instance()


# =====================================================================
# 4. RESET STATUS & SECURITY GUARDS TESTS
# =====================================================================

def test_get_reset_status_endpoint(client: TestClient):
    """Verify GET /api/dashboard/reset-status returns accurate security configuration."""
    res = client.get("/api/dashboard/reset-status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert "environment" in data
    assert "destructive_reset_allowed" in data
    assert "admin_token_required" in data
    assert "storage_backend" in data


def test_reset_data_allowed_in_development_by_default(client: TestClient):
    """In development without admin token configured, reset is permitted."""
    with patch.object(settings, "ENVIRONMENT", "development"):
        with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", True):
            with patch.object(settings, "ADMIN_RESET_TOKEN", None):
                res = client.post("/api/dashboard/reset-data")
                assert res.status_code == 200
                data = res.json()
                assert data["status"] == "success"


def test_reset_data_forbidden_in_production_without_token(client: TestClient):
    """In production with reset disabled, POST /reset-data returns 403 Forbidden."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", False):
            with patch.object(settings, "ADMIN_RESET_TOKEN", None):
                res = client.post("/api/dashboard/reset-data")
                assert res.status_code == 403
                assert "disabled in production" in res.json()["detail"].lower()


def test_reset_data_requires_admin_token_when_configured(client: TestClient):
    """When ADMIN_RESET_TOKEN is set, unauthorized requests return 401."""
    with patch.object(settings, "ADMIN_RESET_TOKEN", "prod-super-secret-token"):
        # 1. No token supplied -> 401
        res_no_tok = client.post("/api/dashboard/reset-data")
        assert res_no_tok.status_code == 401
        assert "invalid or missing" in res_no_tok.json()["detail"].lower()

        # 2. Wrong token supplied -> 401
        res_bad_tok = client.post(
            "/api/dashboard/reset-data",
            headers={"X-Admin-Reset-Token": "wrong-token"}
        )
        assert res_bad_tok.status_code == 401

        # 3. Valid token via header -> 200
        res_good_header = client.post(
            "/api/dashboard/reset-data",
            headers={"X-Admin-Reset-Token": "prod-super-secret-token"}
        )
        assert res_good_header.status_code == 200
        assert res_good_header.json()["status"] == "success"

        # 4. Valid token via query param -> 200
        res_good_query = client.post(
            "/api/dashboard/reset-data?token=prod-super-secret-token"
        )
        assert res_good_query.status_code == 200
        assert res_good_query.json()["status"] == "success"


def test_reset_data_forbidden_when_explicitly_disabled_in_dev(client: TestClient):
    """When ALLOW_DESTRUCTIVE_RESET is explicitly False, POST /reset-data returns 403."""
    with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", False):
        with patch.object(settings, "ADMIN_RESET_TOKEN", None):
            res = client.post("/api/dashboard/reset-data")
            assert res.status_code == 403
            assert "disabled by configuration" in res.json()["detail"].lower()
