# DEMO CHECKLIST & EVALUATION WALKTHROUGH

> **Author:** Raghav Kumar | Software Engineer  
> **Project:** EDVEXA Data Quality & ETL Monitoring Platform  

This document outlines the step-by-step verification sequence to test the **Edvexa Data Quality & ETL Monitor** platform.

---

## 1. Prerequisites Check
Ensure your virtual environment is active and dependencies are installed:
```powershell
cd c:\Users\rjha1\Downloads\csv_project
.\.venv\Scripts\Activate.ps1
```

---

## 2. 13-Step Demonstration Sequence

### Step 1: Start the Application Server
Run the local server:
```powershell
uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
```
- **Expected Outcome:** Server boots on `http://127.0.0.1:8010`. Startup logs indicate database schema initialization. Alternatively, double-click `start_server.bat`.

### Step 2: Open the Dashboard Interface
Open your web browser and navigate to:
[http://127.0.0.1:8010](http://127.0.0.1:8010)
- **Expected Outcome:** The **DATA QUALITY & ETL MONITOR** dashboard displays with clean light background, dark navy typography, sidebar navigation, empty KPI cards, and live API status pill showing **API Connected**.

### Step 3: Ingest the Customers CSV Dataset
1. Click **Import Data** on the sidebar.
2. Select **Customers** in the schema dropdown.
3. Drag & drop `sample_data/customers.csv` (or click to browse).
4. Click **Run Ingestion Pipeline**.
- **Expected Outcome:** Green/success banner appears:
  `Successfully processed 'customers.csv': 5 accepted, 7 rejected, 1 duplicates.`

### Step 4: Verify Accepted Records in Clean Tables
1. Click **Clean Records** in the sidebar.
2. Ensure the **Customers** tab is active.
- **Expected Outcome:** 5 clean, normalized records are displayed:
  - `CUST-001` (Alexander Pierce, `alex.pierce@example.com`)
  - `CUST-002` (Elena Rostova, `elena.rostova@acme.org`)
  - `CUST-003` (Marcus Aurelius Vance, `m.vance@rome.io`)
  - `CUST-004` (Sophia Lin, `sophia.lin@techcorp.com`)
  - `CUST-005` (David Kim, `dkim@startup.co`)
  Notice names are title-cased, whitespace is trimmed, emails are lowercased, and dates are normalized to `YYYY-MM-DD`.

### Step 5: Upload Sales and Inventory Datasets
1. Navigate to **Import Data**.
2. Select **Sales** and upload `sample_data/sales.csv`.
   - Result: 5 accepted, 6 rejected (negative amounts, bad date, bad currency), 1 duplicate.
3. Select **Inventory** and upload `sample_data/inventory.csv`.
   - Result: 5 accepted, 6 rejected (negative quantities/prices, non-integer qty), 1 duplicate.

### Step 6: Open Job Details Modal
1. Click **Processing Jobs** on the sidebar.
2. Click **Details** on Job #1 (`customers.csv`).
- **Expected Outcome:** A detail modal pops up displaying:
  - Status: `completed`
  - Total Rows: `13`
  - Accepted: `5`
  - Rejected: `7`
  - Duplicates: `1`
  - SHA-256 Checksum
  - List of the 8 validation errors recorded for that job.

### Step 7: View the Detailed Validation Error Report
1. Click **Validation Errors** on the sidebar.
- **Expected Outcome:** A comprehensive table itemizes the exact errors:
  - Row 7: `customer_id` &rarr; `DUPLICATE_KEY_IN_FILE`
  - Row 8: `email` &rarr; `INVALID_EMAIL_FORMAT` (`plainaddress`)
  - Row 9: `email` &rarr; `INVALID_EMAIL_FORMAT` (`clara.example.com`)
  - Row 10: `customer_id` &rarr; `MISSING_REQUIRED_FIELD`
  - Row 11: `name` &rarr; `MISSING_REQUIRED_FIELD`
  - Row 12: `email` &rarr; `MISSING_REQUIRED_FIELD`
  - Row 13: `created_at` &rarr; `INVALID_DATE_FORMAT` (`2023-02-29` non-leap-year)
  - Row 14: `created_at` &rarr; `INVALID_DATE_FORMAT` (`not-a-date`)

### Step 8: Upload a File with Duplicate Business Identifiers
1. Notice that `sample_data/customers.csv` has `CUST-001` in row 2 and duplicate `CUST-001` in row 7.
- **Expected Outcome:** Row 2 is accepted into the database, while row 7 is cleanly separated as a duplicate and NOT inserted.

### Step 9: Re-Upload the Same File to Demonstrate Idempotency
1. Go back to **Import Data**.
2. Select **Customers** and upload `sample_data/customers.csv` a second time.
3. Click **Run Ingestion Pipeline**.
- **Expected Outcome:**
  - Notice the response: `File 'customers.csv' matches an existing upload (Hash: b28d1b2b). Processed with idempotency: 0 new, 6 duplicates, 7 rejected.`
  - Navigate to **Clean Records** &rarr; verify the Customers table **STILL HAS EXACTLY 5 RECORDS**. Zero duplicates were inserted.

### Step 10: Review Processing History & Filtering
1. Go to **Processing Jobs**.
2. Filter by dataset: select **Customers** and click **Filter**.
3. Search for a specific job: type `sales` and press Enter.
- **Expected Outcome:** The table dynamically filters to matching jobs with live pagination.

### Step 11: Review Data Quality Trends on Overview
1. Click **Overview** in the sidebar.
- **Expected Outcome:**
  - KPI Cards reflect live database aggregates:
    - Total Ingested: `50`
    - Accepted Clean Records: `15`
    - Validation Rejections: `26`
    - Duplicate Keys Prevented: `9`
    - Overall Quality Pass Rate: `30.0%`
  - **Quality Trends Chart:** Visual bar chart rendering accepted (green), rejected (red), and duplicates (amber) for every executed job.
  - **Record Distribution Chart:** Doughnut chart displaying aggregate percentage shares.

### Step 12: Explore Interactive OpenAPI Swagger Docs
1. Click the **Swagger API** button in the top navigation (or visit [http://127.0.0.1:8010/docs](http://127.0.0.1:8010/docs)).
- **Expected Outcome:** The complete interactive Swagger UI documentation appears with typed Pydantic models for:
  - `GET /api/health`
  - `POST /api/uploads`
  - `GET /api/jobs`
  - `GET /api/jobs/{job_id}`
  - `POST /api/jobs/{job_id}/reprocess`
  - `GET /api/jobs/{job_id}/download/clean`
  - `GET /api/jobs/{job_id}/download/errors`
  - `GET /api/jobs/{job_id}/download/summary`
  - `GET /api/jobs/{job_id}/errors`
  - `GET /api/errors`
  - `GET /api/records`
  - `GET /api/dashboard/summary`
  - `GET /api/dashboard/trends`
2. Test any endpoint directly from Swagger using the "Try it out" feature.

### Step 13: Download Job Results (CSV & Excel)
1. In the **Processing Jobs** table, click **Details** on any job.
2. In the modal, locate the **Export & Download Job Artifacts** section:
   - Click **Clean Records (CSV)**: downloads `clean_<dataset>_job_<id>.csv` containing only records accepted in that job.
   - Click **Validation Errors (CSV)**: downloads `validation_errors_job_<id>.csv` containing all failure diagnostics.
   - Click **Download Excel Report (.xlsx)**: downloads `job_<id>_report.xlsx` containing the formatted 3-sheet Microsoft Excel workbook (Job Summary, Clean Records, Validation Errors).
3. Test a job with 0 accepted records (e.g. Job #10 repeated run):
   - Notice the **Clean Records (CSV)** button is disabled with an explanatory tooltip (`No clean records were accepted from this job`).
   - Notice the **Download Excel Report (.xlsx)** button remains enabled and cleanly includes the explanatory notice inside its Clean Records sheet!
4. Test a job with 0 errors:
   - Notice the **Validation Errors (CSV)** button is disabled with an explanatory tooltip (`No validation errors were recorded for this job`).
   - Notice the **Download Excel Report (.xlsx)** button remains enabled and cleanly includes the explanatory notice inside its Validation Errors sheet!

### Step 14: Execute the Automated Test Suite
Open a terminal and run:
```powershell
python -m pytest -v
```
- **Expected Outcome:** All 65 tests pass with 100% success rate:
  ```
  ======================= 65 passed, 6 warnings in 1.93s ========================
  ```

---

## 3. Authentic Application Screenshots

Verified high-resolution application screenshots are saved directly in the `screenshots/` directory:
- `01_dashboard_overview_light.png`: Overview dashboard in Warm Ivory theme with live KPI cards, donut distribution, and trends chart.
- `02_dashboard_overview_dark.png`: Overview dashboard in Dark Espresso theme.
- `03_import_data_workflow.png`: Ingestion workflow with drag-and-drop zone and dataset schema selector.
- `04_processing_jobs_audit.png`: Processing job history table in dark mode with high-contrast status badges.
- `05_slide_to_reset_modal.png`: iPhone-style compact slide-to-reset secondary confirmation modal.
- `06_mobile_responsive_view.png`: 375px mobile viewport rendering.
