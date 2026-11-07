"""
Survey Raw Respondent Data Parser.
Supports CSV and multi-sheet XLSX formats.
Provides sheet inspection, intelligent header-row detection (handling survey-platform metadata rows),
interactive dataset preview, and full original-data preservation.
"""

import csv
import io
import logging
from typing import Any
import openpyxl

logger = logging.getLogger("edvexa.surveys.data_parser")

COMMON_ID_COLUMNS = [
    "responseid", "respondentid", "respondent_id", "response_id",
    "rid", "respid", "id", "record_id", "caseid", "sys_resp_num",
    "voter_id", "participant_id", "uuid"
]

COMMON_START_TIME_COLS = ["startdate", "start_date", "start_time", "starttime", "begintime"]
COMMON_END_TIME_COLS = ["enddate", "end_date", "end_time", "endtime", "completed_time", "recordeddate"]
COMMON_DURATION_COLS = ["duration (in seconds)", "duration_seconds", "duration", "duration (seconds)", "time_spent", "length_of_interview"]


def get_excel_sheets(content: bytes) -> list[str]:
    """Inspect and return all worksheet titles from an Excel file."""
    try:
        stream = io.BytesIO(content)
        wb = openpyxl.load_workbook(stream, read_only=True)
        sheets = wb.sheetnames
        wb.close()
        return sheets
    except Exception as e:
        logger.error(f"Failed to read sheets from Excel file: {e}")
        return []


def read_raw_rows(content: bytes, filename: str, sheet_name: str | None = None, max_rows: int | None = None) -> list[list[Any]]:
    """Read raw matrix of rows from CSV or XLSX without discarding anything."""
    lower = filename.lower()
    rows: list[list[Any]] = []

    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        stream = io.BytesIO(content)
        wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        if sheet_name and sheet_name in wb.sheetnames:
            sheet = wb[sheet_name]
        else:
            sheet = wb.active or wb.worksheets[0]

        for idx, row in enumerate(sheet.iter_rows(values_only=True)):
            if max_rows and idx >= max_rows:
                break
            # Convert row tuple to list with trimmed string representation for headers
            rows.append(list(row))
        wb.close()
    else:
        # CSV reading with multi-encoding detection
        decoded_text = ""
        for encoding in ["utf-8-sig", "utf-8", "latin-1", "cp1252"]:
            try:
                decoded_text = content.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not decoded_text:
            decoded_text = content.decode("utf-8", errors="replace")

        reader = csv.reader(io.StringIO(decoded_text))
        for idx, row in enumerate(reader):
            if max_rows and idx >= max_rows:
                break
            rows.append(row)

    return rows


def detect_header_and_metadata(rows: list[list[Any]]) -> dict[str, Any]:
    """
    Intelligently inspect top rows to identify column headers and metadata rows.
    Handles standard survey exports:
      Qualtrics / Decipher:
        Row 0: Short column variable names (e.g. ResponseId, Q1, Q2_1)
        Row 1: Full question wording (e.g. Response ID, What is your age?)
        Row 2: Import ID metadata JSON (e.g. {"ImportId":"QID1"})
        Row 3+: Respondent data rows
    """
    if not rows:
        return {
            "recommended_header_row": 0,
            "metadata_rows": [],
            "row_evaluations": []
        }

    evaluations = []
    metadata_rows: list[int] = []

    # Check top min(5, len(rows)) rows
    check_limit = min(5, len(rows))
    for r_idx in range(check_limit):
        row = rows[r_idx]
        non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
        total_cells = len(row)
        non_empty_count = len(non_empty)

        # Uniqueness ratio
        unique_count = len(set(non_empty))
        uniqueness = (unique_count / non_empty_count) if non_empty_count > 0 else 0

        # String vs numeric ratio
        num_numeric = sum(1 for c in non_empty if _is_number(c))
        is_predominantly_text = (num_numeric / non_empty_count < 0.2) if non_empty_count > 0 else False

        # Metadata indicators: contains {"ImportId":...} or similar
        has_import_id = any("importid" in c.lower() or "json" in c.lower() for c in non_empty)
        if has_import_id:
            metadata_rows.append(r_idx)

        # Score row as candidate header
        score = 0
        if is_predominantly_text:
            score += 40
        if uniqueness > 0.8:
            score += 40
        if non_empty_count > (total_cells * 0.4):
            score += 20
        if has_import_id:
            score -= 80  # JSON import ID is metadata, not user header

        evaluations.append({
            "row_index": r_idx,
            "sample_values": non_empty[:5],
            "non_empty_count": non_empty_count,
            "uniqueness": round(uniqueness, 2),
            "score": score
        })

    # Pick highest scoring row as recommended header
    best_row = max(evaluations, key=lambda x: x["score"]) if evaluations else {"row_index": 0}
    recommended = best_row["row_index"]

    # Also detect if row recommended+1 or recommended+2 are metadata description rows
    for r_idx in range(recommended + 1, check_limit):
        row = rows[r_idx]
        non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
        if any("importid" in c.lower() for c in non_empty):
            if r_idx not in metadata_rows:
                metadata_rows.append(r_idx)

    max_meta_idx = max(metadata_rows) if metadata_rows else -1
    if max_meta_idx > recommended:
        for r_idx in range(recommended + 1, max_meta_idx):
            if r_idx not in metadata_rows:
                metadata_rows.append(r_idx)
    metadata_rows.sort()

    return {
        "recommended_header_row": recommended,
        "metadata_rows": metadata_rows,
        "row_evaluations": evaluations
    }


def _is_number(val: str) -> bool:
    try:
        float(val)
        return True
    except ValueError:
        return False


def preview_survey_data(content: bytes, filename: str, sheet_name: str | None = None, header_row_index: int = 0, sample_size: int = 8) -> dict[str, Any]:
    """Preview sheet structure, headers, and sample data for UI configuration."""
    sheets = []
    lower = filename.lower()
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        sheets = get_excel_sheets(content)

    all_rows = read_raw_rows(content, filename, sheet_name, max_rows=100)
    meta_info = detect_header_and_metadata(all_rows)

    effective_header_idx = header_row_index
    headers: list[str] = []
    sample_rows: list[dict[str, Any]] = []

    if all_rows and len(all_rows) > effective_header_idx:
        raw_header = all_rows[effective_header_idx]
        headers = _clean_headers(raw_header)

        # Skip header and any metadata rows
        skip_indices = set(meta_info.get("metadata_rows", []))
        skip_indices.add(effective_header_idx)

        data_rows = [r for idx, r in enumerate(all_rows) if idx not in skip_indices]
        for r in data_rows[:sample_size]:
            row_dict = {}
            for col_idx, h in enumerate(headers):
                val = r[col_idx] if col_idx < len(r) else ""
                row_dict[h] = "" if val is None else str(val).strip()
            sample_rows.append(row_dict)

    # Count total rows if possible
    full_count = len(read_raw_rows(content, filename, sheet_name))
    total_data_rows = max(0, full_count - (effective_header_idx + 1 + len(meta_info.get("metadata_rows", []))))

    is_excel = bool(sheets) or lower.endswith(".xlsx") or lower.endswith(".xls")
    meta_detected_count = len(meta_info.get("metadata_rows", []))
    return {
        "filename": filename,
        "is_excel": is_excel,
        "sheets": sheets,
        "selected_sheet": sheet_name or (sheets[0] if sheets else None),
        "header_row_index": effective_header_idx,
        "detected_headers": headers,
        "columns": headers,
        "total_data_rows": total_data_rows,
        "total_rows_detected": total_data_rows,
        "total_rows": total_data_rows,
        "sample_rows": sample_rows,
        "preview_rows": sample_rows,
        "metadata_rows_detected": meta_detected_count,
        "metadata_rows": meta_info.get("metadata_rows", []),
        "recommended_header_row": meta_info.get("recommended_header_row", 0)
    }


def parse_respondent_records(
    content: bytes,
    filename: str,
    sheet_name: str | None = None,
    header_row_index: int = 0,
    metadata_rows: list[int] | None = None
) -> tuple[list[str], list[dict[str, Any]], str]:
    """
    Parse complete respondent dataset into list of row dictionaries.
    Preserves all original columns and values.
    Returns: (headers, records, respondent_id_column)
    """
    raw_rows = read_raw_rows(content, filename, sheet_name)
    if not raw_rows or len(raw_rows) <= header_row_index:
        return [], [], "id"

    meta_info = detect_header_and_metadata(raw_rows[:10])
    detected_meta_rows = set(metadata_rows if metadata_rows is not None else meta_info.get("metadata_rows", []))
    detected_meta_rows.add(header_row_index)

    headers = _clean_headers(raw_rows[header_row_index])

    # Identify best respondent identifier column
    resp_id_col = _identify_respondent_id_col(headers)

    records: list[dict[str, Any]] = []
    data_row_counter = 1

    for row_idx, raw_row in enumerate(raw_rows):
        if row_idx in detected_meta_rows:
            continue

        # Check if entire row is completely empty
        if not any(c is not None and str(c).strip() for c in raw_row):
            continue

        row_dict: dict[str, Any] = {}
        for col_idx, h in enumerate(headers):
            val = raw_row[col_idx] if col_idx < len(raw_row) else ""
            row_dict[h] = "" if val is None else str(val).strip()

        # Ensure respondent ID exists or assign deterministic fallback
        if not row_dict.get(resp_id_col):
            row_dict[resp_id_col] = f"RESP-{data_row_counter:04d}"

        # Attach internal physical row index for audit reporting
        row_dict["_physical_row"] = row_idx + 1  # 1-based index in file
        records.append(row_dict)
        data_row_counter += 1

    return headers, records, resp_id_col


def _clean_headers(raw_header: list[Any]) -> list[str]:
    """Clean and deduplicate column header names."""
    headers: list[str] = []
    seen: dict[str, int] = {}

    for idx, cell in enumerate(raw_header):
        name = str(cell).strip() if cell is not None else ""
        if not name:
            name = f"COL_{idx + 1}"
        
        # Deduplicate identical header names by appending _2, _3
        lower_name = name.lower()
        if lower_name in seen:
            seen[lower_name] += 1
            name = f"{name}_{seen[lower_name]}"
        else:
            seen[lower_name] = 1

        headers.append(name)

    return headers


def _identify_respondent_id_col(headers: list[str]) -> str:
    """Identify the primary respondent key column from headers."""
    lower_map = {h.lower(): h for h in headers}

    for candidate in COMMON_ID_COLUMNS:
        if candidate in lower_map:
            return lower_map[candidate]

    # Check partial match
    for lower_h, orig_h in lower_map.items():
        if "responseid" in lower_h or "respondentid" in lower_h or "resp_id" in lower_h:
            return orig_h

    # If first column has 'id'
    if headers and "id" in headers[0].lower():
        return headers[0]

    # Fallback to first column or synthesize
    return headers[0] if headers else "RespondentID"
