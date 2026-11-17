"""
Survey Raw Respondent Data Parser.
Supports CSV and multi-sheet XLSX formats.
Provides sheet inspection, intelligent header-row detection (handling survey-platform metadata rows),
interactive dataset preview, and full original-data preservation.
"""

import csv
import io
import json
import logging
import re
import time
from typing import Any
import openpyxl
from app.services.survey_upload_limits import (
    MAX_CSV_CELLS, MAX_CSV_COLUMNS, MAX_EXCEL_SECONDS, MAX_ROWS,
    SurveyUploadLimit, check_excel_time, validate_xlsx,
)

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


def read_raw_rows(content: bytes, filename: str, sheet_name: str | None = None, max_rows: int | None = None, *, source_lines: list[int] | None = None) -> list[list[Any]]:
    """Read raw matrix of rows from CSV or XLSX without discarding anything."""
    lower = filename.lower()
    rows: list[list[Any]] = []

    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        deadline = time.monotonic() + MAX_EXCEL_SECONDS
        validate_xlsx(content, deadline)
        stream = io.BytesIO(content)
        # Preserve formula text from source instead of dropping formulas without cached values.
        wb = openpyxl.load_workbook(stream, read_only=True, data_only=False)
        try:
            if sheet_name and sheet_name not in wb.sheetnames:
                raise DatasetSelectionError("Unknown worksheet.")
            sheet = wb[sheet_name] if sheet_name else wb.worksheets[0]
            sheet.reset_dimensions()
            for idx, row in enumerate(sheet.iter_rows(values_only=True)):
                check_excel_time(deadline)
                if max_rows and idx >= max_rows:
                    break
                rows.append(list(row))
                if source_lines is not None:
                    source_lines.append(idx + 1)
        finally:
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

        reader = csv.reader(io.StringIO(decoded_text, newline=""), strict=True)
        first_line = 1
        cells = 0
        for idx, row in enumerate(reader):
            if max_rows and idx >= max_rows:
                break
            cells += len(row)
            if idx >= MAX_ROWS:
                raise SurveyUploadLimit(f"CSV exceeds the {MAX_ROWS:,} row processing limit.")
            if len(row) > MAX_CSV_COLUMNS:
                raise SurveyUploadLimit(
                    f"Source row {first_line:,} has {len(row):,} columns. "
                    f"CSV supports up to {MAX_CSV_COLUMNS:,} columns."
                )
            if cells > MAX_CSV_CELLS:
                raise SurveyUploadLimit(f"CSV exceeds the {MAX_CSV_CELLS:,} cell processing limit.")
            if any("\x00" in value for value in row):
                raise DatasetSelectionError(f"Source row {first_line} contains an unsupported NUL character.")
            rows.append(row)
            if source_lines is not None:
                source_lines.append(first_line)
            first_line = reader.line_num + 1

    return rows


class DatasetSelectionError(ValueError):
    """The selected worksheet or header cannot safely be used as a dataset."""


def _nonblank(value):
    return value is not None and bool(str(value).strip())


def _normalized_header(value):
    return re.sub(r"[ _-]+", "", str(value).strip().casefold())


def _import_id(value):
    if not _nonblank(value):
        return None
    try:
        data = json.loads(str(value))
    except (ValueError, RecursionError):
        return None
    if isinstance(data, dict) and isinstance(data.get("ImportId"), str):
        return data["ImportId"].strip()
    return None


def _platform_metadata(rows, header_index):
    """Recognize a conventional Qualtrics header, never a lone JSON answer."""
    if header_index >= len(rows):
        return {}
    names = [_normalized_header(v) for v in rows[header_index]]
    if "responseid" not in names:
        return {}
    id_index = names.index("responseid")
    for i in range(header_index + 1, min(header_index + 3, len(rows))):
        row = rows[i]
        ids = [_import_id(v) for v in row]
        if len(row) != len(names) or sum(bool(v) for v in ids) < 2:
            continue
        if (ids[id_index] or "").casefold() not in {"_recordid", "responseid", "response_id"}:
            continue
        if not all(iid for cell, iid in zip(row, ids) if _nonblank(cell)):
            continue
        if i == header_index + 1:
            return {i: "qualtrics_import_ids"}
        labels = rows[header_index + 1]
        if len(labels) == len(names) and _normalized_header(labels[id_index]) == "responseid":
            return {header_index + 1: "qualtrics_question_labels", i: "qualtrics_import_ids"}
    return {}


def detect_header_and_metadata(rows: list[list[Any]]) -> dict[str, Any]:
    """Recommend the first tabular row. Never rank later respondent rows above it."""
    nonempty = [(i, [str(v).strip() for v in row if _nonblank(v)]) for i, row in enumerate(rows[:10])]
    first = next(((i, cells) for i, cells in nonempty if len(cells) >= 2), None)
    if first is None:
        first = next(((i, cells) for i, cells in nonempty if cells), (0, []))
    recommended, cells = first
    normalized = [_normalized_header(v) for v in cells]
    common = {_normalized_header(v) for v in (*COMMON_ID_COLUMNS, *COMMON_START_TIME_COLS,
                                              *COMMON_END_TIME_COLS, *COMMON_DURATION_COLS)}
    identifiable = any(v in common for v in normalized) or any(
        re.fullmatch(r"(?:[qsdoe]+\d+[\w.]*|q[._][\w ./-]+)", cell, re.I) for cell in cells
    )
    ambiguous = bool(cells) and (len(set(normalized)) != len(normalized) or not identifiable)
    metadata = _platform_metadata(rows, recommended)
    return {"recommended_header_row": recommended, "metadata_rows": list(metadata),
            "header_confirmation_required": ambiguous}


def _intake(content, filename, sheet_name, header_row_index, metadata_rows=None, *, preview=False, record_limit=None):
    source_lines: list[int] = []
    rows = read_raw_rows(content, filename, sheet_name, source_lines=source_lines)
    meta = detect_header_and_metadata(rows)
    confirmation_required = header_row_index is None and meta["header_confirmation_required"]
    if confirmation_required and not preview:
        raise DatasetSelectionError("Header is ambiguous. Select the header row explicitly and retry.")
    if header_row_index is None:
        header_row_index = meta["recommended_header_row"]
    if rows and not 0 <= header_row_index < len(rows):
        raise DatasetSelectionError("Header row is outside the dataset.")
    source_header = rows[header_row_index] if rows else []
    excluded = _platform_metadata(rows, header_row_index)
    if metadata_rows is not None:
        excluded = {i: "explicit_metadata_exclusion" for i in metadata_rows}
    metadata = sorted(i for i in excluded if header_row_index < i < len(rows))
    excluded_rows = []
    records = []
    respondent_count = 0
    blank = 0
    intake_error = None
    headers = _clean_headers(source_header)
    is_csv = filename.lower().endswith(".csv")
    for index, row in enumerate(rows):
        if index <= header_row_index:
            reason = "header" if index == header_row_index else "preamble"
        elif index in excluded:
            reason = excluded[index]
        elif not any(_nonblank(value) for value in row):
            reason = "blank"
            blank += 1
        else:
            if len(row) > len(headers):
                error = f"Source row {source_lines[index]} has more values than the selected header."
                if not preview:
                    raise DatasetSelectionError(error)
                intake_error = error
                reason = "more_values_than_header"
            else:
                respondent_count += 1
                if is_csv and respondent_count * len(headers) > MAX_CSV_CELLS:
                    raise SurveyUploadLimit(
                        f"CSV exceeds the {MAX_CSV_CELLS:,} cell processing limit after aligning rows with the header."
                    )
                if record_limit is None or len(records) < record_limit:
                    record = {h: ("" if value is None else str(value)) for h, value in zip(headers, row)}
                    record.update({h: "" for h in headers[len(row):]})
                    record["_physical_row"] = source_lines[index]
                    records.append(record)
                continue
        excluded_rows.append({"row_index": index, "source_row": source_lines[index], "reason": reason})
    source_columns = [{"column": column, "source_header": "" if label is None else str(label)}
                      for column, label in zip(headers, source_header)]
    accounting = {"source_rows": len(rows), "preamble_rows": min(header_row_index, len(rows)),
                  "header_rows": int(bool(rows)), "metadata_rows": len(metadata),
                  "blank_rows": blank, "respondents": respondent_count}
    if intake_error:
        accounting["malformed_rows"] = sum(row["reason"] == "more_values_than_header" for row in excluded_rows)
    return {
        "columns": headers, "records": records, "metadata_rows": metadata, "excluded_rows": excluded_rows,
        "source_columns": source_columns, "row_accounting": accounting, "header_row_index": header_row_index,
        "recommended_header_row": meta["recommended_header_row"], "intake_error": intake_error,
        "header_confirmation_required": confirmation_required,
    }


def preview_survey_data(content: bytes, filename: str, sheet_name: str | None = None,
                        header_row_index: int | None = None, sample_size: int = 8) -> dict[str, Any]:
    intake = _intake(content, filename, sheet_name, header_row_index, preview=True, record_limit=sample_size)
    sheets = get_excel_sheets(content) if filename.lower().endswith((".xlsx", ".xls")) else []
    headers = intake["columns"]
    records = intake.pop("records")
    samples = [{h: r[h] for h in headers} for r in records[:sample_size]]
    count = intake["row_accounting"]["respondents"]
    return {
        **intake, "filename": filename, "is_excel": bool(sheets), "sheets": sheets,
        "selected_sheet": sheet_name or (sheets[0] if sheets else None), "detected_headers": headers,
        "total_data_rows": count, "total_rows_detected": count, "total_rows": count,
        "sample_rows": samples, "preview_rows": samples, "metadata_rows_detected": len(intake["metadata_rows"]),
    }


def parse_respondent_headers(content, filename, sheet_name=None, header_row_index=None):
    """Validate the selected dataset without allocating a dictionary per respondent."""
    return _intake(content, filename, sheet_name, header_row_index, record_limit=0)["columns"]


def parse_respondent_records(content: bytes, filename: str, sheet_name: str | None = None,
                             header_row_index: int | None = None, metadata_rows: list[int] | None = None, *, with_intake=False):
    intake = _intake(content, filename, sheet_name, header_row_index, metadata_rows)
    headers, records = intake["columns"], intake.pop("records")
    parsed = headers, records, _identify_respondent_id_col(headers)
    return (*parsed, intake) if with_intake else parsed


def _clean_headers(raw_header: list[Any]) -> list[str]:
    names = [str(cell).strip() if cell is not None else "" for cell in raw_header]
    reserved = {n.casefold() for n in names if n}
    headers, used = [], {"_physical_row"}
    for i, original in enumerate(names, 1):
        name = original or f"COL_{i}"
        candidate, suffix = name, 2
        while candidate.casefold() in used or ((candidate != original) and candidate.casefold() in reserved):
            candidate = f"{name}_{suffix}"
            suffix += 1
        headers.append(candidate)
        used.add(candidate.casefold())
    return headers


def _identify_respondent_id_col(headers: list[str]) -> str | None:
    """Identify the primary respondent key column from headers."""
    lower_map = {h.lower(): h for h in headers}

    for candidate in COMMON_ID_COLUMNS:
        if candidate in lower_map:
            return lower_map[candidate]

    # Check partial match
    for lower_h, orig_h in lower_map.items():
        if "responseid" in lower_h or "respondentid" in lower_h or "resp_id" in lower_h:
            return orig_h

    return None
