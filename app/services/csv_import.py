import csv
import hashlib
import io
from dataclasses import dataclass
from typing import Any
from app.config import settings
from app.services.validation import DATASET_REQUIRED_COLUMNS

@dataclass
class ParsedCSVRow:
    line_number: int  # 1-based line number in CSV file (line 1 is header)
    data: dict[str, Any]
    raw_json: str

@dataclass
class CSVExtractionResult:
    is_valid: bool
    file_hash: str
    error_message: str | None
    headers: list[str]
    rows: list[ParsedCSVRow]
    total_raw_rows: int

def compute_file_hash(content: bytes) -> str:
    """Calculate SHA-256 hash of raw file content."""
    return hashlib.sha256(content).hexdigest()

def decode_csv_bytes(content: bytes) -> tuple[str | None, str | None]:
    """
    Safely decode CSV bytes using UTF-8-sig, UTF-8, or latin-1.
    Returns:
        (decoded_text, error_message)
    """
    if not content or len(content.strip()) == 0:
        return (None, "Uploaded CSV file is completely empty.")
    
    encodings_to_try = ["utf-8-sig", "utf-8", "latin-1", "cp1252"]
    for enc in encodings_to_try:
        try:
            text = content.decode(enc)
            return (text, None)
        except (UnicodeDecodeError, UnicodeError):
            continue
            
    return (None, "Failed to decode CSV file. Please ensure the file is encoded in valid UTF-8.")

def parse_and_validate_csv(
    content: bytes,
    filename: str,
    dataset_type: str
) -> CSVExtractionResult:
    """
    Safely read, validate, and parse an uploaded CSV file.
    Does not crash on malformed lines or unsupported formatting.
    """
    file_hash = compute_file_hash(content)

    # 1. Size check
    if len(content) > settings.UPLOAD_MAX_SIZE_BYTES:
        max_mb = settings.UPLOAD_MAX_SIZE_BYTES / (1024 * 1024)
        return CSVExtractionResult(
            is_valid=False,
            file_hash=file_hash,
            error_message=f"File size exceeds maximum allowed limit of {max_mb:.0f} MB.",
            headers=[],
            rows=[],
            total_raw_rows=0
        )

    # 2. Extension check
    if not filename.lower().endswith(".csv"):
        return CSVExtractionResult(
            is_valid=False,
            file_hash=file_hash,
            error_message="Invalid file format. Only .csv files are supported.",
            headers=[],
            rows=[],
            total_raw_rows=0
        )

    # 3. Dataset check
    if dataset_type not in DATASET_REQUIRED_COLUMNS:
        supported = ", ".join(DATASET_REQUIRED_COLUMNS.keys())
        return CSVExtractionResult(
            is_valid=False,
            file_hash=file_hash,
            error_message=f"Unsupported dataset type '{dataset_type}'. Supported datasets: {supported}.",
            headers=[],
            rows=[],
            total_raw_rows=0
        )

    # 4. Decode content
    text, decode_err = decode_csv_bytes(content)
    if decode_err:
        return CSVExtractionResult(
            is_valid=False,
            file_hash=file_hash,
            error_message=decode_err,
            headers=[],
            rows=[],
            total_raw_rows=0
        )

    # 5. Parse CSV structure
    try:
        csv_file = io.StringIO(text, newline="")
        reader = csv.reader(csv_file)
        
        # Read header row
        try:
            raw_headers = next(reader)
        except StopIteration:
            return CSVExtractionResult(
                is_valid=False,
                file_hash=file_hash,
                error_message="Uploaded CSV file contains no headers or records.",
                headers=[],
                rows=[],
                total_raw_rows=0
            )

        # Normalize headers
        normalized_headers = [h.strip().lower() for h in raw_headers]
        
        # Check for blank header row
        if not any(normalized_headers):
            return CSVExtractionResult(
                is_valid=False,
                file_hash=file_hash,
                error_message="CSV header row is empty or contains only whitespace.",
                headers=[],
                rows=[],
                total_raw_rows=0
            )

        # Check for duplicate headers
        seen_headers = set()
        for h in normalized_headers:
            if h in seen_headers:
                return CSVExtractionResult(
                    is_valid=False,
                    file_hash=file_hash,
                    error_message=f"CSV header row contains duplicate column: '{h}'.",
                    headers=normalized_headers,
                    rows=[],
                    total_raw_rows=0
                )
            if h:
                seen_headers.add(h)

        # Check for missing required columns
        required_cols = DATASET_REQUIRED_COLUMNS[dataset_type]
        missing_cols = [col for col in required_cols if col not in normalized_headers]
        if missing_cols:
            missing_str = ", ".join(missing_cols)
            return CSVExtractionResult(
                is_valid=False,
                file_hash=file_hash,
                error_message=f"CSV file is missing required column(s) for '{dataset_type}': {missing_str}.",
                headers=normalized_headers,
                rows=[],
                total_raw_rows=0
            )

        # csv.reader.line_num counts physical lines. Capture each record's starting
        # line so quoted fields with embedded newlines do not shift later diagnostics.
        parsed_rows: list[ParsedCSVRow] = []

        import json
        while True:
            row_start_line = reader.line_num + 1
            try:
                row_fields = next(reader)
            except StopIteration:
                break

            if not row_fields or all(f.strip() == "" for f in row_fields):
                continue

            # Reject structurally malformed records rather than dropping extra cells or
            # padding omitted CSV columns. Blank values in existing columns are validated later.
            if len(row_fields) != len(normalized_headers):
                return CSVExtractionResult(
                    is_valid=False,
                    file_hash=file_hash,
                    error_message=(
                        f"Malformed CSV row starting at physical line {row_start_line}: "
                        f"expected {len(normalized_headers)} fields but found {len(row_fields)}."
                    ),
                    headers=normalized_headers,
                    rows=[],
                    total_raw_rows=0
                )

            row_dict: dict[str, Any] = dict(zip(normalized_headers, row_fields))
            raw_json = json.dumps(row_dict, default=str)
            parsed_rows.append(ParsedCSVRow(
                line_number=row_start_line,
                data=row_dict,
                raw_json=raw_json
            ))

        if not parsed_rows:
            return CSVExtractionResult(
                is_valid=False,
                file_hash=file_hash,
                error_message="CSV file contains headers but no data rows.",
                headers=normalized_headers,
                rows=[],
                total_raw_rows=0
            )

        return CSVExtractionResult(
            is_valid=True,
            file_hash=file_hash,
            error_message=None,
            headers=normalized_headers,
            rows=parsed_rows,
            total_raw_rows=len(parsed_rows)
        )

    except csv.Error as e:
        return CSVExtractionResult(
            is_valid=False,
            file_hash=file_hash,
            error_message=f"Malformed CSV formatting: {str(e)}",
            headers=[],
            rows=[],
            total_raw_rows=0
        )
    except Exception:
        return CSVExtractionResult(
            is_valid=False,
            file_hash=file_hash,
            error_message="An unexpected error occurred while parsing the CSV file.",
            headers=[],
            rows=[],
            total_raw_rows=0
        )
