-- ============================================================================
-- EDVEXA DATA QUALITY & ETL MONITORING PLATFORM - DATABASE SCHEMA DDL
-- Target Dialect: SQLite 3.35+ (uses SQLite-specific AUTOINCREMENT syntax)
-- File: schema.sql
-- ============================================================================
-- Architecture:
-- 1. Ingestion & Job Audit:
--    - `processing_jobs`: Tracks each file processing execution, hash, row counts, and status.
--    - `validation_errors`: Granular row- and field-level validation error catalog with error codes.
--    - `job_clean_records`: Job-scoped staging log of clean normalized records accepted per job.
--
-- 2. Domain Business Datasets:
--    - `customers`: Deduplicated canonical customer registry.
--    - `sales`: Deduplicated canonical sales transaction ledger.
--    - `inventory`: Deduplicated canonical product catalog and stock levels.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. PROCESSING JOBS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processing_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename VARCHAR(255) NOT NULL,
    dataset_type VARCHAR(50) NOT NULL,
    file_hash VARCHAR(64) NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'pending',
    total_rows INTEGER NOT NULL DEFAULT 0,
    accepted_rows INTEGER NOT NULL DEFAULT 0,
    rejected_rows INTEGER NOT NULL DEFAULT 0,
    duplicate_rows INTEGER NOT NULL DEFAULT 0,
    skipped_rows INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    processing_duration FLOAT
);

CREATE INDEX IF NOT EXISTS ix_processing_jobs_dataset_type ON processing_jobs (dataset_type);
CREATE INDEX IF NOT EXISTS ix_processing_jobs_file_hash ON processing_jobs (file_hash);
CREATE INDEX IF NOT EXISTS ix_processing_jobs_status ON processing_jobs (status);

-- ----------------------------------------------------------------------------
-- 2. VALIDATION ERRORS
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    row_number INTEGER NOT NULL,
    field_name VARCHAR(100) NOT NULL,
    error_code VARCHAR(100) NOT NULL,
    error_message TEXT NOT NULL,
    original_value TEXT,
    original_row_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_validation_errors_job_id
        FOREIGN KEY (job_id) REFERENCES processing_jobs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_validation_errors_job_id ON validation_errors (job_id);
CREATE INDEX IF NOT EXISTS ix_validation_errors_error_code ON validation_errors (error_code);
CREATE INDEX IF NOT EXISTS ix_val_err_job_row ON validation_errors (job_id, row_number);

-- ----------------------------------------------------------------------------
-- 3. JOB CLEAN RECORDS (Staged per Job Execution)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_clean_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    row_number INTEGER NOT NULL,
    dataset_type VARCHAR(50) NOT NULL,
    business_key VARCHAR(100) NOT NULL,
    record_json TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_job_clean_records_job_id
        FOREIGN KEY (job_id) REFERENCES processing_jobs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_job_clean_records_job_id ON job_clean_records (job_id);
CREATE INDEX IF NOT EXISTS ix_job_clean_rec_job_row ON job_clean_records (job_id, row_number);

-- ----------------------------------------------------------------------------
-- 4. CUSTOMERS DATASET
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS customers (
    customer_id VARCHAR(100) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL,
    phone VARCHAR(50),
    created_at VARCHAR(20) NOT NULL, -- ISO Date: YYYY-MM-DD
    created_timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_customers_customer_id ON customers (customer_id);
CREATE INDEX IF NOT EXISTS ix_customers_email ON customers (email);

-- ----------------------------------------------------------------------------
-- 5. SALES DATASET
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    sale_id VARCHAR(100) PRIMARY KEY,
    customer_id VARCHAR(100) NOT NULL,
    amount FLOAT NOT NULL,
    sale_date VARCHAR(20) NOT NULL, -- ISO Date: YYYY-MM-DD
    currency VARCHAR(10) NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_sales_sale_id ON sales (sale_id);
CREATE INDEX IF NOT EXISTS ix_sales_customer_id ON sales (customer_id);
CREATE INDEX IF NOT EXISTS ix_sales_sale_date ON sales (sale_date);

-- ----------------------------------------------------------------------------
-- 6. INVENTORY DATASET
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inventory (
    product_id VARCHAR(100) PRIMARY KEY,
    product_name VARCHAR(255) NOT NULL,
    quantity INTEGER NOT NULL,
    unit_price FLOAT NOT NULL,
    updated_at VARCHAR(20) NOT NULL -- ISO Date: YYYY-MM-DD
);

CREATE INDEX IF NOT EXISTS ix_inventory_product_id ON inventory (product_id);
