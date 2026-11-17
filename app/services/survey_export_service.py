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
from copy import copy
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from app.models import SurveyAuditRun, SurveyRespondent, SurveyQcFinding, SurveyQuestionMapping
from app.services.export_service import sanitize_cell_value

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
STREAM_EXPORT_CELLS = 250_000


def generate_survey_audit_workbook(
    audit: SurveyAuditRun,
    respondents: list[SurveyRespondent],
    findings: list[SurveyQcFinding],
    mappings: list[SurveyQuestionMapping],
    reviews=None,
) -> io.BytesIO:
    """Generate the complete 8-worksheet Excel audit report."""
    source_headers = _source_headers(respondents)
    streamed = len(respondents) * len(source_headers) * 2 + len(findings) * 13 > STREAM_EXPORT_CELLS
    wb = Workbook(write_only=streamed)

    # 1. Audit Summary
    ws_summary = Workbook().active if streamed else wb.active
    ws_summary.title = "Audit Summary"
    _build_summary_sheet(ws_summary, audit, respondents, findings)
    if streamed:
        _auto_adjust_column_widths(ws_summary)
        _append_summary_sheet(wb.create_sheet(title=ws_summary.title), ws_summary)

    # 2. Imported cell values; original file bytes are stored as an artifact.
    ws_raw = wb.create_sheet(title="Raw Data")
    _build_raw_data_sheet(ws_raw, respondents, source_headers, streamed)

    # 3. Accepted Data
    ws_accepted = wb.create_sheet(title="Accepted Data")
    _build_disposition_sheet(ws_accepted, respondents, source_headers, "ACCEPTED", streamed)

    # 4. Rejected Data
    ws_rejected = wb.create_sheet(title="Rejected Data")
    _build_disposition_sheet(ws_rejected, respondents, source_headers, "REJECTED", streamed)

    # 5. Review Required
    ws_review = wb.create_sheet(title="Review Required")
    _build_disposition_sheet(ws_review, respondents, source_headers, "REVIEW_REQUIRED", streamed)

    # 6. QC Findings
    ws_findings = wb.create_sheet(title="QC Findings")
    _build_findings_sheet(ws_findings, findings, streamed)

    # 7. QQ Mapping
    ws_mapping = wb.create_sheet(title="QQ Mapping")
    _build_mapping_sheet(ws_mapping, mappings, streamed)

    # 8. QC Coverage
    ws_coverage = wb.create_sheet(title="QC Coverage")
    _build_coverage_sheet(ws_coverage, audit, streamed)

    if audit.timing_rules_json:
        provenance = wb.create_sheet(title="Provenance")
        provenance.append(["Effective audit configuration", "Value"])
        snapshot = json.loads(audit.timing_rules_json)
        for key, value in snapshot.items():
            value_text = json.dumps(value, ensure_ascii=False)
            for offset in range(0, max(1, len(value_text)), 30000):
                provenance.append([sanitize_cell_value(key), sanitize_cell_value(value_text[offset:offset+30000])])
        intake = snapshot.get("snapshot", {}).get("intake")
        if intake:
            exclusions = wb.create_sheet(title="Intake")
            exclusions.append(["Source row", "Reason not imported as a respondent"])
            for row in intake["excluded_rows"]:
                exclusions.append([row["source_row"], row["reason"]])
    if reviews:
        history = wb.create_sheet(title="Review History")
        history.append(["Respondent Record ID", "Respondent Key", "Source Row", "Previous", "New", "Reviewer", "Reason", "At"])
        by_id = {r.id: r for r in respondents}
        for event in reviews:
            respondent = by_id[event.respondent_id]
            history.append([event.respondent_id, sanitize_cell_value(respondent.respondent_key), respondent.row_number,
                            event.previous_disposition, event.new_disposition, sanitize_cell_value(event.reviewer),
                            sanitize_cell_value(event.reason), event.created_at.isoformat()])

    if not streamed:
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

    coverage = json.loads(audit.checks_summary_json) if audit.checks_summary_json else []
    qc_breakdown = [(item["check_name"], item["flagged_count"], item["status"], item["rule_description"])
                    for item in coverage]

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


def _append_summary_sheet(target, summary):
    """Keep the summary's layout when the report tables are written as streams."""
    for reference in summary.merged_cells.ranges:
        target.merged_cells.add(str(reference))
    for name, dimension in summary.column_dimensions.items():
        target.column_dimensions[name].width = dimension.width
    for number, dimension in summary.row_dimensions.items():
        target.row_dimensions[number].height = dimension.height
    for row in summary.iter_rows():
        output = []
        for cell in row:
            if cell.value is None:
                output.append(None)
                continue
            item = WriteOnlyCell(target, value=cell.value)
            item.font = copy(cell.font)
            item.fill = copy(cell.fill)
            item.border = copy(cell.border)
            item.alignment = copy(cell.alignment)
            output.append(item)
        target.append(output)


def _source_headers(respondents):
    if not respondents or not respondents[0].raw_data_json:
        return []
    return [header for header in json.loads(respondents[0].raw_data_json) if header != "_physical_row"]


def _write_report_table(ws, headers, rows, row_count, streamed, *, color=NAVY_HEADER, centered=(), filtered=True):
    ws.freeze_panes = "A2"
    if row_count and filtered:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row_count + 1}"

    header_fill = PatternFill("solid", fgColor=color)
    header_cells = []
    for index, header in enumerate(headers, 1):
        cell = WriteOnlyCell(ws, value=sanitize_cell_value(header))
        cell.font = HEADER_FONT
        cell.fill = header_fill
        cell.border = HEADER_BORDER_STYLE
        cell.alignment = Alignment(horizontal="center" if index in centered else "left")
        header_cells.append(cell)
        if streamed:
            ws.column_dimensions[get_column_letter(index)].width = max(14, min(55, len(header) + 3))
    ws.append(header_cells)

    for row in rows:
        values = [sanitize_cell_value(value) if value != "" else None for value in row]
        if streamed:
            ws.append(values)
        else:
            cells = []
            for index, value in enumerate(values, 1):
                cell = WriteOnlyCell(ws, value=value)
                cell.font = VALUE_FONT
                cell.border = CELL_BORDER
                if index in centered:
                    cell.alignment = Alignment(horizontal="center")
                cells.append(cell)
            ws.append(cells)


def _build_raw_data_sheet(ws, respondents, headers, streamed):
    if not respondents or not headers:
        ws.append(["No respondent records imported."])
        return

    def source_rows():
        for respondent in respondents:
            data = json.loads(respondent.raw_data_json or "{}")
            yield (data.get(header, "") for header in headers)

    _write_report_table(ws, headers, source_rows(), len(respondents), streamed)


def _build_disposition_sheet(ws, respondents, source_headers, disposition, streamed):
    filtered = [row for row in respondents if row.disposition == disposition]
    if not filtered:
        cell = WriteOnlyCell(ws, value=f"No respondents classified as {disposition}.")
        cell.font = MUTED_FONT
        ws.append([cell])
        return

    headers = ["Disposition", "Respondent Key", "Source Row", "Manual Decision", "Reviewer", "Review Reason"]
    if disposition == "REJECTED":
        headers.append("Rejection Reasons")
    elif disposition == "REVIEW_REQUIRED":
        headers.append("Review Flags")
    headers.extend(source_headers)
    color = {"ACCEPTED": "065F46", "REJECTED": "991B1B", "REVIEW_REQUIRED": "92400E"}[disposition]

    def disposition_rows():
        for row in filtered:
            data = json.loads(row.raw_data_json or "{}")
            values = [row.disposition, row.respondent_key, row.row_number, row.manual_decision, row.reviewed_by, row.reviewer_notes]
            if disposition == "REJECTED":
                values.append("; ".join(json.loads(row.rejection_reasons_json or "[]")))
            elif disposition == "REVIEW_REQUIRED":
                values.append("; ".join(json.loads(row.review_flags_json or "[]")))
            values.extend(data.get(header, "") for header in source_headers)
            yield values

    _write_report_table(ws, headers, disposition_rows(), len(filtered), streamed, color=color)


def _build_findings_sheet(ws, findings, streamed):
    headers = [
        "Finding ID", "Respondent Key", "Respondent Record ID", "Issue Type", "Severity",
        "Recommendation", "Question Number", "Raw Column",
        "Original Answer", "Observed Value", "Expected Rule / Standard",
        "Detailed Evidence", "Final Review Decision",
    ]
    rows = (
        [item.id, item.respondent_key, item.respondent_id, item.issue_type, item.severity,
         item.review_recommendation, item.question_number or "General", item.raw_column or "N/A",
         item.original_answer or "", item.observed_value or "", item.expected_rule or "",
         item.evidence, item.final_decision or "PENDING"]
        for item in findings
    )
    _write_report_table(ws, headers, rows, len(findings), streamed, centered=(1, 4, 5))


def _build_mapping_sheet(ws, mappings, streamed):
    headers = [
        "QQ Question #", "Questionnaire Text", "Question Type", "Mapped Raw Column(s)",
        "Mapping Status", "Confidence", "Mapping Evidence", "QC Instructions",
    ]
    rows = (
        [item.question_number, item.question_text, item.question_type,
         ", ".join(json.loads(item.mapped_columns_json or "[]")) or "(Unmapped)", item.mapping_status,
         item.confidence, item.mapping_evidence or "", item.qc_instructions or ""]
        for item in mappings
    )
    _write_report_table(ws, headers, rows, len(mappings), streamed, centered=(1, 3, 5, 6))


def _build_coverage_sheet(ws, audit, streamed):
    headers = [
        "Quality Check Name", "Category", "Execution Status",
        "Flagged Count", "Audit Rule Description", "Coverage Limitations & Notes",
    ]
    coverage = json.loads(audit.checks_summary_json or "[]")
    rows = (
        [item.get("check_name", ""), item.get("category", ""), item.get("status", "NOT_CHECKED"),
         item.get("flagged_count", 0), item.get("rule_description", ""), item.get("notes", "")]
        for item in coverage
    )
    _write_report_table(ws, headers, rows, len(coverage), streamed, centered=(3, 4), filtered=False)


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
