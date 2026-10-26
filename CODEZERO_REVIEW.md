# CodeZero Repository Review

**Project:** Edvexa Data Quality & ETL Monitoring Platform  
**Repository:** https://github.com/renavaraghav/edvexa-data-quality-etl-monitor  
**Review date:** October 26, 2026  
**Review scope:** Source inspection, assignment-requirement mapping, local automated test execution, and targeted runtime verification.

## Work performed by CodeZero

- Inspected the existing FastAPI application, CSV import and validation code, transformation and ETL services, database models, dashboard endpoints and JavaScript, API/test modules, README, technical notes, schema DDL, and sample data.
- Compared the implementation with the functional and submission requirements in the Edvexa assignment.
- Ran the local test command:

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -q
  ```

- Reported **99 passed tests and 17 deprecation warnings**. The test fixture uses an in-memory SQLite database; this run did not establish compatibility with a live PostgreSQL database or verify the deployed application.
- Ran a targeted runtime probe for reprocessing and confirmed that a job with one previously accepted record had one clean audit row before reprocessing and zero afterward; the reprocess result showed zero accepted and one duplicate. The review linked this to clean-record export behavior.
- Produced the findings below. **CodeZero did not modify application files during the review session.** A separate follow-up engineering pass subsequently applied targeted code/documentation fixes and added regression tests; that later work is described separately below.

## Subsequent Post-Fix Verification (Separate from the CodeZero Review)

After pulling the latest `main` code at commit `1d45be9`, the project owner reran the full local suite with `.\.venv\Scripts\python.exe -m pytest -q`. Result: **109 passed, 0 failed, 23 deprecation warnings in 19.91 seconds**. This is a later verification run, not the original CodeZero run. The suite uses in-memory SQLite and does not by itself verify a live PostgreSQL database or the deployed Render service.


## Findings from the review

### 1. Reprocessing can remove the original clean-record audit/export result

**Evidence:** `app/services/etl_pipeline.py`, `reprocess_existing_job` (around lines 278–299) deletes the job's `JobCleanRecord` rows before rerunning the pipeline. The runtime probe reported that a previously accepted record was then detected as an existing business-table duplicate, leaving no replacement job-scoped clean-record row. `app/services/export_service.py` rejects clean-record exports when there are no accepted records to export.

**Follow-up applied after review:** `app/services/etl_pipeline.py` now captures keys previously accepted by the same job, avoids re-inserting those business rows, and replaces the job's errors/clean-record snapshot in the reprocessing transaction. Regression assertions were added to `tests/test_etl.py` and `tests/test_api.py` for accepted counts, audit-row preservation, and clean-record export. The subsequent post-fix local full-suite run passed all 109 tests (see the verification section above).

### 2. Trend history can omit newer jobs

**Evidence:** `app/routers/dashboard.py`, `get_dashboard_trends` (around lines 188–208) orders by ascending job ID before applying a 30-item limit. Once the database contains more than 30 jobs, the result can be limited to the oldest jobs.

**Follow-up applied after review:** `app/routers/dashboard.py` now selects the newest 30 completed jobs, then reverses that bounded set for chronological display. `tests/test_dashboard_upgrade.py` now includes a 35-job regression case. It passed in the post-fix local full-suite run documented above.

### 3. The upload-size guard runs after reading the full upload

**Evidence:** `app/routers/uploads.py` reads the uploaded file into memory before enforcing the configured size limit.

**Follow-up applied after review:** `app/routers/uploads.py` now reads at most `UPLOAD_MAX_SIZE_BYTES + 1` bytes before rejecting an oversized upload. `tests/test_api.py` now checks rejection for a payload above the configured limit. It passed in the post-fix local full-suite run documented above.

### 4. CSV shape and row-location edge cases need explicit handling

**Evidence:** `app/services/csv_import.py` pads short rows with empty strings and maps fields only up to the header count, so extra cells can be discarded. The row counter increments per CSV record and does not represent physical file lines correctly when quoted fields contain embedded newlines.

**Follow-up applied after review:** `app/services/csv_import.py` now rejects data records whose field count differs from the header and records each logical record's physical starting line using `csv.reader.line_num`. `tests/test_validation.py` now covers short/extra field counts and quoted multiline rows. The subsequent post-fix local full-suite run passed all 109 tests (see the verification section above).

### 5. Invalid date filters can be silently ignored

**Evidence:** The review found date-parsing paths in the jobs, errors, records, and dashboard routers that catch invalid date input and continue without applying that filter.

**Follow-up applied after review:** The jobs, errors, records, and dashboard date parsers now return HTTP 422 for malformed dates and reversed date ranges. Parameterized API regression tests were added in `tests/test_api.py`. The subsequent post-fix local full-suite run passed all 109 tests (see the verification section above).

### 6. Schema and sample-data documentation needed consistency fixes

**Evidence:** The original `schema.sql` header claimed SQLite, PostgreSQL, and MySQL compatibility while the script uses SQLite-specific `AUTOINCREMENT` declarations. The README sample catalog listed 10 CSVs while 13 CSV files were tracked under `sample_data/`; three `*_mixed_quality.csv` files were not listed.

**Follow-up:** The schema header was narrowed to SQLite-specific DDL, and the README sample-data catalog and folder count were updated to account for all 13 tracked CSV files.

## Scope and limits

- The 99-pass result is the original CodeZero pre-patch baseline. A separate later local run after the follow-up patches passed 109 tests.
- The tests run against in-memory SQLite; the review did not run the suite against PostgreSQL.
- The review did not perform a fresh live-deployment smoke test.
- Targeted follow-up changes and regression tests have been added for Items 1–5. The later local post-fix full-suite run passed all 109 tests; the original 99-pass result applies to the pre-patch CodeZero baseline only.
- This document records the actual review activity and distinguishes it from the earlier full-stack implementation work assisted by Google Antigravity.
