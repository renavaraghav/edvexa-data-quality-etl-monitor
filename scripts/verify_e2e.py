"""
Comprehensive End-to-End Verification Script for Edvexa Data Quality & ETL Monitor.
Performs real HTTP API requests against the live server and verifies every requirement:
- Favicon 200 OK
- Health endpoint
- Valid ingestion & persistence
- Validation error logging
- In-file & DB duplicate detection
- Idempotency on repeated uploads
- Reprocessing endpoint
- Dashboard KPIs and trends
- Accounting rule on all jobs
"""

import os
import sys
import time
import httpx

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8010")
client = httpx.Client(base_url=BASE_URL, timeout=10.0)

def log_step(name):
    print(f"\n========================================\n[STEP] {name}\n========================================")

def verify_all():
    # 1. Health check
    log_step("1. Health Endpoint")
    res = client.get("/api/health")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    health_data = res.json()
    print("Health Status:", health_data)
    assert health_data["status"] == "healthy"
    assert health_data["database_connected"] is True

    # 2. Favicon
    log_step("2. Favicon Endpoint")
    res = client.get("/favicon.ico")
    assert res.status_code == 200, f"Expected 200, got {res.status_code}"
    assert "image" in res.headers.get("content-type", "")
    print(f"Favicon: 200 OK, Content-Type: {res.headers.get('content-type')}, Size: {len(res.content)} bytes")

    # 3. Valid Customers Upload
    log_step("3. Ingest Valid Customers CSV")
    run_id = int(time.time())
    cust1 = f"CUST-V{run_id}1"
    cust2 = f"CUST-V{run_id}2"
    valid_csv = (
        f"customer_id,name,email,phone,created_at\n"
        f"{cust1},  walter white  ,W.WHITE.{run_id}@EXAMPLE.COM,555-0150,2024-01-10\n"
        f"{cust2},Jesse Pinkman,jesse.{run_id}@example.com,,01/15/2024\n"
    ).encode("utf-8")
    files = {"file": (f"valid_customers_{run_id}.csv", valid_csv, "text/csv")}
    res = client.post("/api/uploads", data={"dataset_type": "customers"}, files=files)
    assert res.status_code == 201, f"Expected 201, got {res.status_code}: {res.text}"
    job_v = res.json()
    print("Upload Result:", job_v)
    assert job_v["accepted_rows"] == 2
    assert job_v["rejected_rows"] == 0
    assert job_v["duplicate_rows"] == 0
    assert job_v["total_rows"] == 2

    # Check database persistence
    res_records = client.get(f"/api/records?dataset_type=customers&q={cust1}")
    assert res_records.status_code == 200
    records = res_records.json()
    assert records["total"] >= 1
    found = any(r["customer_id"] == cust1 and r["name"] == "Walter White" for r in records["items"])
    assert found, "Normalized customer record not found in database!"
    print(f"Verified persisted and normalized customer: Walter White ({cust1})")

    # 4. Invalid Customer Rows Upload
    log_step("4. Ingest Invalid Customers (Missing fields, bad email, bad date)")
    invalid_csv = (
        "customer_id,name,email,phone,created_at\n"
        ",No ID User,noid@example.com,,2024-01-01\n"
        "CUST-INV1,,noname@example.com,,2024-01-01\n"
        "CUST-INV2,Bad Email User,bad-email-address,,2024-01-01\n"
        "CUST-INV3,Bad Date User,baddate@example.com,,2023-02-29\n"
    ).encode("utf-8")
    files = {"file": ("invalid_customers.csv", invalid_csv, "text/csv")}
    res = client.post("/api/uploads", data={"dataset_type": "customers"}, files=files)
    assert res.status_code == 201
    job_inv = res.json()
    print("Invalid Upload Result:", job_inv)
    assert job_inv["total_rows"] == 4
    assert job_inv["accepted_rows"] == 0
    assert job_inv["rejected_rows"] == 4

    # Verify Validation Errors
    err_res = client.get(f"/api/jobs/{job_inv['job_id']}/errors")
    assert err_res.status_code == 200
    errors = err_res.json()
    print(f"Validation errors recorded for Job #{job_inv['job_id']}: {errors['total']}")
    codes = [e["error_code"] for e in errors["items"]]
    assert "MISSING_REQUIRED_FIELD" in codes
    assert "INVALID_EMAIL_FORMAT" in codes
    assert "INVALID_DATE_FORMAT" in codes

    # 5. In-File Duplicates
    log_step("5. Ingest CSV with In-File Duplicates")
    dup_id = f"CUST-DUP{run_id}"
    dup_filename = f"duplicate_customers_{run_id}.csv"
    dup_csv = (
        f"customer_id,name,email,phone,created_at\n"
        f"{dup_id},First Occurrence,first.{run_id}@example.com,,2024-01-01\n"
        f"{dup_id},Second Duplicate Occurrence,second.{run_id}@example.com,,2024-01-02\n"
    ).encode("utf-8")
    files = {"file": (dup_filename, dup_csv, "text/csv")}
    res = client.post("/api/uploads", data={"dataset_type": "customers"}, files=files)
    assert res.status_code == 201
    job_dup = res.json()
    print("In-File Duplicate Upload Result:", job_dup)
    assert job_dup["total_rows"] == 2
    assert job_dup["accepted_rows"] == 1
    assert job_dup["duplicate_rows"] == 1
    assert job_dup["rejected_rows"] == 0

    # 6. Repeated Upload (Idempotency)
    log_step("6. Repeat Upload of Same File (Idempotency)")
    files = {"file": (dup_filename, dup_csv, "text/csv")}
    res = client.post("/api/uploads", data={"dataset_type": "customers"}, files=files)
    assert res.status_code == 201
    job_rep = res.json()
    print("Repeated Upload Result:", job_rep)
    assert job_rep["is_repeated_upload"] is True
    assert job_rep["accepted_rows"] == 0
    assert job_rep["duplicate_rows"] == 2
    assert "Processed with idempotency" in job_rep["message"]

    # 7. Reprocess Endpoint
    log_step("7. Test POST /api/jobs/{job_id}/reprocess")
    reprocess_res = client.post(f"/api/jobs/{job_dup['job_id']}/reprocess")
    assert reprocess_res.status_code == 200
    reproc_data = reprocess_res.json()
    assert reproc_data["id"] == job_dup["job_id"]
    assert reproc_data["status"] == "completed"
    print("Reprocess Job Result:", reproc_data["status"], "Duration:", reproc_data["processing_duration"])

    # 8. Check Dashboard Summary & Trends
    log_step("8. Verify Dashboard Summary & Trends")
    dash_res = client.get("/api/dashboard/summary")
    assert dash_res.status_code == 200
    dash_data = dash_res.json()
    kpis = dash_data["kpis"]
    print("Dashboard KPIs:", kpis)
    assert kpis["total_records_processed"] > 0
    assert kpis["accepted_records"] > 0

    trends_res = client.get("/api/dashboard/trends")
    assert trends_res.status_code == 200
    trends_data = trends_res.json()
    print(f"Historical Trends Data Points: {len(trends_data['trends'])}")
    assert len(trends_data["trends"]) > 0

    # 9. Accounting Rule Verification Across All Jobs
    log_step("9. Accounting Rule Check Across All Jobs")
    jobs_res = client.get("/api/jobs?page_size=100")
    assert jobs_res.status_code == 200
    jobs_list = jobs_res.json()["items"]
    for j in jobs_list:
        total = j["total_rows"]
        acc = j["accepted_rows"]
        rej = j["rejected_rows"]
        dup = j["duplicate_rows"]
        skp = j["skipped_rows"]
        sum_parts = acc + rej + dup + skp
        assert total == sum_parts, f"Accounting mismatch for Job #{j['id']}: total={total} vs sum={sum_parts}"
    print(f"Verified accounting invariant on all {len(jobs_list)} jobs: total_rows == accepted + rejected + duplicate + skipped")

    log_step("ALL END-TO-END VERIFICATION CHECKS PASSED!")

if __name__ == "__main__":
    verify_all()
