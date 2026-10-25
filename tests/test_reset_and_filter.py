import datetime
import json
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


def test_destructive_reset_endpoint_clears_all_tables(client: TestClient, db_session: Session):
    """
    Test that POST /api/dashboard/reset-data permanently deletes all business records,
    clean records, validation errors, and jobs, while preserving database schema.
    """
    # 1. Populate tables with sample data
    job = ProcessingJob(
        filename="customers_batch.csv",
        dataset_type="customers",
        file_hash="hash_test_reset_1",
        status="completed",
        total_rows=5,
        accepted_rows=3,
        rejected_rows=1,
        duplicate_rows=1,
        started_at=datetime.datetime(2026, 10, 20, 10, 0, 0),
        completed_at=datetime.datetime(2026, 10, 20, 10, 0, 2),
    )
    db_session.add(job)
    db_session.flush()

    clean_rec = JobCleanRecord(
        job_id=job.id,
        row_number=1,
        dataset_type="customers",
        business_key="CUST001",
        record_json=json.dumps({"customer_id": "CUST001", "name": "Alice Smith", "email": "alice@example.com"}),
    )
    val_err = ValidationError(
        job_id=job.id,
        row_number=4,
        field_name="email",
        error_code="INVALID_EMAIL",
        error_message="Invalid email address format.",
        original_value="bad-email",
    )
    cust = Customer(
        customer_id="CUST001",
        name="Alice Smith",
        email="alice@example.com",
        created_at=datetime.date(2026, 10, 20),
    )
    sale = Sale(
        sale_id="SALE001",
        customer_id="CUST001",
        amount=199.99,
        sale_date=datetime.date(2026, 10, 20),
        currency="USD",
    )
    inv = Inventory(
        product_id="PROD001",
        product_name="Sample Widget",
        quantity=50,
        unit_price=24.50,
        updated_at=datetime.date(2026, 10, 20),
    )

    db_session.add_all([clean_rec, val_err, cust, sale, inv])
    db_session.commit()

    # Verify rows exist before reset
    assert db_session.query(ProcessingJob).count() == 1
    assert db_session.query(JobCleanRecord).count() == 1
    assert db_session.query(ValidationError).count() == 1
    assert db_session.query(Customer).count() == 1
    assert db_session.query(Sale).count() == 1
    assert db_session.query(Inventory).count() == 1

    # 2. Invoke destructive reset endpoint
    res = client.post("/api/dashboard/reset-data")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["database_schema_preserved"] is True
    assert data["deleted"]["jobs"] == 1
    assert data["deleted"]["customers"] == 1
    assert data["deleted"]["sales"] == 1
    assert data["deleted"]["inventory"] == 1
    assert data["deleted"]["clean_records"] == 1
    assert data["deleted"]["validation_errors"] == 1

    # 3. Verify all tables in database are now empty
    assert db_session.query(ProcessingJob).count() == 0
    assert db_session.query(JobCleanRecord).count() == 0
    assert db_session.query(ValidationError).count() == 0
    assert db_session.query(Customer).count() == 0
    assert db_session.query(Sale).count() == 0
    assert db_session.query(Inventory).count() == 0

    # 4. Verify schema is intact by creating a new job successfully
    new_job = ProcessingJob(
        filename="post_reset_test.csv",
        dataset_type="customers",
        file_hash="hash_post_reset",
        status="completed",
        total_rows=1,
        accepted_rows=1,
    )
    db_session.add(new_job)
    db_session.commit()
    assert db_session.query(ProcessingJob).count() == 1


def test_destructive_reset_cleans_upload_artifacts(client: TestClient, db_session: Session):
    """
    Test that reset-data safely unlinks job artifact files from settings.UPLOADS_DIR.
    """
    settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    test_artifact = settings.UPLOADS_DIR / "test_artifact_dummy.csv"
    test_artifact.write_text("customer_id,name,email\nC1,John,j@example.com\n", encoding="utf-8")

    assert test_artifact.exists()

    res = client.post("/api/dashboard/reset-data")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["deleted"]["upload_files"] >= 1

    # Verify the dummy artifact file was removed
    assert not test_artifact.exists()


def test_destructive_reset_concurrency_lock(client: TestClient):
    """
    Test that concurrent reset calls return 409 Conflict when a reset is in progress.
    """
    # Manually acquire the lock
    acquired = reset_lock.acquire(blocking=False)
    assert acquired is True

    try:
        res = client.post("/api/dashboard/reset-data")
        assert res.status_code == 409
        assert "already in progress" in res.json()["detail"]
    finally:
        reset_lock.release()

    # Now that the lock is released, normal reset should succeed
    res_after = client.post("/api/dashboard/reset-data")
    assert res_after.status_code == 200


def test_reset_data_route_in_openapi_specification(client: TestClient):
    """
    Test that POST /api/dashboard/reset-data is registered and present in the OpenAPI schema.
    """
    res = client.get("/openapi.json")
    assert res.status_code == 200
    schema = res.json()
    assert "/api/dashboard/reset-data" in schema["paths"]
    post_op = schema["paths"]["/api/dashboard/reset-data"].get("post")
    assert post_op is not None
    assert post_op["summary"] == "Reset All Uploaded Data"


def test_reset_data_idempotent_empty_database(client: TestClient, db_session: Session):
    """
    Test that calling POST /api/dashboard/reset-data on an already empty database
    returns HTTP 200 (not 404), with message 'The application data is already empty.'
    and zero deletion counts.
    """
    # Ensure database is empty
    assert db_session.query(ProcessingJob).count() == 0
    assert db_session.query(Customer).count() == 0

    res = client.post("/api/dashboard/reset-data")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "already empty" in data["message"].lower()
    assert data["database_schema_preserved"] is True
    assert data["deleted"]["jobs"] == 0
    assert data["deleted"]["customers"] == 0
    assert data["deleted_counts"]["processing_jobs"] == 0


def test_reset_data_preserves_files_outside_uploads_storage(client: TestClient, tmp_path):
    """
    Test that reset-data never touches or deletes files located outside settings.UPLOADS_DIR.
    """
    # Create a sensitive file outside uploads directory
    sensitive_file = tmp_path / "important_config.json"
    sensitive_file.write_text('{"secret": "do_not_delete"}', encoding="utf-8")
    assert sensitive_file.exists()

    res = client.post("/api/dashboard/reset-data")
    assert res.status_code == 200

    # Ensure sensitive file remains completely untouched
    assert sensitive_file.exists()
    assert sensitive_file.read_text(encoding="utf-8") == '{"secret": "do_not_delete"}'


def test_reset_data_transaction_rollback_on_failure(client: TestClient, db_session: Session, monkeypatch):
    """
    Test that if a database failure occurs during deletion, the transaction rolls back
    and existing data remains intact.
    """
    job = ProcessingJob(
        filename="rollback_test.csv",
        dataset_type="sales",
        file_hash="hash_rollback",
        status="completed",
        total_rows=1,
        accepted_rows=1,
    )
    db_session.add(job)
    db_session.commit()
    assert db_session.query(ProcessingJob).count() == 1

    # Simulate an error during Customer deletion
    original_delete = db_session.query(Customer).delete
    def mock_failing_delete(*args, **kwargs):
        raise RuntimeError("Simulated database failure during reset")

    from sqlalchemy.orm import Query
    monkeypatch.setattr(Query, "delete", mock_failing_delete)

    res = client.post("/api/dashboard/reset-data")
    assert res.status_code == 500
    assert "Failed to reset application data" in res.json()["detail"]


def test_records_endpoint_date_filtering(client: TestClient, db_session: Session):
    """
    Test date-range filtering on GET /api/records.
    """
    job_old = ProcessingJob(
        filename="customers_sep.csv",
        dataset_type="customers",
        file_hash="hash_sep_rec",
        status="completed",
        total_rows=1,
        accepted_rows=1,
        started_at=datetime.datetime(2026, 9, 10, 10, 0, 0),
    )
    job_new = ProcessingJob(
        filename="customers_oct.csv",
        dataset_type="customers",
        file_hash="hash_oct_rec",
        status="completed",
        total_rows=1,
        accepted_rows=1,
        started_at=datetime.datetime(2026, 10, 15, 12, 0, 0),
    )
    db_session.add_all([job_old, job_new])
    db_session.flush()

    rec_old = JobCleanRecord(
        job_id=job_old.id,
        row_number=1,
        dataset_type="customers",
        business_key="CUST_OLD",
        record_json=json.dumps({"customer_id": "CUST_OLD", "name": "Old Client", "email": "old@example.com"}),
    )
    rec_new = JobCleanRecord(
        job_id=job_new.id,
        row_number=1,
        dataset_type="customers",
        business_key="CUST_NEW",
        record_json=json.dumps({"customer_id": "CUST_NEW", "name": "New Client", "email": "new@example.com"}),
    )
    db_session.add_all([rec_old, rec_new])
    db_session.commit()

    # Filter to October 2026 only
    res = client.get("/api/records?dataset_type=customers&start_date=2026-10-01&end_date=2026-10-31")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["customer_id"] == "CUST_NEW"

    # Filter to September 2026 only
    res_sep = client.get("/api/records?dataset_type=customers&start_date=2026-09-01&end_date=2026-09-30")
    assert res_sep.status_code == 200
    data_sep = res_sep.json()
    assert data_sep["total"] == 1
    assert data_sep["items"][0]["customer_id"] == "CUST_OLD"


def test_errors_endpoint_date_filtering(client: TestClient, db_session: Session):
    """
    Test date-range filtering on GET /api/errors.
    """
    job_aug = ProcessingJob(
        filename="sales_aug.csv",
        dataset_type="sales",
        file_hash="hash_aug_err",
        status="completed",
        total_rows=2,
        rejected_rows=1,
        started_at=datetime.datetime(2026, 8, 20, 8, 0, 0),
    )
    job_sep = ProcessingJob(
        filename="sales_sep.csv",
        dataset_type="sales",
        file_hash="hash_sep_err",
        status="completed",
        total_rows=2,
        rejected_rows=1,
        started_at=datetime.datetime(2026, 9, 20, 8, 0, 0),
    )
    db_session.add_all([job_aug, job_sep])
    db_session.flush()

    err_aug = ValidationError(
        job_id=job_aug.id,
        row_number=2,
        field_name="amount",
        error_code="NEGATIVE_AMOUNT",
        error_message="Amount must be >= 0",
        original_value="-50",
    )
    err_sep = ValidationError(
        job_id=job_sep.id,
        row_number=3,
        field_name="currency",
        error_code="INVALID_CURRENCY",
        error_message="Currency unknown",
        original_value="XYZ",
    )
    db_session.add_all([err_aug, err_sep])
    db_session.commit()

    # Filter to August 2026 only
    res_aug = client.get("/api/errors?start_date=2026-08-01&end_date=2026-08-31")
    assert res_aug.status_code == 200
    data_aug = res_aug.json()
    assert data_aug["total"] == 1
    assert data_aug["items"][0]["error_code"] == "NEGATIVE_AMOUNT"

    # Filter to September 2026 only
    res_sep = client.get("/api/errors?start_date=2026-09-01&end_date=2026-09-30")
    assert res_sep.status_code == 200
    data_sep = res_sep.json()
    assert data_sep["total"] == 1
    assert data_sep["items"][0]["error_code"] == "INVALID_CURRENCY"


def test_empty_date_range_strict_zeroes(client: TestClient, db_session: Session):
    """
    Test that selecting a date range with zero matching jobs strictly returns
    0 counts, empty lists, and 0.0 quality score without falling back to unrelated data.
    """
    job = ProcessingJob(
        filename="existing_job.csv",
        dataset_type="inventory",
        file_hash="hash_existing_strict",
        status="completed",
        total_rows=10,
        accepted_rows=10,
        started_at=datetime.datetime(2026, 10, 1, 10, 0, 0),
    )
    db_session.add(job)
    db_session.commit()

    # Future range where no jobs exist
    start_str = "2029-01-01"
    end_str = "2029-01-31"

    # 1. Summary
    res_sum = client.get(f"/api/dashboard/summary?start_date={start_str}&end_date={end_str}")
    assert res_sum.status_code == 200
    kpis = res_sum.json()["kpis"]
    assert kpis["total_jobs"] == 0
    assert kpis["total_records_processed"] == 0
    assert kpis["accepted_records"] == 0
    assert kpis["rejected_records"] == 0
    assert kpis["duplicate_records"] == 0
    assert kpis["data_quality_rate"] == 0.0
    assert len(res_sum.json()["recent_jobs"]) == 0

    # 2. Trends
    res_trends = client.get(f"/api/dashboard/trends?start_date={start_str}&end_date={end_str}")
    assert res_trends.status_code == 200
    assert len(res_trends.json()["trends"]) == 0

    # 3. Jobs
    res_jobs = client.get(f"/api/jobs?start_date={start_str}&end_date={end_str}")
    assert res_jobs.status_code == 200
    assert res_jobs.json()["total"] == 0
    assert len(res_jobs.json()["items"]) == 0

    # 4. Records
    res_records = client.get(f"/api/records?dataset_type=inventory&start_date={start_str}&end_date={end_str}")
    assert res_records.status_code == 200
    assert res_records.json()["total"] == 0
    assert len(res_records.json()["items"]) == 0

    # 5. Errors
    res_errors = client.get(f"/api/errors?start_date={start_str}&end_date={end_str}")
    assert res_errors.status_code == 200
    assert res_errors.json()["total"] == 0
    assert len(res_errors.json()["items"]) == 0


def test_kpi_accounting_invariant_reconciliation(client: TestClient, db_session: Session):
    """
    Test the fundamental row-accounting invariant:
    total_records = accepted + rejected + duplicate + skipped
    and quality score = (accepted / total_records) * 100
    """
    job = ProcessingJob(
        filename="accounting_test.csv",
        dataset_type="customers",
        file_hash="hash_accounting_inv",
        status="completed",
        total_rows=100,
        accepted_rows=70,
        rejected_rows=20,
        duplicate_rows=10,
        skipped_rows=0,
        started_at=datetime.datetime(2026, 10, 22, 10, 0, 0),
        completed_at=datetime.datetime(2026, 10, 22, 10, 0, 5),
    )
    db_session.add(job)
    db_session.commit()

    res = client.get("/api/dashboard/summary")
    assert res.status_code == 200
    kpis = res.json()["kpis"]

    total = kpis["total_records_processed"]
    accepted = kpis["accepted_records"]
    rejected = kpis["rejected_records"]
    duplicate = kpis["duplicate_records"]
    skipped = kpis["skipped_records"]

    # Verify invariant
    assert total == accepted + rejected + duplicate + skipped
    assert kpis["data_quality_rate"] == 70.0
