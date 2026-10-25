import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.config import settings
from app.models import (
    ProcessingJob,
    ValidationError,
    JobCleanRecord,
    Customer,
    Sale,
    Inventory,
    utc_now,
)
from app.services.csv_import import parse_and_validate_csv, compute_file_hash
from app.services.validation import (
    validate_row_by_dataset,
    DATASET_PRIMARY_KEYS,
)

logger = logging.getLogger("edvexa.etl")

class ETLProcessingError(Exception):
    """Raised when an unrecoverable error happens during ETL processing."""
    pass

def save_upload_artifact(job_id: int, file_hash: str, content: bytes) -> Path:
    """Save uploaded CSV content securely to the storage directory."""
    clean_filename = f"job_{job_id}_{file_hash[:12]}.csv"
    destination = settings.UPLOADS_DIR / clean_filename
    with open(destination, "wb") as f:
        f.write(content)
    return destination

def get_existing_business_keys(db: Session, dataset_type: str, keys: set[str]) -> set[str]:
    """Fetch subset of keys that already exist in clean database tables."""
    if not keys:
        return set()

    if dataset_type == "customers":
        stmt = select(Customer.customer_id).where(Customer.customer_id.in_(keys))
        return set(db.scalars(stmt).all())
    elif dataset_type == "sales":
        stmt = select(Sale.sale_id).where(Sale.sale_id.in_(keys))
        return set(db.scalars(stmt).all())
    elif dataset_type == "inventory":
        stmt = select(Inventory.product_id).where(Inventory.product_id.in_(keys))
        return set(db.scalars(stmt).all())
    return set()

def execute_etl_job(
    content: bytes,
    filename: str,
    dataset_type: str,
    db: Session,
    existing_job: ProcessingJob | None = None
) -> ProcessingJob:
    """
    Execute full Extract-Transform-Load workflow with strict transaction boundaries
    and mutual-exclusion accounting.
    """
    start_time = time.perf_counter()
    file_hash = compute_file_hash(content)

    # 1. Initialize or update ProcessingJob audit record
    if existing_job is not None:
        job = existing_job
        job.status = "processing"
        job.started_at = utc_now()
        job.error_message = None
    else:
        job = ProcessingJob(
            filename=filename,
            dataset_type=dataset_type,
            file_hash=file_hash,
            status="processing",
            started_at=utc_now(),
        )
        db.add(job)
        db.flush()  # Populate job.id

    # Securely store file artifact for audit & potential re-processing
    try:
        save_upload_artifact(job.id, file_hash, content)
    except Exception as e:
        logger.warning(f"Could not save upload artifact to disk: {e}")

    # 2. EXTRACT & FILE VALIDATION
    extraction = parse_and_validate_csv(content, filename, dataset_type)
    if not extraction.is_valid:
        job.status = "failed"
        job.error_message = extraction.error_message
        job.total_rows = 0
        job.accepted_rows = 0
        job.rejected_rows = 0
        job.duplicate_rows = 0
        job.skipped_rows = 0
        job.completed_at = utc_now()
        job.processing_duration = round(time.perf_counter() - start_time, 4)
        db.commit()
        db.refresh(job)
        return job

    total_rows = extraction.total_raw_rows
    job.total_rows = total_rows

    # 3. TRANSFORM & VALIDATE ROWS
    primary_key_field = DATASET_PRIMARY_KEYS[dataset_type]

    # Pre-collect candidates to batch-check against database
    candidate_keys = set()
    validated_results = []

    for parsed_row in extraction.rows:
        val_res = validate_row_by_dataset(dataset_type, parsed_row.data)
        validated_results.append((parsed_row, val_res))
        if val_res.is_valid and val_res.cleaned_data:
            key_val = val_res.cleaned_data.get(primary_key_field)
            if key_val:
                candidate_keys.add(key_val)

    # Fetch existing DB keys for duplicate check
    existing_db_keys = get_existing_business_keys(db, dataset_type, candidate_keys)

    # Categories for mutual-exclusion accounting
    accepted_records: list[dict[str, Any]] = []
    accepted_entries: list[tuple[int, str, dict[str, Any]]] = []
    validation_error_records: list[ValidationError] = []

    seen_keys_in_file: set[str] = set()
    accepted_count = 0
    rejected_count = 0
    duplicate_count = 0
    skipped_count = 0

    for parsed_row, val_res in validated_results:
        # Check invalid records
        if not val_res.is_valid:
            rejected_count += 1
            for err in val_res.errors:
                validation_error_records.append(ValidationError(
                    job_id=job.id,
                    row_number=parsed_row.line_number,
                    field_name=err.field_name,
                    error_code=err.error_code,
                    error_message=err.error_message,
                    original_value=err.original_value,
                    original_row_json=parsed_row.raw_json,
                    created_at=utc_now()
                ))
            continue

        # Valid row format: now test for business key duplication
        cleaned_dict = val_res.cleaned_data
        key_val = cleaned_dict.get(primary_key_field)

        # Check in-file duplicate
        if key_val in seen_keys_in_file:
            duplicate_count += 1
            validation_error_records.append(ValidationError(
                job_id=job.id,
                row_number=parsed_row.line_number,
                field_name=primary_key_field,
                error_code="DUPLICATE_KEY_IN_FILE",
                error_message=f"Duplicate identifier '{key_val}' was already present in an earlier row in this file.",
                original_value=str(key_val),
                original_row_json=parsed_row.raw_json,
                created_at=utc_now()
            ))
            continue

        seen_keys_in_file.add(key_val)

        # Check existing DB duplicate
        if key_val in existing_db_keys:
            duplicate_count += 1
            validation_error_records.append(ValidationError(
                job_id=job.id,
                row_number=parsed_row.line_number,
                field_name=primary_key_field,
                error_code="DUPLICATE_KEY_IN_DB",
                error_message=f"Business key '{key_val}' already exists in clean '{dataset_type}' records.",
                original_value=str(key_val),
                original_row_json=parsed_row.raw_json,
                created_at=utc_now()
            ))
            continue

        # If it reaches here, the record is accepted!
        accepted_count += 1
        accepted_records.append(cleaned_dict)
        accepted_entries.append((parsed_row.line_number, str(key_val), cleaned_dict))

    # Enforce strict accounting rule:
    # total_rows = accepted_rows + rejected_rows + duplicate_rows + skipped_rows
    assert total_rows == (accepted_count + rejected_count + duplicate_count + skipped_count), (
        f"Accounting mismatch: total={total_rows} vs sum={accepted_count + rejected_count + duplicate_count + skipped_count}"
    )

    # 4. LOAD (ATOMIC TRANSACTION)
    try:
        # Load clean records
        if dataset_type == "customers":
            for rec in accepted_records:
                db.add(Customer(
                    customer_id=rec["customer_id"],
                    name=rec["name"],
                    email=rec["email"],
                    phone=rec.get("phone"),
                    created_at=rec["created_at"],
                    created_timestamp=utc_now()
                ))
        elif dataset_type == "sales":
            for rec in accepted_records:
                db.add(Sale(
                    sale_id=rec["sale_id"],
                    customer_id=rec["customer_id"],
                    amount=rec["amount"],
                    sale_date=rec["sale_date"],
                    currency=rec["currency"],
                ))
        elif dataset_type == "inventory":
            for rec in accepted_records:
                db.add(Inventory(
                    product_id=rec["product_id"],
                    product_name=rec["product_name"],
                    quantity=rec["quantity"],
                    unit_price=rec["unit_price"],
                    updated_at=rec["updated_at"],
                ))

        # Load job-scoped clean records for persistent, isolated export
        for line_no, b_key, rec in accepted_entries:
            db.add(JobCleanRecord(
                job_id=job.id,
                row_number=line_no,
                dataset_type=dataset_type,
                business_key=b_key,
                record_json=json.dumps(rec, default=str),
                created_at=utc_now()
            ))

        # Load validation errors
        if validation_error_records:
            db.add_all(validation_error_records)

        # Update job metrics
        job.total_rows = total_rows
        job.accepted_rows = accepted_count
        job.rejected_rows = rejected_count
        job.duplicate_rows = duplicate_count
        job.skipped_rows = skipped_count
        job.status = "completed"
        job.completed_at = utc_now()
        job.processing_duration = round(time.perf_counter() - start_time, 4)

        db.commit()
        db.refresh(job)
        logger.info(
            f"Job {job.id} ({dataset_type}): total={total_rows}, accepted={accepted_count}, "
            f"rejected={rejected_count}, duplicates={duplicate_count} in {job.processing_duration}s"
        )
        return job

    except Exception as e:
        db.rollback()
        logger.error(f"ETL Load failed for job {job.id}: {e}", exc_info=True)
        job.status = "failed"
        job.error_message = "A database error occurred during data loading. The transaction was safely rolled back."
        job.completed_at = utc_now()
        job.processing_duration = round(time.perf_counter() - start_time, 4)
        db.commit()
        db.refresh(job)
        return job

def reprocess_existing_job(job_id: int, db: Session) -> ProcessingJob:
    """
    Rerun ETL on a previously uploaded job using its securely stored source file.
    Follows strict idempotency.
    """
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job with ID {job_id} not found.")

    artifact_name = f"job_{job.id}_{job.file_hash[:12]}.csv"
    artifact_path = settings.UPLOADS_DIR / artifact_name

    if not artifact_path.exists():
        raise FileNotFoundError(f"Source file artifact for job {job_id} is no longer available.")

    with open(artifact_path, "rb") as f:
        content = f.read()

    # Clear previous validation errors and clean records for this job before re-running
    db.query(ValidationError).filter(ValidationError.job_id == job.id).delete()
    db.query(JobCleanRecord).filter(JobCleanRecord.job_id == job.id).delete()
    db.commit()

    return execute_etl_job(
        content=content,
        filename=job.filename,
        dataset_type=job.dataset_type,
        db=db,
        existing_job=job
    )
