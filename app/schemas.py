from datetime import datetime
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

class HealthResponse(BaseModel):
    status: str
    version: str
    database_connected: bool
    timestamp: datetime

class ValidationErrorItem(BaseModel):
    id: int
    job_id: int
    row_number: int
    field_name: str
    error_code: str
    error_message: str
    original_value: str | None = None
    original_row_json: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class ValidationErrorListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ValidationErrorItem]

class ProcessingJobSummary(BaseModel):
    id: int
    filename: str
    dataset_type: str
    file_hash: str
    status: str
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    skipped_rows: int
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    processing_duration: float | None = None

    model_config = ConfigDict(from_attributes=True)

class ProcessingJobDetail(ProcessingJobSummary):
    validation_errors: list[ValidationErrorItem] = Field(default_factory=list)

class ProcessingJobListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ProcessingJobSummary]

class CustomerRecord(BaseModel):
    customer_id: str
    name: str
    email: str
    phone: str | None = None
    created_at: str
    created_timestamp: datetime

    model_config = ConfigDict(from_attributes=True)

class SaleRecord(BaseModel):
    sale_id: str
    customer_id: str
    amount: float
    sale_date: str
    currency: str

    model_config = ConfigDict(from_attributes=True)

class InventoryRecord(BaseModel):
    product_id: str
    product_name: str
    quantity: int
    unit_price: float
    updated_at: str

    model_config = ConfigDict(from_attributes=True)

class PaginatedRecordsResponse(BaseModel):
    dataset_type: str
    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]]

class DashboardKPIs(BaseModel):
    total_records_processed: int
    accepted_records: int
    rejected_records: int
    duplicate_records: int
    skipped_records: int
    total_jobs: int
    data_quality_rate: float  # Percentage (0 - 100)

class DatasetMetric(BaseModel):
    dataset_type: str
    total: int
    accepted: int
    rejected: int
    duplicates: int
    job_count: int = 0

class DashboardSummaryResponse(BaseModel):
    kpis: DashboardKPIs
    dataset_metrics: list[DatasetMetric]
    recent_jobs: list[ProcessingJobSummary]

class TrendDataPoint(BaseModel):
    job_id: int
    timestamp: str
    filename: str
    dataset_type: str
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    quality_score: float

class DashboardTrendsResponse(BaseModel):
    trends: list[TrendDataPoint]

class UploadResponse(BaseModel):
    job_id: int
    filename: str
    dataset_type: str
    file_hash: str
    status: str
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    skipped_rows: int
    message: str
    processing_duration: float
    is_repeated_upload: bool
