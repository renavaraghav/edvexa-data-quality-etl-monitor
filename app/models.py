from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    DateTime,
    ForeignKey,
    Text,
    Index
)
from sqlalchemy.orm import relationship
from app.database import Base

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

class ProcessingJob(Base):
    __tablename__ = "processing_jobs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    filename = Column(String(255), nullable=False)
    dataset_type = Column(String(50), nullable=False, index=True)
    file_hash = Column(String(64), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="pending", index=True)
    
    total_rows = Column(Integer, default=0, nullable=False)
    accepted_rows = Column(Integer, default=0, nullable=False)
    rejected_rows = Column(Integer, default=0, nullable=False)
    duplicate_rows = Column(Integer, default=0, nullable=False)
    skipped_rows = Column(Integer, default=0, nullable=False)
    
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=utc_now, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    processing_duration = Column(Float, nullable=True)  # in seconds

    # Cascade delete validation errors when job is removed
    validation_errors = relationship(
        "ValidationError",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="ValidationError.row_number"
    )

    # Job-scoped clean records accepted by this specific job
    clean_records = relationship(
        "JobCleanRecord",
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="JobCleanRecord.row_number"
    )

class ValidationError(Base):
    __tablename__ = "validation_errors"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    field_name = Column(String(100), nullable=False)
    error_code = Column(String(100), nullable=False, index=True)
    error_message = Column(Text, nullable=False)
    original_value = Column(Text, nullable=True)
    original_row_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    job = relationship("ProcessingJob", back_populates="validation_errors")

    __table_args__ = (
        Index("ix_val_err_job_row", "job_id", "row_number"),
    )

class JobCleanRecord(Base):
    __tablename__ = "job_clean_records"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("processing_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    dataset_type = Column(String(50), nullable=False)
    business_key = Column(String(100), nullable=False)
    record_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    job = relationship("ProcessingJob", back_populates="clean_records")

    __table_args__ = (
        Index("ix_job_clean_rec_job_row", "job_id", "row_number"),
    )

class Customer(Base):
    __tablename__ = "customers"

    customer_id = Column(String(100), primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    phone = Column(String(50), nullable=True)
    created_at = Column(String(20), nullable=False)  # ISO Date YYYY-MM-DD
    created_timestamp = Column(DateTime, default=utc_now, nullable=False)

class Sale(Base):
    __tablename__ = "sales"

    sale_id = Column(String(100), primary_key=True, index=True)
    customer_id = Column(String(100), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    sale_date = Column(String(20), nullable=False, index=True)  # ISO Date YYYY-MM-DD
    currency = Column(String(10), nullable=False)

class Inventory(Base):
    __tablename__ = "inventory"

    product_id = Column(String(100), primary_key=True, index=True)
    product_name = Column(String(255), nullable=False)
    quantity = Column(Integer, nullable=False)
    unit_price = Column(Float, nullable=False)
    updated_at = Column(String(20), nullable=False)  # ISO Date YYYY-MM-DD


# =========================================================================
# SURVEY DATA SCRUBBING MODULE MODELS
# Strictly isolated from Customer, Sales, and Inventory ETL tables.
# =========================================================================

class SurveyAuditRun(Base):
    """
    Independent audit run for Survey Data Scrubbing.
    Tracks uploaded Questionnaire (QQ), raw respondent dataset, mapping status,
    and aggregate quality metrics across all performed checks.
    """
    __tablename__ = "survey_audit_runs"

    def __init__(self, **kwargs):
        if "file_hash" in kwargs and "raw_file_hash" not in kwargs:
            kwargs["raw_file_hash"] = kwargs.pop("file_hash")
        super().__init__(**kwargs)

    @property
    def file_hash(self) -> str:
        return self.raw_file_hash or ""

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    audit_name = Column(String(255), nullable=False, default="Survey Quality Audit")
    qq_filename = Column(String(255), nullable=True)
    qq_file_hash = Column(String(64), nullable=True, index=True)
    raw_filename = Column(String(255), nullable=False)
    raw_file_hash = Column(String(64), nullable=False, index=True)
    selected_sheet = Column(String(100), nullable=True)
    header_row_index = Column(Integer, default=0, nullable=False)
    status = Column(String(30), nullable=False, default="pending", index=True)  # pending, processing, completed, failed

    # Invariant: total_respondents == accepted_count + rejected_count + review_required_count
    total_respondents = Column(Integer, default=0, nullable=False)
    accepted_count = Column(Integer, default=0, nullable=False)
    rejected_count = Column(Integer, default=0, nullable=False)
    review_required_count = Column(Integer, default=0, nullable=False)

    # Detailed quality flag counts
    speeding_count = Column(Integer, default=0, nullable=False)
    straightlining_count = Column(Integer, default=0, nullable=False)
    suspicious_text_count = Column(Integer, default=0, nullable=False)
    confirmed_duplicates_count = Column(Integer, default=0, nullable=False)
    potential_duplicates_count = Column(Integer, default=0, nullable=False)
    routing_violations_count = Column(Integer, default=0, nullable=False)
    screener_violations_count = Column(Integer, default=0, nullable=False)
    contradiction_count = Column(Integer, default=0, nullable=False)
    incomplete_count = Column(Integer, default=0, nullable=False)
    timestamp_mismatch_count = Column(Integer, default=0, nullable=False)

    # JSON metadata for checks performed / not performed and timing rules
    checks_summary_json = Column(Text, nullable=True)
    timing_rules_json = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    started_at = Column(DateTime, default=utc_now, nullable=False)
    completed_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)

    # Cascade relationships
    respondents = relationship(
        "SurveyRespondent",
        back_populates="audit_run",
        cascade="all, delete-orphan",
        order_by="SurveyRespondent.row_number"
    )
    findings = relationship(
        "SurveyQcFinding",
        back_populates="audit_run",
        cascade="all, delete-orphan"
    )
    mappings = relationship(
        "SurveyQuestionMapping",
        back_populates="audit_run",
        cascade="all, delete-orphan",
        order_by="SurveyQuestionMapping.id"
    )


class SurveyRespondent(Base):
    """
    Individual survey respondent record preserving original imported data
    along with disposition status and human review audit trail.
    """
    __tablename__ = "survey_respondents"

    def __init__(self, **kwargs):
        if "raw_response_json" in kwargs and "raw_data_json" not in kwargs:
            kwargs["raw_data_json"] = kwargs.pop("raw_response_json")
        super().__init__(**kwargs)

    @property
    def raw_response_json(self) -> str:
        return self.raw_data_json

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    audit_id = Column(Integer, ForeignKey("survey_audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    respondent_key = Column(String(150), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    disposition = Column(String(30), nullable=False, index=True)  # ACCEPTED, REJECTED, REVIEW_REQUIRED
    
    rejection_reasons_json = Column(Text, nullable=True)
    review_flags_json = Column(Text, nullable=True)
    
    calculated_duration_seconds = Column(Float, nullable=True)
    exported_duration_seconds = Column(Float, nullable=True)
    duration_delta_seconds = Column(Float, nullable=True)
    local_completion_hour = Column(Integer, nullable=True)
    is_overnight = Column(Integer, default=0, nullable=False)

    raw_data_json = Column(Text, nullable=False)

    manual_decision = Column(String(30), nullable=True)  # ACCEPTED, REJECTED, PENDING
    reviewer_notes = Column(Text, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    audit_run = relationship("SurveyAuditRun", back_populates="respondents")
    findings = relationship(
        "SurveyQcFinding",
        back_populates="respondent",
        cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_survey_resp_audit_row", "audit_id", "row_number"),
        Index("ix_survey_resp_audit_disp", "audit_id", "disposition"),
    )


class SurveyQcFinding(Base):
    """
    Issue-level evidence and diagnostic finding for an individual respondent.
    One respondent may have zero, one, or multiple findings across QC checks.
    """
    __tablename__ = "survey_qc_findings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    audit_id = Column(Integer, ForeignKey("survey_audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    respondent_id = Column(Integer, ForeignKey("survey_respondents.id", ondelete="CASCADE"), nullable=False, index=True)
    respondent_key = Column(String(150), nullable=False, index=True)
    
    issue_type = Column(String(100), nullable=False, index=True)
    question_number = Column(String(100), nullable=True)
    raw_column = Column(String(100), nullable=True)
    original_answer = Column(Text, nullable=True)
    observed_value = Column(Text, nullable=True)
    expected_rule = Column(Text, nullable=True)
    evidence = Column(Text, nullable=False)
    severity = Column(String(30), nullable=False, default="MEDIUM")  # HIGH, MEDIUM, LOW, INFO
    review_recommendation = Column(String(50), nullable=False, default="REVIEW_REQUIRED")  # REJECT, REVIEW_REQUIRED, NOTE
    final_decision = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    audit_run = relationship("SurveyAuditRun", back_populates="findings")
    respondent = relationship("SurveyRespondent", back_populates="findings")

    __table_args__ = (
        Index("ix_qc_find_audit_type", "audit_id", "issue_type"),
    )


class SurveyQuestionMapping(Base):
    """
    Mapping relationship between Questionnaire questions and raw data columns.
    Stores confidence, mapping status, and user confirmation.
    """
    __tablename__ = "survey_question_mappings"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    audit_id = Column(Integer, ForeignKey("survey_audit_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    question_number = Column(String(50), nullable=False)
    question_text = Column(Text, nullable=False)
    question_type = Column(String(50), nullable=False)  # single_choice, multiple_choice, matrix_grid, open_ended, numeric, rating_scale, screener, timer, metadata
    mapped_columns_json = Column(Text, nullable=False)  # JSON list of column names
    mapping_status = Column(String(30), nullable=False, default="SUGGESTED")  # CONFIRMED, SUGGESTED, MANUAL, UNMAPPED
    confidence = Column(String(20), nullable=False, default="MEDIUM")  # HIGH, MEDIUM, LOW, UNMAPPED
    mapping_evidence = Column(Text, nullable=True)
    qc_instructions = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)

    audit_run = relationship("SurveyAuditRun", back_populates="mappings")

    __table_args__ = (
        Index("ix_sqm_audit_qnum", "audit_id", "question_number"),
    )

