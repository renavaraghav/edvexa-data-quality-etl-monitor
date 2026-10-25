"""
Automated Live Deployment Verification Suite for EDVEXA.
Runs comprehensive smoke and regression tests against the deployed Render URL.
"""
import sys
import requests
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DIR = BASE_DIR / "sample_data"

def run_live_verification(base_url: str, admin_token: str = ""):
    base_url = base_url.rstrip("/")
    print(f"\n{'='*70}")
    print(f"VERIFYING LIVE EDVEXA DEPLOYMENT: {base_url}")
    print(f"{'='*70}\n")
    
    session = requests.Session()
    
    # 1. Health Probe
    print("[TEST 1/8] Probing /api/health...")
    r = session.get(f"{base_url}/api/health", timeout=15)
    assert r.status_code == 200, f"Health check failed with {r.status_code}: {r.text}"
    health_data = r.json()
    print("Health response:", health_data)
    assert health_data.get("status") == "healthy", "Expected status == healthy"
    assert health_data.get("database_connected") is True, "Database connection is FALSE!"
    print(">>> PASS: Server is healthy and connected to Neon PostgreSQL!\n")

    # 2. Reset Status & Production Security Guard Probe
    print("[TEST 2/8] Probing /api/dashboard/reset-status & testing reset guard...")
    r = session.get(f"{base_url}/api/dashboard/reset-status", timeout=15)
    assert r.status_code == 200, f"Reset status failed with {r.status_code}: {r.text}"
    status_data = r.json()
    print("Reset status response:", status_data)
    
    # Verify unauthenticated reset attempt is blocked in production
    r_reset = session.post(f"{base_url}/api/dashboard/reset-data", timeout=15)
    print(f"Unauthenticated reset attempt returned status: {r_reset.status_code}")
    assert r_reset.status_code in (401, 403), f"Security flaw: reset returned {r_reset.status_code}!"
    print(">>> PASS: Production destructive reset safeguards are active and enforced!\n")

    # 3. CSV Ingestion (Valid Customers)
    print("[TEST 3/8] Testing CSV upload and ingestion (customers_valid.csv)...")
    valid_csv = SAMPLE_DIR / "customers_valid.csv"
    with open(valid_csv, "rb") as f:
        r_upload = session.post(
            f"{base_url}/api/uploads",
            data={"dataset_type": "customers"},
            files={"file": ("customers_valid.csv", f, "text/csv")},
            timeout=30
        )
    assert r_upload.status_code == 201, f"Upload failed: {r_upload.status_code} - {r_upload.text}"
    job_valid = r_upload.json()
    print("Valid upload result:", job_valid)
    assert job_valid["status"] == "completed"
    assert job_valid["accepted_rows"] == 5
    assert job_valid["rejected_rows"] == 0
    print(">>> PASS: Valid CSV ingested and clean records committed to Neon!\n")

    # 4. CSV Ingestion with Validation Errors
    print("[TEST 4/8] Testing validation errors handling (customers_invalid.csv)...")
    invalid_csv = SAMPLE_DIR / "customers_invalid.csv"
    with open(invalid_csv, "rb") as f:
        r_invalid = session.post(
            f"{base_url}/api/uploads",
            data={"dataset_type": "customers"},
            files={"file": ("customers_invalid.csv", f, "text/csv")},
            timeout=30
        )
    assert r_invalid.status_code == 201, f"Invalid upload failed: {r_invalid.status_code} - {r_invalid.text}"
    job_invalid = r_invalid.json()
    print("Invalid upload result:", job_invalid)
    assert job_invalid["status"] == "completed"
    assert job_invalid["rejected_rows"] > 0
    print(">>> PASS: Validation errors accurately diagnosed and persisted!\n")

    # 5. Idempotency Check (Repeated Upload)
    print("[TEST 5/8] Testing idempotency policy on repeated upload...")
    with open(valid_csv, "rb") as f:
        r_repeat = session.post(
            f"{base_url}/api/uploads",
            data={"dataset_type": "customers"},
            files={"file": ("customers_valid.csv", f, "text/csv")},
            timeout=30
        )
    assert r_repeat.status_code == 201, f"Repeated upload failed: {r_repeat.status_code} - {r_repeat.text}"
    job_repeat = r_repeat.json()
    print("Repeated upload result:", job_repeat)
    assert job_repeat["is_repeated_upload"] is True
    assert job_repeat["accepted_rows"] == 0
    assert job_repeat["duplicate_rows"] == 5
    print(">>> PASS: Idempotency enforced - 0 duplicates double-inserted!\n")

    # 6. Clean Records & Jobs Retrieval
    print("[TEST 6/8] Verifying jobs and clean records persistence...")
    r_jobs = session.get(f"{base_url}/api/jobs?limit=5", timeout=15)
    assert r_jobs.status_code == 200
    jobs_data = r_jobs.json()
    print(f"Total jobs recorded in Neon: {jobs_data['total']}")
    assert jobs_data["total"] >= 3
    print(">>> PASS: Processing jobs list retrieved successfully!\n")

    # 7. Export & Downloads (Clean CSV and Excel Workbook)
    print("[TEST 7/8] Verifying job export endpoints (/download/clean and /download/summary)...")
    first_job_id = job_valid["job_id"]
    r_clean_dl = session.get(f"{base_url}/api/jobs/{first_job_id}/download/clean", timeout=15)
    assert r_clean_dl.status_code == 200
    assert "text/csv" in r_clean_dl.headers.get("content-type", "")
    print(f"Clean records CSV download: {len(r_clean_dl.content)} bytes")

    r_excel_dl = session.get(f"{base_url}/api/jobs/{first_job_id}/download/summary", timeout=15)
    assert r_excel_dl.status_code == 200
    assert "spreadsheetml" in r_excel_dl.headers.get("content-type", "")
    print(f"Multi-sheet Excel report download: {len(r_excel_dl.content)} bytes")
    print(">>> PASS: Artifact exports and Excel report generator operational!\n")

    # 8. Dashboard Summary Aggregations
    print("[TEST 8/8] Verifying real-time dashboard KPIs...")
    r_summary = session.get(f"{base_url}/api/dashboard/summary", timeout=15)
    assert r_summary.status_code == 200
    summary = r_summary.json()
    print("Dashboard KPIs:", summary["kpis"])
    assert summary["kpis"]["total_records_processed"] > 0
    assert summary["kpis"]["accepted_records"] >= 5
    print(">>> PASS: Real-time dashboard KPI metrics verified!\n")

    print(f"{'='*70}")
    print("ALL LIVE PRODUCTION VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    print(f"{'='*70}\n")
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python verify_live_deployment.py <DEPLOYED_URL> [ADMIN_TOKEN]")
        sys.exit(1)
    url = sys.argv[1]
    tok = sys.argv[2] if len(sys.argv) > 2 else ""
    success = run_live_verification(url, tok)
    sys.exit(0 if success else 1)
