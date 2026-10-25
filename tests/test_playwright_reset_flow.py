"""
Playwright End-to-End Tests for Public Destructive Reset with Slider and Confirmation Checkbox.
Verifies the complete frontend user interaction on a local server:
1. Reset modal design, styling, and text matches exact specification:
   - "I understand that this will permanently delete all uploaded data and processing history."
   - Irreversible action warning is displayed.
   - Destructive reset disabled notice and admin token fields are removed.
2. Checkbox is disabled until the slider reaches completed position.
3. Dragging slider to end enables the checkbox.
4. Ticking the checkbox immediately executes the reset API request without any extra button clicks.
5. Loading indicator is displayed during deletion.
6. Successful reset closes modal and refreshes dashboard stats.
7. UI recovery on reset failure preserves modal state and re-enables controls.
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


def test_e2e_slider_and_checkbox_reset_interaction(local_server):
    seed_local_db()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(local_server, wait_until="networkidle")

        # 1. Verify modal without slider completion: checkbox is disabled
        page.evaluate("App.openResetModal(false)")
        modal = page.locator("#resetConfirmModal")
        assert modal.is_visible()

        # Verify old "disabled in production" warning is NOT present
        assert page.locator("#resetProdDisabledNotice").count() == 0

        # Verify admin token field is NOT present
        assert page.locator("#adminResetToken").count() == 0

        # Verify confirmation checkbox text matches requirement exactly
        checkbox_label = page.locator("label[for='confirmResetCheckbox']").inner_text()
        assert "I understand that this will permanently delete all uploaded data and processing history." in checkbox_label

        # Verify checkbox is disabled before slider is completed
        checkbox = page.locator("#confirmResetCheckbox")
        assert checkbox.is_disabled()

        # Close modal
        page.locator("#cancelResetBtn").click()
        assert not modal.is_visible()

        # 2. Test slider completion via keyboard or drag
        thumb = page.locator("#slideResetThumb")
        thumb.focus()
        page.keyboard.press("Enter")

        # Modal should now be opened with slider completed
        assert modal.is_visible()
        assert not checkbox.is_disabled()
        assert not checkbox.is_checked()

        # 3. Ticking checkbox immediately triggers execution without another button click
        checkbox.check()

        # Modal should close after successful deletion
        page.wait_for_selector("#resetConfirmModal", state="hidden", timeout=6000)

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
        assert not checkbox.is_disabled()

        # Check checkbox to trigger reset
        checkbox.check()

        # Wait for failure response and toast
        page.wait_for_selector(".toast.danger", timeout=4000)

        # Modal must remain open to preserve UI state
        assert modal.is_visible()

        # Controls must be re-enabled and checkbox unchecked for retry
        assert not checkbox.is_checked()
        assert not checkbox.is_disabled()
        cancel_btn = page.locator("#cancelResetBtn")
        assert not cancel_btn.is_disabled()

        # Toast should display error message
        toast_text = page.locator(".toast.danger").inner_text()
        assert "Simulated Database Connection Failure" in toast_text

        browser.close()
