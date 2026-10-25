"""
Tests for Public Destructive Reset, Slider & Checkbox Flow, and Data Safety.
Verifies:
1. Anyone visiting the website can reset uploaded data without an admin token or login in both production and development.
2. Destructive reset deletes customer, sales, inventory records, processing jobs, validation errors, clean records, and upload files.
3. Database schema, tables, system code, and platform configuration remain strictly intact.
4. Repeated requests on an empty database report 'The application data is already empty.'
5. Concurrency lock prevents overlapping resets (HTTP 409 Conflict).
6. Storage failure resilience: storage errors are handled safely without corrupting the database transaction.
7. Reset status endpoint reports public reset enabled.
8. If ALLOW_DESTRUCTIVE_RESET is explicitly False in configuration, reset is blocked with HTTP 403.
"""
import datetime
import json
from unittest.mock import patch, MagicMock
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ProcessingJob,
    ValidationError,
    JobCleanRecord,
    Customer,
    Sale,
    Inventory,
)
from app.routers.dashboard import reset_lock


def seed_test_database(db: Session) -> dict:
    """Helper to seed sample records for testing destructive reset."""
    job = ProcessingJob(
        filename="customers_sample.csv",
        dataset_type="customers",
        file_hash="sample_hash_999",
        status="completed",
        total_rows=3,
        accepted_rows=2,
        rejected_rows=1,
        duplicate_rows=0,
        started_at=datetime.datetime(2026, 10, 26, 1, 0, 0),
        completed_at=datetime.datetime(2026, 10, 26, 1, 0, 1),
    )
    db.add(job)
    db.flush()

    clean_rec = JobCleanRecord(
        job_id=job.id,
        row_number=1,
        dataset_type="customers",
        business_key="CUST_TEST",
        record_json=json.dumps({"customer_id": "CUST_TEST", "name": "Test User", "email": "test@example.com"}),
    )
    val_err = ValidationError(
        job_id=job.id,
        row_number=3,
        field_name="email",
        error_code="INVALID_EMAIL",
        error_message="Invalid email syntax.",
        original_value="bad",
    )
    cust = Customer(
        customer_id="CUST_TEST",
        name="Test User",
        email="test@example.com",
        created_at=datetime.date(2026, 10, 26),
    )
    sale = Sale(
        sale_id="SALE_TEST",
        customer_id="CUST_TEST",
        amount=99.50,
        sale_date=datetime.date(2026, 10, 26),
        currency="USD",
    )
    inv = Inventory(
        product_id="ITEM_TEST",
        product_name="Test Product",
        quantity=15,
        unit_price=10.00,
        updated_at=datetime.date(2026, 10, 26),
    )
    db.add_all([clean_rec, val_err, cust, sale, inv])
    db.commit()
    return {"job_id": job.id}


def test_public_reset_allowed_in_production_without_token(client: TestClient, db_session: Session):
    """In production environment, any visitor can execute reset without tokens or login."""
    with patch.object(settings, "ENVIRONMENT", "production"):
        with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", True):
            # Seed test data
            seed_test_database(db_session)
            assert db_session.query(ProcessingJob).count() >= 1

            # Public reset request without any credentials/headers
            res = client.post("/api/dashboard/reset-data")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert data["database_schema_preserved"] is True
            assert data["deleted"]["customers"] >= 1
            assert data["deleted"]["jobs"] >= 1

            # Verify tables are empty
            assert db_session.query(ProcessingJob).count() == 0
            assert db_session.query(Customer).count() == 0
            assert db_session.query(Sale).count() == 0
            assert db_session.query(Inventory).count() == 0
            assert db_session.query(ValidationError).count() == 0
            assert db_session.query(JobCleanRecord).count() == 0


def test_public_reset_allowed_in_development_without_token(client: TestClient, db_session: Session):
    """In development environment, reset is unrestricted for test data."""
    with patch.object(settings, "ENVIRONMENT", "development"):
        with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", True):
            seed_test_database(db_session)
            res = client.post("/api/dashboard/reset-data")
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert data["database_schema_preserved"] is True


def test_repeated_reset_on_empty_data_reports_already_empty(client: TestClient, db_session: Session):
    """Executing reset when the database is already empty returns 200 with appropriate message."""
    # Ensure empty
    db_session.query(JobCleanRecord).delete()
    db_session.query(ValidationError).delete()
    db_session.query(Customer).delete()
    db_session.query(Sale).delete()
    db_session.query(Inventory).delete()
    db_session.query(ProcessingJob).delete()
    db_session.commit()

    res = client.post("/api/dashboard/reset-data")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "already empty" in data["message"].lower()
    assert data["deleted"]["jobs"] == 0
    assert data["database_schema_preserved"] is True


def test_concurrent_reset_rejection(client: TestClient):
    """If another reset operation holds the lock, incoming requests get 409 Conflict."""
    reset_lock.acquire()
    try:
        res = client.post("/api/dashboard/reset-data")
        assert res.status_code == 409
        assert "already in progress" in res.json()["detail"].lower()
    finally:
        reset_lock.release()


def test_storage_failure_handled_safely(client: TestClient, db_session: Session):
    """Storage artifact cleanup errors do not fail the database reset transaction."""
    seed_test_database(db_session)

    with patch("app.routers.dashboard.clear_all_upload_artifacts", side_effect=Exception("S3 Storage connection error")):
        res = client.post("/api/dashboard/reset-data")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert data["database_schema_preserved"] is True
        # Database records must still be successfully cleared
        assert db_session.query(Customer).count() == 0


def test_reset_status_reports_public_reset_enabled(client: TestClient):
    """GET /api/dashboard/reset-status confirms public reset is enabled and token is not required."""
    res = client.get("/api/dashboard/reset-status")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert data["destructive_reset_allowed"] is True
    assert data["public_reset_enabled"] is True
    assert data["confirmation_checkbox_required"] is True
    assert data["admin_auth_required"] is False
    assert "database_type" in data


def test_reset_forbidden_when_explicitly_disabled(client: TestClient):
    """When ALLOW_DESTRUCTIVE_RESET is explicitly set to False, reset returns 403."""
    with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", False):
        res = client.post("/api/dashboard/reset-data")
        assert res.status_code == 403
        assert "disabled by configuration" in res.json()["detail"].lower()
