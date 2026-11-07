from datetime import datetime
from typing import Any, Generic, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")

class HealthResponse(BaseModel):
    status: str
    version: str
    database_connected: bool
    database_type: str
    environment: str
    storage_backend: str
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
    total_records_trend: float | None = None
    data_quality_trend: float | None = None
    trend_period_label: str | None = None

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


# =========================================================================
# SURVEY DATA SCRUBBING SCHEMAS
# =========================================================================

class SurveyQuestionItem(BaseModel):
    question_number: str
    question_text: str
    question_type: str
    options: list[str] = Field(default_factory=list)
    suggested_columns: list[str] = Field(default_factory=list)
    confidence: str = "MEDIUM"
    mapping_evidence: str | None = None
    qc_instructions: str | None = None


class SurveyMappingConfirmItem(BaseModel):
    question_number: str
    question_text: str
    question_type: str
    mapped_columns: list[str]
    confidence: str = "CONFIRMED"
    mapping_status: str = "CONFIRMED"
    mapping_evidence: str | None = None
    qc_instructions: str | None = None


class SurveyMappingConfirmRequest(BaseModel):
    mappings: list[SurveyMappingConfirmItem]
    speeding_threshold_seconds: float = 300.0  # Under 5 mins: strong concern
    expected_duration_min_minutes: float = 7.0
    expected_duration_max_minutes: float = 15.0
    page_timer_threshold_seconds: float = 20.0
    straightlining_min_grid_size: int = 4
    timestamp_tolerance_seconds: float = 2.0


class SurveyAuditRunSummary(BaseModel):
    id: int
    audit_id: int | None = None
    strict_accounting_verified: bool = True
    audit_name: str
    qq_filename: str | None = None
    qq_file_hash: str | None = None
    raw_filename: str
    raw_file_hash: str
    selected_sheet: str | None = None
    header_row_index: int = 0
    status: str
    total_respondents: int
    accepted_count: int
    rejected_count: int
    review_required_count: int
    speeding_count: int
    straightlining_count: int
    suspicious_text_count: int
    confirmed_duplicates_count: int
    potential_duplicates_count: int
    routing_violations_count: int
    screener_violations_count: int
    contradiction_count: int
    incomplete_count: int
    timestamp_mismatch_count: int
    started_at: datetime
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    error_message: str | None = None

    model_config = ConfigDict(from_attributes=True)


class SurveyQcFindingItem(BaseModel):
    id: int
    audit_id: int
    respondent_id: int
    respondent_key: str
    issue_type: str
    question_number: str | None = None
    raw_column: str | None = None
    original_answer: str | None = None
    observed_value: str | None = None
    expected_rule: str | None = None
    evidence: str
    severity: str
    review_recommendation: str
    final_decision: str | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SurveyRespondentItem(BaseModel):
    id: int
    audit_id: int
    respondent_key: str
    row_number: int
    disposition: str
    rejection_reasons: list[str] = Field(default_factory=list)
    review_flags: list[str] = Field(default_factory=list)
    calculated_duration_seconds: float | None = None
    exported_duration_seconds: float | None = None
    duration_delta_seconds: float | None = None
    local_completion_hour: int | None = None
    is_overnight: int = 0
    manual_decision: str | None = None
    reviewer_notes: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime
    raw_data: dict[str, Any] = Field(default_factory=dict)
    findings_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class SurveyCoverageItem(BaseModel):
    check_name: str
    category: str
    status: str  # PASSED, FLAGGED, PARTIALLY_CHECKED, NOT_CHECKED, NOT_APPLICABLE
    flagged_count: int = 0
    rule_description: str
    notes: str


class SurveyDashboardKPIs(BaseModel):
    total_respondents: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    review_required_count: int = 0
    speeding_count: int = 0
    straightlining_count: int = 0
    suspicious_text_count: int = 0
    confirmed_duplicates_count: int = 0
    potential_duplicates_count: int = 0
    routing_violations_count: int = 0
    screener_violations_count: int = 0
    contradiction_count: int = 0
    incomplete_count: int = 0
    timestamp_mismatch_count: int = 0
    total_audits: int = 0
    accepted_rate: float = 0.0


class SurveyDashboardSummaryResponse(BaseModel):
    kpis: SurveyDashboardKPIs
    recent_audits: list[SurveyAuditRunSummary] = Field(default_factory=list)
    active_audit: SurveyAuditRunSummary | None = None
    latest_audit_id: int | None = None
    qc_coverage: list[SurveyCoverageItem] = Field(default_factory=list)


class SurveyReviewDecisionRequest(BaseModel):
    decision: str  # ACCEPTED, REJECTED, REVIEW_REQUIRED
    reviewer_notes: str | None = None
    notes: str | None = None
    reviewed_by: str | None = None
    reviewer_name: str | None = None

    @property
    def effective_reviewer(self) -> str:
        return self.reviewer_name or self.reviewed_by or "Admin Reviewer"

    @property
    def effective_notes(self) -> str | None:
        return self.notes or self.reviewer_notes


class SurveyPreviewResponse(BaseModel):
    filename: str
    sheets: list[str] = Field(default_factory=list)
    selected_sheet: str | None = None
    header_row_index: int = 0
    detected_headers: list[str] = Field(default_factory=list)
    total_data_rows: int = 0
    sample_rows: list[dict[str, Any]] = Field(default_factory=list)
    metadata_rows_detected: list[int] = Field(default_factory=list)
    recommended_header_row: int = 0

