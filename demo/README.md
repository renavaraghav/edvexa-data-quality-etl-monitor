# EDVEXA Application Demonstration Walkthrough

- **Filename:** `edvexa-demo.mp4`
- **Format:** MP4 Video (`video/mp4`)
- **Duration:** 4 minutes 23 seconds
- **File Size:** 249.86 MB (261,994,262 bytes)

## Overview & Scenarios Covered
1. **System Health & Architecture:** Live server running on port 8010, SQLite database connectivity, and OpenAPI documentation at `/docs`.
2. **CSV Ingestion Pipeline:** Drag-and-drop CSV upload for Customers, Sales, and Inventory datasets.
3. **Data Quality Validation:** Real-time diagnostics for missing fields, RFC 5322 email validation, calendar date bounds, and negative amount rejections.
4. **Duplicate Prevention & Idempotency:** In-file duplicate key detection (`DUPLICATE_KEY_IN_FILE`) and database duplicate prevention (`DUPLICATE_KEY_IN_DB`) on repeated uploads.
5. **Interactive Observability Dashboard:** Real-time KPI summary cards, quality distribution donut, and 14-day ingestion trend charts.
6. **Date-Range Filtering:** Synchronized date filtering across dashboard summary, trends, jobs, clean records, and validation errors with strict empty-state displays.
7. **Dual-Theme Design & Contrast:** Warm Ivory default theme and Dark Espresso mode with audited WCAG AA 4.5:1 text contrast.
8. **iPhone-Style Slide-to-Reset:** Compact slide interaction with secondary safety confirmation modal and transactional data cleanup.
9. **Job-Scoped Artifact Downloads:** Exporting Clean Records (CSV), Validation Errors (CSV), and comprehensive multi-sheet Microsoft Excel reports (`.xlsx`).

> **Note on File Hosting:**  
> The full, uncompressed 250 MB demonstration recording is preserved locally in this `demo/` directory as `edvexa-demo.mp4`. Because GitHub enforces a 100 MB per-file limit on standard Git repositories, the large video is hosted externally for streaming playback.
