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
