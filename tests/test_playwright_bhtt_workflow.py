"""
End-to-End Browser Workflow Verification for BHTT Questionnaire Intake,
Column Mapping, and QC Rules Interface on Local Server (Port 8000).

Tests:
1. HTTP 503 health check reproduction and UI badge reaction.
2. Authenticated access to Survey Data Scrubbing module.
3. Upload of actual BHTT Word questionnaire (2026.10.29_INV BHTT_W3B(1).docx).
4. Verification of 62 extracted questions (0 forbidden tokens).
5. Verification of Step 1 summary pills (filename, duration 7-10m, 0 page timers, screeners, routing).
6. Synthetic dataset intake and preview (Step 2).
7. Question-to-column mapping table verification (Step 3).
8. QC Rules configuration, threshold source indicators, and default page timer disabling (Step 4).
9. Capture of high-resolution verification screenshots for artifacts report.
"""

import os
import time
import threading
from pathlib import Path
import pytest
import uvicorn
from playwright.sync_api import sync_playwright

from app.main import app
from app.services.auth import create_admin_session_token, SESSION_COOKIE_NAME

ARTIFACT_DIR = Path(os.getenv("ARTIFACT_DIR", r"C:\Users\rjha1\.gemini\antigravity\brain\e10dc557-49d6-48d4-a0cb-fe1231e7a287"))
BHTT_DOCX = Path(r"C:\Users\rjha1\Downloads\2026.10.29_INV BHTT_W3B(1).docx")
if not BHTT_DOCX.exists():
    BHTT_DOCX = Path(r"C:\Users\rjha1\Downloads\2026.10.29_INV%20BHTT_W3B%281%29.docx")
SYNTHETIC_CSV = Path(__file__).resolve().parent / "fixtures" / "synthetic_bhtt_respondents.csv"


class LocalAppServer:
    def __init__(self, host="127.0.0.1", port=8000):
        self.host = host
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        config = uvicorn.Config(app=app, host=self.host, port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        time.sleep(1.5)

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread:
                self.thread.join(timeout=3)


@pytest.fixture(scope="module")
def local_app_server():
    server = LocalAppServer(host="127.0.0.1", port=8000)
    server.start()
    yield f"http://{server.host}:{server.port}"
    server.stop()


def test_e2e_bhtt_questionnaire_and_qc_rules_workflow(local_app_server):
    if not BHTT_DOCX.exists():
        pytest.skip(f"BHTT questionnaire missing at {BHTT_DOCX}")
    assert SYNTHETIC_CSV.exists(), f"Synthetic raw CSV missing at {SYNTHETIC_CSV}"
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)

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
        assert qq_data["total_questions_detected"] == 62
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
        assert "Questions: 62" in pills_text
        assert "Page Timers: 0 (No timer rule in QQ)" in pills_text
        assert f"Screeners: {len(qq_data['screening_criteria'])}" in pills_text
        assert f"Skip Logic: {len(qq_data['routing_logic'])} rules" in pills_text

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
        assert mapping_rows.count() == 62

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
        assert metadata_unmapped_rows.count() >= 1

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
        assert extreme_val == "2"
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
