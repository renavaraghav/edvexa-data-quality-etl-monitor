"""
Targeted Regression Test Suite for BHTT Questionnaire Parsing,
Frontend/Backend Response Contract Aliasing, and 12-Domain Synthetic QC Auditing.

Ensures:
1. Strict question extraction distinguishing genuine survey questions from
   vendor notes (Dynata), quota instructions, NPS Detractor categories, and hospital references.
2. Complete API response contract parity for frontend consumer widgets.
3. QC engine compliance across all 12 survey data quality domains using purely synthetic fixtures.
"""

import io
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.services.survey_qq_parser import parse_questionnaire
from app.services.survey_mapping_service import suggest_mappings
from app.services.survey_qc_engine import run_survey_qc_audit
from app.services.auth import create_admin_session_token, SESSION_COOKIE_NAME


BHTT_DOCX_PATH = Path(r"C:\Users\rjha1\Downloads\2026.10.29_INV%20BHTT_W3B%281%29.docx")
if not BHTT_DOCX_PATH.exists():
    BHTT_DOCX_PATH = Path(r"C:\Users\rjha1\Downloads\2026.10.29_INV BHTT_W3B(1).docx")


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    token = create_admin_session_token()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    return client


def test_bhtt_docx_strict_question_extraction():
    """Verify BHTT docx extracts genuine questions and rejects non-question boilerplate."""
    if not BHTT_DOCX_PATH.exists():
        pytest.skip(f"BHTT test file not found at {BHTT_DOCX_PATH}")

    with open(BHTT_DOCX_PATH, "rb") as f:
        content = f.read()

    res = parse_questionnaire(content, "2026.10.29_INV BHTT_W3B(1).docx")
    assert res.document_format == "docx"
    assert res.unreadable_or_scanned is False
    assert len(res.questions) >= 50

    q_codes = [q.question_number for q in res.questions]

    # 1. Must NOT extract non-question text blocks
    for forbidden in ["QUAL2585", "DYNATA", "QUOTA", "ST. JOHN", "DETRACTORS"]:
        assert not any(forbidden in c.upper() for c in q_codes), (
            f"Forbidden text block '{forbidden}' was incorrectly extracted as question code!"
        )

    # 2. Must extract substantive questions
    assert "Q.Country" in q_codes
    assert "Q.Age" in q_codes
    assert "Q.PastTreatment" in q_codes

    # 3. Must correctly extract expected survey duration (7-10 minutes)
    assert res.expected_duration_min_minutes == 7.0
    assert res.expected_duration_max_minutes == 10.0
    assert res.duration_source == "Questionnaire Specification"

    # 4. Must NOT invent a page timer rule when none exists in the QQ
    assert res.page_timer_threshold_seconds is None


def test_upload_qq_response_contract_aliases(auth_client: TestClient):
    """Verify upload-qq response contract provides all canonical and frontend-consumed field aliases."""
    qq_text = b"Study: Consumer Brand Perception. Target duration: 7 to 10 minutes.\nQ1. What is your age?\nQ2. Which brands do you know?"
    resp = auth_client.post(
        "/api/surveys/upload-qq",
        files={"file": ("perception_qq.txt", io.BytesIO(qq_text), "text/plain")}
    )
    assert resp.status_code == 200
    data = resp.json()

    # Verify dual-aliased field contracts
    assert data["filename"] == "perception_qq.txt"
    assert data["original_name"] == "perception_qq.txt"
    assert data["unreadable_or_scanned"] is False
    assert data["is_scanned"] is False
    assert data["expected_duration_min_minutes"] == 7.0
    assert data["detected_duration_min"] == 7.0
    assert data["expected_duration_max_minutes"] == 10.0
    assert data["detected_duration_max"] == 10.0
    assert "duration_source" in data
    assert "detected_page_timers" in data
    assert "page_timer_rules" in data
    assert "screening_criteria" in data
    assert "screener_rules" in data
    assert "routing_logic" in data
    assert "skip_logic_rules" in data
    assert data["questions_count"] == 2
    assert data["total_questions_detected"] == 2
    assert isinstance(data["notes"], list)
    assert isinstance(data["extraction_notes"], str)


def test_mapping_service_metadata_exclusion_and_dual_fields():
    """Verify mapping service skips metadata blocks and provides matched_column and evidence."""
    mock_questions = [
        {"question_number": "Q.Country", "question_text": "In which country do you reside?", "question_type": "single_choice"},
        {"question_number": "Q.Age", "question_text": "What is your age in years?", "question_type": "numeric"},
        {"question_number": "Q.StartText", "question_text": "Thank you for agreeing to participate.", "question_type": "instruction"},
        {"question_number": "Q.WAVE_MonthYear", "question_text": "Embedded field tracking survey month and year", "question_type": "metadata"},
    ]
    raw_columns = ["ResponseId", "StartDate", "EndDate", "Duration (in seconds)", "IPAddress", "Q.Country", "Age", "ExtraCol"]

    map_res = suggest_mappings(mock_questions, raw_columns)
    mappings = {m["question_number"]: m for m in map_res["mappings"]}

    # Q.Country -> High confidence, matched_column and mapped_columns populated
    assert mappings["Q.Country"]["confidence"] == "HIGH"
    assert mappings["Q.Country"]["matched_column"] == "Q.Country"
    assert mappings["Q.Country"]["mapped_columns"] == ["Q.Country"]
    assert "evidence" in mappings["Q.Country"]
    assert "mapping_evidence" in mappings["Q.Country"]

    # Q.Age -> High confidence match to 'Age'
    assert mappings["Q.Age"]["confidence"] == "HIGH"
    assert mappings["Q.Age"]["matched_column"] == "Age"

    # Metadata & Instructions -> UNMAPPED, matched_column None
    assert mappings["Q.StartText"]["confidence"] == "UNMAPPED"
    assert mappings["Q.StartText"]["matched_column"] is None
    assert "excluded from audit column mapping" in mappings["Q.StartText"]["evidence"].lower()

    assert mappings["Q.WAVE_MonthYear"]["confidence"] == "UNMAPPED"
    assert mappings["Q.WAVE_MonthYear"]["matched_column"] is None


def test_synthetic_12_domain_qc_audit_engine():
    """
    Execute deterministic QC audit across all 12 domains using 100% synthetic respondents.
    Strictly asserts invariant: Total == Accepted + Rejected + Review Required.
    """
    synthetic_records = [
        # 1. Clean valid complete (Duration 540s = 9 min, normal text, valid responses) -> ACCEPTED
        {
            "ResponseId": "SYNTH_01", "StartDate": "2026-10-29 10:00:00", "EndDate": "2026-10-29 10:09:00",
            "Duration": "540", "IP": "198.51.100.1", "Age": "32", "Q1": "1", "Q2_1": "4", "Q2_2": "5", "Q2_3": "4", "Q2_4": "5",
            "OE_Text": "Clear aligners offer great discretion and comfort during daily work meetings."
        },
        # 2. Extreme Speeder (65 seconds < 120s cutoff) -> REVIEW_REQUIRED (by default)
        {
            "ResponseId": "SYNTH_02", "StartDate": "2026-10-29 10:15:00", "EndDate": "2026-10-29 10:16:05",
            "Duration": "65", "IP": "198.51.100.2", "Age": "29", "Q1": "1", "Q2_1": "4", "Q2_2": "3", "Q2_3": "5", "Q2_4": "4",
            "OE_Text": "Faster treatment time compared to traditional braces."
        },
        # 3. Straightliner (all 5s on 4-item matrix) -> REVIEW_REQUIRED (default review signal)
        {
            "ResponseId": "SYNTH_03", "StartDate": "2026-10-29 10:20:00", "EndDate": "2026-10-29 10:28:30",
            "Duration": "510", "IP": "198.51.100.3", "Age": "45", "Q1": "1", "Q2_1": "5", "Q2_2": "5", "Q2_3": "5", "Q2_4": "5",
            "OE_Text": "Consistent appointments and high quality care."
        },
        # 4. Keyboard walk / gibberish text ("asdfghjk") -> REVIEW_REQUIRED
        {
            "ResponseId": "SYNTH_04", "StartDate": "2026-10-29 10:30:00", "EndDate": "2026-10-29 10:39:00",
            "Duration": "540", "IP": "198.51.100.4", "Age": "38", "Q1": "1", "Q2_1": "3", "Q2_2": "4", "Q2_3": "3", "Q2_4": "4",
            "OE_Text": "asdfghjk qwerty zxcvbnm"
        },
        # 5. Meaningful off-topic brand ("Nike") -> REVIEW_REQUIRED
        {
            "ResponseId": "SYNTH_05", "StartDate": "2026-10-29 10:40:00", "EndDate": "2026-10-29 10:48:40",
            "Duration": "520", "IP": "198.51.100.5", "Age": "27", "Q1": "1", "Q2_1": "4", "Q2_2": "4", "Q2_3": "3", "Q2_4": "4",
            "OE_Text": "Nike athletic footwear and training shoes"
        },
        # 6. Valid no-recall answer ("none come to mind") -> ACCEPTED
        {
            "ResponseId": "SYNTH_06", "StartDate": "2026-10-29 10:50:00", "EndDate": "2026-10-29 10:59:10",
            "Duration": "550", "IP": "198.51.100.6", "Age": "41", "Q1": "1", "Q2_1": "4", "Q2_2": "5", "Q2_3": "3", "Q2_4": "4",
            "OE_Text": "None come to mind at this moment."
        },
        # 7. Exact full duplicate of SYNTH_01 -> REJECTED
        {
            "ResponseId": "SYNTH_01", "StartDate": "2026-10-29 10:00:00", "EndDate": "2026-10-29 10:09:00",
            "Duration": "540", "IP": "198.51.100.1", "Age": "32", "Q1": "1", "Q2_1": "4", "Q2_2": "5", "Q2_3": "4", "Q2_4": "5",
            "OE_Text": "Clear aligners offer great discretion and comfort during daily work meetings."
        },
        # 8. Screener termination (Age 15 < 18 threshold) -> REJECTED
        {
            "ResponseId": "SYNTH_08", "StartDate": "2026-10-29 11:00:00", "EndDate": "2026-10-29 11:08:20",
            "Duration": "500", "IP": "198.51.100.8", "Age": "15", "Q1": "1", "Q2_1": "4", "Q2_2": "3", "Q2_3": "4", "Q2_4": "4",
            "OE_Text": "High school orthodontic clinic recommendation."
        },
        # 9. Incomplete response (< 30% answered) -> REJECTED
        {
            "ResponseId": "SYNTH_09", "StartDate": "2026-10-29 11:10:00", "EndDate": "2026-11-07 11:11:00",
            "Duration": "60", "IP": "198.51.100.9", "Age": "30", "Q1": "", "Q2_1": "", "Q2_2": "", "Q2_3": "", "Q2_4": "",
            "OE_Text": ""
        },
        # 10. Timestamp arithmetic mismatch (Delta 400s) -> REVIEW_REQUIRED
        {
            "ResponseId": "SYNTH_10", "StartDate": "2026-10-29 11:20:00", "EndDate": "2026-10-29 11:22:00",
            "Duration": "520", "IP": "198.51.100.10", "Age": "36", "Q1": "1", "Q2_1": "4", "Q2_2": "4", "Q2_3": "4", "Q2_4": "3",
            "OE_Text": "Smooth teeth movement without gum irritation."
        }
    ]

    mock_mappings = [
        {"question_number": "Q.Age", "question_text": "Age", "question_type": "screener", "min_age": 18.0, "mapped_columns": ["Age"]},
        {"question_number": "Q.PastTreatment", "question_text": "Treatment", "question_type": "single_choice", "mapped_columns": ["Q1"]},
        {"question_number": "Q.MatrixRatings", "question_text": "Ratings", "question_type": "matrix_grid", "mapped_columns": ["Q2_1", "Q2_2", "Q2_3", "Q2_4"]},
        {"question_number": "Q.OEBrand", "question_text": "Brands", "question_type": "open_ended", "mapped_columns": ["OE_Text"]},
    ]

    system_cols = {
        "start_time": "StartDate",
        "end_time": "EndDate",
        "duration": "Duration",
        "ip_address": "IP",
        "respondent_id": "ResponseId"
    }

    # BHTT has no page timer threshold, expected duration 7-10m
    timing_rules = {
        "expected_duration_min_minutes": 7.0,
        "expected_duration_max_minutes": 10.0,
        "speeding_threshold_seconds": 300.0,
        "extreme_speeding_threshold_seconds": 120.0,
        "reject_speeding_under_2min": False,
        "duration_source": "Questionnaire Specification (BHTT)"
    }

    summary = run_survey_qc_audit(
        records=synthetic_records,
        mappings=mock_mappings,
        system_columns=system_cols,
        timer_columns=[],
        resp_id_col="ResponseId",
        timing_rules=timing_rules
    )

    # 1. Strict Accounting Invariant
    assert summary.total_respondents == 10
    assert summary.total_respondents == (summary.accepted_count + summary.rejected_count + summary.review_required_count)

    # 2. Extreme speeding (< 2 min) is REVIEW_REQUIRED when reject_speeding_under_2min is False
    synth_02 = next(r for r in summary.results if r.respondent_key == "SYNTH_02")
    assert synth_02.disposition == "REVIEW_REQUIRED"
    assert any("extreme speed flagged as high-priority review signal" in f.lower() for f in synth_02.review_flags)

    # 3. Duplicate and underage screeners are REJECTED
    synth_01_dup = [r for r in summary.results if r.respondent_key == "SYNTH_01"]
    assert any(r.disposition == "REJECTED" for r in synth_01_dup)

    synth_08 = next(r for r in summary.results if r.respondent_key == "SYNTH_08")
    assert synth_08.disposition == "REJECTED"

    synth_09 = next(r for r in summary.results if r.respondent_key == "SYNTH_09")
    assert synth_09.disposition == "REJECTED"

    # 4. Valid completes are ACCEPTED
    synth_06 = next(r for r in summary.results if r.respondent_key == "SYNTH_06")
    assert synth_06.disposition == "ACCEPTED"
