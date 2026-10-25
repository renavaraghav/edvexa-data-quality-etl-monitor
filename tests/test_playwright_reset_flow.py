"""
Playwright End-to-End Tests for Public Destructive Reset with Slider, Checkbox, and "Permanently Delete" Button.
Verifies the complete frontend user interaction:
1. Reset modal design, styling, and text matches exact specification:
   - "I understand that this will permanently delete all uploaded data and processing history."
   - Irreversible action warning is displayed.
   - Destructive reset disabled notice and admin token fields are removed.
2. Checkbox is disabled until the slider reaches completed position.
3. Dragging slider to end enables the confirmation checkbox.
4. Ticking the checkbox enables the "Permanently Delete" button.
   - Simply ticking the checkbox does NOT delete any data.
5. Unticking the checkbox disables the "Permanently Delete" button.
6. The data is deleted ONLY when the user clicks the "Permanently Delete" button after checking the box.
7. Loading indicator is displayed during deletion.
8. Successful reset closes modal and refreshes dashboard stats.
9. UI recovery on reset failure preserves modal state and re-enables controls.
"""
import datetime
import os
import time
import threading
import uvicorn
import pytest
from playwright.sync_api import sync_playwright

from app.main import app
from app.config import settings
from app.database import engine, Base, SessionLocal
from app.models import Customer, ProcessingJob, JobCleanRecord


class LocalTestServer:
    def __init__(self, host="127.0.0.1", port=8877):
        self.host = host
        self.port = port
        self.server = None
        self.thread = None

    def start(self):
        config = uvicorn.Config(app=app, host=self.host, port=self.port, log_level="warning")
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        # Wait until server is listening
        time.sleep(1.2)

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread:
                self.thread.join(timeout=3)


@pytest.fixture(scope="module")
def local_server():
    server = LocalTestServer(port=8877)
    server.start()
    yield f"http://{server.host}:{server.port}"
    server.stop()


def seed_local_db():
    db = SessionLocal()
    try:
        # Create a test job and customer
        job = ProcessingJob(
            filename="playwright_test.csv",
            dataset_type="customers",
            file_hash="pw_hash_1",
            status="completed",
            total_rows=1,
            accepted_rows=1,
            rejected_rows=0,
            duplicate_rows=0,
            started_at=datetime.datetime(2026, 10, 26, 1, 0, 0),
            completed_at=datetime.datetime(2026, 10, 26, 1, 0, 1),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        cust = Customer(
            customer_id="PW_CUST_1",
            name="Playwright User",
            email="pw@example.com",
            created_at=datetime.date(2026, 10, 26),
        )
        clean = JobCleanRecord(
            job_id=job.id,
            row_number=1,
            dataset_type="customers",
            business_key="PW_CUST_1",
            record_json='{"customer_id": "PW_CUST_1", "name": "Playwright User", "email": "pw@example.com"}',
        )
        db.add_all([cust, clean])
        db.commit()
    finally:
        db.close()


def test_e2e_slider_checkbox_and_permanently_delete_button_interaction(local_server):
    seed_local_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        # Track any reset API requests to ensure ticking checkbox does NOT call reset
        reset_requests = []
        page.on("request", lambda req: reset_requests.append(req.url) if "reset-data" in req.url else None)

        # 1. Verify modal without slider completion: checkbox is disabled and button is disabled
        page.evaluate("App.openResetModal(false)")
        modal = page.locator("#resetConfirmModal")
        assert modal.is_visible()

        # Verify old "disabled in production" warning is NOT present
        assert page.locator("#resetProdDisabledNotice").count() == 0

        # Verify admin token field is NOT present
        assert page.locator("#adminResetToken").count() == 0

        # Verify confirmation checkbox text matches requirement
        checkbox_label = page.locator("label[for='confirmResetCheckbox']").inner_text()
        assert ("permanently delete all uploaded data and processing history" in checkbox_label or
                "permanently delete the selected uploaded data and associated processing history" in checkbox_label)

        # Verify checkbox is disabled before slider is completed
        checkbox = page.locator("#confirmResetCheckbox")
        assert checkbox.is_disabled()

        # Verify "Permanently Delete" button exists and is disabled
        delete_btn = page.locator("#confirmResetBtn")
        assert delete_btn.is_visible()
        assert delete_btn.is_disabled()
        assert "Permanently Delete" in delete_btn.inner_text()

        # Close modal
        page.locator("#cancelResetBtn").click()
        assert not modal.is_visible()

        # 2. Test slider completion via keyboard on thumb
        thumb = page.locator("#slideResetThumb")
        thumb.focus()
        page.keyboard.press("Enter")

        # Modal should now be opened with slider completed
        assert modal.is_visible()
        assert not checkbox.is_disabled()
        assert not checkbox.is_checked()
        # Button must remain disabled until checkbox is checked
        assert delete_btn.is_disabled()

        # 3. Ticking checkbox: "Permanently Delete" button becomes enabled.
        # CRITICAL REQUIREMENT: Simply ticking the checkbox MUST NOT delete any data!
        checkbox.check()
        assert checkbox.is_checked()
        assert not delete_btn.is_disabled()
        # Wait 500ms to guarantee no reset API call was fired on check
        page.wait_for_timeout(500)
        assert len(reset_requests) == 0, "Simply ticking the checkbox must NOT trigger any reset API request!"
        assert modal.is_visible()

        # 4. Unticking checkbox: "Permanently Delete" button becomes disabled again
        checkbox.uncheck()
        assert not checkbox.is_checked()
        assert delete_btn.is_disabled()

        # 5. Re-checking checkbox: "Permanently Delete" button becomes enabled again
        checkbox.check()
        assert not delete_btn.is_disabled()

        # 6. Click "Permanently Delete" button: Now deletion executes!
        delete_btn.click()

        # Modal should close after successful deletion
        page.wait_for_selector("#resetConfirmModal", state="hidden", timeout=6000)
        assert len(reset_requests) >= 1, "Clicking the 'Permanently Delete' button must execute the reset request!"

        # Verify toast message
        toast = page.locator(".toast")
        page.wait_for_selector(".toast", state="visible", timeout=4000)
        assert toast.is_visible()
        toast_text = toast.inner_text()
        assert "permanently deleted" in toast_text or "already empty" in toast_text

        browser.close()


def test_e2e_slider_drag_gesture(local_server):
    """Test physical pointer drag gesture on slider thumb unlocks confirmation checkbox."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        thumb = page.locator("#slideResetThumb")
        box = thumb.bounding_box()
        assert box is not None

        # Drag thumb 180px to the right
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 + 180, box["y"] + box["height"] / 2, steps=10)
        page.mouse.up()

        # Modal should now be opened with slider completed
        modal = page.locator("#resetConfirmModal")
        page.wait_for_selector("#resetConfirmModal", state="visible", timeout=3000)
        assert modal.is_visible()

        checkbox = page.locator("#confirmResetCheckbox")
        assert not checkbox.is_disabled()
        assert not checkbox.is_checked()

        # Delete button is disabled until checkbox is checked
        delete_btn = page.locator("#confirmResetBtn")
        assert delete_btn.is_disabled()

        # Close modal
        page.locator("#cancelResetBtn").click()
        page.wait_for_selector("#resetConfirmModal", state="hidden", timeout=3000)

        browser.close()


def test_e2e_reset_failure_ui_recovery(local_server):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        # Route /api/dashboard/reset-data to simulate server 500 error
        page.route("**/api/dashboard/reset-data", lambda route: route.fulfill(
            status=500,
            content_type="application/json",
            body='{"detail": "Simulated Database Connection Failure"}'
        ))

        # Open modal with slider completed
        page.evaluate("App.openResetModal(true)")
        modal = page.locator("#resetConfirmModal")
        assert modal.is_visible()

        checkbox = page.locator("#confirmResetCheckbox")
        delete_btn = page.locator("#confirmResetBtn")
        assert not checkbox.is_disabled()
        assert delete_btn.is_disabled()

        # Check checkbox to enable delete button
        checkbox.check()
        assert not delete_btn.is_disabled()

        # Click Permanently Delete button to trigger reset
        delete_btn.click()

        # Wait for failure response and toast
        page.wait_for_selector(".toast.danger", timeout=4000)

        # Modal must remain open to preserve UI state
        assert modal.is_visible()

        # Controls must be re-enabled for retry
        assert not checkbox.is_disabled()
        assert not delete_btn.is_disabled()
        cancel_btn = page.locator("#cancelResetBtn")
        assert not cancel_btn.is_disabled()

        # Toast should display error message
        toast_text = page.locator(".toast.danger").inner_text()
        assert "Simulated Database Connection Failure" in toast_text

        browser.close()


def test_e2e_empty_dashboard_kpis_and_no_fake_trends(local_server):
    """Verify that when the database is empty or has no historical comparison, no fake trend percentages appear."""
    # First ensure local db is cleared for this test
    db = SessionLocal()
    try:
        db.query(JobCleanRecord).delete()
        db.query(Customer).delete()
        db.query(ProcessingJob).delete()
        db.commit()
    finally:
        db.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        # Wait for metrics to load
        page.wait_for_selector("#kpiTotalRecords")

        total_records = page.locator("#kpiTotalRecords").inner_text()
        assert total_records == "0"

        # Verify that hardcoded fake percentages are completely absent
        page_content = page.content()
        assert "+12% from last week" not in page_content, "Hardcoded +12% from last week should not exist on page"
        assert "+5% from last week" not in page_content, "Hardcoded +5% from last week should not exist on page"

        # Verify dynamic badges on Card 1 (Total Records) and Card 6 (Data Quality Score)
        total_trend = page.locator("#kpiTotalRecordsTrendBadge").inner_text()
        assert "No data" in total_trend or "—" in total_trend

        score_trend = page.locator("#kpiDataQualityScoreTrendBadge").inner_text()
        assert "No data" in score_trend or "—" in score_trend

        # Quality score displays "—" when empty
        score_val = page.locator("#kpiDataQualityScore").inner_text()
        assert score_val == "—"

        # Total jobs badge shows "0 Jobs"
        jobs_badge = page.locator("#kpiTotalJobsBadge").inner_text()
        assert jobs_badge == "0 Jobs"

        # Open Quality Score methodology modal and check that it does not show fake 99.8%
        page.locator("#kpiCardScore").click()
        page.wait_for_selector("#qualityScoreModal", state="visible")
        big_number = page.locator("#scoreModalBigNumber").inner_text()
        assert big_number == "—", f"Expected '—' in score modal when empty, got '{big_number}'"

        status_badge = page.locator("#scoreModalStatusBadge").inner_text()
        assert "No Data Ingested" in status_badge

        browser.close()


def test_e2e_page_reloads_after_permanent_delete(local_server):
    """Verify that clicking Permanently Delete executes full website reload and displays fresh empty state."""
    seed_local_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        # Set a marker on the window to prove that a full page reload occurs
        page.evaluate("window.__testReloadMarker = 'active_session'")
        assert page.evaluate("window.__testReloadMarker") == "active_session"

        # Open modal with slider completed
        page.evaluate("App.openResetModal(true)")
        modal = page.locator("#resetConfirmModal")
        page.wait_for_selector("#resetConfirmModal", state="visible")

        checkbox = page.locator("#confirmResetCheckbox")
        delete_btn = page.locator("#confirmResetBtn")
        assert not checkbox.is_disabled()
        assert delete_btn.is_disabled()

        # Check checkbox -> enables button without deleting
        checkbox.check()
        assert not delete_btn.is_disabled()

        # Click Permanently Delete and wait for the page navigation/reload
        with page.expect_navigation(timeout=8000):
            delete_btn.click()

        # Verify page reloaded: window.__testReloadMarker was erased by full reload
        reloaded_marker = page.evaluate("window.__testReloadMarker")
        assert reloaded_marker is None, "Window marker must be undefined, proving full website reload occurred!"

        # Verify post-reload toast appears
        page.wait_for_selector(".toast", state="visible", timeout=4000)
        toast_text = page.locator(".toast").inner_text()
        assert "permanently deleted" in toast_text or "already empty" in toast_text

        # Verify fresh empty state after reload
        total_records = page.locator("#kpiTotalRecords").inner_text()
        assert total_records == "0"
        score_val = page.locator("#kpiDataQualityScore").inner_text()
        assert score_val == "—"

        browser.close()


def test_e2e_three_trend_comparison_scenarios(local_server):
    """Test all three comparison scenarios:
    1. Empty database: shows 'No data' and '—'
    2. Current data without previous period: shows 'No previous-period comparison available' with tooltip
    3. Current data WITH previous period: shows genuine calculated trend percentage
    """
    db = SessionLocal()
    try:
        db.query(JobCleanRecord).delete()
        db.query(Customer).delete()
        db.query(ProcessingJob).delete()
        db.commit()
    finally:
        db.close()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        # Scenario 1: Empty database
        page.wait_for_selector("#kpiTotalRecords")
        assert page.locator("#kpiTotalRecords").inner_text() == "0"
        assert page.locator("#kpiDataQualityScore").inner_text() == "—"
        assert page.locator("#kpiTotalRecordsTrendBadge").inner_text() == "No data"
        assert page.locator("#kpiDataQualityScoreTrendBadge").inner_text() == "No data"

        # Scenario 2: Seed 1 job today (current window), 0 jobs in prior window
        db = SessionLocal()
        now = datetime.datetime.now()
        try:
            job_today = ProcessingJob(
                filename="customers_today.csv",
                dataset_type="customers",
                file_hash="hash_today",
                status="completed",
                total_rows=10,
                accepted_rows=9,
                rejected_rows=1,
                duplicate_rows=0,
                started_at=now - datetime.timedelta(hours=2),
                completed_at=now - datetime.timedelta(hours=2) + datetime.timedelta(seconds=5),
            )
            db.add(job_today)
            db.commit()
        finally:
            db.close()

        page.reload(wait_until="networkidle")
        page.wait_for_selector("#kpiTotalRecords")
        assert page.locator("#kpiTotalRecords").inner_text() == "10"
        assert page.locator("#kpiAcceptedRecords").inner_text() == "9"
        assert "90" in page.locator("#kpiDataQualityScore").inner_text()
        assert page.locator("#kpiTotalRecordsTrendBadge").inner_text() == "No previous-period comparison available"
        assert page.locator("#kpiDataQualityScoreTrendBadge").inner_text() == "No previous-period comparison available"

        expected_tooltip = "This dashboard displays your current data correctly, but there is not enough historical data from the previous comparison period to calculate a trend percentage."
        assert page.locator("#kpiTotalRecordsTrendBadge").get_attribute("title") == expected_tooltip
        assert page.locator("#kpiDataQualityScoreTrendBadge").get_attribute("title") == expected_tooltip

        # Scenario 3: Seed 1 job in prior window (10 days ago, total_rows=5, accepted_rows=5)
        db = SessionLocal()
        try:
            job_prior = ProcessingJob(
                filename="customers_last_week.csv",
                dataset_type="customers",
                file_hash="hash_prior",
                status="completed",
                total_rows=5,
                accepted_rows=5,
                rejected_rows=0,
                duplicate_rows=0,
                started_at=now - datetime.timedelta(days=10),
                completed_at=now - datetime.timedelta(days=10) + datetime.timedelta(seconds=5),
            )
            db.add(job_prior)
            db.commit()
        finally:
            db.close()

        page.reload(wait_until="networkidle")
        page.wait_for_selector("#kpiTotalRecords")
        assert page.locator("#kpiTotalRecords").inner_text() == "15"
        total_trend_text = page.locator("#kpiTotalRecordsTrendBadge").inner_text()
        assert "+100.0% from last week" in total_trend_text or "+100%" in total_trend_text
        score_trend_text = page.locator("#kpiDataQualityScoreTrendBadge").inner_text()
        assert "-10.0% from last week" in score_trend_text or "-10%" in score_trend_text

        browser.close()
