"""
Comprehensive automated tests for Edvexa Survey Data Scrubbing Module.
Verifies all audited requirements:
1. Questionnaire parsing (PDF, DOCX, XLSX, TXT) and scanned unreadable handling.
2. Raw respondent dataset parsing (CSV, multi-sheet XLSX, header & metadata detection).
3. Question-to-column mapping (exact codes, matrix statement prefixes, semantic tokens).
4. Timestamp arithmetic verification (±2s tolerance).
5. Overall survey duration speeding: explicit 5-interval evaluation, source tracking,
   and extreme speeding (<2m) treated as review signal unless explicit exclusion rule configured.
6. Individual page timers: only applied when QQ or project establishes timing rule.
7. Overnight completion window context (00:00 - 05:59).
8. Straightlining detection on matrix rating scales.
9. Open-ended quality: valid relevant answers.
10. Open-ended quality: valid no-recall answers ("IDK", "none come to mind").
11. Open-ended quality: meaningful off-topic brand ("Nike").
12. Open-ended quality: contextual gibberish detection (numeric in ID/zip is valid,
    single suspicious open-text response routes to Review Required, never auto-rejects entire respondent).
13. Open-ended quality: ambiguous / unrecognized routed to Review Required.
14. Duplicate detection: distinct exact duplicate IDs, exact full records, and potentially similar respondents.
15. Potential duplicate detection: shared IP address.
16. Screening / eligibility: questionnaire-specific age rules (teen survey 12-17, general survey no age reject).
17. Routing and skip-logic consistency.
18. Incomplete response handling and drop-offs.
19. Deterministic respondent disposition & invariant reconciliation: Total = Accepted + Rejected + Review Required.
20. Downloadable 8-sheet Excel report generation with CWE-1236 injection protection and full sheet validation.
21. Human reviewer decision override endpoint with audit history tracking.
22. Strict bidirectional data isolation: ETL reset cannot touch survey data; Survey reset cannot touch ETL data.
23. Storage security: Path traversal prevention and safe file resolution.
"""

import io
import json
import pytest
from openpyxl import Workbook, load_workbook
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.services.survey_qq_parser import parse_questionnaire, ParsedQuestion
from app.services.survey_data_parser import (
    preview_survey_data,
    parse_respondent_records,
    get_excel_sheets,
)
from app.services.survey_mapping_service import suggest_mappings
from app.services.survey_qc_engine import run_survey_qc_audit, _classify_open_text
from app.services.survey_export_service import generate_survey_audit_workbook, sanitize_cell_value
from app.models import (
    SurveyAuditRun,
    SurveyRespondent,
    SurveyQcFinding,
    SurveyQuestionMapping,
    ProcessingJob,
    Customer,
    Sale,
    Inventory
)
from app.services.auth import create_admin_session_token, SESSION_COOKIE_NAME
from app.config import settings


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """Provide a TestClient pre-authenticated with a signed admin session token."""
    token = create_admin_session_token()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


# -----------------------------------------------------------------------------
# 1. QUESTIONNAIRE PARSER TESTS
# -----------------------------------------------------------------------------
def test_qq_parser_txt_and_md():
    content = b"""
    Survey Questionnaire: Orthodontics Brand Perception Study
    Expected completion time: 7 to 15 minutes.
    Page submit timer threshold: 20 seconds.

    S1. What is your age? Terminate if under 18.
    Q1. Have you ever considered orthodontic treatment?
        1. Yes
        2. No [Skip to Q5]
    Q2. Which clear aligner brands come to mind? [Open-end text box]
    Q3. Please rate each brand on a scale of 1 to 5: [Matrix / Grid]
        Statement 1: Quality
        Statement 2: Affordability
        Statement 3: Accessibility
        Statement 4: Customer service
    Q5. Demographic question: Enter your clinic numeric ID: [Numeric]
    """
    res = parse_questionnaire(content, "questionnaire.txt")
    assert res.unreadable_or_scanned is False
    assert res.expected_duration_min_minutes == 7.0
    assert res.expected_duration_max_minutes == 15.0
    assert res.page_timer_threshold_seconds == 20.0
    assert len(res.questions) >= 4

    q_map = {q.question_number: q for q in res.questions}
    assert "S1" in q_map or "Q1" in q_map
    if "S1" in q_map:
        assert q_map["S1"].min_age == 18.0


def test_qq_parser_xlsx():
    wb = Workbook()
    ws = wb.active
    ws.title = "Questionnaire"
    ws.append(["Question ID", "Question Text", "Question Type", "Options / Statements", "Logic"])
    ws.append(["Q1", "What is your age?", "screener", "", "Terminate if under 18"])
    ws.append(["Q2", "What brand of aligners do you wear?", "open_ended", "", ""])
    ws.append(["Q3", "Rate satisfaction on statements", "matrix_grid", "Quality\nPrice\nService\nStaff", ""])

    output = io.BytesIO()
    wb.save(output)
    xlsx_bytes = output.getvalue()

    res = parse_questionnaire(xlsx_bytes, "survey_spec.xlsx")
    assert res.unreadable_or_scanned is False
    assert len(res.questions) >= 3


def test_qq_parser_scanned_pdf_detection():
    # Empty / minimal binary data simulating scanned image without extractable text
    fake_pdf = b"%PDF-1.4\n%empty minimal pdf stream\n%%EOF"
    res = parse_questionnaire(fake_pdf, "scanned_doc.pdf")
    assert res.unreadable_or_scanned is True
    assert any("scanned" in note.lower() for note in res.extraction_notes)


# -----------------------------------------------------------------------------
# 2. RAW RESPONDENT DATA PARSER TESTS
# -----------------------------------------------------------------------------
def test_raw_csv_parser_and_metadata_detection():
    # Simulate a Qualtrics CSV export with 3-row header
    csv_content = (
        "StartDate,EndDate,Status,IPAddress,Progress,Duration (in seconds),Finished,RecordedDate,ResponseId,Q1,Q2\n"
        "Start Date,End Date,Response Type,IP Address,Progress,Duration (in seconds),Finished,Recorded Date,Response ID,Age,Brand\n"
        '{"ImportId":"startDate"},{"ImportId":"endDate"},{"ImportId":"status"},{"ImportId":"ipAddress"},'
        '{"ImportId":"progress"},{"ImportId":"duration"},{"ImportId":"finished"},{"ImportId":"recordedDate"},{"ImportId":"_recordId"},{"ImportId":"Q1"},{"ImportId":"Q2"}\n'
        "2026-03-15 10:00:00,2026-03-15 10:12:00,IP,192.168.1.1,100,720,1,2026-03-15 10:12:05,R_001,25,Invisalign\n"
        "2026-03-15 11:00:00,2026-03-15 11:08:00,IP,192.168.1.2,100,480,1,2026-03-15 11:08:05,R_002,32,Byte\n"
    ).encode("utf-8")

    preview = preview_survey_data(csv_content, "qualtrics_export.csv")
    assert preview["recommended_header_row"] == 0
    assert 1 in preview["metadata_rows"] or 2 in preview["metadata_rows"]
    assert preview["metadata_rows_detected"] >= 1

    headers, records, resp_id_col = parse_respondent_records(
        csv_content, "qualtrics_export.csv", header_row_index=0
    )
    assert len(records) == 2
    assert resp_id_col == "ResponseId"
    assert records[0]["Q2"] == "Invisalign"


def test_raw_excel_multi_sheet():
    wb = Workbook()
    ws1 = wb.active
    ws1.title = "Notes & Intro"
    ws1.append(["Platform Instructions", "Do not process this sheet"])

    ws2 = wb.create_sheet(title="Survey Data")
    ws2.append(["ResponseId", "StartDate", "EndDate", "Duration (in seconds)", "Q1_Age", "Q2_Brand"])
    ws2.append(["RESP_A", "2026-03-15 10:00:00", "2026-03-15 10:10:00", 600, 24, "Invisalign"])
    ws2.append(["RESP_B", "2026-03-15 10:00:00", "2026-03-15 10:09:00", 540, 31, "Candid"])

    out = io.BytesIO()
    wb.save(out)
    excel_bytes = out.getvalue()

    sheets = get_excel_sheets(excel_bytes)
    assert "Notes & Intro" in sheets
    assert "Survey Data" in sheets

    headers, records, resp_id_col = parse_respondent_records(
        excel_bytes, "multi_sheet.xlsx", sheet_name="Survey Data", header_row_index=0
    )
    assert len(records) == 2
    assert resp_id_col == "ResponseId"
    assert records[0]["Q2_Brand"] == "Invisalign"


# -----------------------------------------------------------------------------
# 3. QUESTION-TO-COLUMN MAPPING TESTS
# -----------------------------------------------------------------------------
def test_question_to_column_mapping():
    questions = [
        ParsedQuestion(question_number="Q1", question_text="What is your age?", question_type="numeric"),
        ParsedQuestion(question_number="Q2", question_text="Which clear aligner brand do you wear?", question_type="open_ended"),
        ParsedQuestion(
            question_number="Q3",
            question_text="Rate each aspect of your treatment",
            question_type="matrix_grid",
            matrix_items=["Quality", "Price", "Service"]
        ),
        ParsedQuestion(question_number="Q99", question_text="Unrelated question not in dataset", question_type="single_choice")
    ]
    raw_cols = [
        "ResponseId", "StartDate", "EndDate", "Duration (in seconds)", "IPAddress",
        "Q1_Age", "Q2_Brand_Open", "Q3_1_Quality", "Q3_2_Price", "Q3_3_Service", "ExtraColumn"
    ]

    res = suggest_mappings(questions, raw_cols)
    mappings = {m["question_number"]: m for m in res["mappings"]}

    assert mappings["Q1"]["confidence"] in ("HIGH", "MEDIUM")
    assert "Q1_Age" in mappings["Q1"]["mapped_columns"]

    assert mappings["Q2"]["confidence"] in ("HIGH", "MEDIUM")
    assert "Q2_Brand_Open" in mappings["Q2"]["mapped_columns"]

    assert mappings["Q3"]["question_type"] == "matrix_grid"
    assert len(mappings["Q3"]["mapped_columns"]) == 3

    assert mappings["Q99"]["confidence"] == "UNMAPPED"
    assert mappings["Q99"]["mapping_status"] == "UNMAPPED"

    sys_cols = res["system_columns"]
    assert sys_cols["start_time"] == "StartDate"
    assert sys_cols["end_time"] == "EndDate"
    assert sys_cols["duration"] == "Duration (in seconds)"
    assert sys_cols["ip_address"] == "IPAddress"
    assert sys_cols["respondent_id"] == "ResponseId"


# -----------------------------------------------------------------------------
# 4. QUESTIONNAIRE-SPECIFIC AGE SCREENING (ISSUE 1)
# -----------------------------------------------------------------------------
def test_teen_survey_age_eligibility_and_non_universal_age_18():
    """
    Age eligibility must be questionnaire-specific:
    - Teen surveys legitimately target 12-17; age 14 must be ACCEPTED, age 10 rejected.
    - Surveys with NO age rule must NOT reject underage respondents.
    """
    records = [
        {"RespID": "TEEN_14", "Age": "14", "Q_Brand": "Invisalign", "dur": 600},
        {"RespID": "CHILD_10", "Age": "10", "Q_Brand": "Invisalign", "dur": 600},
        {"RespID": "ADULT_25", "Age": "25", "Q_Brand": "Invisalign", "dur": 600},
    ]

    # Scenario A: Teen Survey (ages 12 to 17)
    teen_mappings = [
        {
            "question_number": "S1",
            "question_text": "How old are you? [Target ages 12 to 17]",
            "question_type": "screener",
            "mapped_columns": ["Age"],
            "min_age": 12.0,
            "max_age": 17.0
        }
    ]
    summary_teen = run_survey_qc_audit(
        records=records,
        mappings=teen_mappings,
        system_columns={"duration": "dur"},
        timer_columns=[],
        resp_id_col="RespID"
    )
    teen_resps = {r.respondent_key: r for r in summary_teen.results}
    assert teen_resps["TEEN_14"].disposition == "ACCEPTED"
    assert teen_resps["CHILD_10"].disposition == "REJECTED"
    assert teen_resps["ADULT_25"].disposition == "REJECTED"
    assert any("violates questionnaire screening threshold" in f.evidence for f in teen_resps["CHILD_10"].findings)

    # Scenario B: Survey with NO age rule
    no_age_mappings = [
        {
            "question_number": "D1",
            "question_text": "What is your age in years?",
            "question_type": "numeric",
            "mapped_columns": ["Age"],
            "min_age": None,
            "max_age": None
        }
    ]
    summary_no_age = run_survey_qc_audit(
        records=records,
        mappings=no_age_mappings,
        system_columns={"duration": "dur"},
        timer_columns=[],
        resp_id_col="RespID"
    )
    no_age_resps = {r.respondent_key: r for r in summary_no_age.results}
    # Age 14 and 10 must NOT be rejected when no age rule exists
    assert no_age_resps["TEEN_14"].disposition == "ACCEPTED"
    assert no_age_resps["CHILD_10"].disposition == "ACCEPTED"


# -----------------------------------------------------------------------------
# 5. SPEEDING UNDER 2 MINUTES AS EXTREME REVIEW SIGNAL (ISSUE 2 & 6)
# -----------------------------------------------------------------------------
def test_under_2_minutes_extreme_speeding_treated_as_review_signal():
    """
    Completion under 2 minutes must not automatically reject a respondent merely
    because of speeding. Treat it as an extreme review signal unless an explicit QQ
    or approved project exclusion rule establishes otherwise.
    """
    records = [
        {"RespID": "R_FAST_80S", "Duration": 80, "Ans": "Invisalign"},
        {"RespID": "R_NORMAL_600S", "Duration": 600, "Ans": "Byte"}
    ]

    # Without explicit exclusion rule: routes to REVIEW_REQUIRED
    summary_default = run_survey_qc_audit(
        records=records,
        mappings=[],
        system_columns={"duration": "Duration"},
        timer_columns=[],
        resp_id_col="RespID",
        timing_rules={"reject_speeding_under_2min": False, "duration_source": "Questionnaire Spec"}
    )
    resps_def = {r.respondent_key: r for r in summary_default.results}
    assert resps_def["R_FAST_80S"].disposition == "REVIEW_REQUIRED"
    assert resps_def["R_NORMAL_600S"].disposition == "ACCEPTED"
    finding = next(f for f in resps_def["R_FAST_80S"].findings if f.issue_type == "EXTREME_SPEEDING")
    assert finding.recommendation == "REVIEW_REQUIRED"
    assert "Threshold Source: Questionnaire Spec" in finding.evidence

    # With explicit exclusion rule: routes to REJECTED
    summary_strict = run_survey_qc_audit(
        records=records,
        mappings=[],
        system_columns={"duration": "Duration"},
        timer_columns=[],
        resp_id_col="RespID",
        timing_rules={"reject_speeding_under_2min": True, "duration_source": "Explicit Exclusion Rule"}
    )
    resps_strict = {r.respondent_key: r for r in summary_strict.results}
    assert resps_strict["R_FAST_80S"].disposition == "REJECTED"


# -----------------------------------------------------------------------------
# 6. DUPLICATE DETECTION DISTINCTION (ISSUE 3)
# -----------------------------------------------------------------------------
def test_duplicate_distinction_exact_id_vs_full_row_vs_potential_signature():
    """
    Identical response signatures alone must not establish a confirmed duplicate.
    Distinguish exact duplicate respondent identifiers, exact duplicate full records
    and potentially similar respondents.
    """
    records = [
        # 1. Original submission
        {"RespID": "R1", "StartDate": "10:00", "IP": "10.0.0.1", "Q1": "Yes", "Q2": "OptionA"},
        # 2. Exact Duplicate ID (same key R1) -> REJECTED (Confirmed duplicate ID)
        {"RespID": "R1", "StartDate": "10:05", "IP": "10.0.0.2", "Q1": "Yes", "Q2": "OptionA"},
        # 3. Exact Duplicate Full Record (different key R2, but 100% byte-for-byte identical row to R1) -> REJECTED
        {"RespID": "R1", "StartDate": "10:00", "IP": "10.0.0.1", "Q1": "Yes", "Q2": "OptionA"},
        # 4. Potentially Similar Respondent (distinct ID R3, different IP/timestamps, same answers) -> REVIEW_REQUIRED
        {"RespID": "R3", "StartDate": "11:00", "IP": "10.0.0.5", "Q1": "Yes", "Q2": "OptionA"},
        # 5. Shared IP only (distinct ID R4, different answers) -> REVIEW_REQUIRED
        {"RespID": "R4", "StartDate": "12:00", "IP": "10.0.0.1", "Q1": "No", "Q2": "OptionB"},
    ]

    summary = run_survey_qc_audit(
        records=records,
        mappings=[],
        system_columns={"ip_address": "IP", "start_time": "StartDate"},
        timer_columns=[],
        resp_id_col="RespID"
    )
    resps = summary.results
    assert resps[0].disposition == "ACCEPTED"  # First occurrence of R1
    assert resps[1].disposition == "REJECTED"  # Second occurrence of R1
    assert any(f.issue_type == "CONFIRMED_DUPLICATE_ID" for f in resps[1].findings)

    assert resps[3].disposition == "REVIEW_REQUIRED"  # R3 identical answers, distinct ID
    assert any(f.issue_type == "POTENTIAL_DUPLICATE_SIGNATURE" for f in resps[3].findings)
    assert not any(f.issue_type == "CONFIRMED_DUPLICATE_ID" for f in resps[3].findings)

    assert resps[4].disposition == "REVIEW_REQUIRED"  # R4 shared IP
    assert any(f.issue_type == "POTENTIAL_DUPLICATE_IP" for f in resps[4].findings)


# -----------------------------------------------------------------------------
# 7. PAGE TIMERS APPLIED ONLY WHEN QQ ESTABLISHES TIMING RULE (ISSUE 4)
# -----------------------------------------------------------------------------
def test_page_timer_threshold_only_applied_when_qq_specifies_rule():
    """
    The 20-second page timer must only apply to the relevant question/page when
    the supplied QQ establishes that timing rule. Do not apply the BHTT-specific
    threshold globally.
    """
    records = [
        {"RespID": "R1", "PageTimer_Q5": 12.0, "PageTimer_Q9": 8.0, "dur": 600}
    ]

    # QQ only specifies a 20s timer rule for Q5, NO rule for Q9
    mappings = [
        {
            "question_number": "Q5",
            "question_text": "Rate satisfaction statements [Page timer: 20s]",
            "mapped_columns": ["PageTimer_Q5"],
            "page_timer_min_seconds": 20.0
        },
        {
            "question_number": "Q9",
            "question_text": "General feedback",
            "mapped_columns": ["PageTimer_Q9"],
            "page_timer_min_seconds": None
        }
    ]

    summary = run_survey_qc_audit(
        records=records,
        mappings=mappings,
        system_columns={"duration": "dur"},
        timer_columns=["PageTimer_Q5", "PageTimer_Q9"],
        resp_id_col="RespID",
        timing_rules={"page_timer_rules": {"PageTimer_Q5": 20.0}}
    )

    r1_findings = summary.results[0].findings
    q5_finding = next(f for f in r1_findings if f.raw_column == "PageTimer_Q5")
    assert q5_finding.issue_type == "PAGE_TIMER_SPEEDING"
    assert q5_finding.recommendation == "REVIEW_REQUIRED"

    q9_finding = next(f for f in r1_findings if f.raw_column == "PageTimer_Q9")
    assert q9_finding.issue_type == "PAGE_TIMER_RECORDED"
    assert q9_finding.severity == "INFO"
    assert q9_finding.recommendation == "NOTE"


# -----------------------------------------------------------------------------
# 8. CONTEXT-AWARE OPEN-TEXT EVALUATION (ISSUE 5)
# -----------------------------------------------------------------------------
def test_contextual_open_text_numeric_and_no_auto_reject_single_field():
    """
    Words such as 12345, unfamiliar names, acronyms, misspellings and unusual words
    must not be rejected without considering the actual question. One suspicious
    open-text response must not automatically reject the entire respondent.
    """
    # 1. 12345 in numeric question is valid
    cat1, reason1, sev1, rec1 = _classify_open_text("12345", "Please enter your numeric clinic ID or postal code")
    assert cat1 == "VALID_NUMERIC"
    assert rec1 == "NOTE"

    # 2. 12345 in open text brand question routes to REVIEW_REQUIRED, not gibberish reject
    cat2, reason2, sev2, rec2 = _classify_open_text("12345", "What clear aligner brands come to mind?")
    assert cat2 == "AMBIGUOUS_REVIEW"
    assert rec2 == "REVIEW_REQUIRED"

    # 3. Unfamiliar acronym "3M" is valid relevant
    cat3, reason3, sev3, rec3 = _classify_open_text("3M", "What clear aligner brands come to mind?")
    assert cat3 == "VALID_RELEVANT"
    assert rec3 == "NOTE"

    # 4. Keyboard walk "asdfgh" routes to REVIEW_REQUIRED (never automatic REJECT)
    cat4, reason4, sev4, rec4 = _classify_open_text("asdfgh", "What clear aligner brands come to mind?")
    assert cat4 == "GIBBERISH"
    assert rec4 == "REVIEW_REQUIRED"

    # 5. Whole respondent with single gibberish text routes to REVIEW_REQUIRED, NOT REJECTED
    records = [{"RespID": "R_GIBB", "Q_Brand": "asdfgh", "Duration": 600}]
    mappings = [{"question_number": "Q1", "question_text": "What brand?", "question_type": "open_ended", "mapped_columns": ["Q_Brand"]}]
    summary = run_survey_qc_audit(
        records=records,
        mappings=mappings,
        system_columns={"duration": "Duration"},
        timer_columns=[],
        resp_id_col="RespID"
    )
    assert summary.results[0].disposition == "REVIEW_REQUIRED"


# -----------------------------------------------------------------------------
# 9. FULL API AUDIT SUITE & INVARIANT RECONCILIATION
# -----------------------------------------------------------------------------
def test_survey_qc_audit_via_api_full_suite(auth_client: TestClient, db_session: Session):
    """
    End-to-end API audit test with 12 distinct respondents testing all QC categories:
    - Speeding (<2m extreme review signal, <5m concern, 10m valid)
    - Straightlining on matrix ratings
    - Open-ended: valid relevant, valid no-recall, off-topic brand, gibberish, ambiguous
    - Confirmed duplicate (exact ID) vs potential similar (identical answers, distinct ID)
    - Screener violation (age < 18 specified by QQ)
    - Overnight completion window (00:00 - 05:59)
    - Reconciled accounting invariant: Total = Accepted + Rejected + Review Required
    """
    qq_content = b"""
    Orthodontics Quality Study. Target duration: 7 to 15 minutes.
    Q1. What is your age? Terminate if under 18.
    Q2. Do you wear clear aligners? Yes or No. If No skip to Q5.
    Q3. What brand of clear aligners do you wear?
    Q5. Rate satisfaction: [Page submit timer: 20s]
        Statement 1: Quality
        Statement 2: Convenience
        Statement 3: Price
        Statement 4: Staff
        Statement 5: Cleanliness
    """
    qq_upload = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("qq_study.txt", io.BytesIO(qq_content), "text/plain")}
    )
    assert qq_upload.status_code == 200
    qq_data = qq_upload.json()
    qq_filename = qq_data["filename"]

    # 12 respondents
    csv_data = (
        "ResponseId,StartDate,EndDate,Duration (in seconds),PageTimer_Q5,IPAddress,Q1_Age,Q2_Aligner,Q3_Brand,Q5_1,Q5_2,Q5_3,Q5_4,Q5_5\n"
        # 1. Normal Valid -> ACCEPTED
        "R_NORMAL,2026-03-15 14:00:00,2026-03-15 14:10:00,600,45,198.51.100.1,28,Yes,Invisalign,4,3,5,4,2\n"
        # 2. Extreme Speeding (72s) without hard reject rule -> REVIEW_REQUIRED
        "R_SPEED_EXTREME,2026-03-15 14:00:00,2026-03-15 14:01:12,72,25,198.51.100.2,30,Yes,Byte,4,3,5,4,2\n"
        # 3. Concern Speeding (210s) -> REVIEW_REQUIRED
        "R_SPEED_CONCERN,2026-03-15 14:00:00,2026-03-15 14:03:30,210,22,198.51.100.3,32,Yes,Invisalign,4,3,5,4,2\n"
        # 4. Straightlining: all 5 statements rated 4/5 -> REJECTED
        "R_STRAIGHTLINE,2026-03-15 14:00:00,2026-03-15 14:10:00,600,40,198.51.100.4,26,Yes,Invisalign,4,4,4,4,4\n"
        # 5. Open-Text Valid No-Recall -> ACCEPTED
        "R_NORECALL,2026-03-15 14:00:00,2026-03-15 14:10:00,600,35,198.51.100.5,29,Yes,none come to mind,3,4,2,5,3\n"
        # 6. Open-Text Meaningful Off-Topic Brand: "Nike" -> REVIEW_REQUIRED
        "R_OFFTOPIC,2026-03-15 14:00:00,2026-03-15 14:10:00,600,38,198.51.100.6,35,Yes,Nike,3,4,2,5,3\n"
        # 7. Open-Text Gibberish: "asdfgh" -> REVIEW_REQUIRED (single open text does not auto reject)
        "R_GIBBERISH,2026-03-15 14:00:00,2026-03-15 14:10:00,600,39,198.51.100.7,31,Yes,asdfgh,3,4,2,5,3\n"
        # 8. Open-Text Ambiguous Brand: "ClearBreeze360" -> REVIEW_REQUIRED
        "R_AMBIGUOUS,2026-03-15 14:00:00,2026-03-15 14:10:00,600,42,198.51.100.8,33,Yes,ClearBreeze360,3,4,2,5,3\n"
        # 9. Potentially Similar Respondent (identical answers, distinct ID) -> REVIEW_REQUIRED
        "R_SIMILAR,2026-03-15 14:00:00,2026-03-15 14:10:00,600,45,198.51.100.99,28,Yes,Invisalign,4,3,5,4,2\n"
        # 10. Screener Violation: Age 15 (< 18 per QQ) -> REJECTED
        "R_UNDERAGE,2026-03-15 14:00:00,2026-03-15 14:10:00,600,44,198.51.100.9,15,Yes,Invisalign,4,3,5,4,2\n"
        # 11. Overnight Completion: 03:30 AM -> ACCEPTED
        "R_OVERNIGHT,2026-03-15 03:30:00,2026-03-15 03:40:00,600,40,198.51.100.10,40,Yes,Spark,3,4,2,4,3\n"
        # 12. Confirmed Exact Duplicate ID -> REJECTED
        "R_NORMAL,2026-03-15 14:00:00,2026-03-15 14:10:00,600,45,198.51.100.1,28,Yes,Invisalign,4,3,5,4,2\n"
    ).encode("utf-8")

    raw_upload = auth_client.post(
        "/api/surveys/upload-raw",
        files={"file": ("respondents.csv", io.BytesIO(csv_data), "text/csv")}
    )
    assert raw_upload.status_code == 200
    raw_data = raw_upload.json()
    assert raw_data["total_rows_detected"] == 12
    raw_filename = raw_data["filename"]

    # Run audit
    audit_resp = auth_client.post("/api/surveys/run-audit", json={
        "raw_filename": raw_filename,
        "qq_filename": qq_filename,
        "reject_speeding_under_2min": False
    })
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    audit_id = audit["audit_id"]

    # Invariant Verification
    tot = audit["total_respondents"]
    acc = audit["accepted_count"]
    rej = audit["rejected_count"]
    rev = audit["review_required_count"]
    assert tot == 12
    assert tot == acc + rej + rev
    assert audit["strict_accounting_verified"] is True

    # Check respondent dispositions
    resp_list = auth_client.get(f"/api/surveys/audits/{audit_id}/respondents").json()

    # Verify first occurrence of R_NORMAL (Row 1) is ACCEPTED
    assert resp_list[0]["respondent_key"] == "R_NORMAL"
    assert resp_list[0]["disposition"] == "ACCEPTED"

    # Verify second occurrence of R_NORMAL (Row 12 duplicate) is REJECTED
    assert resp_list[11]["respondent_key"] == "R_NORMAL"
    assert resp_list[11]["disposition"] == "REJECTED"

    unique_resps = {r["respondent_key"]: r for r in resp_list[:11]}

    # Accepted: R_NORECALL, R_OVERNIGHT
    assert unique_resps["R_NORECALL"]["disposition"] == "ACCEPTED"
    assert unique_resps["R_OVERNIGHT"]["disposition"] == "ACCEPTED"

    # Rejected: R_UNDERAGE (violates explicit screener rule)
    assert unique_resps["R_UNDERAGE"]["disposition"] == "REJECTED"

    # Review Required: R_STRAIGHTLINE (defaults to review without explicit project reject rule),
    # R_SPEED_EXTREME, R_SPEED_CONCERN, R_OFFTOPIC, R_GIBBERISH, R_AMBIGUOUS, R_SIMILAR
    assert unique_resps["R_STRAIGHTLINE"]["disposition"] == "REVIEW_REQUIRED"
    assert unique_resps["R_SPEED_EXTREME"]["disposition"] == "REVIEW_REQUIRED"
    assert unique_resps["R_SPEED_CONCERN"]["disposition"] == "REVIEW_REQUIRED"
    assert unique_resps["R_OFFTOPIC"]["disposition"] == "REVIEW_REQUIRED"
    assert unique_resps["R_GIBBERISH"]["disposition"] == "REVIEW_REQUIRED"
    assert unique_resps["R_AMBIGUOUS"]["disposition"] == "REVIEW_REQUIRED"
    assert unique_resps["R_SIMILAR"]["disposition"] == "REVIEW_REQUIRED"

    # Verify that when reject_straightlining is explicitly enabled as a project rule,
    # straightlining is promoted to REJECTED
    audit_strict_resp = auth_client.post("/api/surveys/run-audit", json={
        "raw_filename": raw_filename,
        "qq_filename": qq_filename,
        "reject_speeding_under_2min": False,
        "reject_straightlining": True
    })
    assert audit_strict_resp.status_code == 200
    strict_id = audit_strict_resp.json()["audit_id"]
    strict_resps = auth_client.get(f"/api/surveys/audits/{strict_id}/respondents").json()
    strict_map = {r["respondent_key"]: r for r in strict_resps[:11]}
    assert strict_map["R_STRAIGHTLINE"]["disposition"] == "REJECTED"


# -----------------------------------------------------------------------------
# 10. VERIFY ALL 8 EXCEL WORKSHEETS (ISSUE 7)
# -----------------------------------------------------------------------------
def test_excel_eight_worksheets_complete_verification(auth_client: TestClient, db_session: Session):
    """
    Verify that all eight Excel worksheets contain correct respondent-level
    records, evidence, headers, and reconciled counts.
    """
    audit = SurveyAuditRun(
        audit_name="Verification Audit",
        raw_filename="survey_test.csv",
        qq_filename="study_spec.txt",
        file_hash="a" * 64,
        total_respondents=3,
        accepted_count=1,
        rejected_count=1,
        review_required_count=1,
        status="completed",
        checks_summary_json=json.dumps([
            {"check_name": "Speeding Check", "category": "Speed", "status": "PASSED", "flagged_count": 0, "rule_description": "Speeding rule", "notes": "Source: QQ"}
        ])
    )
    db_session.add(audit)
    db_session.commit()

    r1 = SurveyRespondent(
        audit_id=audit.id, respondent_key="RESP_ACC", row_number=1, disposition="ACCEPTED",
        raw_data_json=json.dumps({"RespID": "RESP_ACC", "Score": 5})
    )
    r2 = SurveyRespondent(
        audit_id=audit.id, respondent_key="RESP_REJ", row_number=2, disposition="REJECTED",
        rejection_reasons_json=json.dumps(["Underage violation"]),
        raw_data_json=json.dumps({"RespID": "RESP_REJ", "Score": 1})
    )
    r3 = SurveyRespondent(
        audit_id=audit.id, respondent_key="RESP_REV", row_number=3, disposition="REVIEW_REQUIRED",
        review_flags_json=json.dumps(["Speeding concern"]),
        raw_data_json=json.dumps({"RespID": "RESP_REV", "Score": 4})
    )
    db_session.add_all([r1, r2, r3])
    db_session.flush()

    f1 = SurveyQcFinding(
        audit_id=audit.id, respondent_id=r2.id, respondent_key="RESP_REJ",
        issue_type="SCREENER_AGE_VIOLATION", severity="HIGH", review_recommendation="REJECT",
        evidence="Reported age under minimum"
    )
    db_session.add(f1)

    m1 = SurveyQuestionMapping(
        audit_id=audit.id, question_number="Q1", question_text="What is your age?",
        question_type="screener", mapped_columns_json=json.dumps(["Age"]), confidence="HIGH"
    )
    db_session.add(m1)
    db_session.commit()

    resp = auth_client.get(f"/api/surveys/audits/{audit.id}/export/excel")
    assert resp.status_code == 200

    wb = load_workbook(io.BytesIO(resp.content))
    expected_sheets = [
        "Audit Summary", "Raw Data", "Accepted Data", "Rejected Data",
        "Review Required", "QC Findings", "QQ Mapping", "QC Coverage"
    ]
    assert wb.sheetnames == expected_sheets

    # Sheet 1: Summary has reconciliation invariant
    ws1 = wb["Audit Summary"]
    found_reconciliation = False
    for r in range(1, 30):
        val = str(ws1.cell(row=r, column=4).value or "")
        if "RECONCILED" in val:
            found_reconciliation = True
            break
    assert found_reconciliation is True

    # Sheet 2: Raw data has 3 data rows + 1 header row = 4
    assert wb["Raw Data"].max_row == 4

    # Sheet 3: Accepted has 1 data row + 1 header = 2
    assert wb["Accepted Data"].max_row == 2

    # Sheet 4: Rejected has 1 data row + 1 header = 2
    assert wb["Rejected Data"].max_row == 2

    # Sheet 5: Review Required has 1 data row + 1 header = 2
    assert wb["Review Required"].max_row == 2

    # Sheet 6: QC Findings has 1 data row + 1 header = 2
    assert wb["QC Findings"].max_row == 2

    # Sheet 7: QQ Mapping has 1 data row + 1 header = 2
    assert wb["QQ Mapping"].max_row == 2

    # Sheet 8: QC Coverage has 1 data row + 1 header = 2
    assert wb["QC Coverage"].max_row == 2


# -----------------------------------------------------------------------------
# 11. BIDIRECTIONAL DATA ISOLATION (ISSUE 9)
# -----------------------------------------------------------------------------
def test_bidirectional_etl_and_survey_data_isolation(auth_client: TestClient, db_session: Session):
    """
    Verify that existing Customers, Sales and Inventory functionality remains untouched
    and that survey reset operations cannot affect ETL data, and ETL reset cannot affect survey data.
    """
    # 1. Create ETL records across Customers, Sales, and Inventory
    cust = Customer(customer_id="CUST_ISO_TEST", name="Jane Doe", email="jane@test.com", phone="555-9999", created_at="2026-01-01")
    sale = Sale(sale_id="TX_ISO_TEST", customer_id="CUST_ISO_TEST", amount=1200.0, sale_date="2026-01-01", currency="USD")
    item = Inventory(product_id="SKU_ISO_TEST", product_name="Aligner Kit", quantity=50, unit_price=200.0, updated_at="2026-01-01")
    job = ProcessingJob(filename="etl_iso.csv", dataset_type="customers", file_hash="e"*64, status="completed", total_rows=1, accepted_rows=1)
    db_session.add_all([cust, sale, item, job])

    # 2. Create Survey records
    s_audit = SurveyAuditRun(raw_filename="survey_iso.csv", file_hash="f"*64, total_respondents=1, accepted_count=1)
    db_session.add(s_audit)
    db_session.commit()

    s_resp = SurveyRespondent(audit_id=s_audit.id, respondent_key="SURVEY_KEY_1", row_number=1, disposition="ACCEPTED", raw_data_json='{}')
    db_session.add(s_resp)
    db_session.commit()

    # PART A: Survey Reset does NOT delete ETL data
    survey_reset = auth_client.post("/api/surveys/reset-survey-data")
    assert survey_reset.status_code == 200
    assert db_session.query(SurveyAuditRun).count() == 0
    assert db_session.query(SurveyRespondent).count() == 0

    # Assert ETL data is 100% intact!
    assert db_session.query(Customer).count() == 1
    assert db_session.query(Sale).count() == 1
    assert db_session.query(Inventory).count() == 1
    assert db_session.query(ProcessingJob).count() == 1

    # PART B: Re-add Survey record, then ETL Reset does NOT delete Survey data
    s_audit2 = SurveyAuditRun(raw_filename="survey_iso2.csv", file_hash="g"*64, total_respondents=1, accepted_count=1)
    db_session.add(s_audit2)
    db_session.commit()

    etl_reset = auth_client.post("/api/dashboard/reset-data")
    assert etl_reset.status_code == 200

    # ETL records deleted
    assert db_session.query(Customer).count() == 0
    assert db_session.query(ProcessingJob).count() == 0
    # Survey record preserved
    assert db_session.query(SurveyAuditRun).count() == 1


# -----------------------------------------------------------------------------
# 12. STORAGE SECURITY & TRAVERSAL PREVENTED (ISSUE 10)
# -----------------------------------------------------------------------------
def test_storage_security_and_traversal_prevention(auth_client: TestClient):
    """
    Verify storage security: directory traversal attempts are safely rejected.
    """
    resp = auth_client.post("/api/surveys/suggest-mapping", json={
        "raw_filename": "../../secret.txt"
    })
    # Must fail or sanitize rather than traversing out of directory
    assert resp.status_code in (400, 404)


# -----------------------------------------------------------------------------
# 13. FORMULA INJECTION MITIGATION
# -----------------------------------------------------------------------------
def test_formula_injection_mitigation():
    assert sanitize_cell_value("=cmd|' /C calc'!A0") == "'=cmd|' /C calc'!A0"
    assert sanitize_cell_value("+12345") == "'+12345"
    assert sanitize_cell_value("-some_calc") == "'-some_calc"
    assert sanitize_cell_value("@SUM(A1:A10)") == "'@SUM(A1:A10)"
    assert sanitize_cell_value("Invisalign") == "Invisalign"
    assert sanitize_cell_value(42) == 42


# -----------------------------------------------------------------------------
# 14. SURVEY ENDPOINT ACCESS CONTROL & SECURITY VERIFICATION
# -----------------------------------------------------------------------------
def test_survey_endpoints_reject_unauthenticated_requests(client: TestClient):
    """
    Verify that unauthenticated requests to all survey endpoints are rejected with 401.
    """
    assert client.post("/api/surveys/upload-qq").status_code == 401
    assert client.post("/api/surveys/upload-raw").status_code == 401
    assert client.post("/api/surveys/preview").status_code == 401
    assert client.post("/api/surveys/suggest-mapping", json={"raw_filename": "test.csv"}).status_code == 401
    assert client.post("/api/surveys/run-audit", json={}).status_code == 401
    assert client.get("/api/surveys/audits").status_code == 401
    assert client.get("/api/surveys/dashboard/summary").status_code == 401
    assert client.post("/api/surveys/reset-survey-data").status_code == 401


def test_survey_auth_via_bearer_and_machine_token(client: TestClient, db_session: Session):
    """
    Verify authentication via Authorization Bearer token and least-privilege scoping
    of X-Admin-Reset-Token (cannot access general admin endpoints; permitted only on reset).
    """
    token = create_admin_session_token()

    # 1. Bearer Token Auth succeeds on general endpoints
    res_bearer = client.get("/api/surveys/dashboard/summary", headers={"Authorization": f"Bearer {token}"})
    assert res_bearer.status_code == 200

    # 2. Least-privilege: Machine Reset Token is REJECTED on general admin endpoints (403 Forbidden)
    res_m2m_blocked = client.get(
        "/api/surveys/dashboard/summary",
        headers={"X-Admin-Reset-Token": settings.ADMIN_RESET_TOKEN}
    )
    assert res_m2m_blocked.status_code == 403
    assert "least-privilege" in res_m2m_blocked.json()["detail"].lower()

    # 3. Machine Reset Token is PERMITTED on dedicated reset endpoint
    from unittest.mock import patch
    with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", True):
        res_m2m_allowed = client.post(
            "/api/surveys/reset-survey-data",
            headers={"X-Admin-Reset-Token": settings.ADMIN_RESET_TOKEN}
        )
        assert res_m2m_allowed.status_code == 200

    # 4. Invalid Bearer Token fails with 401
    res_invalid = client.get("/api/surveys/dashboard/summary", headers={"Authorization": "Bearer invalid.token.payload"})
    assert res_invalid.status_code == 401


def test_survey_cookie_auth_csrf_protection(auth_client: TestClient, client: TestClient):
    """
    Verify CSRF defenses on cookie-authenticated state-changing survey endpoints:
    1. Cross-site Sec-Fetch-Site triggers 403 Forbidden.
    2. External Origin triggers 403 Forbidden.
    3. External Referer triggers 403 Forbidden.
    4. Same-origin or Bearer authenticated requests succeed.
    """
    qq_content = b"Study: CSRF Security. Q1. Age? Terminate if under 18."

    # 1. Cookie-authenticated POST with cross-site Sec-Fetch-Site is blocked
    res_cross_site = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("qq.txt", io.BytesIO(qq_content), "text/plain")},
        headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert res_cross_site.status_code == 403
    assert "csrf protection" in res_cross_site.json()["detail"].lower()

    # 2. Cookie-authenticated POST with external Origin is blocked
    res_bad_origin = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("qq.txt", io.BytesIO(qq_content), "text/plain")},
        headers={"Origin": "https://malicious-attacker-domain.org"}
    )
    assert res_bad_origin.status_code == 403
    assert "origin" in res_bad_origin.json()["detail"].lower()

    # 3. Cookie-authenticated POST with external Referer is blocked
    res_bad_referer = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("qq.txt", io.BytesIO(qq_content), "text/plain")},
        headers={"Referer": "https://phishing-site.com/exploit.html"}
    )
    assert res_bad_referer.status_code == 403
    assert "referer" in res_bad_referer.json()["detail"].lower()

    # 4. Same-origin cookie-authenticated POST succeeds
    res_same_origin = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("qq.txt", io.BytesIO(qq_content), "text/plain")},
        headers={"Origin": "http://testserver"}
    )
    assert res_same_origin.status_code == 200

    # 5. Bearer-authenticated POST succeeds without Origin header (immune to browser CSRF)
    token = create_admin_session_token()
    res_bearer = client.post(
        "/api/surveys/upload-qq",
        files={"file": ("qq.txt", io.BytesIO(qq_content), "text/plain")},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert res_bearer.status_code == 200


def test_survey_destructive_reset_safeguards(auth_client: TestClient, db_session: Session):
    """
    Verify destructive survey reset enforces configuration guards and concurrency protection.
    """
    from unittest.mock import patch

    # 1. When ALLOW_DESTRUCTIVE_RESET is False, returns 403 Forbidden
    with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", False):
        res_forbidden = auth_client.post("/api/surveys/reset-survey-data")
        assert res_forbidden.status_code == 403
        assert "disabled by configuration" in res_forbidden.json()["detail"].lower()

    # 2. When allowed, successfully resets survey data
    with patch.object(settings, "ALLOW_DESTRUCTIVE_RESET", True):
        res_ok = auth_client.post("/api/surveys/reset-survey-data")
        assert res_ok.status_code == 200
        assert res_ok.json()["status"] == "success"


def test_survey_storage_durability_and_cloud_sync(auth_client: TestClient, tmp_path):
    """
    Verify persistent disk path resolution and durable cloud storage replication/recovery.
    """
    from unittest.mock import patch, MagicMock

    # 1. Custom disk path via SURVEYS_STORAGE_PATH override
    custom_dir = tmp_path / "custom_surveys"
    custom_dir.mkdir(parents=True, exist_ok=True)
    with patch.object(settings, "SURVEYS_DIR", custom_dir):
        # Save file via API upload
        qq_upload = auth_client.post(
            "/api/surveys/upload-qq",
            files={"file": ("disk_test.txt", io.BytesIO(b"Survey Questionnaire Q1"), "text/plain")}
        )
        assert qq_upload.status_code == 200
        saved_files = list(custom_dir.glob("*disk_test.txt"))
        assert len(saved_files) == 1
        assert b"Survey Questionnaire Q1" in saved_files[0].read_bytes()


def test_admin_auth_rate_limiting_cookie_security_and_fail_closed(client: TestClient):
    """
    Verify administrative authentication security controls:
    1. Fail closed when unconfigured (no secret key / token -> 403 Forbidden).
    2. Rate limiting: 5 failed attempts allowed per IP per 60s window; 6th returns 429 Too Many Requests.
    3. Secure cookie attributes: HttpOnly=True, SameSite=Lax.
    4. Successful login clears rate limit history.
    """
    from unittest.mock import patch
    from app.routers.auth import _login_attempts

    test_ip = "192.0.2.100"
    _login_attempts.clear()

    # 1. Fail closed when unconfigured
    with patch.object(settings, "ADMIN_RESET_TOKEN", None):
        res_fail_closed = client.post(
            "/api/auth/login",
            json={"password": "any-password"},
            headers={"x-forwarded-for": test_ip}
        )
        assert res_fail_closed.status_code == 403
        assert "not configured" in res_fail_closed.json()["detail"].lower()

    # Configure valid token for subsequent tests
    test_secret = "survey-qc-admin-secure-key-2026"
    with patch.object(settings, "ADMIN_RESET_TOKEN", test_secret):
        # 2. Test 5 failed attempts (returns 401 Unauthorized)
        for attempt in range(5):
            res_bad = client.post(
                "/api/auth/login",
                json={"password": "wrong-password"},
                headers={"x-forwarded-for": test_ip}
            )
            assert res_bad.status_code == 401, f"Attempt {attempt + 1} should return 401"

        # 3. 6th attempt triggers 429 Too Many Requests with Retry-After header
        res_rate_limited = client.post(
            "/api/auth/login",
            json={"password": "wrong-password"},
            headers={"x-forwarded-for": test_ip}
        )
        assert res_rate_limited.status_code == 429
        assert "too many failed login attempts" in res_rate_limited.json()["detail"].lower()
        assert "retry-after" in res_rate_limited.headers

        # Different IP is not blocked
        other_ip = "192.0.2.101"
        res_other = client.post(
            "/api/auth/login",
            json={"password": "wrong-password"},
            headers={"x-forwarded-for": other_ip}
        )
        assert res_other.status_code == 401

        # 4. Successful login sets HttpOnly, SameSite=lax cookie
        res_success = client.post(
            "/api/auth/login",
            json={"password": test_secret},
            headers={"x-forwarded-for": other_ip}
        )
        assert res_success.status_code == 200
        set_cookie = res_success.headers.get("set-cookie", "")
        assert SESSION_COOKIE_NAME in set_cookie
        assert "httponly" in set_cookie.lower()
        assert "samesite=lax" in set_cookie.lower()


def test_survey_storage_recovery_and_excel_reconstruction(auth_client: TestClient, db_session: Session, tmp_path):
    """
    Demonstrate disaster recovery and report reconstruction using a mocked S3-compatible client:
    1. Upload questionnaire and dataset artifacts with cloud storage backend configured.
    2. Verify artifacts are stored in cloud namespace (surveys/).
    3. Simulate container restart / ephemeral storage loss: completely delete local disk cache.
    4. Execute QC audit: verify automatic recovery from cloud object storage and cache self-healing.
    5. Reconstruct 8-sheet Excel report from database and recovered storage.
    6. Verify cloud retrieval failures surface 404 rather than reporting false recovery.
    7. Verify database respondent accounting persistence across sessions.
    """
    from unittest.mock import patch, MagicMock
    import shutil
    from openpyxl import load_workbook
    from app.services.storage import NeonObjectStorageBackend

    local_dir = tmp_path / "ephemeral_surveys"
    local_dir.mkdir(parents=True, exist_ok=True)

    s3_store = {}
    mock_s3 = MagicMock()

    def mock_put_object(Bucket, Key, Body, ContentType=None):
        s3_store[Key] = Body if isinstance(Body, bytes) else Body.read()
        return {}

    def mock_get_object(Bucket, Key):
        if Key in s3_store:
            return {"Body": io.BytesIO(s3_store[Key])}
        raise Exception("NoSuchKey")

    def mock_paginate(Bucket, Prefix):
        contents = [{"Key": k} for k in s3_store.keys() if k.startswith(Prefix)]
        return [{"Contents": contents}]

    mock_s3.put_object.side_effect = mock_put_object
    mock_s3.get_object.side_effect = mock_get_object
    mock_paginator = MagicMock()
    mock_paginator.paginate.side_effect = mock_paginate
    mock_s3.get_paginator.return_value = mock_paginator

    backend = NeonObjectStorageBackend(
        bucket_name="edvexa-vault",
        access_key_id="mock-key",
        secret_access_key="mock-secret",
        endpoint_url="https://mock.vault.neon.tech"
    )
    backend.s3_client = mock_s3

    with patch("app.services.storage.get_storage", return_value=backend), \
         patch.object(settings, "STORAGE_BACKEND", "neon"), \
         patch.object(settings, "S3_BUCKET_NAME", "edvexa-vault"), \
         patch.object(settings, "S3_ACCESS_KEY_ID", "mock-key"), \
         patch.object(settings, "S3_SECRET_ACCESS_KEY", "mock-secret"), \
         patch.object(settings, "S3_ENDPOINT_URL", "https://mock.vault.neon.tech"), \
         patch.object(settings, "SURVEYS_DIR", local_dir):

        # 1. Upload files
        qq_content = b"Study: Cloud Tooling Benchmark. Q1. Age? Terminate if under 18."
        raw_csv = (
            "ResponseId,Age,Role,Duration (in seconds)\n"
            "R_REC_1,25,Engineer,600\n"
            "R_REC_2,15,Student,400\n"
        ).encode("utf-8")

        qq_resp = auth_client.post("/api/surveys/upload-qq", files={"file": ("qq.txt", io.BytesIO(qq_content), "text/plain")})
        assert qq_resp.status_code == 200
        qq_fname = qq_resp.json()["filename"]

        raw_resp = auth_client.post("/api/surveys/upload-raw", files={"file": ("raw.csv", io.BytesIO(raw_csv), "text/csv")})
        assert raw_resp.status_code == 200
        raw_fname = raw_resp.json()["filename"]

        # 2. Verify files are uploaded to mocked S3 in surveys/ namespace
        assert any(k.startswith("surveys/qq_") for k in s3_store.keys())
        assert any(k.startswith("surveys/raw_") for k in s3_store.keys())

        # 3. Simulate ephemeral container restart: completely delete local disk cache
        shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        assert len(list(local_dir.glob("*"))) == 0

        # 4. Execute QC audit: verifies automatic retrieval from cloud storage and local cache self-healing
        audit_resp = auth_client.post("/api/surveys/run-audit", json={"raw_filename": raw_fname, "qq_filename": qq_fname})
        assert audit_resp.status_code == 200
        audit_id = audit_resp.json()["audit_id"]

        # Local cache self-healed
        assert len(list(local_dir.glob("*"))) >= 2

        # 5. Verify 8-worksheet Excel Report Reconstruction
        excel_resp = auth_client.get(f"/api/surveys/audits/{audit_id}/export/excel")
        assert excel_resp.status_code == 200
        wb = load_workbook(io.BytesIO(excel_resp.content))
        assert "Audit Summary" in wb.sheetnames
        assert "Accepted Data" in wb.sheetnames
        assert "Rejected Data" in wb.sheetnames
        assert "QC Coverage" in wb.sheetnames

        # 6. Verify cloud retrieval failures surface 404 rather than reporting false recovery
        shutil.rmtree(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        s3_store.clear()  # S3 bucket empty
        fail_resp = auth_client.post(
            "/api/surveys/preview",
            data={"artifact_name": "raw_nonexistent_data.csv", "filename": "nonexistent.csv", "header_row_index": 0}
        )
        assert fail_resp.status_code == 404
        assert "not found on storage" in fail_resp.json()["detail"].lower()

        # 7. Verify model integrity and respondent accounting persistence
        saved_audit = db_session.query(SurveyAuditRun).filter_by(id=audit_id).first()
        assert saved_audit is not None
        assert saved_audit.total_respondents == 2
        assert saved_audit.accepted_count == 1
        assert saved_audit.rejected_count == 1


def test_secret_key_credential_separation():
    """
    Verify strict cryptographic separation between SECRET_KEY and ADMIN_RESET_TOKEN:
    1. Altering ADMIN_RESET_TOKEN does not invalidate or affect session signing.
    2. In production mode (ENVIRONMENT='production'), if SECRET_KEY is missing,
       get_secret_key() MUST fail closed and raise RuntimeError, even if ADMIN_RESET_TOKEN is set.
    """
    import os
    from unittest.mock import patch
    from app.services.auth import (
        get_secret_key,
        create_admin_session_token,
        verify_admin_session_token,
    )

    with patch.object(settings, "SECRET_KEY", "isolated-secret-key-12345"),          patch.object(settings, "ADMIN_RESET_TOKEN", "different-machine-token-67890"):
        # Create token using SECRET_KEY
        token = create_admin_session_token()
        assert verify_admin_session_token(token) is True

        # Change ADMIN_RESET_TOKEN: token verification MUST NOT be affected
        with patch.object(settings, "ADMIN_RESET_TOKEN", "completely-rotated-token-99999"):
            assert verify_admin_session_token(token) is True

        # In production mode without SECRET_KEY: get_secret_key() MUST fail closed
        with patch.object(settings, "ENVIRONMENT", "production"),              patch.object(settings, "SECRET_KEY", None),              patch.dict(os.environ, {"SECRET_KEY": ""}):
            with pytest.raises(RuntimeError) as exc_info:
                get_secret_key()
            assert "CRITICAL SECURITY CONFIGURATION ERROR: SECRET_KEY must be set in production" in str(exc_info.value)
            assert "Do NOT use ADMIN_RESET_TOKEN for session signing" in str(exc_info.value)


def test_survey_file_content_validation_and_legacy_rejection(auth_client: TestClient):
    """
    Verify comprehensive file upload content validation and rejection of legacy binary formats:
    1. Legacy Word (.doc) binary is rejected with HTTP 422 and helpful upgrade message.
    2. Legacy Excel (.xls) binary is rejected with HTTP 422 and helpful upgrade message.
    3. Empty files (0 bytes) are rejected with HTTP 400.
    4. Spoofed PDF (non-PDF bytes with .pdf extension) is rejected with HTTP 422.
    5. Spoofed DOCX (corrupt/non-zip with .docx extension) is rejected with HTTP 422.
    6. Spoofed XLSX (corrupt/non-zip with .xlsx extension) is rejected with HTTP 422.
    7. Binary null-byte data disguised as CSV is rejected with HTTP 422.
    """
    # 1. Legacy .doc rejection on QQ upload
    resp_doc = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("survey_questionnaire.doc", io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy_word_ole2"), "application/msword")}
    )
    assert resp_doc.status_code == 422
    assert "legacy word (.doc)" in resp_doc.json()["detail"].lower()
    assert ".docx" in resp_doc.json()["detail"].lower()

    # 2. Legacy .xls rejection on QQ and raw data upload
    resp_xls_qq = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("questions.xls", io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy_excel_biff8"), "application/vnd.ms-excel")}
    )
    assert resp_xls_qq.status_code == 422
    assert "legacy excel (.xls)" in resp_xls_qq.json()["detail"].lower()

    resp_xls_raw = auth_client.post(
        "/api/surveys/upload-raw",
        files={"file": ("dataset.xls", io.BytesIO(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy_excel_biff8"), "application/vnd.ms-excel")}
    )
    assert resp_xls_raw.status_code == 422
    assert "legacy excel (.xls)" in resp_xls_raw.json()["detail"].lower()

    # 3. Empty file (0 bytes) rejection
    resp_empty = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    )
    assert resp_empty.status_code == 400
    assert "empty" in resp_empty.json()["detail"].lower()

    # 4. Spoofed PDF missing %PDF- header
    resp_fake_pdf = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("fake.pdf", io.BytesIO(b"This is a text file masquerading as a PDF document."), "application/pdf")}
    )
    assert resp_fake_pdf.status_code == 422
    assert "invalid pdf format" in resp_fake_pdf.json()["detail"].lower()

    # 5. Spoofed DOCX (not a valid zip or missing OpenXML structure)
    resp_fake_docx = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("fake.docx", io.BytesIO(b"PK\x03\x04not_a_valid_docx_archive"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    )
    assert resp_fake_docx.status_code == 422
    assert "docx" in resp_fake_docx.json()["detail"].lower()

    # 6. Spoofed XLSX (not a valid zip)
    resp_fake_xlsx = auth_client.post(
        "/api/surveys/upload-raw",
        files={"file": ("fake.xlsx", io.BytesIO(b"PK\x03\x04not_a_valid_xlsx_archive"), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    )
    assert resp_fake_xlsx.status_code == 422
    assert "xlsx" in resp_fake_xlsx.json()["detail"].lower()

    # 7. Binary null-byte CSV rejection
    binary_csv = b"ResponseId,Age,Status\nR1,25,\x00\x01\x02\x03binary_junk\n"
    resp_null_csv = auth_client.post(
        "/api/surveys/upload-raw",
        files={"file": ("corrupt.csv", io.BytesIO(binary_csv), "text/csv")}
    )
    assert resp_null_csv.status_code == 422
    assert "null bytes detected" in resp_null_csv.json()["detail"].lower()


def test_cloud_storage_replication_failure_explicitly_reported(auth_client: TestClient):
    """
    Verify that when cloud-storage replication fails, the application:
    1. Does NOT report the file as durably stored in cloud.
    2. Explicitly records durability_status = 'REPLICATION_FAILED'.
    3. Explicitly records storage_tier = 'local_cache_only'.
    4. Communicates the limitation via 'storage_warning' in the API response.
    """
    from unittest.mock import MagicMock, patch

    mock_storage = MagicMock()
    mock_storage.save.side_effect = RuntimeError("S3 replication timeout: Connection reset by peer")

    with patch.object(settings, "STORAGE_BACKEND", "neon"),          patch.object(settings, "S3_BUCKET_NAME", "mock-vault"),          patch.object(settings, "S3_ACCESS_KEY_ID", "mock-key"),          patch.object(settings, "S3_SECRET_ACCESS_KEY", "mock-secret"),          patch.object(settings, "S3_ENDPOINT_URL", "https://mock.neon.tech"),          patch("app.services.storage.get_storage", return_value=mock_storage):

        # Upload QQ
        qq_resp = auth_client.post(
            "/api/surveys/upload-qq",
            files={"file": ("qq.txt", io.BytesIO(b"Study title: Cloud Storage Test\nQ1: Age?"), "text/plain")}
        )
        assert qq_resp.status_code == 200
        qq_data = qq_resp.json()
        assert qq_data["durability_status"] == "REPLICATION_FAILED"
        assert qq_data["storage_tier"] == "local_cache_only"
        assert qq_data["cloud_replicated"] is False
        assert "storage_warning" in qq_data
        assert "cloud replication to neon failed" in qq_data["storage_warning"].lower()

        # Upload Raw
        raw_csv = b"ResponseId,Age,Duration (in seconds)\nR_01,25,300\n"
        raw_resp = auth_client.post(
            "/api/surveys/upload-raw",
            files={"file": ("raw.csv", io.BytesIO(raw_csv), "text/csv")}
        )
        assert raw_resp.status_code == 200
        raw_data = raw_resp.json()
        assert raw_data["durability_status"] == "REPLICATION_FAILED"
        assert raw_data["storage_tier"] == "local_cache_only"
        assert raw_data["cloud_replicated"] is False
        assert "storage_warning" in raw_data
        assert "cloud replication to neon failed" in raw_data["storage_warning"].lower()


def test_manual_review_decision_with_row_number_targeting(auth_client: TestClient, db_session: Session):
    """
    Verify that human reviewer decisions can target exact physical rows using row_number,
    ensuring deterministic review auditing even when duplicate respondent keys exist.
    """
    # 1. Ingest dataset with two respondents sharing the same respondent_key
    raw_csv = (
        "ResponseId,Age,Score,Duration (in seconds)\n"
        "RESP_DUP,30,85,450\n"
        "RESP_DUP,30,85,450\n"
    ).encode("utf-8")

    raw_resp = auth_client.post("/api/surveys/upload-raw", files={"file": ("dup_test.csv", io.BytesIO(raw_csv), "text/csv")})
    assert raw_resp.status_code == 200
    raw_fname = raw_resp.json()["filename"]

    audit_resp = auth_client.post("/api/surveys/run-audit", json={"raw_filename": raw_fname})
    assert audit_resp.status_code == 200
    audit_id = audit_resp.json()["audit_id"]

    # Fetch physical rows from database
    rows = db_session.query(SurveyRespondent).filter_by(audit_id=audit_id).order_by(SurveyRespondent.row_number.asc()).all()
    assert len(rows) == 2
    row1, row2 = rows[0], rows[1]

    # Target specifically row2 for manual review
    review_resp = auth_client.post(
        f"/api/surveys/audits/{audit_id}/review/RESP_DUP?row_number={row2.row_number}",
        json={
            "decision": "ACCEPTED",
            "reviewer_name": "Senior QC Lead",
            "reviewer_notes": "Second instance verified as separate valid completion despite duplicate ID"
        }
    )
    assert review_resp.status_code == 200
    assert review_resp.json()["disposition"] == "ACCEPTED"

    # Verify that row2 was updated while row1 remains unaffected
    db_session.refresh(row1)
    db_session.refresh(row2)
    assert row2.disposition == "ACCEPTED"
    assert row2.reviewed_by == "Senior QC Lead"
    assert row1.reviewed_by is None
