import csv
from io import BytesIO, StringIO

from openpyxl import Workbook, load_workbook

from app.services.survey_data_parser import preview_survey_data, parse_respondent_records


QQ = """Survey duration: 7 to 10 minutes.
S1. What is your age? [Terminate if under 18]
Q2. Rate each statement. [Matrix]
Statement 1: First
Statement 2: Second
Statement 3: Third
Statement 4: Fourth
Q3. In your own words, describe the experience. [Open End]
"""
CSV = """ResponseId,Duration,S1,Q2_1,Q2_2,Q2_3,Q2_4,Q3
DUP,500,30,5,5,5,5,asdfghjk
DUP,510,15,4,3,2,1,Very helpful service overall
OK,520,25,4,3,2,1,The staff were attentive
"""


def _upload(client, qq=QQ, raw=CSV):
    q = client.post("/api/surveys/upload-qq", files={"file": ("qq.txt", qq.encode())})
    r = client.post("/api/surveys/upload-raw", files={"file": ("rows.csv", raw.encode())})
    assert q.status_code == r.status_code == 200, (q.text, r.text)
    return {"qq_artifact_name": q.json()["artifact_name"], "raw_artifact_name": r.json()["artifact_name"]}


def test_browser_mapping_payload_audit_review_and_workbook(auth_client):
    base = "/api/surveys"
    selection = _upload(auth_client)
    response = auth_client.post(f"{base}/suggest-mapping", json=selection)
    assert response.status_code == 200, response.text
    mappings = response.json()["mappings"]
    assert mappings[1]["mapped_columns"] == ["Q2_1", "Q2_2", "Q2_3", "Q2_4"]
    assert mappings[1]["matrix_items"] == ["First", "Second", "Third", "Fourth"]

    # Browser submits the canonical lists, plus the first-column compatibility alias.
    response = auth_client.post(f"{base}/run-audit", json={
        **selection, "confirmed_mappings": [{**m, "mapping_status": "CONFIRMED"} for m in mappings],
        "timing_rules": {"straightline_threshold_pct": 80},
    })
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["accepted_count"], result["rejected_count"], result["review_required_count"]) == (1, 1, 1)
    run = f"{base}/audits/{result['id']}"
    findings = auth_client.get(f"{run}/findings?paginated=true&limit=1").json()
    assert findings["total"] >= 4 and len(findings["items"]) == 1
    all_findings = auth_client.get(f"{run}/findings").json()
    assert {"STRAIGHTLINING", "GIBBERISH", "SCREENER_AGE_VIOLATION"} <= {f["issue_type"] for f in all_findings}
    rows = auth_client.get(f"{run}/respondents?paginated=true&page_size=1&page=2").json()
    assert rows["total"] == 3 and rows["items"][0]["row_number"] == 3
    body = {"decision": "ACCEPTED", "reviewer_name": "QA", "notes": "Verified source row."}
    assert auth_client.post(f"{run}/review/DUP", json=body).status_code == 409
    row_id = rows["items"][0]["id"]
    assert auth_client.post(f"{run}/review-by-id/{row_id}", json=body).status_code == 200
    second = {**body, "decision": "REJECTED", "notes": "Rechecked eligibility."}
    assert auth_client.post(f"{run}/review-by-id/{row_id}", json=second).status_code == 200
    assert [r["previous_disposition"] for r in auth_client.get(f"{run}/reviews").json()] == ["REJECTED", "ACCEPTED"]
    details = auth_client.get(run).json()
    assert details["effective_rules"]["straightline_threshold_pct"] == 80
    assert details["strict_accounting_verified"]
    workbook = load_workbook(BytesIO(auth_client.get(f"{run}/export/excel").content))
    assert workbook["Review History"].max_row == 3
    assert workbook["Rejected Data"].max_row == 2
    assert "Provenance" in workbook.sheetnames


def test_invalid_mappings_and_ambiguous_legacy_artifacts_fail(auth_client):
    base = "/api/surveys"
    a = _upload(auth_client)
    b = _upload(auth_client, raw=CSV.replace("520", "560"))
    assert a["raw_artifact_name"] != b["raw_artifact_name"]
    assert auth_client.post(f"{base}/suggest-mapping", json={"raw_filename": "rows.csv"}).status_code == 409
    for column_selection in ({}, {"mapped_columns": ["missing"]}, {"mapped_columns": ["ResponseId"]},
                             {"mapped_columns": ["Q2_1"], "mapping_status": ["CONFIRMED"]}):
        response = auth_client.post(f"{base}/run-audit", json={
            **a, "confirmed_mappings": [{"question_number": "Q2", **column_selection}],
        })
        assert response.status_code == 422, response.text
    for rules in ({"straightline_threshold_pct": 101}, {"speeding_threshold_seconds": "nan"}):
        assert auth_client.post(f"{base}/run-audit", json={**a, "timing_rules": rules}).status_code == 422
    skipped = auth_client.post(f"{base}/run-audit", json={**a, "confirmed_mappings": []})
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["straightlining_count"] == 0


def test_wide_qualtrics_csv_reaches_audit_and_workbook(auth_client):
    base = "/api/surveys"
    width, row_count = 5187, 704
    headers = ["ResponseId", "Age", "Repeat", "Repeat", *[f"Column_{i}" for i in range(4, width - 1)], "Q3"]
    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerow(["Response ID", *([""] * (width - 1))])
    writer.writerow(['{"ImportId":"_recordId"}', *([""] * (width - 2)), '{"ImportId":"Q3"}'])
    for index in range(row_count):
        answer = "qwerty" if index == 1 else f"The synthetic project visit {index} was helpful."
        writer.writerow([
            f"wide-{index}", 16 if index == 0 else 32,
            *([str(index)] * 1_300), *([""] * (width - 1_303)), answer,
        ])

    qq = auth_client.post(f"{base}/upload-qq", files={"file": ("wide.txt", b"S1. What is your age? [Terminate if under 18]\nQ3. In your own words, how was the visit? [Open End]")})
    raw = auth_client.post(f"{base}/upload-raw", files={"file": ("wide.csv", output.getvalue().encode())})
    assert qq.status_code == raw.status_code == 200
    intake = raw.json()
    assert intake["total_rows"] == row_count
    assert len(intake["columns"]) == width
    assert intake["header_confirmation_required"]
    assert intake["columns"][2:4] == ["Repeat", "Repeat_2"]
    assert [row["source_row"] for row in intake["excluded_rows"]] == [1, 2, 3]
    assert len(intake["preview_rows"]) == 8
    selection = {
        "qq_artifact_name": qq.json()["artifact_name"],
        "raw_artifact_name": intake["artifact_name"],
        "header_row_index": 0,
    }
    suggested = auth_client.post(f"{base}/suggest-mapping", json=selection)
    assert suggested.status_code == 200
    assert next(item for item in suggested.json()["mappings"] if item["question_number"] == "Q3")["mapped_columns"] == ["Q3"]
    audit = auth_client.post(f"{base}/run-audit", json={
        **selection,
        "confirmed_mappings": [
            {"question_number": "S1", "mapped_columns": ["Age"], "mapping_status": "CONFIRMED"},
            {"question_number": "Q3", "mapped_columns": ["Q3"], "mapping_status": "CONFIRMED"},
        ],
    })
    assert audit.status_code == 200, audit.text
    result = audit.json()
    assert (result["accepted_count"], result["rejected_count"], result["review_required_count"]) == (702, 1, 1)
    report = auth_client.get(f"{base}/audits/{result['id']}/export/excel")
    assert report.status_code == 200
    with BytesIO(report.content) as buffer:
        workbook = load_workbook(buffer, read_only=True)
        try:
            rows = workbook["Raw Data"].iter_rows(values_only=True)
            assert len(next(rows)) == width
            assert next(rows)[-1] == "The synthetic project visit 0 was helpful."
            assert sum(1 for _ in rows) == row_count - 1
            assert next(workbook["Audit Summary"].values)[0].startswith("EDVEXA")
            for sheet, count in (("Accepted Data", 702), ("Rejected Data", 1), ("Review Required", 1)):
                assert sum(1 for _ in workbook[sheet].values) == count + 1
        finally:
            workbook.close()


def test_wide_csv_still_bounds_physical_and_aligned_dimensions(monkeypatch, auth_client):
    from app.services import survey_data_parser

    monkeypatch.setattr(survey_data_parser, "MAX_CSV_COLUMNS", 4)
    monkeypatch.setattr(survey_data_parser, "MAX_CSV_CELLS", 9)
    endpoint = "/api/surveys/upload-raw"
    for content, detail in (
        (b"ResponseId,Q1,Q2,Q3,Q4\n", "5 columns"),
        (b"ResponseId,Q1,Q2\nA,1,2\nB,3,4\nC,5,6\n", "9 cell processing limit"),
        (b"ResponseId,Q1,Q2,Q3\nA\nB\nC\n", "after aligning rows with the header"),
    ):
        response = auth_client.post(endpoint, files={"file": ("limited.csv", content)})
        assert response.status_code == 422 and detail in response.json()["detail"]


def test_preview_preserves_cells_and_reconciles_preamble():
    content = b"Report title\nResponseId,Q1,Q1,Q1_2\n,  original  ,a,b\n\n"
    preview = preview_survey_data(content, "raw.csv", header_row_index=1)
    headers, rows, id_column = parse_respondent_records(content, "raw.csv", header_row_index=1)
    assert len(headers) == len(set(headers)) == 4
    assert len(rows) == preview["total_rows"] == 1
    assert rows[0][id_column] == ""
    assert rows[0]["Q1"] == "  original  "
    assert rows[0]["_physical_row"] == 3
    assert preview["row_accounting"] == {"source_rows": 4, "preamble_rows": 1, "header_rows": 1,
                                          "metadata_rows": 0, "blank_rows": 1, "respondents": 1}
    assert [(row["source_row"], row["reason"]) for row in preview["excluded_rows"]] == [(1, "preamble"), (2, "header"), (4, "blank")]


def test_ambiguous_header_has_to_be_selected_and_preserves_all_respondents(auth_client):
    base = "/api/surveys"
    csv = b"ResponseId,Q1,Q1\nA,Yes,No\nB,No,Yes\n"
    upload = auth_client.post(f"{base}/upload-raw", files={"file": ("duplicate_headers.csv", csv)}).json()
    assert upload["header_row_index"] == 0
    assert upload["header_confirmation_required"]
    assert upload["total_data_rows"] == 2
    selection = {"raw_artifact_name": upload["artifact_name"]}
    for endpoint in ("suggest-mapping", "run-audit"):
        result = auth_client.post(f"{base}/{endpoint}", json=selection)
        assert result.status_code == 422 and "Select the header row explicitly" in result.json()["detail"]
    result = auth_client.post(f"{base}/run-audit", json={**selection, "header_row_index": 0})
    assert result.status_code == 200 and result.json()["total_respondents"] == 2, result.text


def test_importid_answer_and_source_underscore_columns_survive_export(auth_client):
    base = "/api/surveys"
    csv = ('ResponseId,_private_column,Comment\nA,  value  ,"{""ImportId"": ""valid answer""}"\n'
           'B,Other,"First line\nSecond line"\n\nC,Third,Present\n')
    upload = auth_client.post(f"{base}/upload-raw", files={"file": ("rows.csv", csv)}).json()
    assert upload["total_data_rows"] == 3 and upload["metadata_rows_detected"] == 0
    run = auth_client.post(f"{base}/run-audit", json={"raw_artifact_name": upload["artifact_name"]})
    assert run.status_code == 200, run.text
    path = f"{base}/audits/{run.json()['id']}"
    rows = auth_client.get(f"{path}/respondents").json()
    assert [row["row_number"] for row in rows] == [2, 3, 6]
    assert rows[0]["raw_data"]["Comment"] == '{"ImportId": "valid answer"}'
    wb = load_workbook(BytesIO(auth_client.get(f"{path}/export/excel").content), read_only=True)
    assert list(wb["Raw Data"].values)[1][:2] == ("A", "  value  ")
    assert "_private_column" in list(wb["Accepted Data"].values)[0]
    assert list(wb["Intake"].values)[1:] == [(1, "header"), (5, "blank")]


def test_cells_with_uncached_excel_formulas_are_retained_as_literal_source(auth_client):
    workbook = Workbook()
    workbook.active.append(["ResponseId", "Formula"])
    workbook.active.append(["A", "=1+2"])
    data = BytesIO()
    workbook.save(data)
    base = "/api/surveys"
    uploaded = auth_client.post(f"{base}/upload-raw", files={"file": ("formula.xlsx", data.getvalue())}).json()
    run = auth_client.post(f"{base}/run-audit", json={"raw_artifact_name": uploaded["artifact_name"]})
    assert run.status_code == 200, run.text
    path = f"{base}/audits/{run.json()['id']}"
    row = auth_client.get(f"{path}/respondents").json()[0]
    assert row["raw_data"]["Formula"] == "=1+2" and row["row_number"] == 2
    report = load_workbook(BytesIO(auth_client.get(f"{path}/export/excel").content))
    safe_cell = report["Raw Data"]["B2"]
    assert safe_cell.value == "'=1+2" and safe_cell.data_type != "f"


def test_unconfigured_timing_and_routing_do_not_claim_coverage(auth_client):
    base = "/api/surveys"
    response = auth_client.post(f"{base}/upload-raw", files={"file": ("timing.csv", b"ResponseId,Duration,PageTimer_Q1\nA,10,3\n")})
    audit = auth_client.post(f"{base}/run-audit", json={"raw_artifact_name": response.json()["artifact_name"]})
    assert audit.status_code == 200, audit.text
    assert audit.json()["accepted_count"] == 1
    run = auth_client.get(f"{base}/audits/{audit.json()['id']}").json()
    checks = {x["check_name"]: x for x in run["qc_coverage"]}
    for name in ("Overall Survey Speeding", "Individual Question/Page Timers", "Survey Routing & Skip Logic"):
        assert checks[name]["status"] == "NOT_CHECKED"


def test_suggested_age_mapping_does_not_reject_until_explicitly_confirmed(auth_client):
    base = "/api/surveys"
    selection = _upload(auth_client, qq="S1. What is the current participant age? [Terminate if under 18]\n",
                        raw="ResponseId,current_participant_count\nA,3\n")
    suggestion = auth_client.post(f"{base}/suggest-mapping", json=selection).json()["mappings"][0]
    assert suggestion["confidence"] == "LOW"
    assert suggestion["mapped_columns"] == ["current_participant_count"]
    for overrides in ({}, {"confirmed_mappings": [suggestion]}):
        run = auth_client.post(f"{base}/run-audit", json={**selection, **overrides})
        assert run.status_code == 200, run.text
        assert run.json()["rejected_count"] == 0
        findings = auth_client.get(f"{base}/audits/{run.json()['id']}/findings").json()
        assert not any(f["issue_type"].startswith("SCREENER") for f in findings)


def test_raw_only_audit_requires_a_validated_manual_eligibility_rule(auth_client):
    base = "/api/surveys"
    data = _upload(auth_client, raw="ResponseId,Age\nA,17\n")
    mapping = {"question_number": "Age", "matched_column": "Age", "question_type": "screener"}
    raw_only = {"raw_filename": data["raw_artifact_name"]}
    rejected = auth_client.post(f"{base}/run-audit", json={**raw_only, "confirmed_mappings": [{**mapping, "min_age": 18}]})
    assert rejected.status_code == 422 and "require a questionnaire" in rejected.json()["detail"]
    for value in (True, -1, 150, "18"):
        invalid = auth_client.post(f"{base}/run-audit", json={**raw_only, "confirmed_mappings": [mapping],
            "timing_rules": {"manual_eligibility_rules": [{"question_number": "Age", "min_age": value}]}})
        assert invalid.status_code == 422, invalid.text
    run = auth_client.post(f"{base}/run-audit", json={**raw_only, "confirmed_mappings": [mapping],
        "timing_rules": {"manual_eligibility_rules": [{"question_number": "Age", "min_age": 18}]}})
    assert run.status_code == 200 and run.json()["rejected_count"] == 1, run.text
    findings = auth_client.get(f"{base}/audits/{run.json()['id']}/findings").json()
    assert "Manual configuration" in findings[0]["evidence"]


def test_scoped_page_and_us_age_rules_reject_only_where_applicable(auth_client):
    base = "/api/surveys"
    qq = ("Q1. What is your role?\nPage timer minimum: 20 seconds for Q1 only.\n"
          "Q2. What is your job title?\nQ.Country: Where do you live?\n"
          "S1. What is your age? [Terminate if under 18 in the US only.]")
    raw = ("ResponseId,Q1,Q2,Q.Country,S1,PageTimer_Q1,PageTimer_Q2\n"
           "CA,Engineer,A,CA,17,40,5\nUS,Developer,B,U.S.A.,17,30,5\n"
           "FAST,Manager,C,CA,25,5,45\nUNKNOWN,Lead,D,,17,40,5\n")
    selection = _upload(auth_client, qq=qq, raw=raw)
    mappings = [{"question_number": q, "matched_column": q} for q in ("Q1", "Q2", "Q.Country", "S1")]
    run = auth_client.post(f"{base}/run-audit", json={**selection, "confirmed_mappings": mappings, "reject_page_timers": True})
    assert run.status_code == 200, run.text
    assert (run.json()["rejected_count"], run.json()["speeding_count"]) == (2, 1)
    path = f"{base}/audits/{run.json()['id']}"
    findings = auth_client.get(f"{path}/findings").json()
    assert {(f["respondent_key"], f["issue_type"]) for f in findings if f["review_recommendation"] == "REJECT"} == {
        ("US", "SCREENER_AGE_VIOLATION"), ("FAST", "PAGE_TIMER_SPEEDING")}
    detail = auth_client.get(path).json()
    rules = detail["effective_rules"]
    assert rules["effective_page_rules"] == {"PageTimer_Q1": dict(threshold=20, source="Questionnaire Specification",
        units="seconds", scope={"column": "PageTimer_Q1", "question_number": "Q1"}, action="REJECT")}
    assert rules["effective_eligibility_rules"][0]["scope"]["countries"] == ["US"]
    assert detail["speeding_flagged_count"] == 1
    assert next(x for x in detail["qc_coverage"] if x["check_name"] == "Individual Question/Page Timers")["flagged_count"] == 1


def test_explicit_cutoff_for_strict_speeding_and_maximum_only_duration(auth_client):
    base = "/api/surveys"
    artifact = _upload(auth_client, raw="ResponseId,Duration\nA,90\nB,660\n")["raw_artifact_name"]
    raw_only = {"raw_filename": artifact}
    invalid = auth_client.post(f"{base}/run-audit", json={**raw_only, "reject_speeding_under_2min": True})
    assert invalid.status_code == 422 and "explicit positive cutoff" in invalid.json()["detail"]
    for strict, expected in ((False, 0), (True, 1)):
        body = {**raw_only, "max_expected_duration_min": 10}
        if strict:
            body.update(reject_speeding_under_2min=True, extreme_speeding_cutoff_min=2)
        run = auth_client.post(f"{base}/run-audit", json=body)
        assert run.status_code == 200 and run.json()["rejected_count"] == expected, run.text
        path = f"{base}/audits/{run.json()['id']}"
        findings = auth_client.get(f"{path}/findings").json()
        assert next(f for f in findings if f["issue_type"] == "EXTENDED_DURATION")["expected_rule"].startswith("Maximum duration 10 min")
        if strict:
            rules = auth_client.get(path).json()["effective_rules"]["effective_duration_rules"]
            assert rules["extreme_speeding_threshold_seconds"] == dict(threshold=120, source="Manual configuration",
                units="seconds", scope="survey", action="REJECT")


def test_results_paginate_and_filter_beyond_old_limits(auth_client):
    base = "/api/surveys"
    source = CSV.splitlines()[0] + "\n" + "\n".join(
        f"R{i:02},500,30,5,5,5,5,asdfghjk {i}" for i in range(60)
    )
    selection = _upload(auth_client, raw=source)
    audit = auth_client.post(f"{base}/run-audit", json=selection)
    assert audit.status_code == 200, audit.text
    run = f"{base}/audits/{audit.json()['id']}"
    late = auth_client.get(f"{run}/respondents?paginated=true&search=R59&disposition=REVIEW_REQUIRED").json()
    assert late["total"] == 1 and late["items"][0]["row_number"] == 61
    findings = auth_client.get(f"{run}/findings?paginated=true&limit=50&page=2&issue_type=GIBBERISH").json()
    assert findings["total"] == 60 and len(findings["items"]) == 10


def test_structured_routing_excludes_legitimate_skip_from_completeness(auth_client):
    base = "/api/surveys"
    qq = "Q1. Do you currently use the service?\nQ2. In your own words describe the service."
    raw = "ResponseId,Q1,Q2\nA,No,\nB,No,this should have been skipped\nC,Yes,\nD,,\n"
    selection = _upload(auth_client, qq=qq, raw=raw)
    response = auth_client.post(f"{base}/run-audit", json={**selection, "timing_rules": {
        "required_questions": ["Q2"],
        "routing_rules": [{"control_question": "Q1", "equals": "No", "skip_question": "Q2"}],
    }})
    assert response.status_code == 200, response.text
    run = f"{base}/audits/{response.json()['id']}"
    findings = auth_client.get(f"{run}/findings").json()
    assert {(x["respondent_key"], x["issue_type"]) for x in findings} == {
        ("B", "ROUTING_VIOLATION"), ("C", "INCOMPLETE_RESPONSE")
    }

def test_additive_review_migration_and_concurrent_updates(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import create_engine, inspect, select
    from sqlalchemy.orm import Session
    from app import database
    from app.models import SurveyAuditRun, SurveyRespondent, SurveyReviewEvent
    from app.routers.surveys import _save_review
    from app.schemas import SurveyReviewDecisionRequest

    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite3'}")
    monkeypatch.setattr(database, "engine", engine)
    before = [table for table in database.Base.metadata.sorted_tables if table is not SurveyReviewEvent.__table__]
    database.Base.metadata.create_all(engine, tables=before)
    with Session(engine) as db:
        run = SurveyAuditRun(raw_filename="legacy.csv", raw_file_hash="a" * 64,
                             status="completed", total_respondents=2, review_required_count=2)
        db.add(run)
        db.flush()
        rows = [SurveyRespondent(audit_id=run.id, respondent_key=f"SYNTH_{i}", row_number=i,
                                 disposition="REVIEW_REQUIRED", raw_data_json="{}") for i in (1, 2)]
        db.add_all(rows)
        db.flush()
        run_id, ids = run.id, [row.id for row in rows]
        db.commit()
    database.init_db()
    database.init_db()
    assert "survey_review_events" in inspect(engine).get_table_names()

    barrier = Barrier(2)
    def review(row_id, decision):
        with Session(engine) as db:
            barrier.wait(timeout=5)
            _save_review(db, run_id, SurveyReviewDecisionRequest(decision=decision, notes="Synthetic review"),
                         respondent_id=row_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        actions = [pool.submit(review, row_id, decision) for row_id, decision in zip(ids, ("ACCEPTED", "REJECTED"))]
        for action in actions:
            action.result(timeout=10)
    with Session(engine) as db:
        run = db.get(SurveyAuditRun, run_id)
        assert run.raw_filename == "legacy.csv"
        assert (run.accepted_count, run.rejected_count, run.review_required_count) == (1, 1, 0)
        assert len(list(db.scalars(select(SurveyReviewEvent)))) == 2
    engine.dispose()
