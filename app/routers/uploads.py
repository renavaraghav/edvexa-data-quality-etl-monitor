import logging
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session
from sqlalchemy import select

from app.config import settings
from app.database import get_db
from app.models import ProcessingJob
from app.schemas import UploadResponse
from app.services.csv_import import compute_file_hash
from app.services.etl_pipeline import execute_etl_job

logger = logging.getLogger("edvexa.api.uploads")
router = APIRouter(prefix="/api/uploads", tags=["CSV Upload & Ingestion"])

@router.post("", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_csv_file(
    file: UploadFile = File(..., description="CSV data file to ingest"),
    dataset_type: str = Form(..., description="Dataset type: 'customers', 'sales', or 'inventory'"),
    db: Session = Depends(get_db)
):
    """
    Upload and process a dataset CSV file through the automated ETL pipeline.
    Validates file format, size, schema, cleans data, checks business duplicates,
    and loads clean records into database.
    """
    # 1. Dataset type validation
    clean_dataset = dataset_type.strip().lower()
    if clean_dataset not in settings.SUPPORTED_DATASETS:
        supported = ", ".join(settings.SUPPORTED_DATASETS)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid dataset_type '{dataset_type}'. Supported datasets are: {supported}."
        )

    # 2. File extension validation
    filename = file.filename or "unknown.csv"
    if not filename.lower().endswith(".csv"):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported media type. Only standard CSV files (.csv) are accepted."
        )

    # 3. Read content safely with size check
    try:
        # Read only the configured limit plus one byte before rejecting oversized files.
        content = await file.read(settings.UPLOAD_MAX_SIZE_BYTES + 1)
    except Exception as e:
        logger.error(f"Error reading uploaded file: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to read the uploaded file stream."
        )

    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes). Please upload a valid CSV file with data."
        )

    if len(content) > settings.UPLOAD_MAX_SIZE_BYTES:
        max_mb = settings.UPLOAD_MAX_SIZE_BYTES / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds maximum allowed upload size of {max_mb:.0f} MB."
        )

    file_hash = compute_file_hash(content)

    # Check if identical file was previously uploaded for this dataset
    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.file_hash == file_hash, ProcessingJob.dataset_type == clean_dataset)
        .order_by(ProcessingJob.id.desc())
    )
    previous_job = db.scalars(stmt).first()
    is_repeated = previous_job is not None

    # 4. Execute ETL Pipeline
    job = execute_etl_job(
        content=content,
        filename=filename,
        dataset_type=clean_dataset,
        db=db
    )

    # Generate meaningful response message
    if job.status == "failed":
        message = f"Processing failed: {job.error_message}"
    elif is_repeated:
        message = (
            f"File '{filename}' matches an existing upload (Hash: {file_hash[:8]}). "
            f"Processed with idempotency: {job.accepted_rows} new, {job.duplicate_rows} duplicates, "
            f"{job.rejected_rows} rejected."
        )
    else:
        message = (
            f"Successfully processed '{filename}': {job.accepted_rows} accepted, "
            f"{job.rejected_rows} rejected, {job.duplicate_rows} duplicates."
        )

    return UploadResponse(
        job_id=job.id,
        filename=job.filename,
        dataset_type=job.dataset_type,
        file_hash=job.file_hash,
        status=job.status,
        total_rows=job.total_rows,
        accepted_rows=job.accepted_rows,
        rejected_rows=job.rejected_rows,
        duplicate_rows=job.duplicate_rows,
        skipped_rows=job.skipped_rows,
        message=message,
        processing_duration=job.processing_duration or 0.0,
        is_repeated_upload=is_repeated
    )
