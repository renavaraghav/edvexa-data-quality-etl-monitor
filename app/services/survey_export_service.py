"""
Survey Quality Control Multi-Sheet Excel Workbook Export Service.
Generates a comprehensive, audit-ready Microsoft Excel workbook (.xlsx) containing 8 worksheets:
1. Audit Summary
2. Raw Data
3. Accepted Data
4. Rejected Data
5. Review Required
6. QC Findings
7. QQ Mapping
8. QC Coverage
Applies CWE-1236 spreadsheet injection mitigation, auto-filters, frozen panes, and professional styling.
"""

import io
import json
import logging
from typing import Any
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.models import SurveyAuditRun, SurveyRespondent, SurveyQcFinding, SurveyQuestionMapping
from app.services.export_service import sanitize_cell_value

logger = logging.getLogger("edvexa.surveys.export")

# Color Tokens
NAVY_HEADER = "1B365D"
DARK_TEXT = "1A1917"
LIGHT_BORDER = "D1D5DB"
HEADER_BORDER = "0F233D"
ZEBRA_FILL = "F9FAFB"
WHITE = "FFFFFF"
EMERALD_TINT = "ECFDF5"
CRIMSON_TINT = "FEF2F2"
AMBER_TINT = "FFFBEB"
BLUE_TINT = "EFF6FF"

THIN_SIDE = Side(border_style="thin", color=LIGHT_BORDER)
CELL_BORDER = Border(left=THIN_SIDE, right=THIN_SIDE, top=THIN_SIDE, bottom=THIN_SIDE)
HEADER_SIDE = Side(border_style="thin", color=HEADER_BORDER)
HEADER_BORDER_STYLE = Border(left=HEADER_SIDE, right=HEADER_SIDE, top=HEADER_SIDE, bottom=HEADER_SIDE)

FONT_FAMILY = "Segoe UI"
HEADER_FONT = Font(name=FONT_FAMILY, size=11, bold=True, color=WHITE)
TITLE_FONT = Font(name=FONT_FAMILY, size=14, bold=True, color=NAVY_HEADER)
SECTION_FONT = Font(name=FONT_FAMILY, size=12, bold=True, color="2C3E50")
LABEL_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color="374151")
VALUE_FONT = Font(name=FONT_FAMILY, size=10, color=DARK_TEXT)
MUTED_FONT = Font(name=FONT_FAMILY, size=9, italic=True, color="6B7280")
BOLD_VALUE_FONT = Font(name=FONT_FAMILY, size=10, bold=True, color=DARK_TEXT)


def generate_survey_audit_workbook(
    audit: SurveyAuditRun,
    respondents: list[SurveyRespondent],
    findings: list[SurveyQcFinding],
    mappings: list[SurveyQuestionMapping]
) -> io.BytesIO:
    """Generate the complete 8-worksheet Excel audit report."""
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    # 1. Audit Summary
    ws_summary = wb.create_sheet(title="Audit Summary")
    _build_summary_sheet(ws_summary, audit, respondents, findings)

    # 2. Raw Data (100% original dataset preserved)
    ws_raw = wb.create_sheet(title="Raw Data")
    _build_raw_data_sheet(ws_raw, respondents)

    # 3. Accepted Data
    ws_accepted = wb.create_sheet(title="Accepted Data")
    _build_disposition_sheet(ws_accepted, respondents, "ACCEPTED")

    # 4. Rejected Data
    ws_rejected = wb.create_sheet(title="Rejected Data")
    _build_disposition_sheet(ws_rejected, respondents, "REJECTED", include_reasons=True)

    # 5. Review Required
    ws_review = wb.create_sheet(title="Review Required")
    _build_disposition_sheet(ws_review, respondents, "REVIEW_REQUIRED", include_flags=True)

    # 6. QC Findings
    ws_findings = wb.create_sheet(title="QC Findings")
    _build_findings_sheet(ws_findings, findings)

    # 7. QQ Mapping
    ws_mapping = wb.create_sheet(title="QQ Mapping")
    _build_mapping_sheet(ws_mapping, mappings)

    # 8. QC Coverage
    ws_coverage = wb.create_sheet(title="QC Coverage")
    _build_coverage_sheet(ws_coverage, audit)

    # Auto-adjust column widths across all sheets
    for ws in wb.worksheets:
        _auto_adjust_column_widths(ws)

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


def _build_summary_sheet(ws, audit: SurveyAuditRun, respondents: list[SurveyRespondent], findings: list[SurveyQcFinding]):
    """Build Sheet 1: Audit Summary & Invariant Reconciliation."""
    ws.views.sheetView[0].showGridLines = True

    # Title Block
    ws.merge_cells("A1:G1")
    title_cell = ws["A1"]
    title_cell.value = "EDVEXA — SURVEY DATA SCRUBBING AUDIT REPORT"
    title_cell.font = TITLE_FONT
    title_cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 28

    ws["A2"].value = "Automated Quality Control, Fraud Detection & Respondent Disposition Audit"
    ws["A2"].font = MUTED_FONT
    ws.row_dimensions[2].height = 18

    # Section 1: Execution Metadata
    ws["A4"].value = "1. AUDIT EXECUTION METADATA"
    ws["A4"].font = SECTION_FONT
    ws.merge_cells("A4:D4")

    meta_rows = [
        ("Audit Run ID:", f"#{audit.id}", "Status:", audit.status.upper()),
        ("Audit Name:", audit.audit_name, "Processing Duration:", f"{audit.duration_seconds or 0.0:.2f} seconds"),
        ("Raw Data File:", audit.raw_filename, "Worksheet Selected:", audit.selected_sheet or "Default"),
        ("Questionnaire (QQ):", audit.qq_filename or "None Provided (Self-Contained QC)", "Header Row Index:", f"Row {audit.header_row_index + 1} (0-indexed: {audit.header_row_index})"),
        ("Started At:", audit.started_at.strftime("%Y-%m-%d %H:%M:%S UTC") if audit.started_at else "N/A", "Completed At:", audit.completed_at.strftime("%Y-%m-%d %H:%M:%S UTC") if audit.completed_at else "N/A")
    ]

    r_curr = 5
    for lbl1, val1, lbl2, val2 in meta_rows:
        ws[f"A{r_curr}"].value = lbl1
        ws[f"A{r_curr}"].font = LABEL_FONT
        ws[f"B{r_curr}"].value = sanitize_cell_value(val1)
        ws[f"B{r_curr}"].font = VALUE_FONT
        ws[f"C{r_curr}"].value = lbl2
        ws[f"C{r_curr}"].font = LABEL_FONT
        ws[f"D{r_curr}"].value = sanitize_cell_value(val2)
        ws[f"D{r_curr}"].font = VALUE_FONT
        r_curr += 1

    # Section 2: Respondent Reconciliation Invariant (CRITICAL)
    r_curr += 2
    ws[f"A{r_curr}"].value = "2. RESPONDENT DISPOSITION & ACCOUNTING RECONCILIATION"
    ws[f"A{r_curr}"].font = SECTION_FONT
    ws.merge_cells(f"A{r_curr}:D{r_curr}")
    r_curr += 1

    ws[f"A{r_curr}"].value = "Category"
    ws[f"B{r_curr}"].value = "Respondent Count"
    ws[f"C{r_curr}"].value = "Percentage"
    ws[f"D{r_curr}"].value = "Accounting Status"
    for col in ["A", "B", "C", "D"]:
        cell = ws[f"{col}{r_curr}"]
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=NAVY_HEADER)
        cell.alignment = Alignment(horizontal="center" if col != "A" else "left")
    r_curr += 1

    tot = audit.total_respondents or len(respondents)
    acc = audit.accepted_count
    rej = audit.rejected_count
    rev = audit.review_required_count
    sum_parts = acc + rej + rev
    is_reconciled = (tot == sum_parts)

    disp_rows = [
        ("Accepted Respondents", acc, (acc / tot * 100) if tot else 0.0, "PASSED", EMERALD_TINT),
        ("Review Required Respondents", rev, (rev / tot * 100) if tot else 0.0, "PENDING REVIEW", AMBER_TINT),
        ("Rejected Respondents", rej, (rej / tot * 100) if tot else 0.0, "EXCLUDED", CRIMSON_TINT),
    ]

    for label, count, pct, status_str, fill_color in disp_rows:
        ws[f"A{r_curr}"].value = label
        ws[f"A{r_curr}"].font = VALUE_FONT
        ws[f"A{r_curr}"].fill = PatternFill("solid", fgColor=fill_color)
        ws[f"B{r_curr}"].value = count
        ws[f"B{r_curr}"].font = BOLD_VALUE_FONT
        ws[f"B{r_curr}"].alignment = Alignment(horizontal="right")
        ws[f"C{r_curr}"].value = f"{pct:.1f}%"
        ws[f"C{r_curr}"].font = VALUE_FONT
        ws[f"C{r_curr}"].alignment = Alignment(horizontal="right")
        ws[f"D{r_curr}"].value = status_str
        ws[f"D{r_curr}"].font = BOLD_VALUE_FONT
        ws[f"D{r_curr}"].alignment = Alignment(horizontal="center")
        for col in ["A", "B", "C", "D"]:
            ws[f"{col}{r_curr}"].border = CELL_BORDER
        r_curr += 1

    # Total row
    ws[f"A{r_curr}"].value = "Total Imported Respondents"
    ws[f"A{r_curr}"].font = BOLD_VALUE_FONT
    ws[f"B{r_curr}"].value = tot
    ws[f"B{r_curr}"].font = BOLD_VALUE_FONT
    ws[f"B{r_curr}"].alignment = Alignment(horizontal="right")
    ws[f"C{r_curr}"].value = "100.0%"
    ws[f"C{r_curr}"].font = BOLD_VALUE_FONT
    ws[f"C{r_curr}"].alignment = Alignment(horizontal="right")
    ws[f"D{r_curr}"].value = "RECONCILED (100% INVARIANT MATCH)" if is_reconciled else f"MISMATCH ({tot - sum_parts})"
    ws[f"D{r_curr}"].font = BOLD_VALUE_FONT
    ws[f"D{r_curr}"].alignment = Alignment(horizontal="center")
    for col in ["A", "B", "C", "D"]:
        ws[f"{col}{r_curr}"].border = CELL_BORDER
    r_curr += 2

    # Section 3: Detailed QC Flag Breakdown
    ws[f"A{r_curr}"].value = "3. QUALITY CONTROL FINDINGS BREAKDOWN (BY CATEGORY)"
    ws[f"A{r_curr}"].font = SECTION_FONT
    ws.merge_cells(f"A{r_curr}:D{r_curr}")
    r_curr += 1

    ws[f"A{r_curr}"].value = "Quality Check Issue Type"
    ws[f"B{r_curr}"].value = "Flagged Count"
    ws[f"C{r_curr}"].value = "Default Action"
    ws[f"D{r_curr}"].value = "Description / Standard"
    for col in ["A", "B", "C", "D"]:
        cell = ws[f"{col}{r_curr}"]
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=NAVY_HEADER)
        cell.alignment = Alignment(horizontal="center" if col in ("B", "C") else "left")
    r_curr += 1

    qc_breakdown = [
        ("Survey Speeding Concerns", audit.speeding_count, "Review Required / Reject", "Completion faster than threshold (under 5m or under 2m) or question timer flags"),
        ("Straightlining Responses", audit.straightlining_count, "Review Required", "Zero variance across scale statements on matrix questions"),
        ("Suspicious Open-Text / Gibberish", audit.suspicious_text_count, "Review Required", "Gibberish, repeated characters, vowel-less strings, or meaningful off-topic brands"),
        ("Confirmed Duplicates", audit.confirmed_duplicates_count, "Reject", "Duplicate Respondent IDs or 100% identical response signatures"),
        ("Potential Duplicates", audit.potential_duplicates_count, "Review Required", "Shared IP address or high partial similarity"),
        ("Screening & Eligibility Violations", audit.screener_violations_count, "Reject", "Failed demographic criteria (e.g. age < 18) or selected termination option"),
        ("Routing / Skip Logic Inconsistencies", audit.routing_violations_count, "Review Required", "Answers present on questions that should have been skipped"),
        ("Contradictory Responses", audit.contradiction_count, "Review Required", "Mutually exclusive answer choices selected together"),
        ("Incomplete Submissions", audit.incomplete_count, "Review Required / Reject", "Premature drop-offs or substantial unanswered fields"),
        ("Timestamp Arithmetic Discrepancies", audit.timestamp_mismatch_count, "Review Required", "Calculated duration (EndDate − StartDate) differs from exported duration")
    ]

    for q_name, q_cnt, q_act, q_desc in qc_breakdown:
        ws[f"A{r_curr}"].value = q_name
        ws[f"A{r_curr}"].font = VALUE_FONT
        ws[f"B{r_curr}"].value = q_cnt
        ws[f"B{r_curr}"].font = BOLD_VALUE_FONT if q_cnt > 0 else VALUE_FONT
        ws[f"B{r_curr}"].alignment = Alignment(horizontal="right")
        ws[f"C{r_curr}"].value = q_act
        ws[f"C{r_curr}"].font = VALUE_FONT
        ws[f"D{r_curr}"].value = q_desc
        ws[f"D{r_curr}"].font = MUTED_FONT
        for col in ["A", "B", "C", "D"]:
            ws[f"{col}{r_curr}"].border = CELL_BORDER
        r_curr += 1


def _build_raw_data_sheet(ws, respondents: list[SurveyRespondent]):
    """Build Sheet 2: Raw Data (100% original dataset preserved)."""
    ws.views.sheetView[0].showGridLines = True
    if not respondents:
        ws["A1"].value = "No respondent records imported."
        return

    # Extract headers from first record
    first_dict = json.loads(respondents[0].raw_data_json) if respondents[0].raw_data_json else {}
    headers = [k for k in first_dict.keys() if not k.startswith("_")]

    # Write Header Row
    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=sanitize_cell_value(h))
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=NAVY_HEADER)
        cell.border = HEADER_BORDER_STYLE
        cell.alignment = Alignment(horizontal="left")

    # Freeze header row
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(respondents) + 1}"

    # Write Data Rows
    for r_idx, resp in enumerate(respondents, 2):
        row_dict = json.loads(resp.raw_data_json) if resp.raw_data_json else {}
        for col_idx, h in enumerate(headers, 1):
            val = row_dict.get(h, "")
            cell = ws.cell(row=r_idx, column=col_idx, value=sanitize_cell_value(val))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER


def _build_disposition_sheet(ws, respondents: list[SurveyRespondent], target_disposition: str, include_reasons: bool = False, include_flags: bool = False):
    """Build Sheet 3/4/5: Accepted, Rejected, or Review Required respondent tables."""
    ws.views.sheetView[0].showGridLines = True
    filtered = [r for r in respondents if r.disposition == target_disposition]

    if not filtered:
        ws["A1"].value = f"No respondents classified as {target_disposition}."
        ws["A1"].font = MUTED_FONT
        return

    first_dict = json.loads(filtered[0].raw_data_json) if filtered[0].raw_data_json else {}
    base_headers = [k for k in first_dict.keys() if not k.startswith("_")]

    headers = ["Disposition"]
    if include_reasons:
        headers.append("Rejection Reasons")
    if include_flags:
        headers.append("Review Flags")
    headers.extend(base_headers)

    header_fill_color = NAVY_HEADER
    if target_disposition == "ACCEPTED":
        header_fill_color = "065F46"  # Emerald
    elif target_disposition == "REJECTED":
        header_fill_color = "991B1B"  # Crimson
    elif target_disposition == "REVIEW_REQUIRED":
        header_fill_color = "92400E"  # Amber

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=sanitize_cell_value(h))
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=header_fill_color)
        cell.border = HEADER_BORDER_STYLE
        cell.alignment = Alignment(horizontal="left")

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(filtered) + 1}"

    for r_idx, resp in enumerate(filtered, 2):
        row_dict = json.loads(resp.raw_data_json) if resp.raw_data_json else {}
        col_cursor = 1

        # Disposition
        cell = ws.cell(row=r_idx, column=col_cursor, value=resp.disposition)
        cell.font = BOLD_VALUE_FONT
        cell.border = CELL_BORDER
        col_cursor += 1

        if include_reasons:
            reasons = json.loads(resp.rejection_reasons_json) if resp.rejection_reasons_json else []
            cell = ws.cell(row=r_idx, column=col_cursor, value=sanitize_cell_value("; ".join(reasons)))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER
            col_cursor += 1

        if include_flags:
            flags = json.loads(resp.review_flags_json) if resp.review_flags_json else []
            cell = ws.cell(row=r_idx, column=col_cursor, value=sanitize_cell_value("; ".join(flags)))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER
            col_cursor += 1

        for h in base_headers:
            val = row_dict.get(h, "")
            cell = ws.cell(row=r_idx, column=col_cursor, value=sanitize_cell_value(val))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER
            col_cursor += 1


def _build_findings_sheet(ws, findings: list[SurveyQcFinding]):
    """Build Sheet 6: Itemized QC Findings with evidence and recommendations."""
    ws.views.sheetView[0].showGridLines = True
    headers = [
        "Finding ID", "Respondent Key", "Issue Type", "Severity",
        "Recommendation", "Question Number", "Raw Column",
        "Original Answer", "Observed Value", "Expected Rule / Standard",
        "Detailed Evidence", "Final Review Decision"
    ]

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=NAVY_HEADER)
        cell.border = HEADER_BORDER_STYLE
        cell.alignment = Alignment(horizontal="center" if col_idx in (1, 4, 5) else "left")

    ws.freeze_panes = "A2"
    if findings:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(findings) + 1}"

    for r_idx, f in enumerate(findings, 2):
        row_data = [
            f.id,
            f.respondent_key,
            f.issue_type,
            f.severity,
            f.review_recommendation,
            f.question_number or "General",
            f.raw_column or "N/A",
            f.original_answer or "",
            f.observed_value or "",
            f.expected_rule or "",
            f.evidence,
            f.final_decision or "PENDING"
        ]
        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=r_idx, column=col_idx, value=sanitize_cell_value(val))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER
            if col_idx in (1, 4, 5):
                cell.alignment = Alignment(horizontal="center")


def _build_mapping_sheet(ws, mappings: list[SurveyQuestionMapping]):
    """Build Sheet 7: QQ Question-to-Column Mapping."""
    ws.views.sheetView[0].showGridLines = True
    headers = [
        "QQ Question #", "Questionnaire Text", "Question Type",
        "Mapped Raw Column(s)", "Mapping Status", "Confidence",
        "Mapping Evidence", "QC Instructions"
    ]

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=NAVY_HEADER)
        cell.border = HEADER_BORDER_STYLE
        cell.alignment = Alignment(horizontal="center" if col_idx in (1, 3, 5, 6) else "left")

    ws.freeze_panes = "A2"
    if mappings:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{len(mappings) + 1}"

    for r_idx, m in enumerate(mappings, 2):
        cols = json.loads(m.mapped_columns_json) if m.mapped_columns_json else []
        row_data = [
            m.question_number,
            m.question_text,
            m.question_type,
            ", ".join(cols) if cols else "(Unmapped)",
            m.mapping_status,
            m.confidence,
            m.mapping_evidence or "",
            m.qc_instructions or ""
        ]
        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=r_idx, column=col_idx, value=sanitize_cell_value(val))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER
            if col_idx in (1, 3, 5, 6):
                cell.alignment = Alignment(horizontal="center")


def _build_coverage_sheet(ws, audit: SurveyAuditRun):
    """Build Sheet 8: Quality Control Coverage Matrix."""
    ws.views.sheetView[0].showGridLines = True
    headers = [
        "Quality Check Name", "Category", "Execution Status",
        "Flagged Count", "Audit Rule Description", "Coverage Limitations & Notes"
    ]

    for col_idx, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col_idx, value=h)
        cell.font = HEADER_FONT
        cell.fill = PatternFill("solid", fgColor=NAVY_HEADER)
        cell.border = HEADER_BORDER_STYLE
        cell.alignment = Alignment(horizontal="center" if col_idx in (3, 4) else "left")

    ws.freeze_panes = "A2"

    coverage_items = json.loads(audit.checks_summary_json) if audit.checks_summary_json else []
    for r_idx, item in enumerate(coverage_items, 2):
        row_data = [
            item.get("check_name", ""),
            item.get("category", ""),
            item.get("status", "NOT_CHECKED"),
            item.get("flagged_count", 0),
            item.get("rule_description", ""),
            item.get("notes", "")
        ]
        for col_idx, val in enumerate(row_data, 1):
            cell = ws.cell(row=r_idx, column=col_idx, value=sanitize_cell_value(val))
            cell.font = VALUE_FONT
            cell.border = CELL_BORDER
            if col_idx in (3, 4):
                cell.alignment = Alignment(horizontal="center")


def _auto_adjust_column_widths(ws):
    """Dynamically size columns based on contents with safety bounds."""
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            val_str = str(cell.value or "")
            if len(val_str) > max_len:
                max_len = len(val_str)
        # Cap width between 10 and 55 characters
        ws.column_dimensions[col_letter].width = max(10, min(55, max_len + 3))
