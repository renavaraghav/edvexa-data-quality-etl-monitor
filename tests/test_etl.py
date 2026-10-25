import pytest
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.models import Customer, ProcessingJob, ValidationError
from app.services.etl_pipeline import execute_etl_job, reprocess_existing_job

def test_etl_valid_customers_loaded(db_session: Session):
    csv_content = (
        "customer_id,name,email,created_at\n"
        "CUST-001,Alice Smith,alice@example.com,2024-01-01\n"
        "CUST-002,Bob Jones,bob@example.com,2024-01-02\n"
    ).encode("utf-8")

    job = execute_etl_job(csv_content, "customers.csv", "customers", db_session)

    assert job.status == "completed"
    assert job.total_rows == 2
    assert job.accepted_rows == 2
    assert job.rejected_rows == 0
    assert job.duplicate_rows == 0
    assert job.skipped_rows == 0

    # Verify rows in DB
    customers = db_session.scalars(select(Customer)).all()
    assert len(customers) == 2
    assert {c.customer_id for c in customers} == {"CUST-001", "CUST-002"}

def test_etl_in_file_duplicate_detection(db_session: Session):
    csv_content = (
        "customer_id,name,email,created_at\n"
        "CUST-001,Alice Smith,alice@example.com,2024-01-01\n"
        "CUST-001,Alice Duplicate,alice.dup@example.com,2024-01-02\n"
    ).encode("utf-8")

    job = execute_etl_job(csv_content, "customers_dup.csv", "customers", db_session)

    assert job.status == "completed"
    assert job.total_rows == 2
    assert job.accepted_rows == 1
    assert job.duplicate_rows == 1
    assert job.rejected_rows == 0

    # Ensure accounting equality
    assert job.total_rows == job.accepted_rows + job.rejected_rows + job.duplicate_rows + job.skipped_rows

    # Verify only 1 customer inserted in DB
    count = db_session.scalar(select(func.count(Customer.customer_id)))
    assert count == 1

def test_etl_existing_db_duplicate_prevention(db_session: Session):
    # First upload
    csv_1 = (
        "customer_id,name,email,created_at\n"
        "CUST-100,Existing User,user100@example.com,2024-01-01\n"
    ).encode("utf-8")
    job1 = execute_etl_job(csv_1, "first.csv", "customers", db_session)
    assert job1.accepted_rows == 1

    # Second upload with same customer_id
    csv_2 = (
        "customer_id,name,email,created_at\n"
        "CUST-100,Different Name,user100@example.com,2024-01-05\n"
        "CUST-101,New User,user101@example.com,2024-01-06\n"
    ).encode("utf-8")
    job2 = execute_etl_job(csv_2, "second.csv", "customers", db_session)

    assert job2.total_rows == 2
    assert job2.accepted_rows == 1  # CUST-101
    assert job2.duplicate_rows == 1  # CUST-100 prevented
    assert job2.rejected_rows == 0

    # Ensure database holds exactly 2 clean customers and no duplicates
    count = db_session.scalar(select(func.count(Customer.customer_id)))
    assert count == 2

    # Check validation error recorded for duplicate
    err = db_session.scalars(select(ValidationError).where(ValidationError.job_id == job2.id)).first()
    assert err.error_code == "DUPLICATE_KEY_IN_DB"
    assert err.field_name == "customer_id"

def test_etl_repeated_file_idempotency(db_session: Session):
    csv_content = (
        "customer_id,name,email,created_at\n"
        "CUST-201,John Doe,john@example.com,2024-02-01\n"
        "CUST-202,Jane Doe,jane@example.com,2024-02-02\n"
    ).encode("utf-8")

    # Run 1
    job1 = execute_etl_job(csv_content, "file.csv", "customers", db_session)
    assert job1.accepted_rows == 2

    # Run 2 with identical file
    job2 = execute_etl_job(csv_content, "file.csv", "customers", db_session)
    assert job2.total_rows == 2
    assert job2.accepted_rows == 0
    assert job2.duplicate_rows == 2
    assert job2.rejected_rows == 0

    # Total customers in DB remains 2
    count = db_session.scalar(select(func.count(Customer.customer_id)))
    assert count == 2

def test_etl_reprocess_job(db_session: Session):
    csv_content = (
        "customer_id,name,email,created_at\n"
        "CUST-301,Reprocess Test,re@example.com,2024-02-01\n"
    ).encode("utf-8")

    job = execute_etl_job(csv_content, "reprocess.csv", "customers", db_session)
    assert job.accepted_rows == 1

    # Reprocess the job
    updated_job = reprocess_existing_job(job.id, db_session)
    assert updated_job.id == job.id
    assert updated_job.status == "completed"
    # Idempotent: row is recognized as existing DB key
    assert updated_job.duplicate_rows == 1

def test_customers_mixed_quality_empty_db_vs_populated_db(db_session: Session):
    csv_content = (
        "customer_id,name,email,phone,created_at\n"
        "CUST101,  NEHA VERMA  ,NEHA@EXAMPLE.COM,9876543210,2026-10-10\n"
        "CUST102,Missing Email,,9876543211,2026-10-11\n"
        "CUST103,Invalid Email,not-an-email,9876543212,2026-10-12\n"
        "CUST104,,blankname@example.com,9876543213,2026-10-13\n"
        "CUST105,Invalid Date,date@example.com,9876543214,not-a-date\n"
        "CUST101,Duplicate Customer,duplicate@example.com,9876543215,2026-10-14\n"
    ).encode("utf-8")

    # --- Run 1: On an Empty Database ---
    job_empty = execute_etl_job(csv_content, "customers_mixed_quality.csv", "customers", db_session)
    assert job_empty.total_rows == 6
    assert job_empty.accepted_rows == 1     # CUST101 is new and valid
    assert job_empty.rejected_rows == 4     # 4 invalid rows (lines 3-6)
    assert job_empty.duplicate_rows == 1    # line 7 is in-file duplicate of line 2
    assert job_empty.skipped_rows == 0
    assert job_empty.total_rows == (job_empty.accepted_rows + job_empty.rejected_rows + job_empty.duplicate_rows + job_empty.skipped_rows)

    # In-file duplicate error code
    errs_run1 = db_session.scalars(select(ValidationError).where(ValidationError.job_id == job_empty.id)).all()
    assert len(errs_run1) == 5
    assert any(e.error_code == "DUPLICATE_KEY_IN_FILE" and e.row_number == 7 for e in errs_run1)
    assert not any(e.error_code == "DUPLICATE_KEY_IN_DB" for e in errs_run1)

    # --- Run 2: Re-running on Populated Database (Mirrors Job #10 condition) ---
    job_pop = execute_etl_job(csv_content, "customers_mixed_quality.csv", "customers", db_session)
    assert job_pop.total_rows == 6
    assert job_pop.accepted_rows == 0      # CUST101 already exists in DB from Run 1!
    assert job_pop.rejected_rows == 4      # 4 invalid rows
    assert job_pop.duplicate_rows == 2     # Line 2 (DB duplicate) + Line 7 (in-file duplicate)
    assert job_pop.skipped_rows == 0
    assert job_pop.total_rows == (job_pop.accepted_rows + job_pop.rejected_rows + job_pop.duplicate_rows + job_pop.skipped_rows)

    errs_run2 = db_session.scalars(select(ValidationError).where(ValidationError.job_id == job_pop.id)).all()
    assert len(errs_run2) == 6             # 4 rejected row errors + 1 DB duplicate + 1 file duplicate
    assert any(e.error_code == "DUPLICATE_KEY_IN_DB" and e.row_number == 2 for e in errs_run2)
    assert any(e.error_code == "DUPLICATE_KEY_IN_FILE" and e.row_number == 7 for e in errs_run2)

def test_row_not_classified_as_both_rejected_and_duplicate(db_session: Session):
    # Pre-populate DB with CUST-EXISTING
    db_session.add(Customer(customer_id="CUST-EXISTING", name="Existing User", email="exist@example.com", created_at="2024-01-01"))
    db_session.commit()

    # Upload a row with CUST-EXISTING, BUT with an INVALID email
    csv_content = (
        "customer_id,name,email,created_at\n"
        "CUST-EXISTING,Existing User,bad-email-without-at,2024-01-01\n"
    ).encode("utf-8")

    job = execute_etl_job(csv_content, "invalid_and_duplicate.csv", "customers", db_session)
    assert job.total_rows == 1
    assert job.accepted_rows == 0
    assert job.rejected_rows == 1   # Row failed validation -> MUST be counted as rejected ONLY
    assert job.duplicate_rows == 0  # MUST NOT be counted as duplicate
    assert job.skipped_rows == 0
    assert job.total_rows == (job.accepted_rows + job.rejected_rows + job.duplicate_rows + job.skipped_rows)

    errors = db_session.scalars(select(ValidationError).where(ValidationError.job_id == job.id)).all()
    assert len(errors) == 1
    assert errors[0].error_code == "INVALID_EMAIL_FORMAT"
    assert errors[0].field_name == "email"

def test_multiple_errors_on_single_row_exceeds_rejected_count(db_session: Session):
    # Single row with TWO errors: missing name AND invalid date
    csv_content = (
        "customer_id,name,email,created_at\n"
        "CUST-MULT1,,user@example.com,not-a-valid-date\n"
    ).encode("utf-8")

    job = execute_etl_job(csv_content, "multi_error.csv", "customers", db_session)
    assert job.total_rows == 1
    assert job.accepted_rows == 0
    assert job.rejected_rows == 1   # 1 rejected row
    assert job.duplicate_rows == 0
    assert job.skipped_rows == 0

    # But 2 distinct validation error items recorded!
    errors = db_session.scalars(select(ValidationError).where(ValidationError.job_id == job.id)).all()
    assert len(errors) == 2
    err_fields = {e.field_name for e in errors}
    assert err_fields == {"name", "created_at"}

