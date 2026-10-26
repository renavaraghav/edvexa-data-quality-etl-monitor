from datetime import datetime, time
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.config import settings
from app.database import get_db
from app.models import ProcessingJob, ValidationError
from app.schemas import ValidationErrorItem, ValidationErrorListResponse

router = APIRouter(prefix="/api", tags=["Validation Errors"])


def parse_date_boundaries(start_date: str | None, end_date: str | None):
    """Parse optional YYYY-MM-DD filters, rejecting malformed dates with HTTP 422."""
    start_dt = None
    end_dt = None
    if start_date:
        try:
            d = datetime.strptime(start_date.strip(), "%Y-%m-%d").date()
            start_dt = datetime.combine(d, time.min)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid start_date '{start_date}'. Expected YYYY-MM-DD."
            ) from exc
    if end_date:
        try:
            d = datetime.strptime(end_date.strip(), "%Y-%m-%d").date()
            end_dt = datetime.combine(d, time.max)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid end_date '{end_date}'. Expected YYYY-MM-DD."
            ) from exc
    if start_dt and end_dt and start_dt > end_dt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_date must be on or before end_date."
        )
    return start_dt, end_dt

@router.get("/jobs/{job_id}/errors", response_model=ValidationErrorListResponse)
def get_job_errors(
    job_id: int,
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE, description="Items per page"),
    error_code: str | None = Query(None, description="Filter by error code"),
    field_name: str | None = Query(None, description="Filter by field name"),
    db: Session = Depends(get_db)
):
    """Retrieve paginated validation errors for a specific processing job."""
    job = db.get(ProcessingJob, job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Processing job with ID {job_id} was not found."
        )

    query = select(ValidationError).where(ValidationError.job_id == job_id)

    if error_code:
        query = query.where(ValidationError.error_code == error_code.strip())
    if field_name:
        query = query.where(ValidationError.field_name == field_name.strip())

    count_query = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_query) or 0

    offset = (page - 1) * page_size
    query = query.order_by(ValidationError.row_number.asc(), ValidationError.id.asc()).offset(offset).limit(page_size)
    items = db.scalars(query).all()

    return ValidationErrorListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ValidationErrorItem.model_validate(i) for i in items]
    )

@router.get("/errors", response_model=ValidationErrorListResponse)
def list_all_errors(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE, description="Items per page"),
    job_id: int | None = Query(None, description="Filter by job ID"),
    error_code: str | None = Query(None, description="Filter by error code"),
    field_name: str | None = Query(None, description="Filter by field name"),
    start_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date filter (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """Retrieve paginated validation errors across all jobs with optional date-range filtering."""
    query = select(ValidationError)

    start_dt, end_dt = parse_date_boundaries(start_date, end_date)
    if start_dt or end_dt:
        query = query.join(ProcessingJob, ValidationError.job_id == ProcessingJob.id)
        if start_dt:
            query = query.where(ProcessingJob.started_at >= start_dt)
        if end_dt:
            query = query.where(ProcessingJob.started_at <= end_dt)

    if job_id is not None:
        query = query.where(ValidationError.job_id == job_id)
    if error_code:
        query = query.where(ValidationError.error_code == error_code.strip())
    if field_name:
        query = query.where(ValidationError.field_name == field_name.strip())

    count_query = select(func.count()).select_from(query.subquery())
    total = db.scalar(count_query) or 0

    offset = (page - 1) * page_size
    query = query.order_by(ValidationError.id.desc()).offset(offset).limit(page_size)
    items = db.scalars(query).all()

    return ValidationErrorListResponse(
        total=total,
        page=page,
        page_size=page_size,
        items=[ValidationErrorItem.model_validate(i) for i in items]
    )
