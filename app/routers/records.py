import csv
import io
import json
from datetime import datetime, time
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session
from sqlalchemy import select, func, or_

from app.config import settings
from app.database import get_db
from app.models import Customer, Sale, Inventory, JobCleanRecord, ProcessingJob
from app.schemas import PaginatedRecordsResponse
from app.services.export_service import DATASET_EXPORT_COLUMNS

router = APIRouter(prefix="/api/records", tags=["Clean Business Records"])


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


@router.get("", response_model=PaginatedRecordsResponse)
def get_clean_records(
    dataset_type: str = Query(..., description="Dataset type: 'customers', 'sales', or 'inventory'"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(settings.DEFAULT_PAGE_SIZE, ge=1, le=settings.MAX_PAGE_SIZE, description="Items per page"),
    q: str | None = Query(None, description="Search term across key fields"),
    start_date: str | None = Query(None, description="Start date filter (YYYY-MM-DD)"),
    end_date: str | None = Query(None, description="End date filter (YYYY-MM-DD)"),
    db: Session = Depends(get_db)
):
    """
    Retrieve paginated clean records that have passed all ETL validation rules
    and are persisted in the business data tables. Supports date-range filtering.
    """
    clean_type = dataset_type.strip().lower()
    if clean_type not in settings.SUPPORTED_DATASETS:
        supported = ", ".join(settings.SUPPORTED_DATASETS)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported dataset type '{dataset_type}'. Supported datasets: {supported}."
        )

    offset = (page - 1) * page_size
    start_dt, end_dt = parse_date_boundaries(start_date, end_date)

    # If date range is specified, query job-scoped clean records joined with jobs
    if start_dt or end_dt:
        query = (
            select(JobCleanRecord)
            .join(ProcessingJob, JobCleanRecord.job_id == ProcessingJob.id)
            .where(JobCleanRecord.dataset_type == clean_type)
        )
        if start_dt:
            query = query.where(ProcessingJob.started_at >= start_dt)
        if end_dt:
            query = query.where(ProcessingJob.started_at <= end_dt)
        if q:
            term = f"%{q.strip()}%"
            query = query.where(
                or_(
                    JobCleanRecord.business_key.ilike(term),
                    JobCleanRecord.record_json.ilike(term)
                )
            )

        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = db.scalars(query.order_by(JobCleanRecord.id.desc()).offset(offset).limit(page_size)).all()
        serialized = []
        for r in items:
            try:
                rec_dict = json.loads(r.record_json)
                serialized.append(rec_dict)
            except Exception:
                pass

        return PaginatedRecordsResponse(
            dataset_type=clean_type,
            total=total,
            page=page,
            page_size=page_size,
            items=serialized
        )

    # Fallback to persistent global tables when no date filter is applied
    if clean_type == "customers":
        query = select(Customer)
        if q:
            term = f"%{q.strip()}%"
            query = query.where(
                or_(
                    Customer.customer_id.ilike(term),
                    Customer.name.ilike(term),
                    Customer.email.ilike(term),
                    Customer.phone.ilike(term),
                )
            )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = db.scalars(query.order_by(Customer.created_timestamp.desc()).offset(offset).limit(page_size)).all()
        serialized = [
            {
                "customer_id": r.customer_id,
                "name": r.name,
                "email": r.email,
                "phone": r.phone or "-",
                "created_at": r.created_at,
                "created_timestamp": r.created_timestamp.isoformat(),
            }
            for r in items
        ]

    elif clean_type == "sales":
        query = select(Sale)
        if q:
            term = f"%{q.strip()}%"
            query = query.where(
                or_(
                    Sale.sale_id.ilike(term),
                    Sale.customer_id.ilike(term),
                    Sale.currency.ilike(term),
                )
            )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = db.scalars(query.order_by(Sale.sale_date.desc()).offset(offset).limit(page_size)).all()
        serialized = [
            {
                "sale_id": r.sale_id,
                "customer_id": r.customer_id,
                "amount": r.amount,
                "sale_date": r.sale_date,
                "currency": r.currency,
            }
            for r in items
        ]

    elif clean_type == "inventory":
        query = select(Inventory)
        if q:
            term = f"%{q.strip()}%"
            query = query.where(
                or_(
                    Inventory.product_id.ilike(term),
                    Inventory.product_name.ilike(term),
                )
            )
        total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
        items = db.scalars(query.order_by(Inventory.product_id.asc()).offset(offset).limit(page_size)).all()
        serialized = [
            {
                "product_id": r.product_id,
                "product_name": r.product_name,
                "quantity": r.quantity,
                "unit_price": r.unit_price,
                "updated_at": r.updated_at,
            }
            for r in items
        ]

    else:
        supported = ", ".join(settings.SUPPORTED_DATASETS)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported dataset type '{dataset_type}'. Supported datasets: {supported}."
        )

    return PaginatedRecordsResponse(
        dataset_type=clean_type,
        total=total,
        page=page,
        page_size=page_size,
        items=serialized
    )
