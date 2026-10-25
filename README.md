# Edvexa Data Quality & ETL Monitor

> **Subtitle:** Reliable Data. Clean Pipelines. Trusted Decisions.  
> **Author:** Edvexa Software Engineering Intern Candidate  
> **Platform Version:** 2.6.0  
> **Technology Stack:** Python 3.11+, FastAPI, SQLAlchemy 2.x, Pydantic, Pandas, SQLite / PostgreSQL, Vanilla JS, Chart.js

---

## 1. Project Overview

**Edvexa Data Quality & ETL Monitor** is a production-grade, locally runnable web application and data quality observability engine designed to ingest, validate, sanitize, and load operational CSV datasets (Customers, Sales, and Inventory).

It guarantees strict data integrity before raw records can enter downstream clean storage tables. Every ingested row is accounted for deterministically under the fundamental invariant:

$$\text{total\_rows} = \text{accepted\_rows} + \text{rejected\_rows} + \text{duplicate\_rows} + \text{skipped\_rows}$$

The application provides:
- **Resilient CSV Ingestion:** Automatic charset detection (UTF-8, UTF-8 BOM, Latin-1), schema enforcement, and 10 MB upload guardrails.
- **Granular Field Validation:** Standardized email validation, calendar date verification, whitespace stripping, title capitalization, numeric bounds checking, and supported currency enforcement.
- **Idempotency & Duplicate Prevention:** SHA-256 file checksum tracking, in-file duplicate detection, and database unique key constraints that prevent double-insertion on repeated uploads.
- **Interactive Visual Dashboard:** Real-time KPI summary cards, historical ingestion quality trend charts (Chart.js), dataset breakdowns, paginated job history, validation error reports, and a clean records browser.
- **Interactive OpenAPI Documentation:** Fully typed Swagger UI (`/docs`) and ReDoc (`/redoc`) endpoints.
- **Export & Download Engine:** Direct job-scoped downloads for Clean Records (CSV), Validation Errors (CSV), and comprehensive Job Audit Workbooks (Excel `.xlsx`).
- **Administrative Destructive Reset:** iPhone-style compact slide-to-reset with concurrency-locked transactional data deletion (`POST /api/dashboard/reset-data`) clearing uploaded records while strictly preserving database schema, with idempotent empty-state handling (`HTTP 200`).
- **End-to-End Date Range Filtering:** Synchronized date filtering across dashboard summary, trends, jobs, clean records, and validation errors with strict empty-state displays.
- **Dynamic Real-Event Notifications:** Event-driven notification store tracking uploads, exports, reprocessing, and reset actions with unread badge count and individual item dismissal.
- **Dual Aesthetic Themes & WCAG AA Contrast:** Warm beige & ivory default theme and dark espresso mode with audited 4.5:1 text contrast across all form controls, drop zones, and data tables.
- **Automated Test Suite:** 65 comprehensive unit, integration, ETL, reset, date-filtering, and API tests executed via `pytest`.

---

## 2. System Architecture

```
edvexa-data-quality/
│
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI application factory, middleware, lifecycle & routing
│   ├── config.py                   # Pydantic & environment configuration settings
│   ├── database.py                 # SQLAlchemy engine, session maker & schema initialization
│   ├── models.py                   # SQLAlchemy 2.x models (ProcessingJob, ValidationError, Clean Tables)
│   ├── schemas.py                  # Pydantic v2 validation models & response serialization
│   ├── services/
│   │   ├── __init__.py
│   │   ├── csv_import.py           # Robust file parsing, header verification & charset decoding
│   │   ├── transformation.py       # Deterministic cleaning, casing, date & numeric parsers
│   │   ├── validation.py           # Schema rules, email regex, bounds checks & error generation
│   │   └── etl_pipeline.py         # Atomic ETL execution, transaction management & idempotency
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── uploads.py              # POST /api/uploads multipart CSV endpoint
│   │   ├── jobs.py                 # GET /api/jobs, GET /api/jobs/{id}, POST /api/jobs/{id}/reprocess
│   │   ├── errors.py               # GET /api/jobs/{id}/errors, GET /api/errors
│   │   ├── records.py              # GET /api/records (Customers, Sales, Inventory)
│   │   └── dashboard.py            # GET /api/dashboard/summary, GET /api/dashboard/trends
│   ├── templates/
│   │   └── index.html              # Responsive SPA layout (Sidebar, Overview, Ingestion, Browser)
│   └── static/
│       ├── css/
│       │   └── styles.css          # Navy typography, light backgrounds, restrained blue accents
│       └── js/
│           ├── app.js              # SPA navigation, modal dialogs, health polling & toast alerts
│           └── dashboard.js        # Chart.js charts, filters, upload drag-and-drop & tables
│
├── tests/
│   ├── __init__.py
│   ├── conftest.py                 # Test fixtures, in-memory SQLite isolation & TestClient
│   ├── test_transformation.py      # Tests for cleaners, string normalizers & date/numeric parsers
│   ├── test_validation.py          # Field validation rules, regex, bounds & CSV structural tests
│   ├── test_etl.py                 # ETL load, duplicate detection, accounting rule & idempotency
│   ├── test_api.py                 # Health, upload, jobs, errors, records, dashboard & downloads
│   ├── test_dashboard_upgrade.py   # Preference resets & dashboard date-range filtering tests
│   └── test_reset_and_filter.py    # Destructive reset safety, concurrency lock, OpenAPI, idempotency & joins
│
├── sample_data/
│   ├── customers.csv               # 13 synthetic records (valid, duplicate, invalid email/date)
│   ├── sales.csv                   # 12 synthetic records (valid, negative amount, currency errors)
│   └── inventory.csv               # 12 synthetic records (valid, negative qty/price, malformed rows)
│
├── scripts/
│   └── generate_sample_data.py     # Deterministic sample dataset generator
│
├── requirements.txt                # Pinned dependencies
├── .env.example                    # Non-secret environment defaults
├── .gitignore                      # Git exclusions (venv, databases, caches, logs)
├── README.md                       # Comprehensive operational guide
├── TECHNICAL_NOTES.md              # Engineering decisions, trade-offs & development notes
└── DEMO_CHECKLIST.md               # 13-step verification walkthrough
```

---

## 3. Prerequisites

- **Operating System:** Windows 10/11, macOS, or Linux.
- **Python:** Python 3.11, 3.12, or newer.
- **Git:** Installed and available on system PATH.
- **Modern Web Browser:** Chrome, Edge, Firefox, or Safari with JavaScript enabled.

---

## 4. Local Setup & Installation (Windows PowerShell / Command Prompt)

### Step 1: Clone or Navigate to the Workspace
```powershell
cd c:\Users\rjha1\Downloads\csv_project
```

### Step 2: Create an Isolated Virtual Environment
```powershell
python -m venv .venv
```

### Step 3: Activate the Virtual Environment
- **PowerShell:**
  ```powershell
  .\.venv\Scripts\Activate.ps1
  ```
- **Command Prompt (`cmd.exe`):**
  ```cmd
  .venv\Scripts\activate.bat
  ```

### Step 4: Upgrade Pip & Install Dependencies
```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 5: Configure Environment Variables (Optional)
Copy `.env.example` to `.env`:
```powershell
Copy-Item .env.example .env
```
*(By default, the platform runs using a local SQLite file database `edvexa_data.db`. PostgreSQL connection strings are fully supported via the `DATABASE_URL` setting).*

---

## 5. Application Startup & Browser Access

### Start the Application Server
Run Uvicorn from the project root:
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```
*Or double-click `start_server.bat` on Windows for one-click startup.*

Upon startup, the application initializes the SQLite database schema automatically.

### Access Endpoints
- **Primary Dashboard UI:** [http://127.0.0.1:8010](http://127.0.0.1:8010)
- **Interactive Swagger API Docs:** [http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)
- **ReDoc Technical Schema:** [http://127.0.0.1:8010/redoc](http://127.0.0.1:8010/redoc)
- **Health Verification:** [http://127.0.0.1:8010/api/health](http://127.0.0.1:8010/api/health)

---

## 6. Sample Datasets & Ingestion Instructions

Synthetic test datasets are located in `sample_data/`. You can regenerate them at any time:
```powershell
python scripts\generate_sample_data.py
```

### Ingestion via Web Interface:
1. Open [http://127.0.0.1:8010](http://127.0.0.1:8010) in your browser.
2. Click **Import Data** in the left sidebar navigation.
3. Select **Customers** in the dataset dropdown.
4. Drag and drop `sample_data/customers.csv` (or click to browse).
5. Click **Run Ingestion Pipeline**.
6. The dashboard displays the real-time processing outcome:
   - **Total:** 13 rows
   - **Accepted:** 5 clean rows
   - **Rejected:** 7 validation errors (missing fields, invalid emails, impossible leap year dates)
   - **Duplicates:** 1 in-file duplicate key (`CUST-001`)
7. Repeat for `sample_data/sales.csv` (Select **Sales**) and `sample_data/inventory.csv` (Select **Inventory**).
8. Re-upload `sample_data/customers.csv` to demonstrate **Idempotency**:
   - 0 clean records inserted, 6 duplicate keys detected, 7 rejected, clean tables remain untouched.

---

## 7. Export & Download Job Artifacts

The platform provides dedicated, job-scoped export capabilities for every processing run. Users can download artifacts directly after uploading via the ingestion feedback card, or anytime from the **Job Details** modal.

### Available Download Actions & Endpoints

| Endpoint | Method | Output Format | Description |
| :--- | :--- | :--- | :--- |
| `/api/jobs/{id}/download/clean` | `GET` | CSV (`text/csv`) | Exports only clean, normalized records accepted in this specific job with exact dataset column names. Returns `HTTP 400` if 0 rows accepted. |
| `/api/jobs/{id}/download/errors` | `GET` | CSV (`text/csv`) | Exports all validation errors recorded for this job (`job_id, row_number, dataset_type, field_name, error_code, error_message, original_value`). Returns `HTTP 400` if 0 errors. |
| `/api/jobs/{id}/download/summary` | `GET` | Excel (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`) | Exports a multi-sheet Microsoft Excel workbook (`job_<id>_report.xlsx`) containing Job Summary, Clean Records, and Validation Errors. |

### UI Behavior & Features
- **Clean Records (CSV):** Disabled with a tooltip (`No clean records were accepted from this job`) if accepted rows equal 0.
- **Validation Errors (CSV):** Disabled with a tooltip (`No validation errors were recorded for this job`) if the run had zero validation errors.
- **Download Excel Report (.xlsx):** Always available for completed jobs, even if clean records or validation errors are empty.
- **Double-Click Prevention:** Download buttons enter a loading state during export and re-enable upon file delivery.

### Excel Report Structure (`job_<id>_report.xlsx`)
The exported workbook contains three formatted worksheets:
1. **`Job Summary`:**
   - **Execution Metadata:** Job ID, original filename, dataset type, status, SHA-256 checksum, timestamps, and duration.
   - **Pipeline Metrics:** Total rows, accepted records, rejections, duplicate keys prevented, skipped rows, validation error count, and data quality pass rate.
   - **Accounting Reconciliation Audit:** Verifies $\text{total\_rows} = \text{accepted} + \text{rejected} + \text{duplicates} + \text{skipped}$. Displays sum of categories, difference, and reconciliation status (`PASSED`).
2. **`Clean Records`:**
   - Contains only the normalized records accepted by the specific job.
   - Dataset columns (`customers`, `sales`, `inventory`) with frozen header row (`A2`), auto-filters, and formatted numbers/currencies.
   - If 0 clean records were accepted, displays an explanatory notice.
3. **`Validation Errors`:**
   - Itemizes all errors: `job_id, row_number, dataset_type, field_name, error_code, error_message, original_value`.
   - Frozen header row (`A2`), auto-filters, and wrapped error descriptions.
   - If clean run (0 errors), displays an explanatory notice.

---

## 8. Data Validation & Business Rules

| Dataset | Field | Type / Requirement | Normalization Applied | Rejection Error Code |
| :--- | :--- | :--- | :--- | :--- |
| **Customers** | `customer_id` | Required string | Trimmed | `MISSING_REQUIRED_FIELD` |
| | `name` | Required string | Trimmed, internal spaces collapsed, Title Case | `MISSING_REQUIRED_FIELD` |
| | `email` | Required RFC 5322 pattern | Trimmed, Lowercase | `MISSING_REQUIRED_FIELD`, `INVALID_EMAIL_FORMAT` |
| | `phone` | Optional string | Trimmed | N/A |
| | `created_at` | Required calendar date | Normalized to ISO `YYYY-MM-DD` | `MISSING_REQUIRED_FIELD`, `INVALID_DATE_FORMAT` |
| **Sales** | `sale_id` | Required unique PK | Trimmed | `MISSING_REQUIRED_FIELD` |
| | `customer_id` | Required foreign ref | Trimmed | `MISSING_REQUIRED_FIELD` |
| | `amount` | Required numeric $\ge 0.0$ | Parsed float, rounded to 2 decimals | `MISSING_REQUIRED_FIELD`, `INVALID_NUMERIC_VALUE`, `NEGATIVE_VALUE` |
| | `sale_date` | Required calendar date | Normalized to ISO `YYYY-MM-DD` | `MISSING_REQUIRED_FIELD`, `INVALID_DATE_FORMAT` |
| | `currency` | Required supported ISO code | Trimmed, Uppercase (`USD, EUR, GBP, CAD, AUD, JPY, INR`) | `MISSING_REQUIRED_FIELD`, `INVALID_CURRENCY` |
| **Inventory** | `product_id` | Required unique PK | Trimmed | `MISSING_REQUIRED_FIELD` |
| | `product_name`| Required string | Trimmed, Title Case | `MISSING_REQUIRED_FIELD` |
| | `quantity` | Required integer $\ge 0$ | Parsed integer | `MISSING_REQUIRED_FIELD`, `INVALID_NUMERIC_VALUE`, `NEGATIVE_VALUE` |
| | `unit_price` | Required numeric $\ge 0.0$ | Parsed float, rounded to 2 decimals | `MISSING_REQUIRED_FIELD`, `INVALID_NUMERIC_VALUE`, `NEGATIVE_VALUE` |
| | `updated_at` | Required calendar date | Normalized to ISO `YYYY-MM-DD` | `MISSING_REQUIRED_FIELD`, `INVALID_DATE_FORMAT` |

---

## 8. Duplicate Handling & Idempotency Policy

1. **In-File Duplicates:** If a primary business key (`customer_id`, `sale_id`, or `product_id`) appears more than once within the same uploaded file, the first valid occurrence is accepted, and all subsequent occurrences are classified as `duplicate_rows` with error code `DUPLICATE_KEY_IN_FILE`.
2. **Database Duplicates:** If an uploaded record contains a primary key that already exists in the clean database table, it is classified as `duplicate_rows` with error code `DUPLICATE_KEY_IN_DB`.
3. **Repeated File Uploads:** Uploaded raw bytes are hashed via SHA-256 and matched against previous runs. The repeated run is recorded in `processing_jobs` for audit transparency, but clean records are never double-inserted or overwritten.
4. **Mutual Exclusion Accounting:**
   $$\text{total\_rows} = \text{accepted\_rows} + \text{rejected\_rows} + \text{duplicate\_rows} + \text{skipped\_rows}$$
   Every row belongs to exactly one category.

---

## 9. Automated Testing Suite

The test suite runs against an isolated, in-memory SQLite database via temporary test fixtures so your local development database is never altered.

### Run All Tests:
```powershell
python -m pytest -v
```

### Test Coverage Summary:
- **`tests/test_transformation.py`:** String cleaning, name title-casing, email lowercasing, currency normalizers, ISO date parsers, leap-year boundary checks, numeric float/int parsers.
- **`tests/test_validation.py`:** Field-level validation rules for all 3 schemas, regex conformance, non-negative value checks, CSV header inspection, missing column detection, empty file handling, and UTF-8 BOM encoding.
- **`tests/test_etl.py`:** End-to-end ETL execution, clean table insertion, validation error persistence, in-file duplicate detection, database duplicate prevention, repeated upload idempotency, and transactional rollbacks.
- **`tests/test_api.py`:** Full HTTP endpoint coverage: `/api/health`, `/api/uploads` (success, 400 empty, 415 invalid type, 422 bad dataset), `/api/jobs`, `/api/jobs/{id}`, `/api/jobs/{id}/errors`, `/api/errors`, `/api/records`, `/api/dashboard/summary`, `/api/dashboard/trends`, `/api/jobs/{id}/reprocess`, and download endpoints (`/download/clean`, `/download/errors`, `/download/summary`, edge cases, isolation, escaping).
- **`tests/test_dashboard_upgrade.py`:** UI preferences reset (`POST /api/dashboard/reset-preferences`) and dashboard summary/trends date-range filtering.
- **`tests/test_reset_and_filter.py`:** Destructive data reset (`POST /api/dashboard/reset-data`), physical artifact cleanup, path traversal protection, concurrency lock enforcement, OpenAPI schema presence, idempotent empty-database responses, transaction rollback on failure, records/errors date-range filtering, and KPI mathematical invariants.

---

## 11. Database Schema Overview

```mermaid
erDiagram
    PROCESSING_JOBS ||--o{ VALIDATION_ERRORS : "has"
    PROCESSING_JOBS ||--o{ JOB_CLEAN_RECORDS : "stores"
    PROCESSING_JOBS {
        int id PK
        string filename
        string dataset_type
        string file_hash
        string status
        int total_rows
        int accepted_rows
        int rejected_rows
        int duplicate_rows
        int skipped_rows
        text error_message
        datetime started_at
        datetime completed_at
        float processing_duration
    }
    JOB_CLEAN_RECORDS {
        int id PK
        int job_id FK
        int row_number
        string dataset_type
        string business_key
        text record_json
        datetime created_at
    }
    VALIDATION_ERRORS {
        int id PK
        int job_id FK
        int row_number
        string field_name
        string error_code
        text error_message
        text original_value
        text original_row_json
        datetime created_at
    }
    CUSTOMERS {
        string customer_id PK
        string name
        string email
        string phone
        string created_at
        datetime created_timestamp
    }
    SALES {
        string sale_id PK
        string customer_id
        float amount
        string sale_date
        string currency
    }
    INVENTORY {
        string product_id PK
        string product_name
        int quantity
        float unit_price
        string updated_at
    }
```

---

## 11. Known Limitations & Production Considerations

1. **Authentication & Authorization:** The assignment explicitly specifies no multi-user admin authentication for local development simplicity. In a production cloud deployment, JWT / OAuth2 bearer tokens and role-based access control (RBAC) should protect upload and administrative endpoints.
2. **Synchronous Execution:** Uploads are processed synchronously in the local environment, which is fast and deterministic for files under 10 MB. For files exceeding hundreds of thousands of rows, processing should be offloaded to an asynchronous message broker (e.g., Celery + Redis).
3. **Database Dialect:** Defaults to SQLite for zero-configuration local execution on Windows. SQLAlchemy 2.x models and queries are dialect-agnostic and compatible with PostgreSQL.

---

## 12. Troubleshooting Guide

- **Port 8010 Already in Use:**
  Run on a different port:
  ```powershell
  uvicorn app.main:app --port 8080 --reload
  ```
- **PowerShell Script Execution Policy Error:**
  If `.venv\Scripts\Activate.ps1` gives an execution policy warning:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
- **Resetting the Local Database:**
  To start with a clean slate:
  ```powershell
  Remove-Item edvexa_data.db -ErrorAction SilentlyContinue
  Remove-Item uploads_storage\*.csv -ErrorAction SilentlyContinue
  ```
  Restarting the server will recreate fresh empty tables.

---

## 13. AI Engineering Tooling & CodeZero Demarcation

In compliance with assignment evaluation criteria:
- **CodeZero:** Employed during initial project exploration in repository `renavaraghav/edvexa-data-quality-etl-monitor` to inspect the initial CSV checker CLI requirements and draft the feature specification (commit `7983368`, plan `6adcf6109cb64fb3236a5352`).
- **Google Antigravity:** Served as the pair programmer for complete end-to-end full-stack implementation, FastAPI REST architecture, openpyxl multi-sheet Excel engine, dual-theme frontend, and 65 automated regression tests.
