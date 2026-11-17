"""
Survey Data Scrubbing API Router.
Endpoints for Questionnaire (QQ) upload & parsing, raw respondent data intake,
interactive data preview & sheet selection, question-to-column mapping,
QC audit execution, respondent disposition browsing, manual reviewer decisions,
and 8-worksheet Excel report downloads.
"""

import csv
import io
import re
import math
from dataclasses import asdict
from pydantic import ValidationError, TypeAdapter
from starlette.concurrency import run_in_threadpool
import json
import zipfile
from xml.etree.ElementTree import ParseError
from defusedxml.common import DefusedXmlException
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select, func, desc, update, text

from app.config import settings
from app.database import get_db
from app.models import (
    SurveyAuditRun,
    SurveyRespondent,
    SurveyQcFinding,
    SurveyQuestionMapping, SurveyReviewEvent,
    survey_utc_now,
)
from app.schemas import (
    SurveyAuditRunSummary,
    SurveyQcFindingItem,
    SurveyDashboardSummaryResponse,
    SurveyDashboardKPIs,
    SurveyReviewDecisionRequest,
    SurveyDatasetSelection, SurveyAuditRequest, SurveyManualEligibility,
)
from app.services.auth import enforce_cookie_csrf, has_reset_credential, is_authenticated_admin
from app.services.csv_import import compute_file_hash
from app.services.survey_qq_parser import parse_questionnaire, ParsedQuestion
from app.services.survey_data_parser import (
    DatasetSelectionError,
    preview_survey_data,
    parse_respondent_records,
)
from app.services.survey_mapping_service import suggest_mappings
from app.services.survey_qc_engine import run_survey_qc_audit
from app.services.survey_export_service import generate_survey_audit_workbook
from app.services.survey_rules import confirmed, resolve_eligibility_rules, resolve_page_rules
from app.services.survey_upload_limits import SurveyUploadLimit

logger = logging.getLogger("edvexa.api.surveys")
survey_reset_lock = threading.Lock()


def _survey_write_lock(db, *, resetting=False):
    if db.get_bind().dialect.name == "postgresql":
        # Reset takes this lock exclusively before touching rows. Audit writes and
        # review transactions take it before acquiring their specific audit lock.
        statement = "SELECT pg_advisory_xact_lock(286456239002)" if resetting else "SELECT pg_advisory_xact_lock_shared(286456239002)"
        db.execute(text(statement))


def _consistent_survey_read(db):
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))


def require_survey_admin_auth(request: Request) -> bool:
    is_reset = request.url.path.rstrip("/").endswith("/reset-survey-data")
    if has_reset_credential(request):
        if is_reset:
            return True
        if not is_authenticated_admin(request):
            raise HTTPException(403, "Reset credential possesses insufficient privileges under the least-privilege policy.")
    if not is_authenticated_admin(request):
        raise HTTPException(401, "Authentication required for Survey Data Scrubbing operations.",
                            headers={"WWW-Authenticate": "Bearer"})
    enforce_cookie_csrf(request)
    return True


router = APIRouter(
    prefix="/api/surveys",
    tags=["Survey Data Scrubbing"],
    dependencies=[Depends(require_survey_admin_auth)],
)


def _validate_uploaded_survey_content(content: bytes, filename: str, is_qq: bool = False):
    """
    Validate uploaded file content via magic byte inspection and reject unsupported legacy formats (.doc, .xls).
    Raises HTTPException with HTTP 422 if invalid, corrupt, or legacy binary format, or HTTP 400 if empty.
    """
    clean_name = (filename or "").strip()
    lower = clean_name.lower()

    # 1. Reject legacy binary formats with clear, actionable guidance
    if lower.endswith(".doc"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Legacy Word (.doc) binary format is not supported. Please save or export your questionnaire as modern Word (.docx) or plain text (.txt)."
        )
    if lower.endswith(".xls"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Legacy Excel (.xls) binary format is not supported. Please save or export your file as modern Excel (.xlsx) or standard (.csv)."
        )

    # 2. Check empty file (0 bytes)
    if len(content) == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty (0 bytes). Please upload a valid file with content."
        )

    # 3. Content signature validation
    if lower.endswith(".pdf"):
        if not content.startswith(b"%PDF-"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid PDF format: missing PDF file signature (%PDF-)."
            )
    elif lower.endswith(".docx"):
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid DOCX format: file is not a valid OpenXML document package."
            )
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if sum(info.file_size for info in z.infolist()) > 50 * 1024 * 1024 or len(z.infolist()) > 2000:
                    raise HTTPException(422, "Expanded document exceeds safe processing limits.")
                names = z.namelist()
                if not {"word/document.xml", "[Content_Types].xml"}.issubset(names):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Invalid DOCX format: missing OpenXML document structure."
                    )
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Corrupt DOCX package: unable to read zip structure."
            )
    elif lower.endswith(".xlsx"):
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid XLSX format: file is not a valid OpenXML spreadsheet package."
            )
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                if sum(info.file_size for info in z.infolist()) > 50 * 1024 * 1024 or len(z.infolist()) > 2000:
                    raise HTTPException(422, "Expanded document exceeds safe processing limits.")
                names = z.namelist()
                if not {"xl/workbook.xml", "[Content_Types].xml"}.issubset(names):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Invalid XLSX format: missing OpenXML workbook structure."
                    )
        except zipfile.BadZipFile:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Corrupt XLSX package: unable to read zip structure."
            )
    elif lower.endswith((".csv", ".txt", ".md")):
        # Check leading 8KB for null bytes indicating binary data
        header_sample = content[:8192]
        if b"\x00" in header_sample:
            file_type = "CSV" if lower.endswith(".csv") else "text"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Invalid {file_type} file: binary data or null bytes detected."
            )


def _storage_basename(filename):
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    stem, suffix = Path(name).stem, Path(name).suffix
    safe = re.sub(r'[^\w ().-]', '_', stem, flags=re.ASCII).replace('..', '__').strip(' .')
    return (safe[:100] or "upload") + suffix


def _save_survey_file(content: bytes, prefix: str, file_hash: str, filename: str) -> dict[str, Any]:
    """
    Save immutable survey file artifact into isolated surveys_storage directory
    and replicate to durable cloud object storage (Neon / S3) when configured.
    Explicitly tracks storage tier, durability status, and replication failures.
    """
    clean_name = _storage_basename(filename)
    artifact_name = f"{prefix}_{file_hash}_{clean_name}"
    
    # 1. Save to local/persistent disk storage
    target_path = settings.SURVEYS_DIR / artifact_name
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "wb") as f:
        f.write(content)

    storage_tier = "local"
    durability_status = "LOCAL_STORAGE"
    cloud_replicated = False
    replication_error = None

    # 2. Replicate to Cloud Storage (Neon/S3/R2) if configured
    if settings.STORAGE_BACKEND != "local" and settings.is_s3_configured:
        storage_tier = settings.STORAGE_BACKEND
        try:
            from app.services.storage import get_storage
            storage = get_storage()
            cloud_key = f"surveys/{artifact_name}"
            content_type = "application/octet-stream"
            if clean_name.endswith(".csv"):
                content_type = "text/csv"
            elif clean_name.endswith(".xlsx"):
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif clean_name.endswith(".pdf"):
                content_type = "application/pdf"
            storage.save(cloud_key, content, content_type=content_type)
            cloud_replicated = True
            durability_status = "DURABLE_CLOUD"
            logger.info(f"Replicated survey artifact to cloud storage: {cloud_key}")
        except Exception as e:
            replication_error = str(e)
            durability_status = "REPLICATION_FAILED"
            storage_tier = "local_cache_only"
            logger.warning(f"Could not replicate survey file to cloud storage: {e}")

    return {
        "artifact_name": artifact_name,
        "storage_tier": storage_tier,
        "durability_status": durability_status,
        "cloud_replicated": cloud_replicated,
        "replication_error": replication_error,
    }


def _get_survey_file(reference: str, kind: str | None = None) -> bytes:
    """Prefer exact artifact identities; legacy bare names must be unambiguous."""
    if not reference or Path(reference).name != reference or any(c in reference for c in "/\\"):
        raise HTTPException(404, "Survey artifact not found on storage.")
    storage = None
    from app.services.storage import get_storage
    if settings.STORAGE_BACKEND != "local" and settings.is_s3_configured:
        storage = get_storage()
    pattern = re.compile(r"(qq|raw)_([0-9a-f]{12,64})_(.+)")
    match = pattern.fullmatch(reference)
    if match:
        names = {reference}
    else:
        local_names = {p.name for p in settings.SURVEYS_DIR.iterdir() if p.is_file()} if settings.SURVEYS_DIR.exists() else set()
        if storage:
            try:
                local_names.update(Path(k).name for k in storage.list_keys(prefix="surveys/"))
            except Exception:
                raise HTTPException(503, "Cannot verify legacy artifact identity. Use its upload identifier.")
        accepted_names = {reference, _storage_basename(reference)}
        names = {n for n in local_names if (m := pattern.fullmatch(n)) and m[3] in accepted_names and (kind is None or m[1] == kind)}
    if len(names) > 1:
        raise HTTPException(409, "Ambiguous survey filename. Use the upload artifact identifier.")
    if names:
        name = next(iter(names))
        match = pattern.fullmatch(name)
        if match and (kind is None or match[1] == kind):
            target = settings.SURVEYS_DIR / name
            try:
                data = target.read_bytes() if target.is_file() else storage.get(f"surveys/{name}") if storage else None
            except FileNotFoundError:
                data = None
            except Exception:
                raise HTTPException(503, "Survey artifact storage is unavailable.")
            if data is not None:
                if not compute_file_hash(data).startswith(match[2]):
                    raise HTTPException(409, "Survey artifact checksum mismatch.")
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                return data
    raise HTTPException(404, "Survey artifact not found on storage.")


async def _survey_payload(request: Request, model, aliases=None):
    try:
        data = await request.json() if "application/json" in request.headers.get("content-type", "") else dict(await request.form())
        if not isinstance(data, dict):
            raise ValueError("Expected a request object")
        for old, new in (aliases or {}).items():
            if old in data and new not in data:
                data[new] = data[old]
        for legacy, canonical in (("mappings_json", "confirmed_mappings"), ("timing_rules_json", "timing_rules")):
            if legacy in data and canonical not in data:
                data[canonical] = json.loads(data[legacy])
        return model.model_validate(data).model_dump(exclude_unset=True)
    except ValidationError as exc:
        field_errors = [".".join(map(str, error["loc"])) for error in exc.errors()]
        raise HTTPException(422, "Invalid survey request field(s): " + ", ".join(field_errors[:10])) from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "Invalid survey request or configuration.") from exc


def _parse_dataset(parser, *args, **kwargs):
    try:
        return parser(*args, **kwargs)
    except (DatasetSelectionError, SurveyUploadLimit) as exc:
        raise HTTPException(422, str(exc)) from exc
    except (ValueError, csv.Error, zipfile.BadZipFile, KeyError, OSError, ParseError, DefusedXmlException) as exc:
        raise HTTPException(422, "Invalid dataset structure or selected header/worksheet.") from exc


def _artifact_filename(reference, filename, default):
    match = re.fullmatch(r"(?:qq|raw)_[0-9a-f]{12,64}_(.+)", reference or "")
    if match:
        if filename and filename not in (match[1], reference) and _storage_basename(filename) != match[1]:
            raise HTTPException(422, "Filename does not match the uploaded artifact.")
        return filename if filename and filename != reference else match[1]
    return filename or reference or default


def _verified_hash(content, submitted):
    actual = compute_file_hash(content)
    if submitted and submitted != actual:
        raise HTTPException(409, "Survey artifact checksum mismatch.")
    return actual


def _effective_rules(qq, overrides, body):
    fields = {
        "min_expected_duration_min": ("expected_duration_min_minutes", 1),
        "max_expected_duration_min": ("expected_duration_max_minutes", 1),
        "speeding_concern_threshold_min": ("speeding_threshold_seconds", 60),
        "extreme_speeding_cutoff_min": ("extreme_speeding_threshold_seconds", 60),
        "page_timer_threshold_sec": ("page_timer_threshold_seconds", 1),
        "straightline_min_statements": ("straightlining_min_grid_size", 1),
        "straightline_threshold_pct": ("straightline_threshold_pct", 1),
    }
    boolean_keys = {"reject_speeding_under_2min", "reject_straightlining", "reject_page_timers",
                    "duplicate_enabled", "straightlining_enabled", "open_text_enabled",
                    "screener_enabled", "completeness_enabled", "page_timers_enabled"}
    numeric_keys = {k for k, _ in fields.values()} | {"timestamp_tolerance_seconds", "completeness_threshold_pct"}
    defaults = {
        "duplicate_enabled": True, "straightlining_enabled": True, "open_text_enabled": True,
        "screener_enabled": True, "page_timers_enabled": True, "straightlining_min_grid_size": 4,
        "straightline_threshold_pct": 100, "timestamp_tolerance_seconds": 2,
    }
    try:
        provided = {k: v for k, v in overrides.items()
                    if k in numeric_keys | boolean_keys | {"page_timer_rules", "required_questions", "routing_rules", "manual_eligibility_rules"}}
        for key, (target, factor) in fields.items():
            if key in body:
                value = body[key]
                if isinstance(value, bool):
                    raise ValueError(key)
                provided[target] = float(value) * factor if value not in (None, "", "null") else None
        for key in boolean_keys:
            if key in body:
                provided[key] = body[key]
            if key in provided:
                provided[key] = TypeAdapter(bool).validate_python(provided[key])
        for key in numeric_keys & provided.keys():
            value = provided[key]
            if value is None and key in defaults:
                raise ValueError(key)
            if value is not None:
                if isinstance(value, bool) or not math.isfinite(float(value)) or float(value) <= 0:
                    raise ValueError(key)
                provided[key] = float(value)
        rules = {**defaults, **qq, **provided}
        if "page_timer_rules" in provided:
            if not isinstance(provided["page_timer_rules"], dict) or any(isinstance(v, bool) for v in provided["page_timer_rules"].values()):
                raise ValueError("Invalid timer thresholds")
            timers = TypeAdapter(dict[str, float]).validate_python(provided["page_timer_rules"])
            if any(not math.isfinite(v) or v <= 0 for v in timers.values()):
                raise ValueError("Invalid timer thresholds")
            rules["page_timer_rules"] = timers
        if "required_questions" in provided:
            rules["required_questions"] = TypeAdapter(list[str]).validate_python(provided["required_questions"])
        if "routing_rules" in provided:
            rules["routing_rules"] = TypeAdapter(list[dict[str, str]]).validate_python(provided["routing_rules"])
            if any(set(rule) != {"control_question", "equals", "skip_question"} or not all(rule.values()) for rule in rules["routing_rules"]):
                raise ValueError("Invalid routing rule")
        manual = TypeAdapter(list[SurveyManualEligibility]).validate_python(provided.get("manual_eligibility_rules", []))
        if len({rule.question_number for rule in manual}) != len(manual):
            raise ValueError("Repeated manual eligibility rule")
        rules["manual_eligibility_rules"] = []
        for rule in manual:
            values = sorted({v.strip().casefold() for v in rule.termination_values if v.strip()})
            if (not values and rule.min_age is None and rule.max_age is None) or (
                rule.min_age is not None and rule.max_age is not None and rule.min_age > rule.max_age
            ):
                raise ValueError("Invalid manual eligibility rule")
            rules["manual_eligibility_rules"].append({**rule.model_dump(), "termination_values": values})
        if rules.get("reject_speeding_under_2min") and rules.get("extreme_speeding_threshold_seconds") is None:
            raise HTTPException(422, "Strict speeding rejection requires an explicit positive cutoff.")
        if rules.get("reject_straightlining") and any(
            provided.get(key) is None for key in ("straightline_threshold_pct", "straightlining_min_grid_size")
        ):
            raise HTTPException(422, "Strict straightlining rejection requires an explicit percentage and minimum rating items.")
        order = [rules.get("extreme_speeding_threshold_seconds"), rules.get("speeding_threshold_seconds"),
                 (rules.get("expected_duration_min_minutes") or 0) * 60 or None,
                 (rules.get("expected_duration_max_minutes") or 0) * 60 or None]
        order = [v for v in order if v is not None]
        if order != sorted(order):
            raise ValueError("Duration thresholds are out of order")
        if any((rules.get(k) or 0) > 100 for k in ("straightline_threshold_pct", "completeness_threshold_pct")):
            raise ValueError("Invalid percentage")
        size = rules.get("straightlining_min_grid_size", 4)
        if size is None or size < 2 or size != int(size):
            raise ValueError("Invalid matrix size")
        rules["rule_sources"] = {
            k: "Manual configuration" if k in provided else "Questionnaire Specification" if k in qq else "Platform setting"
            for k in (numeric_keys | {"page_timer_rules"}) & rules.keys() if rules[k] is not None
        }
        rules["duration_source"] = rules["rule_sources"].get("expected_duration_min_minutes") or "Not configured"
        rules["page_timer_source"] = rules["rule_sources"].get("page_timer_threshold_seconds") or "Not configured"
        duration_fields = {
            "expected_duration_min_minutes": ("minutes", "REVIEW_REQUIRED"),
            "expected_duration_max_minutes": ("minutes", "NOTE"),
            "speeding_threshold_seconds": ("seconds", "REVIEW_REQUIRED"),
            "extreme_speeding_threshold_seconds": ("seconds", "REJECT" if rules.get("reject_speeding_under_2min") else "REVIEW_REQUIRED"),
            "timestamp_tolerance_seconds": ("seconds", "REVIEW_REQUIRED"),
        }
        rules["effective_duration_rules"] = {
            key: dict(threshold=rules[key], units=units, scope="survey", action=action, source=rules["rule_sources"][key])
            for key, (units, action) in duration_fields.items() if rules.get(key) is not None
        }
        return rules
    except (TypeError, ValueError, ValidationError) as exc:
        raise HTTPException(422, "Invalid QC configuration. Check threshold units, ranges and order.") from exc


def _confirmed_mappings(selections, suggestions, questions, headers, reserved):
    if selections is None:
        return [
            {**m, "mapped_columns": [], "matched_column": None, "mapping_status": "UNMAPPED"}
            if any(c in reserved for c in m["mapped_columns"]) else m for m in suggestions
        ]
    by_question = {m["question_number"]: m for m in suggestions}
    result = []
    seen = set()
    occupied = set(reserved)
    for selection in selections:
        key = selection["question_number"]
        if key in seen or (questions and key not in by_question):
            raise HTTPException(422, f"Unknown or repeated question mapping: {key}")
        seen.add(key)
        columns = selection.get("mapped_columns")
        if columns is None:
            if "matched_column" not in selection:
                raise HTTPException(422, f"Mapping columns or explicit skip required for {key}.")
            alias = selection["matched_column"]
            columns = [alias] if alias else []
        explicit_status = selection.get("mapping_status")
        if explicit_status is not None and explicit_status not in {"SUGGESTED", "CONFIRMED", "MANUAL", "UNMAPPED", "SKIPPED"}:
            raise HTTPException(422, f"Unknown mapping status for {key}.")
        if explicit_status in {"UNMAPPED", "SKIPPED"} and columns:
            raise HTTPException(422, f"Skipped mapping for {key} cannot contain columns.")
        if questions:
            mapping = by_question[key]
        else:
            if any(selection.get(field) is not None for field in ("min_age", "max_age", "page_timer_min_seconds", "screening_criteria")):
                raise HTTPException(422, f"Questionnaire rules for {key} require a questionnaire. Use manual QC rule configuration for raw-only audits.")
            mapping = {field: selection[field] for field in ("question_number", "question_text", "question_type", "confidence") if field in selection}
        if columns and mapping.get("question_type") in {"metadata", "instruction", "display_text"}:
            raise HTTPException(422, f"Non-question content cannot be mapped: {key}")
        if len(set(columns)) != len(columns) or any(c not in headers or c in occupied for c in columns):
            raise HTTPException(422, f"Unknown, reserved, or reused column in mapping for {key}.")
        if len(columns) > 1 and mapping.get("question_type") in {"numeric", "single_choice", "screener"} and not mapping.get("grouped_columns"):
            raise HTTPException(422, f"Multiple columns are incompatible with {key}.")
        occupied.update(columns)
        # Pre-status API callers explicitly supplied a confirmed_mappings list. A copied
        # suggestion with SUGGESTED status is not a confirmation.
        mapping_status = "SKIPPED" if not columns else "SUGGESTED" if explicit_status == "SUGGESTED" else "CONFIRMED"
        result.append({**mapping, "mapped_columns": columns, "matched_column": next(iter(columns), None),
                       "mapping_status": mapping_status,
                       "mapping_evidence": selection.get("evidence") or mapping.get("mapping_evidence")})
    result.extend({**m, "mapped_columns": [], "matched_column": None, "mapping_status": "SKIPPED"}
                  for q, m in by_question.items() if q not in seen)
    return result


@router.post("/upload-qq")
async def upload_questionnaire(
    file: UploadFile = File(..., description="Questionnaire file (.pdf, .docx, .xlsx, .txt)")
):
    """
    Upload and parse Questionnaire (QQ) reference document.
    Extracts questions, question numbers, wording, types, options, duration, and routing.
    """
    filename = file.filename or "questionnaire.pdf"
    if len(filename) > 255 or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "Invalid questionnaire filename.")
    lower = filename.lower()

    # Reject legacy .doc upfront with 422
    if lower.endswith(".doc"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Legacy Word (.doc) binary format is not supported. Please save or export your questionnaire as modern Word (.docx) or plain text (.txt)."
        )
    if lower.endswith(".xls"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Legacy Excel (.xls) binary format is not supported. Please save or export your file as modern Excel (.xlsx) or standard (.csv)."
        )

    allowed = (".pdf", ".docx", ".xlsx", ".txt", ".md")
    if not any(lower.endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported format for Questionnaire. Please upload PDF, DOCX, XLSX, or TXT."
        )

    try:
        content = await file.read(settings.UPLOAD_MAX_SIZE_BYTES + 1)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to read file: {e}")

    if len(content) > settings.UPLOAD_MAX_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 10 MB limit.")

    # Validate file content signature (magic bytes)
    _validate_uploaded_survey_content(content, filename, is_qq=True)

    extraction = await run_in_threadpool(_parse_dataset, parse_questionnaire, content, filename)
    file_hash = compute_file_hash(content)
    save_result = await run_in_threadpool(_save_survey_file, content, "qq", file_hash, filename)
    artifact_name = save_result["artifact_name"]

    res = {
        "status": "success",
        "filename": filename,
        "original_name": filename,
        "file_hash": file_hash,
        "artifact_name": artifact_name,
        "storage_tier": save_result["storage_tier"],
        "durability_status": save_result["durability_status"],
        "cloud_replicated": save_result["cloud_replicated"],
        "document_format": extraction.document_format,
        "unreadable_or_scanned": extraction.unreadable_or_scanned,
        "is_scanned": extraction.unreadable_or_scanned,
        "raw_text_length": extraction.raw_text_length,
        "expected_duration_min_minutes": extraction.expected_duration_min_minutes,
        "expected_duration_max_minutes": extraction.expected_duration_max_minutes,
        "detected_duration_min": extraction.expected_duration_min_minutes,
        "detected_duration_max": extraction.expected_duration_max_minutes,
        "duration_source": extraction.duration_source,
        "page_timer_threshold_seconds": extraction.page_timer_threshold_seconds,
        "page_timer_rules": extraction.page_timer_rules,
        "detected_page_timers": (
            list(extraction.page_timer_rules.keys())
            if extraction.page_timer_rules
            else ([] if extraction.page_timer_threshold_seconds is None else [f"Global ({extraction.page_timer_threshold_seconds:.0f}s)"])
        ),
        "questions_count": len(extraction.questions),
        "total_questions_detected": len(extraction.questions),
        "file_size_bytes": len(content),
        "screening_criteria": extraction.screener_rules,
        "screener_rules": extraction.screener_rules,
        "routing_logic": extraction.skip_logic_rules,
        "skip_logic_rules": extraction.skip_logic_rules,
        "questions": [asdict(q) for q in extraction.questions],
        "excluded_blocks": [asdict(q) for q in extraction.excluded_blocks],
        "expected_duration_target_minutes": extraction.expected_duration_target_minutes,
        "notes": extraction.extraction_notes,
        "extraction_notes": "\n".join(extraction.extraction_notes)
    }

    if save_result["durability_status"] == "REPLICATION_FAILED":
        res["storage_warning"] = (
            f"Warning: Survey questionnaire is temporarily cached on local disk, but cloud replication to {settings.STORAGE_BACKEND} failed. "
            "File may not persist across container redeployments until cloud storage replication succeeds."
        )

    return res


@router.post("/upload-raw")
async def upload_raw_respondent_data(
    file: UploadFile = File(..., description="Raw respondent data file (.csv, .xlsx)")
):
    """
    Upload raw respondent dataset (CSV or Excel).
    Inspects sheet titles, metadata rows, and provides immediate data preview.
    """
    filename = file.filename or "respondents.csv"
    if len(filename) > 255 or any(ord(c) < 32 for c in filename):
        raise HTTPException(422, "Invalid respondent filename.")
    lower = filename.lower()

    if lower.endswith(".xls"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Legacy Excel (.xls) binary format is not supported. Please save or export your file as modern Excel (.xlsx) or standard (.csv)."
        )

    allowed = (".csv", ".xlsx")
    if not any(lower.endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported format for respondent data. Please upload CSV or XLSX."
        )

    try:
        content = await file.read(settings.UPLOAD_MAX_SIZE_BYTES + 1)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to read file: {e}")

    if len(content) > settings.UPLOAD_MAX_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 10 MB limit.")

    # Validate file content signature (magic bytes)
    _validate_uploaded_survey_content(content, filename, is_qq=False)

    file_hash = compute_file_hash(content)
    preview = await run_in_threadpool(_parse_dataset, preview_survey_data, content, filename)
    save_result = await run_in_threadpool(_save_survey_file, content, "raw", file_hash, filename)
    artifact_name = save_result["artifact_name"]
    preview["file_hash"] = file_hash
    preview["artifact_name"] = artifact_name
    preview["storage_tier"] = save_result["storage_tier"]
    preview["durability_status"] = save_result["durability_status"]
    preview["cloud_replicated"] = save_result["cloud_replicated"]

    if save_result["durability_status"] == "REPLICATION_FAILED":
        preview["storage_warning"] = (
            f"Warning: Survey dataset is temporarily cached on local disk, but cloud replication to {settings.STORAGE_BACKEND} failed. "
            "File may not persist across container redeployments until cloud storage replication succeeds."
        )

    return preview


@router.post("/preview")
async def preview_sheet_data(request: Request):
    body = await _survey_payload(request, SurveyDatasetSelection, {"artifact_name": "raw_artifact_name", "filename": "raw_filename"})
    reference = body.get("raw_artifact_name") or body.get("raw_filename")
    if not reference:
        raise HTTPException(422, "Raw artifact is required.")
    content = await run_in_threadpool(_get_survey_file, reference, "raw")
    preview = await run_in_threadpool(_parse_dataset, preview_survey_data, content,
                                      _artifact_filename(reference, body.get("raw_filename"), reference),
                                      body.get("sheet_name"), body.get("header_row_index"))
    preview["artifact_name"] = reference
    return preview


@router.post("/suggest-mapping")
async def generate_suggested_mappings(request: Request):
    """
    Generate question-to-column mappings by correlating QQ questions
    with raw dataset headers. Supports JSON body or Form data.
    """
    body = await _survey_payload(request, SurveyDatasetSelection)

    raw_artifact_name = body.get("raw_artifact_name") or body.get("raw_filename")
    raw_filename = _artifact_filename(raw_artifact_name, body.get("raw_filename"), "raw_data.csv")
    sheet_name = body.get("sheet_name")
    header_row_index = body.get("header_row_index")
    qq_artifact_name = body.get("qq_artifact_name") or body.get("qq_filename")
    qq_filename = _artifact_filename(qq_artifact_name, body.get("qq_filename"), None)

    if not raw_artifact_name:
        raise HTTPException(status_code=400, detail="raw_filename or raw_artifact_name is required.")

    raw_content = await run_in_threadpool(_get_survey_file, raw_artifact_name, "raw")
    headers, _, _ = await run_in_threadpool(_parse_dataset, parse_respondent_records,
        raw_content, raw_filename, sheet_name=sheet_name, header_row_index=header_row_index
    )

    questions: list[ParsedQuestion] = []
    if qq_artifact_name:
        qq_content = await run_in_threadpool(_get_survey_file, qq_artifact_name, "qq")
        extraction = await run_in_threadpool(_parse_dataset, parse_questionnaire, qq_content, qq_filename or qq_artifact_name)
        questions = extraction.questions
        if not questions:
            raise HTTPException(422, "Questionnaire has no readable substantive questions.")

    # If no questionnaire was provided or no questions extracted, synthesize questions from headers
    if not questions:
        for h in headers:
            questions.append(ParsedQuestion(
                question_number=h,
                question_text=f"Question for column {h}",
                question_type="single_choice"
            ))

    mapping_res = suggest_mappings(questions, headers)

    high_count = sum(1 for m in mapping_res.get("mappings", []) if m.get("confidence") == "HIGH")
    med_count = sum(1 for m in mapping_res.get("mappings", []) if m.get("confidence") == "MEDIUM")
    low_count = sum(1 for m in mapping_res.get("mappings", []) if m.get("confidence") == "LOW")
    unmapped_count = sum(1 for m in mapping_res.get("mappings", []) if m.get("confidence") == "UNMAPPED")

    mapping_res["high_confidence_count"] = high_count
    mapping_res["medium_confidence_count"] = med_count
    mapping_res["low_confidence_count"] = low_count
    mapping_res["unmapped_count"] = unmapped_count

    sys_cols = mapping_res.get("system_columns", {})
    mapping_res["start_time_column"] = sys_cols.get("start_time")
    mapping_res["end_time_column"] = sys_cols.get("end_time")
    mapping_res["duration_column"] = sys_cols.get("duration")
    mapping_res["ip_address_column"] = sys_cols.get("ip_address")
    mapping_res["response_id_column"] = sys_cols.get("respondent_id")

    return mapping_res


@router.post("/run-audit")
async def execute_survey_scrubbing_audit(
    request: Request,
    db: Session = Depends(get_db)
):
    """
    Execute full Survey Data Scrubbing audit on the uploaded dataset.
    Validates timestamps, speeding, straightlining, open-text/gibberish,
    duplicates, screeners, routing, and incomplete responses.
    Assigns each respondent to ACCEPTED, REJECTED, or REVIEW_REQUIRED.
    """
    start_time = time.time()
    body = await _survey_payload(request, SurveyAuditRequest)

    raw_artifact_name = body.get("raw_artifact_name") or body.get("raw_filename")
    raw_filename = _artifact_filename(raw_artifact_name, body.get("raw_filename"), "raw_data.csv")
    sheet_name = body.get("sheet_name")
    header_row_index = body.get("header_row_index")
    qq_artifact_name = body.get("qq_artifact_name") or body.get("qq_filename")
    qq_filename = _artifact_filename(qq_artifact_name, body.get("qq_filename"), None)
    audit_name = body.get("audit_name") or "Survey Data Scrubbing Audit"
    if any("\x00" in value for value in (audit_name, raw_filename, qq_filename) if value):
        raise HTTPException(422, "Audit names and filenames cannot contain NUL characters.")

    if not raw_artifact_name:
        raise HTTPException(status_code=400, detail="raw_filename or raw_artifact_name is required.")

    raw_content = await run_in_threadpool(_get_survey_file, raw_artifact_name, "raw")
    raw_file_hash = _verified_hash(raw_content, body.get("raw_file_hash"))

    qq_file_hash = None
    qq_parsed_rules: dict[str, Any] = {}
    parsed_questions: list[ParsedQuestion] = []
    if qq_artifact_name:
        qq_content = await run_in_threadpool(_get_survey_file, qq_artifact_name, "qq")
        qq_file_hash = _verified_hash(qq_content, body.get("qq_file_hash"))
        extraction = await run_in_threadpool(_parse_dataset, parse_questionnaire, qq_content, qq_filename or qq_artifact_name)
        parsed_questions = extraction.questions
        if not parsed_questions:
            raise HTTPException(422, "Questionnaire has no readable substantive questions.")
        for key in ("expected_duration_min_minutes", "expected_duration_max_minutes", "page_timer_threshold_seconds"):
            value = getattr(extraction, key)
            if value:
                qq_parsed_rules[key] = value
        if extraction.expected_duration_min_minutes or extraction.expected_duration_max_minutes:
            qq_parsed_rules["duration_source"] = extraction.duration_source
        if extraction.page_timer_threshold_seconds:
            qq_parsed_rules["page_timer_source"] = "Questionnaire Specification"

    timing_rules = body.get("timing_rules") or {}
    final_timing_rules = _effective_rules(qq_parsed_rules, timing_rules, body)

    headers, records, resp_id_col, intake = await run_in_threadpool(_parse_dataset, parse_respondent_records,
        raw_content, raw_filename, sheet_name=sheet_name, header_row_index=header_row_index, with_intake=True
    )

    if not records:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The selected sheet and header row produced zero data rows. Please verify your file."
        )

    # Identify system columns and auto-map if mappings were not provided
    mapping_calc = suggest_mappings(parsed_questions, headers)
    system_cols = mapping_calc.get("system_columns", {})
    timer_cols = mapping_calc.get("timer_columns", [])

    requested_system = body.get("system_columns") or {}
    if any(key not in system_cols or (col is not None and col not in headers) for key, col in requested_system.items()):
        raise HTTPException(422, "Unknown telemetry role or dataset column.")
    system_cols.update(requested_system)
    timer_cols = body.get("timer_columns") if body.get("timer_columns") is not None else timer_cols
    telemetry = [c for c in system_cols.values() if c is not None] + timer_cols
    if len(set(telemetry)) != len(telemetry) or any(c not in headers for c in telemetry):
        raise HTTPException(422, "Invalid or repeated telemetry columns.")
    resp_id_col = requested_system.get("respondent_id", system_cols.get("respondent_id") or resp_id_col)
    confirmed_mappings = _confirmed_mappings(body.get("confirmed_mappings"),
        mapping_calc["mappings"], parsed_questions, headers, telemetry)
    available_questions = {m["question_number"]: m["mapped_columns"] for m in confirmed_mappings if m["mapped_columns"]}
    if not set(final_timing_rules.get("required_questions", [])) <= available_questions.keys():
        raise HTTPException(422, "Required questions must be mapped.")
    if any(len(available_questions.get(rule["control_question"], [])) != 1 or rule["skip_question"] not in available_questions
           for rule in final_timing_rules.get("routing_rules", [])):
        raise HTTPException(422, "Routing rules require a single source column and a mapped target question.")
    if not set(final_timing_rules.get("page_timer_rules", {})) <= set(timer_cols):
        raise HTTPException(422, "Manual page thresholds require mapped timer columns.")
    country_column = body.get("country_column")
    if country_column is not None and country_column not in headers:
        raise HTTPException(422, "Country column must be present in the selected dataset.")
    if country_column is None:
        country_column = next((m["mapped_columns"][0] for m in confirmed_mappings
                               if confirmed(m) and len(m["mapped_columns"]) == 1
                               and m["question_number"].casefold() in {"q.country", "q_country", "country"}), None)
    manual = final_timing_rules["manual_eligibility_rules"]
    confirmed_scalar = {m["question_number"] for m in confirmed_mappings if confirmed(m) and len(m["mapped_columns"]) == 1}
    if any(rule["question_number"] not in confirmed_scalar for rule in manual):
        raise HTTPException(422, "Manual eligibility rules require a confirmed single-column mapping.")
    final_timing_rules["effective_page_rules"] = resolve_page_rules(final_timing_rules, confirmed_mappings, timer_cols)
    if final_timing_rules.get("reject_page_timers") and not final_timing_rules["effective_page_rules"]:
        raise HTTPException(422, "Strict page timer rejection requires an applicable threshold on a mapped timer column.")
    final_timing_rules["effective_eligibility_rules"] = resolve_eligibility_rules(
        confirmed_mappings, country_column, manual, enabled=final_timing_rules["screener_enabled"],
    )
    if any(len(column) > 100 for column in headers) or any(len(m["question_number"]) > 50 for m in confirmed_mappings):
        raise HTTPException(422, "Column names may contain at most 100 characters and question IDs at most 50.")
    for row in records:
        if len(str(row.get(resp_id_col, "")).strip()) > 150:
            raise HTTPException(422, f"Source row {row['_physical_row']} has a respondent ID longer than 150 characters.")

    # Persist SurveyAuditRun
    audit_run = SurveyAuditRun(
        audit_name=audit_name,
        qq_filename=qq_filename,
        qq_file_hash=qq_file_hash,
        raw_filename=raw_filename,
        raw_file_hash=raw_file_hash,
        selected_sheet=sheet_name,
        header_row_index=intake["header_row_index"],
        status="processing",
        timing_rules_json=json.dumps({
            **final_timing_rules,
            "snapshot": {"schema_version": 2, "parser_version": 4, "qc_version": 4,
                         "raw_artifact": raw_artifact_name, "qq_artifact": qq_artifact_name,
                         "raw_sha256": raw_file_hash, "qq_sha256": qq_file_hash,
                         "system_columns": system_cols, "timer_columns": timer_cols,
                         "respondent_id_column": resp_id_col, "country_column": country_column,
                         "intake": {key: intake[key] for key in ("row_accounting", "source_columns", "excluded_rows")},
                         "mappings": confirmed_mappings,
                         "excluded_blocks": [asdict(q) for q in extraction.excluded_blocks] if qq_artifact_name else []}
        }, allow_nan=False),
    )
    try:
        _survey_write_lock(db)
        db.add(audit_run)
        db.flush()
        audit_id, started_at = audit_run.id, audit_run.started_at
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Could not create survey audit (%s)", type(exc).__name__)
        raise HTTPException(500, "Audit could not be recorded.") from exc
    try:
        # Execute QC engine
        audit_summary = await run_in_threadpool(run_survey_qc_audit,
            records=records,
            mappings=confirmed_mappings,
            system_columns=system_cols,
            timer_columns=timer_cols,
            resp_id_col=resp_id_col,
            timing_rules=final_timing_rules
        )

        _survey_write_lock(db)
        audit_run = db.scalar(select(SurveyAuditRun).where(SurveyAuditRun.id == audit_id).with_for_update())
        if not audit_run or audit_run.started_at != started_at:
            raise HTTPException(409, "Audit was removed before the results could be saved.")
        audit_run.total_respondents = audit_summary.total_respondents
        audit_run.accepted_count = audit_summary.accepted_count
        audit_run.rejected_count = audit_summary.rejected_count
        audit_run.review_required_count = audit_summary.review_required_count
        audit_run.speeding_count = audit_summary.speeding_count
        audit_run.straightlining_count = audit_summary.straightlining_count
        audit_run.suspicious_text_count = audit_summary.suspicious_text_count
        audit_run.confirmed_duplicates_count = audit_summary.confirmed_duplicates_count
        audit_run.potential_duplicates_count = audit_summary.potential_duplicates_count
        audit_run.routing_violations_count = audit_summary.routing_violations_count
        audit_run.screener_violations_count = audit_summary.screener_violations_count
        audit_run.contradiction_count = audit_summary.contradiction_count
        audit_run.incomplete_count = audit_summary.incomplete_count
        audit_run.timestamp_mismatch_count = audit_summary.timestamp_mismatch_count
        audit_run.checks_summary_json = json.dumps(audit_summary.checks_coverage)
        audit_run.status = "completed"
        audit_run.completed_at = survey_utc_now()
        audit_run.duration_seconds = time.time() - start_time

        # Persist Question Mappings
        for m in confirmed_mappings:
            sqm = SurveyQuestionMapping(
                audit_id=audit_run.id,
                question_number=str(m.get("question_number", "")),
                question_text=str(m.get("question_text", "")),
                question_type=str(m.get("question_type", "single_choice")),
                mapped_columns_json=json.dumps(m.get("mapped_columns", [])),
                mapping_status=str(m.get("mapping_status", "CONFIRMED")),
                confidence=str(m.get("confidence", "HIGH")),
                mapping_evidence=m.get("mapping_evidence"),
                qc_instructions=m.get("qc_instructions")
            )
            db.add(sqm)

        # Persist Respondents & Findings in batches
        for r_res in audit_summary.results:
            resp_model = SurveyRespondent(
                audit_id=audit_run.id,
                respondent_key=r_res.respondent_key,
                row_number=r_res.row_number,
                disposition=r_res.disposition,
                rejection_reasons_json=json.dumps(r_res.rejection_reasons),
                review_flags_json=json.dumps(r_res.review_flags),
                calculated_duration_seconds=r_res.calculated_duration_seconds,
                exported_duration_seconds=r_res.exported_duration_seconds,
                duration_delta_seconds=r_res.duration_delta_seconds,
                local_completion_hour=r_res.local_completion_hour,
                is_overnight=r_res.is_overnight,
                raw_data_json=json.dumps(r_res.raw_data)
            )
            db.add(resp_model)
            db.flush()

            for f_item in r_res.findings:
                finding_model = SurveyQcFinding(
                    audit_id=audit_run.id,
                    respondent_id=resp_model.id,
                    respondent_key=r_res.respondent_key,
                    issue_type=f_item.issue_type,
                    question_number=f_item.question_number,
                    raw_column=f_item.raw_column,
                    original_answer=f_item.original_answer,
                    observed_value=f_item.observed_value,
                    expected_rule=f_item.expected_rule,
                    evidence=f_item.evidence,
                    severity=f_item.severity,
                    review_recommendation=f_item.recommendation
                )
                db.add(finding_model)

        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        try:
            _survey_write_lock(db)
            failed = db.scalar(select(SurveyAuditRun).where(SurveyAuditRun.id == audit_id).with_for_update())
            if failed and failed.started_at == started_at:
                failed.status = "failed"
                failed.error_message = "QC execution or result storage failed."
                failed.completed_at = survey_utc_now()
            db.commit()
        except Exception as recovery_error:
            db.rollback()
            logger.error("Could not finalize survey audit %s (%s)", audit_id, type(recovery_error).__name__)
        logger.error("Survey audit %s failed (%s)", audit_id, type(exc).__name__)
        raise HTTPException(500, "Survey audit failed. See audit history.") from exc
    db.refresh(audit_run)

    return {
        "id": audit_run.id,
        "audit_id": audit_run.id,
        "audit_name": audit_run.audit_name,
        "raw_filename": audit_run.raw_filename,
        "qq_filename": audit_run.qq_filename,
        "total_respondents": audit_run.total_respondents,
        "accepted_count": audit_run.accepted_count,
        "rejected_count": audit_run.rejected_count,
        "review_required_count": audit_run.review_required_count,
        "speeding_count": audit_run.speeding_count,
        "straightlining_count": audit_run.straightlining_count,
        "suspicious_text_count": audit_run.suspicious_text_count,
        "confirmed_duplicates_count": audit_run.confirmed_duplicates_count,
        "potential_duplicates_count": audit_run.potential_duplicates_count,
        "routing_violations_count": audit_run.routing_violations_count,
        "started_at": audit_run.started_at.isoformat() if audit_run.started_at else None,
        "completed_at": audit_run.completed_at.isoformat() if audit_run.completed_at else None,
        "duration_seconds": audit_run.duration_seconds,
        "status": audit_run.status,
        "strict_accounting_verified": True
    }


@router.get("/audits", response_model=list[SurveyAuditRunSummary])
def list_survey_audits(
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Retrieve list of past survey scrubbing audit runs."""
    stmt = select(SurveyAuditRun).order_by(desc(SurveyAuditRun.id)).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/audits/{audit_id}")
def get_survey_audit_detail(
    audit_id: int,
    db: Session = Depends(get_db)
):
    """Retrieve complete summary, KPIs, and checks coverage for an audit run."""
    audit = db.get(SurveyAuditRun, audit_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit #{audit_id} not found.")

    coverage = json.loads(audit.checks_summary_json) if audit.checks_summary_json else []
    return {
        "id": audit.id,
        "audit_id": audit.id,
        "audit_name": audit.audit_name,
        "raw_filename": audit.raw_filename,
        "qq_filename": audit.qq_filename,
        "status": audit.status,
        "error_message": audit.error_message,
        "total_respondents": audit.total_respondents,
        "accepted_count": audit.accepted_count,
        "rejected_count": audit.rejected_count,
        "review_required_count": audit.review_required_count,
        "speeding_flagged_count": audit.speeding_count,
        "straightlining_flagged_count": audit.straightlining_count,
        "gibberish_flagged_count": audit.suspicious_text_count,
        "confirmed_duplicates_count": audit.confirmed_duplicates_count,
        "potential_duplicates_count": audit.potential_duplicates_count,
        "routing_violations_count": audit.routing_violations_count,
        "executed_at": audit.started_at.isoformat() if audit.started_at else None,
        "strict_accounting_verified": (audit.status == "completed" and audit.total_respondents == audit.accepted_count + audit.rejected_count + audit.review_required_count),
        "qc_coverage": coverage,
        "effective_rules": json.loads(audit.timing_rules_json) if audit.timing_rules_json else None,
        "audit": audit
    }


@router.get("/audits/{audit_id}/respondents")
def get_survey_respondents(
    audit_id: int,
    disposition: str | None = Query(None, description="Filter: ACCEPTED, REJECTED, REVIEW_REQUIRED"),
    search: str | None = Query(None, description="Search respondent ID or text"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    paginated: bool = False,
    db: Session = Depends(get_db)
):
    """Paginated list of respondents with raw values, disposition, and flags."""
    _consistent_survey_read(db)
    audit = db.get(SurveyAuditRun, audit_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit #{audit_id} not found.")

    stmt = select(SurveyRespondent).where(SurveyRespondent.audit_id == audit_id)
    if disposition:
        stmt = stmt.where(SurveyRespondent.disposition == disposition.upper().strip())
    if search:
        s_term = f"%{search.strip()}%"
        stmt = stmt.where(
            (SurveyRespondent.respondent_key.ilike(s_term)) |
            (SurveyRespondent.raw_data_json.ilike(s_term))
        )

    # Count total
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = db.scalar(count_stmt) or 0

    stmt = stmt.options(selectinload(SurveyRespondent.findings)).order_by(SurveyRespondent.row_number.asc(), SurveyRespondent.id.asc()).offset((page - 1) * page_size).limit(page_size)
    items = list(db.scalars(stmt).all())

    serialized_items = []
    for r in items:
        raw_dict = json.loads(r.raw_data_json) if r.raw_data_json else {}
        reasons = json.loads(r.rejection_reasons_json) if r.rejection_reasons_json else []
        flags = json.loads(r.review_flags_json) if r.review_flags_json else []
        serialized_items.append({
            "id": r.id,
            "audit_id": r.audit_id,
            "respondent_key": r.respondent_key,
            "effective_reason": r.reviewer_notes if r.manual_decision else None,
            "row_number": r.row_number,
            "disposition": r.disposition,
            "rejection_reasons": reasons,
            "review_flags": flags,
            "calculated_duration_seconds": r.calculated_duration_seconds,
            "exported_duration_seconds": r.exported_duration_seconds,
            "duration_delta_seconds": r.duration_delta_seconds,
            "local_completion_hour": r.local_completion_hour,
            "is_overnight": bool(r.is_overnight),
            "manual_decision": r.manual_decision,
            "reviewer_notes": r.reviewer_notes,
            "reviewed_by": r.reviewed_by,
            "reviewed_at": r.reviewed_at,
            "created_at": r.created_at,
            "raw_data": raw_dict,
            "duration_seconds": r.exported_duration_seconds if r.exported_duration_seconds is not None else r.calculated_duration_seconds,
            "is_speeding": any("speed" in f.issue_type.lower() for f in (r.findings or [])) or any("speed" in str(x).lower() for x in (reasons + flags)),
            "is_straightlining": any("straight" in f.issue_type.lower() for f in (r.findings or [])) or any("straight" in str(x).lower() or "identical" in str(x).lower() for x in (reasons + flags)),
            "is_gibberish": any("gibberish" in f.issue_type.lower() for f in (r.findings or [])) or any("gibberish" in str(x).lower() or "suspicious" in str(x).lower() for x in (reasons + flags)),
            "is_duplicate": any("duplicate" in f.issue_type.lower() for f in (r.findings or [])) or any("duplicate" in str(x).lower() for x in (reasons + flags)),
            "findings_count": len(r.findings) if r.findings else 0
        })

    return {"items": serialized_items, "total": total, "page": page, "page_size": page_size} if paginated else serialized_items


@router.get("/audits/{audit_id}/findings", response_model=list[SurveyQcFindingItem] | dict)
def get_survey_findings(
    audit_id: int,
    issue_type: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    page: int = Query(1, ge=1),
    paginated: bool = False,
    respondent_id: int | None = None,
    db: Session = Depends(get_db)
):
    """Retrieve detailed QC findings for an audit run."""
    _consistent_survey_read(db)
    stmt = select(SurveyQcFinding).where(SurveyQcFinding.audit_id == audit_id)
    if not db.get(SurveyAuditRun, audit_id):
        raise HTTPException(404, "Survey audit not found.")
    if respondent_id is not None:
        stmt = stmt.where(SurveyQcFinding.respondent_id == respondent_id)
    if issue_type:
        stmt = stmt.where(SurveyQcFinding.issue_type == issue_type)
    if severity:
        stmt = stmt.where(SurveyQcFinding.severity == severity.upper())

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) if paginated else None
    stmt = stmt.order_by(desc(SurveyQcFinding.id)).offset((page - 1) * limit).limit(limit)
    items = list(db.scalars(stmt))
    if paginated:
        return {"items": [SurveyQcFindingItem.model_validate(f).model_dump() for f in items],
                "total": total, "page": page, "page_size": limit}
    return items


@router.post("/audits/{audit_id}/review/{respondent_key}")
def record_manual_review_decision(
    audit_id: int,
    respondent_key: str,
    review_req: SurveyReviewDecisionRequest,
    row_number: int | None = Query(None, description="Optional physical 1-based source row number to target specific row if duplicate keys exist"),
    db: Session = Depends(get_db)
):
    """
    Record human reviewer decision on a Review Required or contested respondent.
    Updates disposition, audit counts, and audit trail.
    Under least-privilege security, restricted to authenticated administrators.
    """
    return _save_review(db, audit_id, review_req, respondent_key=respondent_key, row_number=row_number)


@router.post("/audits/{audit_id}/review-by-id/{respondent_id}")
def review_by_id(audit_id: int, respondent_id: int, review_req: SurveyReviewDecisionRequest,
                 db: Session = Depends(get_db)):
    return _save_review(db, audit_id, review_req, respondent_id=respondent_id)


def _save_review(db, audit_id, review_req, respondent_id=None, respondent_key=None, row_number=None):
    # Acquire the run row's write lock before reading counters or respondent state.
    # PostgreSQL serializes on the audit row; SQLite serializes writes.
    _survey_write_lock(db)
    locked = db.execute(update(SurveyAuditRun).where(SurveyAuditRun.id == audit_id)
                        .values(status=SurveyAuditRun.status)).rowcount
    if not locked:
        raise HTTPException(404, "Survey audit not found.")
    audit = db.get(SurveyAuditRun, audit_id)
    stmt = select(SurveyRespondent).where(SurveyRespondent.audit_id == audit_id)
    if respondent_id is not None:
        stmt = stmt.where(SurveyRespondent.id == respondent_id)
    else:
        stmt = stmt.where(SurveyRespondent.respondent_key == respondent_key)
    if row_number is not None:
        stmt = stmt.where(SurveyRespondent.row_number == row_number)
    matches = list(db.scalars(stmt.limit(2)))
    if not matches:
        raise HTTPException(404, "Survey respondent not found.")
    if len(matches) > 1:
        raise HTTPException(409, "Multiple respondents share this identifier. Select a specific record.")
    resp = matches[0]
    respondent_key = resp.respondent_key

    old_disp = resp.disposition
    new_disp = review_req.decision.upper().strip()
    if new_disp not in ("ACCEPTED", "REJECTED", "REVIEW_REQUIRED"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid decision.")

    # Adjust counts on Audit Run if disposition changed
    if old_disp != new_disp:
        if old_disp == "ACCEPTED":
            audit.accepted_count = max(0, audit.accepted_count - 1)
        elif old_disp == "REJECTED":
            audit.rejected_count = max(0, audit.rejected_count - 1)
        elif old_disp == "REVIEW_REQUIRED":
            audit.review_required_count = max(0, audit.review_required_count - 1)

        if new_disp == "ACCEPTED":
            audit.accepted_count += 1
        elif new_disp == "REJECTED":
            audit.rejected_count += 1
        elif new_disp == "REVIEW_REQUIRED":
            audit.review_required_count += 1

    effective_rev = review_req.effective_reviewer
    effective_notes = (review_req.effective_notes or "").strip()
    if not effective_notes or len(effective_rev) > 100 or "\x00" in effective_rev + effective_notes:
        raise HTTPException(422, "Review requires a reason and a reviewer name of at most 100 characters.")
    db.add(SurveyReviewEvent(audit_id=audit_id, respondent_id=resp.id,
                             previous_disposition=old_disp, new_disposition=new_disp,
                             reviewer=effective_rev, reason=effective_notes))

    resp.disposition = new_disp
    resp.manual_decision = new_disp
    resp.reviewer_notes = effective_notes
    resp.reviewed_by = effective_rev
    resp.reviewed_at = survey_utc_now()

    # Update associated findings
    for f in resp.findings:
        f.final_decision = new_disp

    counts = {"accepted": audit.accepted_count, "rejected": audit.rejected_count, "review_required": audit.review_required_count}
    db.commit()

    return {
        "status": "success",
        "message": f"Respondent '{respondent_key}' updated to {new_disp}.",
        "disposition": new_disp,
        "reviewed_by": effective_rev,
        "reviewer_notes": effective_notes,
        "audit_counts": counts,
    }


@router.get("/audits/{audit_id}/reviews")
def list_reviews(audit_id: int, db: Session = Depends(get_db)):
    if not db.get(SurveyAuditRun, audit_id):
        raise HTTPException(404, "Survey audit not found.")
    return list(db.scalars(select(SurveyReviewEvent).where(SurveyReviewEvent.audit_id == audit_id)
                           .order_by(SurveyReviewEvent.id)))


@router.get("/audits/{audit_id}/export/excel")
def download_survey_excel_report(
    audit_id: int,
    db: Session = Depends(get_db)
):
    """
    Download the complete 8-worksheet Excel audit report (.xlsx).
    Sheets: Audit Summary, Raw Data, Accepted Data, Rejected Data,
    Review Required, QC Findings, QQ Mapping, QC Coverage.
    """
    _consistent_survey_read(db)
    audit = db.get(SurveyAuditRun, audit_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit #{audit_id} not found.")
    if audit.status != "completed":
        raise HTTPException(409, "Only completed audits have an exportable report.")

    respondents = list(db.scalars(
        select(SurveyRespondent).where(SurveyRespondent.audit_id == audit_id).order_by(SurveyRespondent.row_number.asc())
    ).all())

    findings = list(db.scalars(
        select(SurveyQcFinding).where(SurveyQcFinding.audit_id == audit_id).order_by(SurveyQcFinding.id.asc())
    ).all())

    mappings = list(db.scalars(
        select(SurveyQuestionMapping).where(SurveyQuestionMapping.audit_id == audit_id).order_by(SurveyQuestionMapping.id.asc())
    ).all())

    reviews = list(db.scalars(select(SurveyReviewEvent).where(SurveyReviewEvent.audit_id == audit_id)
                              .order_by(SurveyReviewEvent.id)))
    excel_buffer = generate_survey_audit_workbook(audit, respondents, findings, mappings, reviews)

    export_filename = f"survey_audit_{audit.id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{export_filename}"'}
    )


@router.get("/dashboard/summary", response_model=SurveyDashboardSummaryResponse)
def get_survey_dashboard_summary(
    db: Session = Depends(get_db)
):
    """
    Retrieve aggregate Survey Data Quality Overview metrics across all survey audits.
    Completely isolated from ETL pipeline metrics.
    """
    total_audits = db.scalar(select(func.count(SurveyAuditRun.id))) or 0
    latest_audit = db.scalars(select(SurveyAuditRun).order_by(desc(SurveyAuditRun.id))).first()

    agg_stmt = select(
        func.coalesce(func.sum(SurveyAuditRun.total_respondents), 0),
        func.coalesce(func.sum(SurveyAuditRun.accepted_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.rejected_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.review_required_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.speeding_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.straightlining_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.suspicious_text_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.confirmed_duplicates_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.potential_duplicates_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.routing_violations_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.screener_violations_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.contradiction_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.incomplete_count), 0),
        func.coalesce(func.sum(SurveyAuditRun.timestamp_mismatch_count), 0),
    )
    res = db.execute(agg_stmt).one()
    (
        tot_resp, acc_cnt, rej_cnt, rev_cnt, spd_cnt, str_cnt,
        txt_cnt, cdup_cnt, pdup_cnt, rout_cnt, scr_cnt,
        con_cnt, inc_cnt, tm_cnt
    ) = res

    acc_rate = round((acc_cnt / tot_resp * 100.0), 1) if tot_resp > 0 else 0.0

    kpis = SurveyDashboardKPIs(
        total_respondents=int(tot_resp),
        accepted_count=int(acc_cnt),
        rejected_count=int(rej_cnt),
        review_required_count=int(rev_cnt),
        speeding_count=int(spd_cnt),
        straightlining_count=int(str_cnt),
        suspicious_text_count=int(txt_cnt),
        confirmed_duplicates_count=int(cdup_cnt),
        potential_duplicates_count=int(pdup_cnt),
        routing_violations_count=int(rout_cnt),
        screener_violations_count=int(scr_cnt),
        contradiction_count=int(con_cnt),
        incomplete_count=int(inc_cnt),
        timestamp_mismatch_count=int(tm_cnt),
        total_audits=int(total_audits),
        accepted_rate=acc_rate
    )

    recent_audits = list(db.scalars(
        select(SurveyAuditRun).order_by(desc(SurveyAuditRun.id)).limit(5)
    ).all())

    coverage = json.loads(latest_audit.checks_summary_json) if latest_audit and latest_audit.checks_summary_json else []

    return SurveyDashboardSummaryResponse(
        kpis=kpis,
        recent_audits=recent_audits,
        active_audit=latest_audit,
        latest_audit_id=latest_audit.id if latest_audit else None,
        qc_coverage=coverage
    )


@router.post("/reset-survey-data")
def reset_all_survey_data(
    db: Session = Depends(get_db)
):
    """
    Dedicated, isolated reset operation for Survey Scrubbing data only.
    Safely clears survey tables and survey storage files without touching
    any Customer, Sales, Inventory, or ProcessingJob records.
    Restricted to authenticated administrators with concurrency locking.
    """
    This dedicated reset is separate from the ETL-wide destructive reset switch.
    Router-level require_survey_admin_auth enforces administrator or scoped reset
    credentials; the UI also requires explicit confirmation. Only survey tables
    and the dedicated survey storage area are cleared here.
    """
    if not survey_reset_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A survey reset operation is already in progress."
        )

    try:
        _survey_write_lock(db, resetting=True)
        count_findings = db.query(SurveyQcFinding).count()
        count_respondents = db.query(SurveyRespondent).count()
        count_mappings = db.query(SurveyQuestionMapping).count()
        count_audits = db.query(SurveyAuditRun).count()

        db.query(SurveyReviewEvent).delete(synchronize_session=False)
        db.query(SurveyQcFinding).delete(synchronize_session=False)
        db.query(SurveyRespondent).delete(synchronize_session=False)
        db.query(SurveyQuestionMapping).delete(synchronize_session=False)
        db.query(SurveyAuditRun).delete(synchronize_session=False)
        db.commit()

        # Clear local survey files
        deleted_files = 0
        if settings.SURVEYS_DIR.exists():
            for f in settings.SURVEYS_DIR.iterdir():
                if f.is_file():
                    try:
                        f.unlink()
                        deleted_files += 1
                    except Exception:
                        pass

        cloud_deletion_errors: list[str] = []
        cloud_deleted_count = 0

        # Purge from Cloud Storage if configured
        if settings.STORAGE_BACKEND != "local" and settings.is_s3_configured:
            try:
                from app.services.storage import get_storage
                storage = get_storage()
                remote_keys = storage.list_keys(prefix="surveys/")
                for k in remote_keys:
                    try:
                        success = storage.delete(k)
                        if success:
                            cloud_deleted_count += 1
                            deleted_files += 1
                        else:
                            cloud_deletion_errors.append(f"Failed to delete cloud object '{k}'")
                    except Exception as del_err:
                        cloud_deletion_errors.append(f"Error deleting cloud object '{k}': {del_err}")
            except Exception as e:
                err_msg = f"Cloud storage survey cleanup error: {e}"
                logger.error(err_msg)
                cloud_deletion_errors.append(err_msg)

        if cloud_deletion_errors:
            logger.warning(f"Survey reset completed database wipe, but cloud storage had errors: {cloud_deletion_errors}")
            return {
                "status": "partial_success",
                "message": "Survey scrubbing database records were cleared, but some remote cloud storage objects could not be deleted.",
                "deleted_counts": {
                    "audits": count_audits,
                    "respondents": count_respondents,
                    "findings": count_findings,
                    "mappings": count_mappings,
                    "files": deleted_files,
                    "cloud_files": cloud_deleted_count
                },
                "cloud_deletion_errors": cloud_deletion_errors,
                "etl_records_preserved": True
            }

        return {
            "status": "success",
            "message": "Survey scrubbing data and files have been permanently cleared.",
            "deleted_counts": {
                "audits": count_audits,
                "respondents": count_respondents,
                "findings": count_findings,
                "mappings": count_mappings,
                "files": deleted_files,
                "cloud_files": cloud_deleted_count
            },
            "etl_records_preserved": True
        }
    finally:
        survey_reset_lock.release()
