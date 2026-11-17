"""
End-to-End Browser Workflow Verification for BHTT Questionnaire Intake,
Column Mapping, and QC Rules Interface on the isolated loopback server.

Tests:
1. HTTP 503 health check reproduction and UI badge reaction.
2. Authenticated access to Survey Data Scrubbing module.
3. Upload of actual BHTT Word questionnaire (2026.10.29_INV BHTT_W3B(1).docx).
4. Verification of the current 59-candidate inventory (0 forbidden tokens).
5. Verification of Step 1 summary pills (filename, duration 7-10m, 0 page timers, screeners, routing).
6. Synthetic dataset intake and preview (Step 2).
7. Question-to-column mapping table verification (Step 3).
8. QC Rules configuration, threshold source indicators, and default page timer disabling (Step 4).
9. Capture of high-resolution verification screenshots for artifacts report.
"""

import os
from hashlib import sha256
from pathlib import Path
import pytest
sync_playwright = pytest.importorskip("playwright.sync_api").sync_playwright

from app.services.auth import create_admin_session_token, SESSION_COOKIE_NAME
from tests.test_survey_bhtt_regression import BHTT_CHARACTERIZATION_SHA256

BHTT_DOCX = Path(os.environ["BHTT_QQ_PATH"]) if os.environ.get("BHTT_QQ_PATH") else None
SYNTHETIC_CSV = Path(__file__).resolve().parent / "fixtures" / "synthetic_bhtt_respondents.csv"


@pytest.fixture
def local_app_server(live_test_server):
    return live_test_server


def test_e2e_bhtt_questionnaire_and_qc_rules_workflow(local_app_server, tmp_path):
    if BHTT_DOCX is None or not BHTT_DOCX.exists():
        pytest.skip(f"BHTT questionnaire missing at {BHTT_DOCX}")
    assert sha256(BHTT_DOCX.read_bytes()).hexdigest() == BHTT_CHARACTERIZATION_SHA256
    assert SYNTHETIC_CSV.exists(), f"Synthetic raw CSV missing at {SYNTHETIC_CSV}"
    ARTIFACT_DIR = tmp_path

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 960})

        # Inject cryptographically valid admin session cookie
        admin_token = create_admin_session_token()
        context.add_cookies([{
            "name": SESSION_COOKIE_NAME,
            "value": admin_token,
            "domain": "127.0.0.1",
            "path": "/"
        }])

        page = context.new_page()

        # ---------------------------------------------------------------------
        # 1. API Health Check & 503 Reproduction
        # ---------------------------------------------------------------------
        # Route intercept to simulate Render 503 Service Unavailable / cold-start
        def handle_503(route):
            route.fulfill(
                status=503,
                content_type="text/plain",
                body="Service Unavailable (Render container cold-start warming up)"
            )

        page.route("**/api/health", handle_503)
        page.goto(local_app_server, wait_until="networkidle")
        page.evaluate("App.checkHealth()")
        page.wait_for_timeout(300)

        badge_503 = page.locator("#apiStatusBadge")
        assert "degraded" in badge_503.get_attribute("class")
        badge_text_503 = badge_503.locator(".status-text").inner_text()
        assert "503" in badge_text_503 or "Warming" in badge_text_503

        page.screenshot(path=str(ARTIFACT_DIR / "health_check_503_reproduction.png"))

        # Unroute and verify normal 200 health check
        page.unroute("**/api/health", handle_503)
        page.evaluate("App.checkHealth()")
        page.wait_for_timeout(300)

        badge_200 = page.locator("#apiStatusBadge")
        assert "connected" in badge_200.get_attribute("class")
        assert "API Online" in badge_200.locator(".status-text").inner_text()

        page.screenshot(path=str(ARTIFACT_DIR / "health_check_200_online.png"))

        # ---------------------------------------------------------------------
        # 2. Navigate to Survey Data Scrubbing Module
        # ---------------------------------------------------------------------
        page.evaluate("App.navigateTo('surveys')")
        page.wait_for_timeout(500)
        assert page.locator("#view-surveys").is_visible()

        # Step 1 Dropzone is active
        assert page.locator("#surveyStep1").is_visible()

        # ---------------------------------------------------------------------
        # 3. Upload BHTT Questionnaire (.docx)
        # ---------------------------------------------------------------------
        with page.expect_response("**/api/surveys/upload-qq") as qq_resp_info:
            page.set_input_files("#surveyQqFileInput", str(BHTT_DOCX))
        qq_resp = qq_resp_info.value
        assert qq_resp.status == 200
        qq_data = qq_resp.json()

        # Verify parsed payload
        assert qq_data["total_questions_detected"] == 59
        assert qq_data["detected_duration_min"] == 7.0
        assert qq_data["detected_duration_max"] == 10.0
        assert qq_data["page_timer_threshold_seconds"] is None

        # Verify forbidden codes are absent
        q_codes = [q["question_number"] for q in qq_data["questions"]]
        for forbidden in ["QUAL2585", "DYNATA", "QUOTA", "ST. JOHN", "DETRACTORS"]:
            assert not any(forbidden in c.upper() for c in q_codes)

        # Wait for Step 1 Summary Box to be rendered
        summary_box = page.locator("#surveyQqSummaryBox")
        page.wait_for_selector("#surveyQqSummaryBox:not(.d-none)", timeout=5000)

        file_name_text = page.locator("#surveyQqFileName").inner_text()
        assert BHTT_DOCX.name in file_name_text or "2026.10.29_INV BHTT_W3B(1).docx" in file_name_text

        pills_text = page.locator("#surveyQqPills").inner_text()
        assert f"Candidate questions: {len(qq_data['questions'])}" in pills_text
        assert "Page Timers: 0 (No timer rule in QQ)" in pills_text
        assert f"Screeners: {len(qq_data['screening_criteria'])}" in pills_text
        assert f"Routing notes: {len(qq_data['routing_logic'])}" in pills_text

        # Capture Step 1 Screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "bhtt_step1_qq_parsed.png"))

        # ---------------------------------------------------------------------
        # 4. Advance to Step 2 and Upload Synthetic Raw Dataset
        # ---------------------------------------------------------------------
        page.click("#surveyStep1NextBtn")
        page.wait_for_selector("#surveyStep2:not(.d-none)", timeout=3000)

        with page.expect_response("**/api/surveys/upload-raw") as raw_resp_info:
            page.set_input_files("#surveyRawFileInput", str(SYNTHETIC_CSV))
        raw_resp = raw_resp_info.value
        assert raw_resp.status == 200
        raw_data = raw_resp.json()
        assert raw_data["total_rows_detected"] == 5

        page.wait_for_selector("#surveyDataPreviewContainer:not(.d-none)", timeout=5000)
        assert page.locator("#surveyPreviewBody tr").count() == 5

        # Capture Step 2 Screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "bhtt_step2_raw_intake.png"))

        # ---------------------------------------------------------------------
        # 5. Advance to Step 3: Column Mapping Review
        # ---------------------------------------------------------------------
        page.click("#surveyStep2NextBtn")
        page.wait_for_selector("#surveyStep3:not(.d-none)", timeout=3000)

        # Wait for mapping suggestions to load into the table
        page.wait_for_selector("#surveyMappingTableBody tr:nth-child(5)", timeout=10000)
        mapping_rows = page.locator("#surveyMappingTableBody tr")
        assert mapping_rows.count() == len(qq_data["questions"])

        # Verify confidence counters
        high_badge = page.locator("#surveyMapHighCount").inner_text()
        unmapped_badge = page.locator("#surveyMapUnmappedCount").inner_text()
        assert "High:" in high_badge
        assert "Unmapped:" in unmapped_badge

        # Verify evidence column is non-empty
        first_evidence = page.locator("#surveyMappingTableBody tr:nth-child(1) td:nth-child(5)").inner_text()
        assert len(first_evidence) > 5

        # Verify metadata exclusion in mapping table (instruction / metadata blocks)
        metadata_unmapped_rows = page.locator("#surveyMappingTableBody tr:has-text('Non-question content')")
        assert metadata_unmapped_rows.count() == 0
        assert len(qq_data["excluded_blocks"]) == 7

        # Capture Step 3 Screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "bhtt_step3_mapping_table.png"))

        # ---------------------------------------------------------------------
        # 6. Advance to Step 4: QC Rules & Thresholds
        # ---------------------------------------------------------------------
        page.click("#surveyStep3NextBtn")
        page.wait_for_selector("#surveyStep4:not(.d-none)", timeout=3000)

        # 6a. Verify Target Expected Duration inputs & badge
        min_dur = page.locator("#ruleMinExpectedDuration").input_value()
        max_dur = page.locator("#ruleMaxExpectedDuration").input_value()
        dur_badge = page.locator("#ruleDurationSourceBadge").inner_text()
        assert min_dur == "7"
        assert max_dur == "10"
        assert "Questionnaire Specification" in dur_badge

        # 6b. Verify Extreme Speeding Cutoff & Checkbox default
        extreme_val = page.locator("#ruleExtremeSpeeding").input_value()
        assert extreme_val == ""
        reject_extreme_cb = page.locator("#ruleRejectExtremeSpeeding")
        assert reject_extreme_cb.is_checked() is False

        # 6c. Verify Page Timer Default Disabled State
        page_timer_cb = page.locator("#rulePageTimerEnabled")
        page_timer_inp = page.locator("#rulePageTimer")
        page_timer_badge = page.locator("#rulePageTimerSourceBadge").inner_text()
        page_timer_help = page.locator("#rulePageTimerHelp").inner_text()

        assert page_timer_cb.is_checked() is False
        assert page_timer_inp.is_disabled() is True
        assert page_timer_inp.input_value() == "20"
        assert "Not in QQ" in page_timer_badge or "Telemetry Only" in page_timer_badge
        assert "No page timer rule in QQ" in page_timer_help

        # Capture Step 4 Default Configuration Screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "bhtt_step4_qc_rules_default.png"))

        # ---------------------------------------------------------------------
        # 7. Test Dynamic Manual Overrides on Step 4
        # ---------------------------------------------------------------------
        # Adjust min duration
        page.fill("#ruleMinExpectedDuration", "8")
        page.wait_for_timeout(200)
        dur_badge_override = page.locator("#ruleDurationSourceBadge").inner_text()
        assert "Manual Override" in dur_badge_override

        # Enable page timer rule
        page_timer_cb.check()
        page.wait_for_timeout(200)
        assert page_timer_inp.is_disabled() is False
        page_timer_badge_override = page.locator("#rulePageTimerSourceBadge").inner_text()
        assert "Manual Override" in page_timer_badge_override

        # Capture Step 4 Manual Override Screenshot
        page.screenshot(path=str(ARTIFACT_DIR / "bhtt_step4_manual_override.png"))

        print("\nAll Playwright UI validations and screenshots completed successfully!")


def test_browser_qc_review_reload_and_export(live_test_server):
    from base64 import b64encode
    from io import BytesIO
    from openpyxl import Workbook, load_workbook
    from tests.test_survey_workflow_contract import QQ, CSV
    from app.config import settings

    dataset = Workbook()
    dataset.active.title = "Unselected"
    sheet = dataset.create_sheet("Respondents")
    sheet.append(["Test preamble"])
    import csv
    respondent_payload = '<img src=x onerror=window.edvexaToastPayload=1>'
    filename = '<img src=x onerror=window.edvexaToastPayload=1>.xlsx'
    for index, row in enumerate(csv.reader(CSV.replace('DUP', respondent_payload).splitlines())):
        sheet.append([*row, "exported region" if index == 0 else "Canada"])
    data = BytesIO()
    dataset.save(data)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        page.goto(live_test_server)
        page.evaluate("App.navigateTo('surveys')")
        page.locator('#surveyAdminPasswordInput').fill(settings.ADMIN_LOGIN_PASSWORD)
        with page.expect_response('**/api/auth/login') as login:
            page.locator('#surveyAdminPasswordInput').press('Enter')
        assert login.value.ok
        questionnaire = QQ.replace("[Terminate if under 18]", "[Terminate if under 18 in Canada only]")
        page.set_input_files("#surveyQqFileInput", {"name": "qq.txt", "mimeType": "text/plain", "buffer": questionnaire.encode()})
        page.click("#surveyStep1NextBtn")
        # Construct a browser File directly so the host OS cannot sanitize the payload filename.
        with page.expect_response("**/api/surveys/upload-raw") as upload:
            page.evaluate("""({name, data}) => {
                const input = document.getElementById('surveyRawFileInput');
                const files = new DataTransfer();
                const bytes = Uint8Array.from(atob(data), ch => ch.charCodeAt(0));
                files.items.add(new File([bytes], name));
                input.files = files.files;
                input.dispatchEvent(new Event('change', {bubbles: true}));
            }""", {"name": filename, "data": b64encode(data.getvalue()).decode()})
        assert upload.value.ok and upload.value.json()["filename"] == filename
        page.locator('#toastContainer').get_by_text(f'Uploading respondent data: {filename}...', exact=True).wait_for(timeout=3000)
        assert page.locator('#toastContainer img').count() == 0
        with page.expect_response("**/api/surveys/preview") as selected:
            page.select_option("#surveySheetSelect", "Respondents")
        assert selected.value.ok
        with page.expect_response("**/api/surveys/preview") as header:
            assert page.locator("#surveyHeaderRow").input_value() == "2"
            page.get_by_role("button", name="Use this header row").click()
        assert header.value.ok
        page.click("#surveyStep2NextBtn")
        page.wait_for_selector("#surveyMappingTableBody select[multiple]")
        page.locator("#surveyMappingTableBody select[multiple]").select_option(["Q2_1", "Q2_2", "Q2_3", "Q2_4"])
        assert page.locator("[data-confirm-index]:checked").count() == 0
        page.get_by_role("button", name="Confirm all mapped questions").click()
        assert page.locator("[data-confirm-index]:checked").count() == 3
        page.select_option("#surveyCountryColumn", "exported region")
        page.click("#surveyStep3NextBtn")
        page.locator("#ruleStraightlineThreshold").fill("80")
        with page.expect_response("**/api/surveys/run-audit") as run:
            page.click("#surveyExecuteAuditBtn")
        result = run.value.json()
        assert run.value.ok, result
        assert (result["accepted_count"], result["rejected_count"], result["review_required_count"]) == (1, 1, 1)
        for tab, source_row in (("Accepted", "Row 5"), ("Review", "Row 3"), ("Rejected", "Row 4")):
            page.locator(f"#surveyTab{tab}").click()
            page.locator("#surveyRespondentsTableBody tr").get_by_text(source_row, exact=True).wait_for()
        rejected = page.locator("#surveyRespondentsTableBody tr").filter(has_text="Row 4")
        rejected.get_by_role("button", name="Review").click()
        page.locator("#surveyReviewFindingsList").get_by_text("SCREENER_AGE_VIOLATION", exact=False).wait_for()
        page.select_option("#surveyReviewDecisionSelect", "ACCEPTED")
        page.fill("#surveyReviewNotes", "Verified the respondent's eligibility offline.")
        with page.expect_response("**/review-by-id/*") as review:
            page.click("#saveSurveyReviewBtn")
        assert review.value.ok
        page.get_by_text(f'Updated respondent {respondent_payload} to ACCEPTED. Invariant reconciled.', exact=True).wait_for()
        assert page.locator('#toastContainer img').count() == 0
        assert page.evaluate('Boolean(window.edvexaToastPayload)') is False
        page.reload()
        page.evaluate("App.navigateTo('surveys')")
        page.evaluate("(id) => Surveys.loadAuditDetails(id)", result["id"])
        with page.expect_download() as report:
            page.click("#surveyExportCurrentBtn")
        workbook = load_workbook(BytesIO(report.value.path().read_bytes()))
        assert workbook["Accepted Data"].max_row == 3
        assert workbook["Review Required"].max_row == 2
        assert workbook["Review History"].max_row == 2
        assert list(workbook["Intake"].values)[1:] == [(1, "preamble"), (2, "header")]
        browser.close()
