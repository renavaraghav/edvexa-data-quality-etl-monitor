from datetime import datetime, time
import json
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_

from app.config import settings
from app.database import get_db
from app.models import ProcessingJob
from app.schemas import (
    ProcessingJobSummary,
    ProcessingJobDetail,
    ProcessingJobListResponse,
    ValidationErrorItem,
)
from app.services.etl_pipeline import reprocess_existing_job
from app.services.export_service import (
    generate_clean_records_csv,
    generate_validation_errors_csv,
    generate_job_summary_json,
    generate_job_excel_report,
)

router = APIRouter(prefix="/api/jobs", tags=["Processing Jobs"])

@router.get("", response_model=ProcessingJobListResponse)
def list_jobs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE, description="Items per page"),
    dataset_type: str | None = Query(None, description="Filter by dataset type"),
    status_filter: str | None = Query(None, alias="status", description="Filter by job status"),
    search: str | None = Query(None, description="Search by filename or job ID"),
    start_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date filter (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """List ETL processing jobs with filtering, search, date range, and pagination."""
    query = select(ProcessingJob)

    if dataset_type:
        query = query.where(ProcessingJob.dataset_type == dataset_type.strip().lower())

    if status_filter:
        query = query.where(ProcessingJob.status == status_filter.strip().lower())

    if search:
        search_term = f"%{search.strip()}%"
        # Check if search is numeric to search ID directly
        if search.strip().isdigit():
            query = query.where(or_(ProcessingJob.filename.ilike(search_term), ProcessingJob.id == int(search.strip())))
        else:
            query = query.where(ProcessingJob.filename.ilike(search_term))

    if start_date:
        try:
            d = datetime.strptime(start_date.strip()[:10], "%Y-%m-%d").date()
            query = query.where(ProcessingJob.started_at >= datetime.combine(d, time.min))
        except Exception:
            pass

    if end_date:
        try:
            d = datetime.strptime(end_date.strip()[:10], "%Y-%m-%d").date()
            query = query.where(ProcessingJob.started_at <= datetime.combine(d, time.max))
        except Exception:
            pass

    # Total count query
    count_query = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_query) or 0

    # Paginate
    offset = (page - 1) * page_size
    query = query.order_by(ProcessingJob.id.desc()).offset(offset).limit(page_size)
    items = db.scalars(query).all()

    return ProcessingJobListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ProcessingJobSummary.model_validate(item) for item in items]
    )

@router.get("/{job_id}", response_model=ProcessingJobDetail)
def get_job_detail(
    job_id: int,
    db: Session = Depends(get_db)
):
    """Retrieve detailed metadata and validation error summary for a single job."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID {job_id} was not found."
        )

    # Order validation errors by row_number
    errors = [ValidationErrorItem.model_validate(e) for e in job.validation_errors]

    detail = ProcessingJobDetail(
        id=job.id,
        filename=job.filename,
        dataset_type=job.dataset_type,
        file_hash=job.file_hash,
        status=job.status,
        total_rows=job.total_rows,
        accepted_rows=job.accepted_rows,
        rejected_rows=job.rejected_rows,
        duplicate_rows=job.duplicate_rows,
        skipped_rows=job.skipped_rows,
        error_message=job.error_message,
        started_at=job.started_at,
        completed_at=job.completed_at,
        processing_duration=job.processing_duration,
        validation_errors=errors
    )
    return detail

@router.post("/{job_id}/reprocess", response_model=ProcessingJobDetail)
def reprocess_job(
    job_id: int,
    db: Session = Depends(get_db)
):
    """
    Rerun an ETL job from its securely stored source file artifact.
    Preserves strict idempotency to prevent duplicate clean records.
    """
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID {job_id} was not found."
        )

    try:
        updated_job = reprocess_existing_job(job_id=job_id, db=db)
    except FileNotFoundError as fnf:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=str(fnf)
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An unexpected error occurred during reprocessing: {str(e)}"
        )

    errors = [ValidationErrorItem.model_validate(e) for e in updated_job.validation_errors]

    return ProcessingJobDetail(
        id=updated_job.id,
        filename=updated_job.filename,
        dataset_type=updated_job.dataset_type,
        file_hash=updated_job.file_hash,
        status=updated_job.status,
        total_rows=updated_job.total_rows,
        accepted_rows=updated_job.accepted_rows,
        rejected_rows=updated_job.rejected_rows,
        duplicate_rows=updated_job.duplicate_rows,
        skipped_rows=updated_job.skipped_rows,
        error_message=updated_job.error_message,
        started_at=updated_job.started_at,
        completed_at=updated_job.completed_at,
        processing_duration=updated_job.processing_duration,
        validation_errors=errors
    )

@router.get("/{job_id}/download/clean", summary="Download Clean Accepted Records (CSV)")
def download_clean_records(job_id: int, db: Session = Depends(get_db)):
    """
    Download a CSV file containing only clean, normalized records accepted from this specific job.
    Never includes rejected or duplicate records.
    """
    if job_id <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job ID must be a positive integer.")
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Processing job with ID {job_id} was not found.")
    
    csv_content, filename = generate_clean_records_csv(job, db)
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    )

@router.get("/{job_id}/download/errors", summary="Download Validation Errors (CSV)")
def download_validation_errors(job_id: int, db: Session = Depends(get_db)):
    """
    Download a CSV file containing all validation and duplicate-detection error records for this job.
    Includes every recorded error.
    """
    if job_id <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job ID must be a positive integer.")
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Processing job with ID {job_id} was not found.")

    csv_content, filename = generate_validation_errors_csv(job, db)
    return Response(
        content=csv_content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    )

@router.get("/{job_id}/download/summary", summary="Download Job Summary Report (Excel)")
def download_job_summary(job_id: int, db: Session = Depends(get_db)):
    """
    Download a genuine Microsoft Excel (.xlsx) workbook containing:
    1. Job Summary & Accounting Reconciliation
    2. Clean Records accepted for this job
    3. Validation Errors recorded for this job
    """
    if job_id <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Job ID must be a positive integer.")
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Processing job with ID {job_id} was not found.")

    excel_bytes, filename = generate_job_excel_report(job, db)
    return Response(
        content=excel_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition",
        }
    )
