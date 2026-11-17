"""Exercise the survey API using unrelated synthetic projects and flexible inputs."""

import csv
from io import BytesIO, StringIO

import pytest
from openpyxl import load_workbook


BASE = "/api/surveys"


def _upload(client, questionnaire, csv_text):
    qq = client.post(f"{BASE}/upload-qq", files={"file": ("survey.txt", questionnaire.encode())})
    raw = client.post(f"{BASE}/upload-raw", files={"file": ("answers.csv", csv_text.encode())})
    assert qq.status_code == 200, qq.text
    assert raw.status_code == 200, raw.text
    return qq.json(), raw.json()


def test_single_short_questionnaire_is_readable(auth_client):
    qq, raw = _upload(auth_client, "Q1: Was it useful?", "ResponseId,Q1\nA,Yes\n")
    assert not qq["unreadable_or_scanned"]
    assert [q["question_number"] for q in qq["questions"]] == ["Q1"]
    assert raw["total_rows_detected"] == 1


@pytest.mark.parametrize(("topic", "prefix", "question_count", "row_count", "custom_headers"), [
    ("Library services", "Q", 25, 8, False),
    ("Field inspections", "FD", 62, 13, True),
    ("Training programs", "Q.Program", 100, 34, False),
    ("Freight deliveries", "LX", 151, 53, True),
])
def test_survey_workflow_adapts_to_inputs(auth_client, topic, prefix, question_count, row_count, custom_headers):
    question_ids = [f"{prefix}{i}" for i in range(1, question_count + 1)]
    age, matrix, text, control, follow_up, multi, rating = question_ids[:7]
    question_ids[7] = "Q.Tracking"
    questionnaire = "\n".join([
        topic,
        "Target duration: 5 to 9 minutes.",
        "Q.ImportContext",
        "Program embedded variable for cohort assignment.",
        "Q.Introduction",
        "Display text",
        "Thank you for taking part.",
        f"{age}: What is your age? [Screener] [Terminate if under 21 for Canada respondents only.]",
        f"{matrix}: How would you rate these aspects? [Matrix] [Question timer: 18 seconds]",
        "1. Poor", "2. Fair", "3. Average", "4. Good", "5. Excellent",
        "Statement 1: Staff", "Statement 2: Clarity", "Statement 3: Speed", "Statement 4: Access",
        f"{text}: In your own words, what happened during your visit? [Open End]",
        f"{control}: Would you answer a follow-up? [Single Choice] If No, skip to {multi}.",
        "1. Yes", "2. No",
        f"{follow_up}: Which location would you visit next? [Single Choice]",
        "1. East", "2. West",
        f"{multi}: Which arrangements work for you? [Multiple Choice]",
        "1. Pick up", "2. Delivery", "3. Other (please specify)",
        f"{rating}: How likely are you to recommend the service? [Rating Scale]",
        "Q.Tracking: Did you track your appointment? [Single Choice]",
        *(f"{qid}: Would you use reminder option {number}? [Single Choice]"
          for number, qid in enumerate(question_ids[8:-1], 1)),
        f"{question_ids[-1]}: What else should we know? [Open End]",
    ])

    id_col, duration_col = ("case reference", "elapsed seconds") if custom_headers else ("ResponseId", "Duration")
    timer_col = f"QuestionTimer_{matrix}"
    country_col = "market region"
    columns = {qid: [qid] for qid in question_ids[:-1]}
    columns[matrix] = [f"{matrix}_{i}" for i in range(1, 5)]
    columns[multi] = [f"{multi}_pickup", f"{multi}_delivery"]
    if custom_headers:
        columns[age] = ["reported years"]
        columns[text] = ["interview narrative"]
        columns[matrix] = ["staff rating", "clarity rating", "speed rating", "access rating"]
    headers = [id_col, duration_col, timer_col, country_col, *(c for group in columns.values() for c in group)]
    output = StringIO(newline="")
    writer = csv.writer(output)
    if custom_headers:
        writer.writerow(["Synthetic project export"])
    writer.writerow(headers)
    for i in range(row_count):
        scores = ["1", "Average", "4. Good", "5"]
        if i == 3:
            scores = ["3"] * 4
        if i == 6:
            scores[0] = "10"
        answers = {
            age: ["17" if i in (1, 2) else "35"], matrix: scores,
            text: ["asdfgh" if i == 3 else f"The service made it easier to complete visit {i}."],
            control: ["No" if i in (4, 5) else "Yes"],
            follow_up: ["" if i in (4, 6) else "East"], multi: ["1", "0"], rating: ["5"],
        }
        writer.writerow([
            f"case-{0 if i == 7 else i}", 45 if i == 2 else 420, 5 if i == 4 else 33,
            "United States" if i == 2 else "Canada",
            *(value for qid in columns for value in answers.get(qid, ["Yes"])),
        ])

    qq, raw = _upload(auth_client, questionnaire, output.getvalue())
    detected = {q["question_number"]: q for q in qq["questions"]}
    assert set(detected) == set(question_ids)
    assert qq["total_questions_detected"] == qq["questions_count"] == question_count
    assert {q["question_number"] for q in qq["excluded_blocks"]} == {"Q.ImportContext", "Q.Introduction"}
    assert all(q["source_line"] and q["source_text"] for q in detected.values())
    assert [detected[qid]["question_type"] for qid in (age, matrix, text, multi, rating)] == [
        "screener", "matrix_grid", "open_ended", "multiple_choice", "rating_scale",
    ]
    assert detected[matrix]["matrix_items"] == ["Staff", "Clarity", "Speed", "Access"]
    assert qq["page_timer_rules"] == {matrix: 18}
    assert detected[age]["age_countries"] == ["Canada"]
    assert (qq["detected_duration_min"], qq["detected_duration_max"]) == (5, 9)
    assert detected[control]["routing_logic"].endswith(f"skip to {multi}.")
    assert raw["columns"] == headers
    assert raw["total_rows_detected"] == row_count

    selection = {
        "qq_artifact_name": qq["artifact_name"], "raw_artifact_name": raw["artifact_name"],
        "header_row_index": int(custom_headers),
    }
    response = auth_client.post(f"{BASE}/suggest-mapping", json=selection)
    assert response.status_code == 200, response.text
    suggestions = {m["question_number"]: m for m in response.json()["mappings"]}
    assert set(suggestions) == set(question_ids)
    assert suggestions[question_ids[-1]]["mapping_status"] == "UNMAPPED"
    assert suggestions[multi]["mapped_columns"] == sorted(columns[multi])
    if custom_headers:
        assert not suggestions[matrix]["mapped_columns"]
    else:
        assert suggestions[matrix]["mapped_columns"] == columns[matrix]

    response = auth_client.post(f"{BASE}/run-audit", json={
        **selection,
        "confirmed_mappings": [
            {"question_number": qid, "mapped_columns": mapped, "mapping_status": "CONFIRMED"}
            for qid, mapped in columns.items()
        ],
        "system_columns": {"respondent_id": id_col, "duration": duration_col},
        "country_column": country_col,
        "timing_rules": {
            "extreme_speeding_threshold_seconds": 60,
            "required_questions": [follow_up],
            "routing_rules": [{"control_question": control, "equals": "No", "skip_question": follow_up}],
        },
    })
    assert response.status_code == 200, response.text
    audit = response.json()
    assert audit["total_respondents"] == row_count
    assert (audit["accepted_count"], audit["rejected_count"], audit["review_required_count"]) == (row_count - 7, 2, 5)
    run = f"{BASE}/audits/{audit['id']}"
    rows = auth_client.get(f"{run}/respondents?paginated=true&page_size={row_count}").json()
    assert len(rows["items"]) == rows["total"] == row_count
    assert [r["row_number"] for r in rows["items"]] == list(range(2 + custom_headers, row_count + 2 + custom_headers))
    findings = auth_client.get(f"{run}/findings").json()
    assert {(f["respondent_key"], f["issue_type"]) for f in findings} == {
        ("case-1", "SCREENER_AGE_VIOLATION"), ("case-2", "EXTREME_SPEEDING"),
        ("case-3", "STRAIGHTLINING"), ("case-3", "GIBBERISH"),
        ("case-4", "PAGE_TIMER_SPEEDING"), ("case-5", "ROUTING_VIOLATION"),
        ("case-6", "INCOMPLETE_RESPONSE"), ("case-6", "MATRIX_INVALID_OPTION"),
        ("case-0", "CONFIRMED_DUPLICATE_ID"),
    }
    details = auth_client.get(run)
    assert details.status_code == 200, details.text
    coverage = {item["check_name"]: item["status"] for item in details.json()["qc_coverage"]}
    assert coverage["Matrix Response Options"] == "FLAGGED"
    assert coverage["Open-Ended Topic Relevance"] == coverage["Contradictory Responses"] == "NOT_CHECKED"
    export = auth_client.get(f"{run}/export/excel")
    assert export.status_code == 200
    workbook = load_workbook(BytesIO(export.content), read_only=True)
    assert workbook["Raw Data"].max_row == row_count + 1
    assert workbook["QQ Mapping"].max_row == question_count + 1
    assert [workbook[name].max_row - 1 for name in ("Accepted Data", "Rejected Data", "Review Required")] == [
        row_count - 7, 2, 5,
    ]
    workbook.close()


def test_explicit_codes_take_precedence_and_unmapped_checks_are_reported(auth_client):
    qq, raw = _upload(auth_client, """
Q1: Would you use the service again?
Q1_2: Is the pickup location convenient?
Q2: How would you rate the service? [Matrix]
1. Poor
2. Good
Row 1: Speed
Row 2: Reliability
Q3: In your own words, how could the service improve? [Open End]
""", "ResponseId,Q1,Q1_2\nCASE1,Yes,No\n")
    selection = {"qq_artifact_name": qq["artifact_name"], "raw_artifact_name": raw["artifact_name"]}
    response = auth_client.post(f"{BASE}/suggest-mapping", json=selection)
    assert response.status_code == 200, response.text
    mappings = {m["question_number"]: m["mapped_columns"] for m in response.json()["mappings"]}
    assert mappings == {"Q1": ["Q1"], "Q1_2": ["Q1_2"], "Q2": [], "Q3": []}
    response = auth_client.post(f"{BASE}/run-audit", json={
        **selection, "confirmed_mappings": [
            {"question_number": qid, "mapped_columns": cols, "mapping_status": "CONFIRMED" if cols else "SKIPPED"}
            for qid, cols in mappings.items()
        ],
    })
    assert response.status_code == 200, response.text
    audit = response.json()
    assert audit["total_respondents"] == audit["accepted_count"] == 1
    details = auth_client.get(f"{BASE}/audits/{audit['id']}")
    assert details.status_code == 200, details.text
    coverage = {item["check_name"]: item["status"] for item in details.json()["qc_coverage"]}
    for check in ("Overall Survey Speeding", "Straightlining Detection", "Matrix Response Options",
                  "Open-Ended Quality & Gibberish", "Survey Routing & Skip Logic", "Completeness & Missing Answers"):
        assert coverage[check] == "NOT_CHECKED"
