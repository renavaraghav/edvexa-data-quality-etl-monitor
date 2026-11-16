"""Opt in with EDVEXA_TEST_POSTGRES_URL pointing to a disposable loopback database."""

import os
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from threading import Barrier, Event, current_thread
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import create_engine, event, func, insert, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import database
from app.main import app
from app.models import SurveyAuditRun, SurveyQcFinding, SurveyRespondent, SurveyReviewEvent
from app.routers.surveys import _save_review, download_survey_excel_report, reset_all_survey_data
from app.schemas import SurveyReviewDecisionRequest
from app.services.auth import create_admin_session_token


@pytest.fixture
def pg(monkeypatch):
    dsn = os.getenv("EDVEXA_TEST_POSTGRES_URL")
    if not dsn:
        pytest.skip("Set EDVEXA_TEST_POSTGRES_URL for a disposable loopback PostgreSQL database")
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql" or url.host not in {"127.0.0.1", "::1"} or not (url.database or "").startswith("edvexa_test_") or url.query:
        pytest.fail("Integration tests require a loopback PostgreSQL database named edvexa_test_*, with no DSN query overrides")
    schema = "survey_test_" + uuid4().hex
    management = create_engine(url)
    with management.begin() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(url, connect_args={"options": f"-c search_path={schema} -c statement_timeout=15000 -c timezone=Asia/Kolkata"})
    with monkeypatch.context() as fixture:
        fixture.setattr(database, "engine", engine)
        fixture.setitem(database.SessionLocal.kw, "bind", engine)
        try:
            yield engine
        finally:
            engine.dispose()
            with management.begin() as conn:
                conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            management.dispose()


@pytest.fixture
def pg_client(pg):
    with TestClient(app, headers={"Authorization": f"Bearer {create_admin_session_token()}"}) as client:
        yield client


def _seed_review_queue(pg, n=2):
    with Session(pg) as db, db.begin():
        run = SurveyAuditRun(raw_filename="pg.csv", raw_file_hash="a" * 64, status="completed",
                             total_respondents=n, review_required_count=n)
        db.add(run)
        db.flush()
        rows = [SurveyRespondent(audit_id=run.id, respondent_key=f"S{i}", row_number=i+2,
                                 disposition="REVIEW_REQUIRED", raw_data_json='{"id":"synthetic"}') for i in range(n)]
        db.add_all(rows)
        db.flush()
        return run.id, [r.id for r in rows]


def test_pg_clean_install_and_concurrent_upgrade(pg):
    previous = [table for table in database.Base.metadata.sorted_tables if table is not SurveyReviewEvent.__table__]
    database.Base.metadata.create_all(pg, tables=previous)
    run_id, ids = _seed_review_queue(pg)
    assert "survey_review_events" not in inspect(pg).get_table_names()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(database.init_db) for _ in range(2)]
        for future in futures:
            future.result(timeout=12)
    inspector = inspect(pg)
    assert {fk["referred_table"] for fk in inspector.get_foreign_keys("survey_review_events")} == {
        "survey_audit_runs", "survey_respondents"}
    with Session(pg) as db:
        assert db.get(SurveyAuditRun, run_id).total_respondents == 2
        _save_review(db, run_id, SurveyReviewDecisionRequest(decision="ACCEPTED", notes="Verified"), respondent_id=ids[0])
        assert db.scalar(select(SurveyReviewEvent)).id > 0


def test_pg_audit_review_export_and_write_rollback(pg, pg_client):
    base = "/api/surveys"
    qq = pg_client.post(f"{base}/upload-qq", files={"file": ("qq.txt", b"S1. What is your age? [Terminate if under 18]")})
    assert qq.status_code == 200
    file_id = qq.json()["artifact_name"]

    def execute(rows):
        raw = pg_client.post(f"{base}/upload-raw", files={"file": ("raw.csv", rows)})
        assert raw.status_code == 200
        return pg_client.post(f"{base}/run-audit", json={"raw_artifact_name": raw.json()["artifact_name"],
            "qq_artifact_name": file_id, "confirmed_mappings": [{"question_number": "S1", "matched_column": "Age"}]})

    result = execute(b"ResponseId,Age\nSAME,35\nSAME,17\nOTHER,25\n")
    assert result.status_code == 200 and result.json()["rejected_count"] == 1, result.text
    run_id = result.json()["id"]
    path = f"{base}/audits/{run_id}"
    rows = pg_client.get(f"{path}/respondents?paginated=true&page_size=1&page=2").json()
    assert rows["total"] == 3 and rows["items"][0]["row_number"] == 3
    rid = rows["items"][0]["id"]
    assert pg_client.post(f"{path}/review-by-id/{rid}", json={"decision": "ACCEPTED", "notes": "Synthetic QA"}).status_code == 200
    wb = load_workbook(BytesIO(pg_client.get(f"{path}/export/excel").content), read_only=True)
    assert wb["Accepted Data"].max_row == 4 and wb["Review History"].max_row == 2

    with pg.begin() as conn:
        conn.execute(text("ALTER TABLE survey_respondents ADD CONSTRAINT deliberate_test_failure CHECK (respondent_key != 'FAIL_TEST')"))
    failed = execute(b"ResponseId,Age\nSAFE,21\nFAIL_TEST,25\n")
    assert failed.status_code == 500
    with Session(pg) as db:
        run = db.scalars(select(SurveyAuditRun).order_by(SurveyAuditRun.id.desc())).first()
        assert run.status == "failed" and run.total_respondents == 0
        assert db.scalar(select(func.count()).select_from(SurveyRespondent).where(SurveyRespondent.audit_id == run.id)) == 0
        failed_id = run.id
    assert pg_client.get(f"{base}/audits/{failed_id}/export/excel").status_code == 409


def test_pg_concurrent_reviews_and_reset_lock_order(pg):
    database.init_db()
    audit, ids = _seed_review_queue(pg)
    barrier = Barrier(3)

    def decide(rid, value, *, racing=False):
        with Session(pg) as db:
            if racing:
                barrier.wait(timeout=5)
            return _save_review(db, audit, SurveyReviewDecisionRequest(decision=value, notes="Parallel test"), respondent_id=rid)

    with ThreadPoolExecutor(max_workers=3) as pool:
        changes = [(ids[0], "REJECTED"), (ids[0], "ACCEPTED"), (ids[1], "ACCEPTED")]
        futures = [pool.submit(decide, rid, value, racing=True) for rid, value in changes]
        for change in futures:
            change.result(timeout=15)

    with Session(pg) as db:
        run = db.get(SurveyAuditRun, audit)
        events = list(db.scalars(select(SurveyReviewEvent).order_by(SurveyReviewEvent.id)))
        same_row = [e for e in events if e.respondent_id == ids[0]]
        assert len(events) == 3 and same_row[0].new_disposition == same_row[1].previous_disposition
        assert run.accepted_count + run.rejected_count == 2 and run.review_required_count == 0

    review_locked, reset_waiting, release = Event(), Event(), Event()

    def after(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith("UPDATE survey_audit_runs") and not review_locked.is_set():
            review_locked.set()
            assert release.wait(8)

    def before(conn, cursor, statement, parameters, context, executemany):
        if "pg_advisory_xact_lock(286456239002)" in statement:
            reset_waiting.set()

    def reset():
        with Session(pg) as db:
            return reset_all_survey_data(db)

    event.listen(pg, "after_cursor_execute", after)
    event.listen(pg, "before_cursor_execute", before)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            review = pool.submit(decide, ids[0], "REVIEW_REQUIRED")
            try:
                assert review_locked.wait(5)
                resetting = pool.submit(reset)
                assert reset_waiting.wait(5) and not resetting.done()
            finally:
                release.set()
            assert review.result(timeout=10)["status"] == "success"
            assert resetting.result(timeout=10)["status"] == "success"
    finally:
        event.remove(pg, "after_cursor_execute", after)
        event.remove(pg, "before_cursor_execute", before)
    with Session(pg) as db:
        assert db.scalar(select(func.count()).select_from(SurveyReviewEvent)) == 0


def test_pg_export_uses_one_snapshot_while_review_commits(pg, monkeypatch):
    database.init_db()
    audit, ids = _seed_review_queue(pg, n=1)
    saw_summary, release = Event(), Event()
    from app.routers import surveys
    build = surveys.generate_survey_audit_workbook
    exported = {}

    def capture(run, rows, findings, mappings, reviews):
        exported.update(counts=(run.accepted_count, run.review_required_count), disposition=rows[0].disposition, reviews=len(reviews))
        return build(run, rows, findings, mappings, reviews)

    def after(conn, cursor, statement, parameters, context, executemany):
        if current_thread().name.startswith("snapshot-export") and "FROM survey_audit_runs" in statement:
            saw_summary.set()
            assert release.wait(8)

    def export():
        with Session(pg) as db:
            return download_survey_excel_report(audit, db)

    monkeypatch.setattr(surveys, "generate_survey_audit_workbook", capture)
    event.listen(pg, "after_cursor_execute", after)
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="snapshot-export") as pool:
            request = pool.submit(export)
            try:
                assert saw_summary.wait(5)
                with Session(pg) as db:
                    _save_review(db, audit, SurveyReviewDecisionRequest(decision="ACCEPTED", notes="Late review"), respondent_id=ids[0])
            finally:
                release.set()
            assert request.result(timeout=10).status_code == 200
    finally:
        event.remove(pg, "after_cursor_execute", after)
    assert exported == dict(counts=(0, 1), disposition="REVIEW_REQUIRED", reviews=0)


def test_pg_pagination_search_and_query_plan(pg, pg_client):
    audit, ids = _seed_review_queue(pg, n=1)
    other, _ = _seed_review_queue(pg, n=1)
    with pg.begin() as conn:
        conn.execute(insert(SurveyRespondent), [dict(audit_id=run, respondent_key=f"R-{i:05d}", row_number=i+3,
            disposition="ACCEPTED", raw_data_json="{}") for run, n in ((audit, 699), (other, 4_000)) for i in range(n)])
        conn.execute(insert(SurveyQcFinding), [dict(audit_id=audit, respondent_id=ids[0], respondent_key="S0",
            issue_type="TEST", severity="LOW", evidence="Synthetic") for _ in range(55)])
        conn.execute(text("ANALYZE survey_respondents"))
        plan = conn.execute(text("EXPLAIN (ANALYZE, FORMAT JSON) SELECT id FROM survey_respondents WHERE audit_id=:audit ORDER BY row_number, id LIMIT 25"),
                            {"audit": audit}).scalar()[0]
        assert "ix_survey_resp_audit_row" in str(plan["Plan"])
    run = f"/api/surveys/audits/{audit}"
    rows = pg_client.get(f"{run}/respondents?paginated=true&search=r-00698&disposition=ACCEPTED").json()
    assert rows["total"] == 1 and rows["items"][0]["row_number"] == 701
    page = pg_client.get(f"{run}/findings?paginated=true&page=2&limit=50&severity=LOW").json()
    assert page["total"] == 55 and len(page["items"]) == 5
