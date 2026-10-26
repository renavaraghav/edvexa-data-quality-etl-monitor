import datetime
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.models import ProcessingJob

def test_reset_preferences_endpoint(client: TestClient):
    """Test the POST /api/dashboard/reset-preferences endpoint."""
    res = client.post("/api/dashboard/reset-preferences")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert "reset" in data["message"].lower()

def test_dashboard_summary_and_trends_date_filtering(client: TestClient, db_session: Session):
    """Test date-range filtering on summary and trends."""
    # Create two jobs with different dates
    job1 = ProcessingJob(
        filename="customers_old.csv",
        dataset_type="customers",
        file_hash="hash_old_123",
        status="completed",
        total_rows=10,
        accepted_rows=8,
        rejected_rows=1,
        duplicate_rows=1,
        started_at=datetime.datetime(2026, 9, 15, 10, 0, 0),
        completed_at=datetime.datetime(2026, 9, 15, 10, 0, 5),
    )
    job2 = ProcessingJob(
        filename="customers_recent.csv",
        dataset_type="customers",
        file_hash="hash_recent_456",
        status="completed",
        total_rows=20,
        accepted_rows=18,
        rejected_rows=2,
        duplicate_rows=0,
        started_at=datetime.datetime(2026, 10, 20, 12, 0, 0),
        completed_at=datetime.datetime(2026, 10, 20, 12, 0, 6),
    )
    db_session.add(job1)
    db_session.add(job2)
    db_session.commit()

    # 1. Summary without date filter -> returns both jobs (total 30)
    res_all = client.get("/api/dashboard/summary")
    assert res_all.status_code == 200
    kpis_all = res_all.json()["kpis"]
    assert kpis_all["total_jobs"] == 2
    assert kpis_all["total_records_processed"] == 30
    assert kpis_all["accepted_records"] == 26

    # 2. Summary filtered to October 2026 only -> returns job2 only (total 20)
    res_oct = client.get("/api/dashboard/summary?start_date=2026-10-01&end_date=2026-10-31")
    assert res_oct.status_code == 200
    kpis_oct = res_oct.json()["kpis"]
    assert kpis_oct["total_jobs"] == 1
    assert kpis_oct["total_records_processed"] == 20
    assert kpis_oct["accepted_records"] == 18

    # 3. Trends filtered to September 2026 only -> returns job1 only
    res_trends_sep = client.get("/api/dashboard/trends?start_date=2026-09-01&end_date=2026-09-30")
    assert res_trends_sep.status_code == 200
    trends_sep = res_trends_sep.json()["trends"]
    assert len(trends_sep) == 1
    assert trends_sep[0]["filename"] == "customers_old.csv"

    # 4. Jobs list filtered to October 2026 only
    res_jobs = client.get("/api/jobs?start_date=2026-10-01&end_date=2026-10-31")
    assert res_jobs.status_code == 200
    items = res_jobs.json()["items"]
    assert len(items) == 1
    assert items[0]["filename"] == "customers_recent.csv"

    # 5. Empty date range (no jobs match)
    res_empty = client.get("/api/dashboard/summary?start_date=2025-01-01&end_date=2025-01-31")
    assert res_empty.status_code == 200
    kpis_empty = res_empty.json()["kpis"]
    assert kpis_empty["total_jobs"] == 0
    assert kpis_empty["total_records_processed"] == 0
    assert kpis_empty["data_quality_rate"] == 0.0



def test_dashboard_trends_returns_newest_30_jobs_in_chronological_order(
    client: TestClient, db_session: Session
):
    base_time = datetime.datetime(2026, 10, 1, 0, 0, 0)
    jobs = [
        ProcessingJob(
            filename=f"trend_job_{index:02d}.csv",
            dataset_type="customers",
            file_hash=f"trend_hash_{index:02d}",
            status="completed",
            total_rows=1,
            accepted_rows=1,
            started_at=base_time + datetime.timedelta(minutes=index),
            completed_at=base_time + datetime.timedelta(minutes=index + 1),
        )
        for index in range(35)
    ]
    db_session.add_all(jobs)
    db_session.commit()

    response = client.get("/api/dashboard/trends")
    assert response.status_code == 200
    trends = response.json()["trends"]
    assert len(trends) == 30
    assert trends[0]["filename"] == "trend_job_05.csv"
    assert trends[-1]["filename"] == "trend_job_34.csv"
    assert [point["job_id"] for point in trends] == sorted(point["job_id"] for point in trends)
