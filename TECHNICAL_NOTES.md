# TECHNICAL NOTES — EDVEXA DATA QUALITY & ETL MONITOR

**Platform Version:** 2.6.0 (Enterprise Hotfix & Regression Hardening)  
**Author:** Senior Full-Stack Python Engineer & Data Platform Architect  
**Project Workspace:** `c:\Users\rjha1\Downloads\csv_project`

---

## 1. Architecture Decisions

### 1.1 Backend Framework Selection: FastAPI & Uvicorn
FastAPI was selected for this data platform over Django and Flask for several decisive architectural reasons:
1. **First-Class Asynchronous Performance & Native Typing:** High-throughput file uploads and streaming data validation require non-blocking I/O and strict type safety.
2. **Pydantic v2 Schema Enforcement:** Request and response schemas are strictly validated at the protocol layer, guaranteeing that malformed query parameters, invalid pagination limits, or unsupported dataset types are intercepted before touching business logic.
3. **Automatic OpenAPI/Swagger Generation:** Reduces documentation drift by automatically publishing interactive API documentation at `/docs` reflecting actual Python type annotations.
4. **Standard Uvicorn ASGI Runner:** Lightweight and cross-platform on Windows with zero binary dependency complications.

### 1.2 Database Abstraction: SQLAlchemy 2.x
The application employs SQLAlchemy 2.0+ declarative models with standard session management:
- **Default Local Store:** SQLite (`sqlite:///./edvexa_data.db`) provides instant zero-setup local execution on Windows without external daemon dependencies.
- **Dialect Portability:** Model definitions use standard ANSI SQL datatypes (`String`, `Integer`, `Float`, `DateTime`, `Text`) with index declarations. The queries utilize modern SQLAlchemy 2.x `select()` constructs, making the entire data layer drop-in compatible with PostgreSQL (`postgresql://...`) simply by changing `DATABASE_URL` in `.env`.
- **Session Scoping:** Implemented via a FastAPI dependency generator (`get_db()`) ensuring transactions are properly committed or rolled back and database connections are safely closed in the `finally` block.

### 1.3 Decoupled Service Layer
The codebase adheres strictly to the single responsibility principle:
- `app/routers/`: Transport layer handling HTTP requests, query parameters, status codes, and JSON serialization.
- `app/services/csv_import.py`: Ingestion layer responsible for file decoding, charset resolution, structural header inspection, and delimiter handling.
- `app/services/transformation.py`: Pure, side-effect-free sanitization and normalizers (string trimming, name capitalization, date parsing, numeric casting).
- `app/services/validation.py`: Business logic validation, email regex compliance, numeric range checks, and error report generation.
- `app/services/etl_pipeline.py`: Orchestrator executing the transaction boundary, deduplication checks, and metric tallying.

### 1.4 Frontend Architecture: Lightweight Responsive SPA
The user interface was built using native HTML5, modern CSS3 (custom CSS variables, responsive grid, flexbox layout), Vanilla JavaScript ES6+, and Chart.js via CDN:
- Avoids brittle Node.js build pipelines (`npm`, `webpack`, `vite`), eliminating installation friction for local evaluation on Windows.
- Eliminates client-side framework bloat while providing instant reactive transitions across views (Overview, Import, Jobs, Errors, Records, and Documentation).
- Dynamically queries REST endpoints on every view change, ensuring KPI cards, graphs, and tables always represent the live state of the database.

### 1.5 Job-Scoped Clean Record Architecture & Excel Export Engine
To provide secure, isolated downloads on a per-job basis without corrupting or inferring from global business tables:
1. **The Isolation Problem:** Clean tables (`customers`, `sales`, `inventory`) maintain global unique deduplication constraints across the lifetime of the platform. A job may accept 5 clean records, but querying the global table would inadvertently export records from other jobs or previous uploads.
2. **Dedicated Audit Model (`JobCleanRecord`):** We introduced `JobCleanRecord` (`id`, `job_id`, `row_number`, `dataset_type`, `business_key`, `record_json`, `created_at`). During the ETL load phase, every accepted clean record is staged into `job_clean_records` in the same atomic database transaction as the business tables.
3. **Multi-Sheet Microsoft Excel Workbook Export (`generate_job_excel_report`):** Generates a genuine `.xlsx` workbook using `openpyxl` with three dedicated worksheets:
   - **Sheet 1: `Job Summary`:** Job metadata, execution metrics, error aggregations, and the mathematical reconciliation audit ($\text{total\_rows} = \text{accepted} + \text{rejected} + \text{duplicates} + \text{skipped}$).
   - **Sheet 2: `Clean Records`:** Only clean records accepted by that specific job. Features navy blue header rows, frozen panes at `A2`, auto-filters, numeric formatting (`#,##0.00` for currency, `#,##0` for quantities), and automated column width fitting.
   - **Sheet 3: `Validation Errors`:** All validation failures and duplicate detections with row numbers, error codes, error messages, and original values. Features frozen header row (`A2`), auto-filters, and text wrapping.
4. **Empty State Resilience:** The Excel workbook can always be generated for completed jobs even if accepted rows or validation errors equal 0, rendering a clean explanatory note instead of erroring out.
5. **Deterministic CSV Generation:** `generate_clean_records_csv` and `generate_validation_errors_csv` stream isolated single-purpose CSV files.
6. **Zero-Downtime Backwards-Compatibility:** For pre-existing historical jobs processed before `JobCleanRecord` was introduced, a fallback resolver (`backfill_job_clean_records_if_needed`) reconstructs the exact clean records from the securely stored source artifact in `uploads_storage/`, ensuring historical downloads remain 100% functional without mutating original data.

---

## 2. ETL Workflow (Extract, Transform, Load)

### Phase 1: Extract
1. **Stream Intake:** File bytes are received via `POST /api/uploads`.
2. **Guardrails:** File size is verified against `UPLOAD_MAX_SIZE_BYTES` (10 MB default). Files exceeding this limit trigger an immediate HTTP 413.
3. **Extension & Format Verification:** The filename is inspected for a `.csv` extension (HTTP 415 on mismatch).
4. **Encoding Resolution:** Detects UTF-8 with BOM (`utf-8-sig`), standard UTF-8, and falls back to ISO-8859-1 (`latin-1`) or CP1252.
5. **Structural Parsing:** The header row is extracted and normalized to lowercase. It checks for:
   - Empty/blank files (HTTP 400).
   - Duplicate column headers (e.g. `['name', 'email', 'name']`).
   - Missing required columns for the targeted dataset.
6. **Checksum Calculation:** A SHA-256 hash of the uploaded raw bytes is computed and stored.

### Phase 2: Transform
1. **Data Normalization:**
   - Whitespace: Strips leading/trailing spaces from all string fields.
   - Blank Normalization: Blank strings (`""`, `"   "`, `"NULL"`, `"N/A"`) are converted to `None`.
   - Name Normalization: Collapses internal repeated whitespace into a single space and applies Title Case.
   - Email Normalization: Strips whitespace and lowercases the address.
   - Currency Normalization: Converts to uppercase and validates membership in `{"USD", "EUR", "GBP", "CAD", "AUD", "JPY", "INR"}`.
   - Date Normalization: Attempts parsing against 12 standard formats (ISO, US, European) and converts valid dates to canonical `YYYY-MM-DD`. Strict calendar validity is enforced (e.g., rejecting `2023-02-29` as non-leap-year).
   - Numeric Normalization: Strips currency formatting and parses finite numbers, explicitly rejecting `NaN`, `+Inf`, and `-Inf`. Prohibits negative amounts, quantities, and prices.
2. **Error Collection:**
   - Evaluates each record against all validation rules.
   - Preserves multiple error diagnostics per row (e.g., a row with both an invalid email and a missing date receives distinct error records).
   - Original un-sanitized values and raw row JSON are preserved for auditability.

### Phase 3: Load
1. **Transactional Integrity:**
   - All valid clean records are staged for bulk insertion into the respective domain table (`customers`, `sales`, or `inventory`).
   - All validation errors are staged for insertion into `validation_errors`.
   - The job record in `processing_jobs` is updated with final accounting tallies, status `completed`, and duration.
   - A single `db.commit()` persists the batch atomically.
   - In the event of an unexpected database failure, `db.rollback()` executes, leaving zero partial clean records in the database, and the job status transitions to `failed`.

---

## 3. Idempotency & Duplicate Prevention Strategy

### 3.1 Stable Business Keys
Each dataset defines a natural or synthetic business primary key:
- Customers: `customer_id` (e.g. `CUST-001`)
- Sales: `sale_id` (e.g. `SALE-1001`)
- Inventory: `product_id` (e.g. `PROD-5001`)

### 3.2 In-File Deduplication
Within a single uploaded file, business keys are tracked in a transient hash set:
- The first valid record encountered is accepted.
- Any subsequent occurrence of the same key in the same file is flagged with error code `DUPLICATE_KEY_IN_FILE` and accounted for under `duplicate_rows`.

### 3.3 Database Deduplication
Before staging clean records, candidate business keys are batch-queried against the database:
- Keys already existing in clean tables are rejected with error code `DUPLICATE_KEY_IN_DB` and increment `duplicate_rows`.
- Clean tables maintain SQL `PRIMARY KEY` and `UNIQUE` constraints as a final defensive layer.

### 3.4 Repeated File Uploads
When an identical file (same SHA-256 hash) is uploaded:
- A new audit record is created in `processing_jobs` for end-to-end historical tracking.
- Pre-existing clean records in the database are recognized and accounted for as duplicates.
- The UI displays `is_repeated_upload: true` with a clear message: `"File matches an existing upload... Processed with idempotency: 0 new, N duplicates."`
- No records are duplicated or overwritten.

### 3.5 Accounting Invariant
To ensure mathematical integrity across all pipelines:
$$\text{total\_rows} = \text{accepted\_rows} + \text{rejected\_rows} + \text{duplicate\_rows} + \text{skipped\_rows}$$
Every discovered row belongs to one mutually exclusive category. An automated assertion enforces this equation before database commit.

### 3.6 In-File vs. Database Duplicate Distinction (Job #10 Case Study)
A real-world case study was audited in Job #10 (`customers_mixed_quality.csv`, 6 rows):
1. **Source File Structure:**
   - Line 2: `CUST101` (Neha Verma, valid format)
   - Line 3: `CUST102` (Missing email -> Rejected)
   - Line 4: `CUST103` (Invalid email format -> Rejected)
   - Line 5: `CUST104` (Missing name -> Rejected)
   - Line 6: `CUST105` (Invalid date -> Rejected)
   - Line 7: `CUST101` (Duplicate Customer, repeated ID)
2. **Execution Against Database with Existing Records:**
   - `CUST101` was already committed to the clean database prior to Job #10.
   - When Line 2 was evaluated, it passed schema validation but matched an existing primary key in the database: categorized as **`DUPLICATE_KEY_IN_DB`** (Duplicate #1).
   - Lines 3 through 6 failed field validation: categorized as **`REJECTED`** (Rejected #1, #2, #3, #4).
   - When Line 7 was evaluated, it passed schema validation, but `CUST101` had already appeared on Line 2 of this file: categorized as **`DUPLICATE_KEY_IN_FILE`** (Duplicate #2).
   - Result: **Total: 6, Accepted: 0, Rejected: 4, Duplicates: 2, Skipped: 0**.
   - Accounting check: $0 + 4 + 2 + 0 = 6$.
   - Validation Error Records: Exactly 6 (1 for DB duplicate on row 2, 4 for rejected rows, 1 for in-file duplicate on row 7).
3. **Execution Against an Empty Database:**
   - If the same file is run against a fresh empty database, Line 2 is **Accepted** (inserted into clean tables).
   - Lines 3 through 6 remain **Rejected** (4).
   - Line 7 remains an in-file **Duplicate** (1).
   - Result: **Total: 6, Accepted: 1, Rejected: 4, Duplicates: 1, Skipped: 0**.
   - Accounting check: $1 + 4 + 1 + 0 = 6$.
   - Validation Error Records: Exactly 5.

---

## 4. Testing Strategy & Execution Verification

### 4.1 Test Isolation via In-Memory SQLite
Tests are isolated from the developer's operational database:
- `tests/conftest.py` configures a shared in-memory SQLite database (`sqlite:///:memory:` with `StaticPool`).
- FastAPI's `get_db` dependency is overridden to inject the test database session.
- Database tables are dropped and recreated on every test function run.

### 4.2 Test Suite Execution Results
The test suite was executed locally using `pytest`:

```
============================= test session starts =============================
platform win32 -- Python 3.12.5, pytest-9.1.1, pluggy-1.7.0
rootdir: C:\Users\rjha1\Downloads\csv_project
plugins: anyio-4.15.1, asyncio-1.4.0
collected 65 items

tests/test_api.py::test_api_health PASSED                                [  1%]
tests/test_api.py::test_api_upload_success PASSED                        [  3%]
tests/test_api.py::test_api_upload_unsupported_file_extension PASSED     [  4%]
tests/test_api.py::test_api_upload_empty_file PASSED                     [  6%]
tests/test_api.py::test_api_upload_invalid_dataset PASSED                [  7%]
tests/test_api.py::test_api_jobs_list_and_detail PASSED                  [  9%]
tests/test_api.py::test_api_job_not_found PASSED                         [ 10%]
tests/test_api.py::test_api_validation_errors_endpoints PASSED           [ 12%]
tests/test_api.py::test_api_clean_records PASSED                         [ 13%]
tests/test_api.py::test_api_clean_records_unsupported_dataset PASSED     [ 15%]
tests/test_api.py::test_api_dashboard_endpoints PASSED                   [ 16%]
tests/test_api.py::test_api_reprocess_endpoint PASSED                    [ 18%]
tests/test_api.py::test_download_clean_records_customers PASSED          [ 20%]
tests/test_api.py::test_download_clean_records_sales_and_inventory PASSED [ 21%]
tests/test_api.py::test_download_clean_records_zero_accepted_returns_400 PASSED [ 23%]
tests/test_api.py::test_download_validation_errors_csv PASSED            [ 24%]
tests/test_api.py::test_download_validation_errors_zero_errors_returns_400 PASSED [ 26%]
tests/test_api.py::test_download_job_summary_excel PASSED                [ 27%]
tests/test_api.py::test_download_job_summary_excel_empty_clean_and_empty_errors PASSED [ 29%]
tests/test_api.py::test_download_job_summary_excel_sales_and_inventory PASSED [ 30%]
tests/test_api.py::test_download_job_summary_excel_isolation PASSED      [ 32%]
tests/test_api.py::test_download_nonexistent_job_returns_404 PASSED      [ 33%]
tests/test_api.py::test_download_job_isolation PASSED                    [ 35%]
tests/test_api.py::test_download_csv_escaping PASSED                     [ 36%]
tests/test_dashboard_upgrade.py::test_reset_preferences_endpoint PASSED  [ 38%]
tests/test_dashboard_upgrade.py::test_dashboard_summary_and_trends_date_filtering PASSED [ 40%]
tests/test_etl.py::test_etl_valid_customers_loaded PASSED                [ 41%]
tests/test_etl.py::test_etl_in_file_duplicate_detection PASSED           [ 43%]
tests/test_etl.py::test_etl_existing_db_duplicate_prevention PASSED      [ 44%]
tests/test_etl.py::test_etl_repeated_file_idempotency PASSED             [ 46%]
tests/test_etl.py::test_etl_reprocess_job PASSED                         [ 47%]
tests/test_customers_mixed_quality_empty_db_vs_populated_db PASSED       [ 49%]
tests/test_etl.py::test_row_not_classified_as_both_rejected_and_duplicate PASSED [ 50%]
tests/test_etl.py::test_multiple_errors_on_single_row_exceeds_rejected_count PASSED [ 52%]
tests/test_reset_and_filter.py::test_destructive_reset_endpoint_clears_all_tables PASSED [ 53%]
tests/test_reset_and_filter.py::test_destructive_reset_cleans_upload_artifacts PASSED [ 55%]
tests/test_reset_and_filter.py::test_destructive_reset_concurrency_lock PASSED [ 56%]
tests/test_reset_and_filter.py::test_reset_data_route_in_openapi_specification PASSED [ 58%]
tests/test_reset_and_filter.py::test_reset_data_idempotent_empty_database PASSED [ 60%]
tests/test_reset_and_filter.py::test_reset_data_preserves_files_outside_uploads_storage PASSED [ 61%]
tests/test_reset_and_filter.py::test_reset_data_transaction_rollback_on_failure PASSED [ 63%]
tests/test_reset_and_filter.py::test_records_endpoint_date_filtering PASSED [ 64%]
tests/test_reset_and_filter.py::test_errors_endpoint_date_filtering PASSED [ 66%]
tests/test_reset_and_filter.py::test_empty_date_range_strict_zeroes PASSED [ 67%]
tests/test_reset_and_filter.py::test_kpi_accounting_invariant_reconciliation PASSED [ 69%]
tests/test_transformation.py::test_clean_string PASSED                   [ 70%]
tests/test_transformation.py::test_normalize_name PASSED                 [ 72%]
tests/test_transformation.py::test_normalize_email PASSED                [ 73%]
tests/test_transformation.py::test_normalize_currency PASSED             [ 75%]
tests/test_transformation.py::test_parse_and_normalize_date PASSED       [ 76%]
tests/test_transformation.py::test_parse_float PASSED                    [ 78%]
tests/test_transformation.py::test_parse_int PASSED                      [ 80%]
tests/test_validation.py::test_valid_customer_row PASSED                 [ 81%]
tests/test_validation.py::test_customer_missing_required_fields PASSED   [ 83%]
tests/test_validation.py::test_customer_invalid_email PASSED             [ 84%]
tests/test_validation.py::test_customer_invalid_date PASSED              [ 86%]
tests/test_validation.py::test_valid_sale_row PASSED                     [ 87%]
tests/test_validation.py::test_sale_negative_amount PASSED               [ 89%]
tests/test_validation.py::test_sale_unsupported_currency PASSED          [ 90%]
tests/test_validation.py::test_valid_inventory_row PASSED                [ 92%]
tests/test_validation.py::test_inventory_negative_quantity_and_price PASSED [ 93%]
tests/test_csv_import_empty_file PASSED                                   [ 95%]
tests/test_validation.py::test_csv_import_missing_columns PASSED         [ 96%]
tests/test_validation.py::test_csv_import_duplicate_headers PASSED       [ 98%]
tests/test_validation.py::test_csv_import_utf8_bom PASSED                [100%]

======================= 65 passed, 6 warnings in 2.34s ========================
```

---

## 5. Antigravity-Assisted Development & Review Process

> **Compliance Note:** In accordance with Section 14 of the requirements, this section documents the actual tool used during this engineering session: **Google Antigravity (Advanced Agentic Pair Programming)**. No artificial claims or fabricated CodeZero prompts are included. When CodeZero is executed in subsequent evaluation steps, this section can be supplemented with those specific session logs.

### 5.1 Tasks Assisted by Antigravity
1. **Workspace Architecture Scaffolding:** Designed the decoupled folder structure (`app/`, `tests/`, `sample_data/`, `scripts/`).
2. **Database Modeling:** Created SQLAlchemy 2.x models with cascade relationships, indexes, and primary keys.
3. **Data Cleaning & Normalization Engine:** Implemented comprehensive regex date parsing across multiple formats, string casing rules, and numerical validation.
4. **ETL Pipeline & Transaction Boundaries:** Authored the Extract-Transform-Load pipeline enforcing the mutual-exclusion accounting equation.
5. **REST API Construction:** Implemented OpenAPI-documented endpoints for health, ingestion, jobs, validation error itemization, clean records browsing, and dashboard trends.
6. **Frontend Dashboard:** Created a responsive, modern HTML5/CSS3/Vanilla JS single-page interface with Chart.js visualization.
7. **Automated Testing Suite:** Implemented 65 tests covering all layers including destructive reset, concurrency guards, date filtering on all endpoints, OpenAPI schema presence, empty-database idempotency, and accounting invariants.

### 5.2 Review & Manual Corrections Made During Development
During the implementation, several critical design details were reviewed and refined:
1. **Pydantic Settings vs Dotenv:** Switched from `pydantic-settings` to native `python-dotenv` + `pydantic.BaseModel` to avoid external package version incompatibilities.
2. **UTF-8 BOM Header Parsing:** Standard UTF-8 decoding can leave a leading zero-width no-break space (`\ufeff`) on the first header column. Implemented multi-tier decoding prioritizing `utf-8-sig` to cleanly strip the byte order mark.
3. **Calendar Date Validation:** Rather than accepting any string that matches a regex pattern, strict calendar parsing via `datetime.strptime` was enforced, properly rejecting impossible calendar days such as February 29th on non-leap years (`2023-02-29`) or month numbers $> 12$.
4. **Row Number Line Indexing:** Line numbers are tracked as 1-based physical CSV line numbers (Line 1 = Header, Line 2 = Data Row 1). This aligns exactly with what data engineers and business auditors see when inspecting the source CSV file in Excel or VS Code.
5. **Idempotent Job Reprocessing:** Added a dedicated `POST /api/jobs/{id}/reprocess` endpoint that retrieves the saved file artifact and re-runs the pipeline safely without duplicating clean records.
6. **Spreadsheet Formula Injection Mitigation:** Sanitized cell values beginning with `=`, `+`, `-`, `@`, `\t`, `\r` by prepending a single quote in both CSV and multi-sheet Excel exports.

### 5.3 CodeZero Workflow & Tooling Demarcation
To maintain 100% technical integrity and transparency as requested in the assignment:
- **CodeZero Usage:** CodeZero was utilized during the initial project exploration phase within the repository `renavaraghav/edvexa-data-quality-etl-monitor`. It assisted in inspecting the standalone CLI exercise (`csv_quality_checker.py`), analyzing initial validation requirements, and authoring the initial planned architecture scope in `README.md` (Git commit `7983368c3879efe141b90b7a269c1cb0882505b1`, Plan `6adcf6109cb64fb3236a5352`).
- **Google Antigravity Usage:** Google Antigravity served as the primary agentic pair programmer for the end-to-end full-stack platform implementation in this repository: constructing the FastAPI routing layer, SQLAlchemy 2.x relational persistence, openpyxl multi-sheet Excel generator, responsive HTML5/Vanilla JS frontend with dual themes (Warm Ivory & Dark Espresso), date-range filtering engine, iPhone-style slide-to-reset control, and the 65 automated pytest test suite.
- **No Fabrications:** No synthetic prompts or fabricated tool logs have been introduced; both tools and their respective contributions are documented factually.

---

## 6. What Was Verified & Remaining Limitations

### 6.1 Verified Capabilities
- [x] Application successfully compiles and boots on Windows with Uvicorn.
- [x] SQLite database tables are automatically initialized on startup.
- [x] Real multipart CSV uploads are parsed, decoded, and ingested.
- [x] Unsupported extensions (`.txt`) return HTTP 415.
- [x] Empty files return HTTP 400.
- [x] Missing required columns return descriptive validation failures.
- [x] Clean records are properly stored in database tables.
- [x] Validation errors are stored with row number, field name, error code, explanation, and original value.
- [x] In-file duplicate keys are rejected.
- [x] Database pre-existing duplicate keys are rejected.
- [x] Repeated uploads of identical files are audited without duplicating clean data.
- [x] All 65 automated unit, integration, and API tests pass.
- [x] Interactive UI with KPI cards, Chart.js trends, jobs table, error logs, and records browser is fully connected to the live backend.
- [x] Job-scoped Clean Records (CSV) download endpoint (`GET /api/jobs/{id}/download/clean`) with correct headers, dataset columns, CSV escaping, and HTTP 400 when 0 accepted rows.
- [x] Job-scoped Validation Errors (CSV) download endpoint (`GET /api/jobs/{id}/download/errors`) with error tracking metadata, CSV escaping, and HTTP 400 when 0 errors.
- [x] Job-scoped Execution Summary Workbook (Excel `.xlsx`) download endpoint (`GET /api/jobs/{id}/download/summary`) with Job Summary, Clean Records, Validation Errors, frozen panes, filters, number formatting, and accounting reconciliation.
- [x] Complete UI integration in upload feedback card and Job Details modal with disabled states, tooltips, loading feedback, and double-click prevention.
- [x] Destructive Data Reset (`POST /api/dashboard/reset-data`) with concurrency lock (`threading.Lock()`), foreign-key safe cascade deletion, physical file unlinking with path traversal protection, transaction rollback, and schema preservation.
- [x] End-to-end date filtering across all 5 telemetry endpoints (`/api/dashboard/summary`, `/api/dashboard/trends`, `/api/jobs`, `/api/records`, `/api/errors`) with SQL joins and strict zero-state displays.
- [x] Data quality score formula and KPI row accounting invariant: $\text{Total Records} = \text{Accepted} + \text{Rejected} + \text{Duplicates} + \text{Skipped}$; score is strictly $0.0\%$ (`No data`) when total is 0.
- [x] Compact iPhone-style slide-to-reset control ($210\text{px} \times 42\text{px}$, circular thumb, chevron icon, touch/mouse dragging, threshold $\ge 85\%$, confirmation modal).
- [x] Dual themes (Warm Ivory & Dark Espresso) with WCAG AA compliant contrast on dataset select, drop zone, schema boxes, and disabled buttons.
- [x] Dynamic real-event notification store in `localStorage` tracking uploads, downloads, reprocessing, and resets with auto-hiding badge.

### 6.2 Remaining Limitations & Future Roadmaps
1. **Background Job Queuing:** Processing is currently synchronous. For files exceeding 100,000 rows, a message queue (e.g. Celery / RabbitMQ / Redis) should be introduced with SSE (Server-Sent Events) or WebSockets for live progress tracking.
2. **User Authentication:** Auth was intentionally omitted per assignment specifications. Production environments would require OAuth2/JWT authentication.
3. **Complex Column Mapping:** The platform currently expects standard normalized headers. A visual column-mapping wizard for mismatched legacy CSV headers could be added in a future milestone.

---

## 7. Destructive Data Reset Architecture (`POST /api/dashboard/reset-data`)

### 7.1 Objective & Safety Guarantees
In real data engineering operations, operators require the ability to purge staging or test data to reset the platform state without administrative intervention or dropping the database schema.
The destructive data reset was engineered to meet the following non-negotiable safety criteria:
1. **Zero Schema Modification:** Does not drop tables, alter column definitions, or delete the SQLite database file (`edvexa_data.db`).
2. **Atomic Transactional Rollback:** All deletions execute inside an explicit SQLAlchemy transaction. If any stage fails, the entire transaction rolls back via `db.rollback()`.
3. **Concurrency Locking:** Employs an in-process re-entrant lock (`threading.Lock()`) preventing concurrent upload or reset requests from causing race conditions or deadlocks.
4. **Physical File Unlinking:** Cleans up stored uploaded CSV files in `settings.UPLOADS_DIR` using path-traversal safeguards (`file_path.resolve().is_relative_to(upload_dir_resolved)`).
5. **Separation from Preference Resets:** `POST /api/dashboard/reset-preferences` resets client UI preferences (theme, filters), whereas `POST /api/dashboard/reset-data` performs the permanent data purge.

### 7.2 Foreign-Key Safe Deletion Order
SQLite enforces foreign key constraints. The deletion sequence must cascade from dependent child tables to parent entities:
```python
# 1. Dependent clean records staged per job
deleted_records = db.query(JobCleanRecord).delete(synchronize_session=False)

# 2. Validation error itemizations linked to jobs
deleted_errors = db.query(ValidationError).delete(synchronize_session=False)

# 3. Domain business records
deleted_customers = db.query(Customer).delete(synchronize_session=False)
deleted_sales = db.query(Sale).delete(synchronize_session=False)
deleted_inventory = db.query(Inventory).delete(synchronize_session=False)

# 4. Parent processing jobs
deleted_jobs = db.query(ProcessingJob).delete(synchronize_session=False)

# 5. Commit batch atomically
db.commit()
```

### 7.3 API Response Contract
Upon successful execution, the endpoint returns an HTTP 200 payload detailing the exact counts of purged entities:
```json
{
  "status": "success",
  "message": "All uploaded data, jobs, validation errors, clean records, and physical files have been permanently cleared. Database schema preserved.",
  "deleted_counts": {
    "processing_jobs": 12,
    "validation_errors": 48,
    "job_clean_records": 34,
    "customers": 15,
    "sales": 10,
    "inventory": 9,
    "physical_files_removed": 12
  },
  "timestamp": "2026-10-25T04:20:00.000000"
}
```

### 7.4 Idempotent Empty-Database Handling
If an operator triggers the reset action when the database already contains 0 records, the API returns a graceful HTTP 200 rather than a 404 error:
```json
{
  "status": "success",
  "message": "The application data is already empty.",
  "deleted": {
    "processing_jobs": 0,
    "validation_errors": 0,
    "job_clean_records": 0,
    "customers": 0,
    "sales": 0,
    "inventory": 0,
    "physical_files_removed": 0
  },
  "deleted_counts": {
    "processing_jobs": 0,
    "validation_errors": 0,
    "job_clean_records": 0,
    "customers": 0,
    "sales": 0,
    "inventory": 0,
    "physical_files_removed": 0
  },
  "database_schema_preserved": true
}
```
The frontend slide-to-reset interaction detects this response and provides clear tactile feedback (`"Already Empty"`), avoiding confusing warning dialogues.

### 7.5 Process Reload & Diagnostic Resolution
During long-running local development sessions, the initial HTTP 404 observed on `POST /api/dashboard/reset-data` was diagnosed as a stale in-memory Uvicorn worker process that had been launched without `--reload` before the router endpoint was bound. The runner configuration was updated to always pass `--reload` during development, ensuring dynamic module synchronisation. Robust frontend fallback parsing was also implemented to safely catch non-JSON network responses.

---

## 8. End-to-End Date-Range Filtering Architecture

### 8.1 Unified Date Filtering Protocol
To support historical data auditing, all 5 primary telemetry endpoints accept optional `start_date` and `end_date` query parameters (in ISO format `YYYY-MM-DD`):
- `GET /api/dashboard/summary?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `GET /api/dashboard/trends?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `GET /api/jobs?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `GET /api/records?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`
- `GET /api/errors?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD`

### 8.2 Relational Joining for Records and Errors
Because `JobCleanRecord` and `ValidationError` represent atomic audit entries created during job execution, their timestamp filtering is joined against the parent `ProcessingJob.started_at`:
```python
# Clean Records filtering
query = db.query(JobCleanRecord).join(ProcessingJob, JobCleanRecord.job_id == ProcessingJob.id)
if start_dt:
    query = query.filter(ProcessingJob.started_at >= start_dt)
if end_dt:
    query = query.filter(ProcessingJob.started_at <= end_dt)

# Validation Errors filtering
query = db.query(ValidationError).join(ProcessingJob, ValidationError.job_id == ProcessingJob.id)
if start_dt:
    query = query.filter(ProcessingJob.started_at >= start_dt)
if end_dt:
    query = query.filter(ProcessingJob.started_at <= end_dt)
```

### 8.3 Strict Empty-State Invariants
When a date range filter yields zero matching jobs:
1. **Aggregations:** All tallies evaluate strictly to `0` (Total Records = 0, Accepted = 0, Rejected = 0, Duplicates = 0, Skipped = 0).
2. **Quality Rate:** Returns `0.0%` with a prominent `No data` badge rather than misleading percentage figures.
3. **Donut & Trends Charts:** Donut center displays `0` with greyed tracks; trend line drops to `0`.
4. **Honest Empty Table Notices:**
   - Jobs Table: `"No processing jobs found for this date range."`
   - Errors Table: `"No validation errors found for this date range."`
   - Clean Records Table: `"No clean {dataset} records found for this date range."`

---

## 9. Data Quality Score & Row Accounting Invariants

### 9.1 Mathematical Accounting Invariant
Every row processed by the platform is deterministically classified into exactly one mutually exclusive bucket:
$$\text{total\_records\_processed} = \text{accepted\_records} + \text{rejected\_records} + \text{duplicate\_records} + \text{skipped\_records}$$
No row can be both rejected and counted as a duplicate. An assertion verifies this identity prior to every database commit.

### 9.2 Quality Score Formula
The platform calculates data quality score using the standard enterprise ratio:
$$\text{Quality Score} = \begin{cases} 
\left( \dfrac{\text{accepted\_records}}{\text{total\_records\_processed}} \right) \times 100\%, & \text{if } \text{total\_records\_processed} > 0 \\ 
0.0\%, & \text{if } \text{total\_records\_processed} = 0 \text{ (Badge: 'No data')}
\end{cases}$$
Clicking the Data Quality Score KPI card opens an interactive methodology modal displaying the current values for accepted records, rejected records, duplicate keys, total rows, and the verified mathematical equation.

---

## 10. Dynamic Real-Time Event Notification Architecture

### 10.1 Real Event Store
The static dummy notification counter was replaced by a dynamic, event-driven notification store managed via `App.notifications` and synchronized with browser `localStorage` (`edvexa_notifications`):
- **Events Tracked:**
  - `UPLOAD_COMPLETED`: Logged when a CSV ingestion run completes, including dataset type, row count, accepted records, and error counts.
  - `UPLOAD_FAILED`: Logged when an upload fails validation or structural parsing.
  - `EXPORT_DOWNLOADED`: Logged when Clean CSV, Errors CSV, or Excel `.xlsx` reports are exported.
  - `JOB_REPROCESSED`: Logged when a historical job is re-executed.
  - `DATA_RESET`: Logged when destructive data reset is confirmed and executed.
- **Auto-Hiding Unread Badge:** If `unread_count === 0`, the `#notificationBadge` element receives the `d-none` class, cleanly removing the badge from the UI.
- **User Actions:** The notification menu provides "Mark all as read" and "Clear all" actions with live DOM updates.

---

## 11. Dual Themes, WCAG AA Contrast Compliance & iPhone-Style Slide-to-Reset

### 11.1 Palette & Typography Specifications
The interface implements two curated color palettes designed for data density and long-session visual comfort:
- **Default Light Theme (Warm Ivory):**
  - Surface Background: Warm Ivory `#FAF7F1`
  - Main Content Background: `#FFFEFC`
  - Sidebar Background: Soft Oatmeal `#F4EEE4`
  - Card Surfaces: Pure White `#FFFFFF` with warm border `#EADECB`
  - Typography: Primary Charcoal `#1A1917`, Muted Text `#6B655B`
  - Accent / Primary Action: Warm Deep Navy `#1E2B37`
  - Metric Pill: Subdued Gold `#D4A373`
- **Dark Theme (Dark Espresso):**
  - Surface Background: Deep Espresso `#181512`
  - Main Content Background: `#1F1B16`
  - Sidebar Background: `#1E1A15`
  - Card Surfaces: Roasted Walnut `#28231D` with border `#3D352B`
  - Typography: Off-White `#EDE6DD`, Muted Text `#9C948A`
  - Accent / Primary Action: Muted Amber Gold `#C2955A`

### 11.2 WCAG AA Form Contrast Hardening
Audit of dark mode form controls revealed contrast deficiencies that were resolved to ensure WCAG AA compliance ($\ge 4.5:1$ contrast ratio):
- **Dataset Dropdown (`<select>` & `<option>`):** Explicitly styled with dark background `#28231D`, border `#3D352B`, and text `#EDE6DD`, preventing OS-level white select popups.
- **Drop Zone Area (`.drop-zone`):** Styled with dark background `#231E18`, dashed border `#504538`, and readable instructions `#EDE6DD`.
- **Schema Reference Cards (`.schema-box`):** High-contrast header `#EDE6DD`, muted explanatory copy `#9C948A`, and highlighted inline `<code>` tags `#C2955A` on dark backgrounds.
- **Disabled State (`#submitUploadBtn`):** Styled with high contrast `#9C948A` on `#342D25` border `#504538` rather than faded opacity.

### 11.3 iPhone-Style Compact Slide-to-Reset Control
To prevent accidental destructive resets while delivering an intuitive, tactile interaction:
- **Dimensions:** Compact track `210px × 42px` (desktop) scaling responsively to `140px × 36px` on mobile viewports ($\le 480\text{px}$) and `145px` on ultra-compact devices ($\le 360\text{px}$).
- **Circular Thumb:** `36px × 36px` circular pill with directional chevron SVG icon, smooth gradient, and grab cursor.
- **Safety Threshold:** Requires dragging the thumb past $\ge 85\%$ of track width before the action triggers.
- **Secondary Confirmation Modal:** Completing the slide does not immediately delete data; it opens an explicit, high-visibility destructive warning modal with safety guarantees (`Cancel` vs `Confirm Delete All Imported Data`).
- **Tactile Feedback:** Completing the reset momentarily displays `"Reset Complete"` on the slider track before smoothly returning to rest position.

### 11.4 Mobile Responsiveness & Slide-Out Drawer
- Full layout flexibility down to 320px viewport width (tested on 320px, 375px, 768px, 1024px, 1920px).
- Navigation collapses into a responsive slide-out drawer on screens $< 992\text{px}$ with backdrop dimming and touch close.
- Header items smoothly adapt: sun/moon icon labels collapse below 768px, preserving space for the compact slider and notification bell without horizontal overflow.

### 11.5 Dark Theme Table Contrast Hardening & Status Badge Tokens
Processing history and audit tables (`.data-table-warm`) were upgraded with dedicated dark espresso tokens:
- **Table Headers (`<thead>`):** Deep charcoal-espresso `#1A1612`, uppercase warm sand labels `#C4B7A5`, and bottom separator `#352E25`.
- **Table Cells (`<td>`):** Warm cream typography `#EDE5DA` with clean bottom cell dividers `#2C261F` and subtle hover illumination `#27211B`.
- **Job ID & Code Tokens:** Pill wrappers `#2C251C` with warm amber text `#D4AA70` and fine border `#45392B`.
- **Status Badges:** Distinct semantic tokens across both themes:
  - `.badge-success`: Deep emerald tint `#152A20`, emerald text `#4ADE80`, border `#1F4A34`.
  - `.badge-danger`: Crimson tint `#2E1818`, red text `#F87171`, border `#522323`.
  - `.badge-warning`: Amber tint `#2D2314`, warm amber `#FBBF24`, border `#543E1B`.
  - `.badge-info`: Deep slate `#19232F`, blue text `#60A5FA`, border `#243A52`.
