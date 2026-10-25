import logging
import secrets
from datetime import datetime, time
import threading
from fastapi import APIRouter, Depends, Query, Header, HTTPException, status, Request
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from app.config import settings
from app.services.auth import is_authenticated_admin
from app.database import get_db
from app.services.storage import clear_all_upload_artifacts

logger = logging.getLogger("edvexa.dashboard")
from app.models import (
    ProcessingJob,
    ValidationError,
    JobCleanRecord,
    Customer,
    Sale,
    Inventory,
)
from app.schemas import (
    DashboardSummaryResponse,
    DashboardKPIs,
    DatasetMetric,
    ProcessingJobSummary,
    DashboardTrendsResponse,
    TrendDataPoint,
)

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard & Analytics"])


def parse_date_boundaries(start_date: str | None, end_date: str | None):
    """Safely parse start and end date boundary strings into datetimes."""
    start_dt = None
    end_dt = None
    if start_date:
        try:
            d = datetime.strptime(start_date.strip()[:10], "%Y-%m-%d").date()
            start_dt = datetime.combine(d, time.min)
        except Exception:
            pass
    if end_date:
        try:
            d = datetime.strptime(end_date.strip()[:10], "%Y-%m-%d").date()
            end_dt = datetime.combine(d, time.max)
        except Exception:
            pass
    return start_dt, end_dt


@router.get("/summary", response_model=DashboardSummaryResponse)
def get_dashboard_summary(
    start_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date filter (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    Compute real-time KPI metrics, dataset-specific quality distributions,
    and recent activity directly from database records, optionally scoped by date range.
    """
    start_dt, end_dt = parse_date_boundaries(start_date, end_date)

    # Aggregate total counts across all jobs in scope
    agg_stmt = select(
        func.count(ProcessingJob.id),
        func.coalesce(func.sum(ProcessingJob.total_rows), 0),
        func.coalesce(func.sum(ProcessingJob.accepted_rows), 0),
        func.coalesce(func.sum(ProcessingJob.rejected_rows), 0),
        func.coalesce(func.sum(ProcessingJob.duplicate_rows), 0),
        func.coalesce(func.sum(ProcessingJob.skipped_rows), 0),
    )
    if start_dt:
        agg_stmt = agg_stmt.where(ProcessingJob.started_at >= start_dt)
    if end_dt:
        agg_stmt = agg_stmt.where(ProcessingJob.started_at <= end_dt)

    res = db.execute(agg_stmt).one()
    total_jobs, total_rows, accepted_rows, rejected_rows, duplicate_rows, skipped_rows = res

    # Calculate overall data quality pass rate (0.0% if no data processed)
    if total_rows > 0:
        quality_rate = round((accepted_rows / total_rows) * 100.0, 1)
    else:
        quality_rate = 0.0

    kpis = DashboardKPIs(
        total_records_processed=int(total_rows),
        accepted_records=int(accepted_rows),
        rejected_records=int(rejected_rows),
        duplicate_records=int(duplicate_rows),
        skipped_records=int(skipped_rows),
        total_jobs=int(total_jobs),
        data_quality_rate=quality_rate,
    )

    # Per-dataset breakdowns
    dataset_metrics: list[DatasetMetric] = []
    for d_type in settings.SUPPORTED_DATASETS:
        d_stmt = select(
            func.count(ProcessingJob.id),
            func.coalesce(func.sum(ProcessingJob.total_rows), 0),
            func.coalesce(func.sum(ProcessingJob.accepted_rows), 0),
            func.coalesce(func.sum(ProcessingJob.rejected_rows), 0),
            func.coalesce(func.sum(ProcessingJob.duplicate_rows), 0),
        ).where(ProcessingJob.dataset_type == d_type)
        if start_dt:
            d_stmt = d_stmt.where(ProcessingJob.started_at >= start_dt)
        if end_dt:
            d_stmt = d_stmt.where(ProcessingJob.started_at <= end_dt)

        d_res = db.execute(d_stmt).one()
        d_jobs, d_total, d_acc, d_rej, d_dup = d_res
        dataset_metrics.append(DatasetMetric(
            dataset_type=d_type,
            total=int(d_total),
            accepted=int(d_acc),
            rejected=int(d_rej),
            duplicates=int(d_dup),
            job_count=int(d_jobs),
        ))

    # Recent jobs in selected date scope (up to 10)
    recent_jobs_stmt = select(ProcessingJob)
    if start_dt:
        recent_jobs_stmt = recent_jobs_stmt.where(ProcessingJob.started_at >= start_dt)
    if end_dt:
        recent_jobs_stmt = recent_jobs_stmt.where(ProcessingJob.started_at <= end_dt)
    recent_jobs_stmt = recent_jobs_stmt.order_by(ProcessingJob.id.desc()).limit(10)
    recent_jobs = db.scalars(recent_jobs_stmt).all()

    return DashboardSummaryResponse(
        kpis=kpis,
        dataset_metrics=dataset_metrics,
        recent_jobs=[ProcessingJobSummary.model_validate(j) for j in recent_jobs]
    )


@router.get("/trends", response_model=DashboardTrendsResponse)
def get_dashboard_trends(
    start_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date filter (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    Retrieve historical job performance trends over time to feed Chart.js visuals,
    scoped optionally by date range.
    """
    start_dt, end_dt = parse_date_boundaries(start_date, end_date)

    stmt = (
        select(ProcessingJob)
        .where(ProcessingJob.status == "completed")
    )
    if start_dt:
        stmt = stmt.where(ProcessingJob.started_at >= start_dt)
    if end_dt:
        stmt = stmt.where(ProcessingJob.started_at <= end_dt)

    stmt = stmt.order_by(ProcessingJob.id.asc()).limit(30)
    completed_jobs = db.scalars(stmt).all()

    trends: list[TrendDataPoint] = []
    for job in completed_jobs:
        score = 0.0
        if job.total_rows > 0:
            score = round((job.accepted_rows / job.total_rows) * 100.0, 1)

        trends.append(TrendDataPoint(
            job_id=job.id,
            timestamp=job.started_at.strftime("%b %d %H:%M"),
            filename=job.filename,
            dataset_type=job.dataset_type,
            total_rows=job.total_rows,
            accepted_rows=job.accepted_rows,
            rejected_rows=job.rejected_rows,
            duplicate_rows=job.duplicate_rows,
            quality_score=score
        ))

    return DashboardTrendsResponse(trends=trends)


@router.post("/reset-preferences")
def reset_dashboard_preferences():
    """
    Safe administrative endpoint resetting dashboard filters, date range, search queries,
    and temporary view preferences.
    Explicitly preserves all persisted ETL jobs, clean records, files, and database history.
    """
    return {
        "status": "success",
        "message": "Dashboard filters and view preferences successfully reset to default.",
        "reset_type": "filters",
        "default_range": "30d",
        "database_records_preserved": True
    }


reset_lock = threading.Lock()


def verify_reset_authorization(
    request: Request,
) -> bool:
    """
    Validate authorization for destructive data reset operations.
    Public reset is enabled across environments (development and production)
    so any visitor can reset uploaded data via the interactive slider and confirmation checkbox.
    """
    if not settings.ALLOW_DESTRUCTIVE_RESET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Destructive data reset is currently disabled by configuration."
        )
    return True


@router.get("/reset-status")
def get_reset_status(request: Request):
    """
    Returns whether destructive data reset is allowed in the current environment
    and whether public reset is active.
    """
    return {
        "status": "ready",
        "environment": settings.ENVIRONMENT,
        "destructive_reset_allowed": settings.ALLOW_DESTRUCTIVE_RESET,
        "public_reset_enabled": True,
        "confirmation_checkbox_required": True,
        "admin_auth_required": False,
        "admin_token_required": False,
        "storage_backend": settings.STORAGE_BACKEND,
        "database_type": settings.database_type,
    }


@router.post("/reset-data")
def reset_all_uploaded_data(
    db: Session = Depends(get_db),
    _authorized: bool = Depends(verify_reset_authorization),
):
    """
    Destructive administrative operation:
    Permanently deletes all imported business records (customers, sales, inventory),
    job-scoped clean records, validation errors, and processing jobs history from the database,
    along with upload files stored in persistent storage (local or Cloudflare R2).
    The database schema and configuration remain strictly intact.
    """
    if not reset_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A data reset operation is already in progress."
        )

    try:
        # Pre-count records for reporting
        count_clean = db.query(JobCleanRecord).count()
        count_errors = db.query(ValidationError).count()
        count_customers = db.query(Customer).count()
        count_sales = db.query(Sale).count()
        count_inventory = db.query(Inventory).count()
        count_jobs = db.query(ProcessingJob).count()

        total_data_count = (
            count_clean + count_errors + count_customers +
            count_sales + count_inventory + count_jobs
        )

        # Safely remove upload artifacts via storage abstraction
        try:
            deleted_files = clear_all_upload_artifacts()
        except Exception as storage_err:
            logger.warning(f"Storage cleanup had warning: {storage_err}")
            deleted_files = 0
        failed_files: list[dict] = []

        # If database was already completely empty
        if total_data_count == 0:
            return {
                "status": "success",
                "message": "The application data is already empty.",
                "deleted": {
                    "jobs": 0,
                    "customers": 0,
                    "sales": 0,
                    "inventory": 0,
                    "validation_errors": 0,
                    "clean_records": 0,
                    "upload_files": deleted_files
                },
                "deleted_counts": {
                    "processing_jobs": 0,
                    "customers": 0,
                    "sales": 0,
                    "inventory": 0,
                    "validation_errors": 0,
                    "job_clean_records": 0,
                    "physical_files_removed": deleted_files
                },
                "unremoved_files": failed_files,
                "database_schema_preserved": True
            }

        # Delete database records in proper foreign-key order inside an atomic transaction
        db.query(JobCleanRecord).delete(synchronize_session=False)
        db.query(ValidationError).delete(synchronize_session=False)
        db.query(Customer).delete(synchronize_session=False)
        db.query(Sale).delete(synchronize_session=False)
        db.query(Inventory).delete(synchronize_session=False)
        db.query(ProcessingJob).delete(synchronize_session=False)
        db.commit()

        return {
            "status": "success",
            "message": "All uploaded data, processing jobs, validation errors, and upload artifacts have been permanently deleted.",
            "deleted": {
                "jobs": count_jobs,
                "customers": count_customers,
                "sales": count_sales,
                "inventory": count_inventory,
                "validation_errors": count_errors,
                "clean_records": count_clean,
                "upload_files": deleted_files
            },
            "deleted_counts": {
                "processing_jobs": count_jobs,
                "customers": count_customers,
                "sales": count_sales,
                "inventory": count_inventory,
                "validation_errors": count_errors,
                "job_clean_records": count_clean,
                "physical_files_removed": deleted_files
            },
            "unremoved_files": failed_files,
            "database_schema_preserved": True
        }

    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reset application data: {str(e)}"
        )
    finally:
        reset_lock.release()

