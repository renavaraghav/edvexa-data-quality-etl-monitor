"""
Survey Data Scrubbing API Router.
Endpoints for Questionnaire (QQ) upload & parsing, raw respondent data intake,
interactive data preview & sheet selection, question-to-column mapping,
QC audit execution, respondent disposition browsing, manual reviewer decisions,
and 8-worksheet Excel report downloads.
"""

import json
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from sqlalchemy import select, func, desc

from app.config import settings
from app.database import get_db
from app.models import (
    SurveyAuditRun,
    SurveyRespondent,
    SurveyQcFinding,
    SurveyQuestionMapping,
    utc_now,
)
from app.schemas import (
    SurveyAuditRunSummary,
    SurveyRespondentItem,
    SurveyQcFindingItem,
    SurveyDashboardSummaryResponse,
    SurveyDashboardKPIs,
    SurveyReviewDecisionRequest,
    SurveyPreviewResponse,
)
from app.services.auth import is_authenticated_admin, is_authorized_reset_client, SESSION_COOKIE_NAME
from app.services.csv_import import compute_file_hash
from app.services.survey_qq_parser import parse_questionnaire, ParsedQuestion
from app.services.survey_data_parser import (
    preview_survey_data,
    parse_respondent_records,
    get_excel_sheets,
)
from app.services.survey_mapping_service import suggest_mappings
from app.services.survey_qc_engine import run_survey_qc_audit
from app.services.survey_export_service import generate_survey_audit_workbook

logger = logging.getLogger("edvexa.api.surveys")
survey_reset_lock = threading.Lock()


def require_survey_admin_auth(request: Request) -> bool:
    """
    Strict security dependency ensuring that access to Survey Data Scrubbing endpoints
    is restricted to authenticated administrators.
    Under least-privilege policy:
      - Destructive reset (POST /api/surveys/reset-survey-data) accepts full admin sessions
        or scoped machine reset tokens (X-Admin-Reset-Token).
      - All other survey endpoints (ingest, audit, respondents, export, review) require a full
        admin session (Cookie, Bearer, X-Admin-Session-Token). Machine reset tokens are rejected with 403.
      - Cookie-authenticated state-changing requests enforce CSRF verification (Origin, Referer,
        Sec-Fetch-Site, and custom anti-CSRF headers).
    """
    is_reset_path = request.url.path.rstrip("/").endswith("/reset-survey-data")
    if is_reset_path:
        if not is_authorized_reset_client(request):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required for survey reset. Provide an admin session or X-Admin-Reset-Token.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return True

    # 1. Least-privilege check: reject naked reset token on general administrative endpoints
    direct_reset_token = (
        request.headers.get("X-Admin-Reset-Token")
        or request.query_params.get("token")
        or ""
    ).strip()
    if direct_reset_token and not is_authenticated_admin(request):
        configured_token = (settings.ADMIN_RESET_TOKEN or "").strip()
        if configured_token and secrets.compare_digest(direct_reset_token, configured_token):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Reset token possesses insufficient privileges for this endpoint. Under the least-privilege security policy, reset tokens are restricted exclusively to destructive reset operations.",
            )

    # 2. Authenticate admin session
    if not is_authenticated_admin(request):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required for Survey Data Scrubbing operations. Please provide a valid admin session cookie, Bearer token, or X-Admin-Session-Token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 3. CSRF Verification for Cookie-Authenticated State-Changing Requests
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME)
    is_state_changing = request.method in ("POST", "PUT", "DELETE", "PATCH")

    # Only enforce CSRF when authentication is carried by browser cookies (Bearer/API headers are immune to browser CSRF)
    has_bearer = bool(request.headers.get("Authorization", "").startswith("Bearer "))
    has_session_header = bool(request.headers.get("X-Admin-Session-Token"))

    if cookie_token and is_state_changing and not (has_bearer or has_session_header):
        # A. Sec-Fetch-Site check (browser-enforced metadata)
        sec_fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
        if sec_fetch_site == "cross-site":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF protection: cross-site state-changing request blocked.",
            )

        # B. Origin / Referer validation
        server_host = (request.headers.get("host") or request.url.netloc or "").split(":")[0].lower()
        forwarded_host = (request.headers.get("x-forwarded-host") or "").split(",")[0].split(":")[0].strip().lower()
        allowed_hosts = {server_host, forwarded_host, "testserver", "localhost", "127.0.0.1"} - {""}

        origin = request.headers.get("origin")
        if origin:
            try:
                origin_host = urlparse(origin).netloc.split(":")[0].lower()
                if origin_host not in allowed_hosts:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"CSRF protection: origin '{origin_host}' does not match server host.",
                    )
            except Exception:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="CSRF protection: malformed Origin header.",
                )
        else:
            referer = request.headers.get("referer")
            if referer:
                try:
                    referer_host = urlparse(referer).netloc.split(":")[0].lower()
                    if referer_host not in allowed_hosts:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"CSRF protection: referer '{referer_host}' does not match server host.",
                        )
                except Exception:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="CSRF protection: malformed Referer header.",
                    )

    return True


router = APIRouter(
    prefix="/api/surveys",
    tags=["Survey Data Scrubbing"],
    dependencies=[Depends(require_survey_admin_auth)],
)


def _save_survey_file(content: bytes, prefix: str, file_hash: str, filename: str) -> str:
    """
    Save immutable survey file artifact into isolated surveys_storage directory
    and replicate to durable cloud object storage (Neon / S3) when configured.
    """
    clean_name = Path(filename).name
    artifact_name = f"{prefix}_{file_hash[:12]}_{clean_name}"
    
    # 1. Save to local/persistent disk storage
    target_path = settings.SURVEYS_DIR / artifact_name
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with open(target_path, "wb") as f:
        f.write(content)
        
    # 2. Replicate to Cloud Storage (Neon/S3/R2) if configured
    try:
        from app.services.storage import get_storage
        storage = get_storage()
        if settings.STORAGE_BACKEND != "local" and settings.is_s3_configured:
            cloud_key = f"surveys/{artifact_name}"
            content_type = "application/octet-stream"
            if clean_name.endswith(".csv"):
                content_type = "text/csv"
            elif clean_name.endswith(".xlsx"):
                content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            elif clean_name.endswith(".pdf"):
                content_type = "application/pdf"
            storage.save(cloud_key, content, content_type=content_type)
            logger.info(f"Replicated survey artifact to cloud storage: {cloud_key}")
    except Exception as e:
        logger.warning(f"Could not replicate survey file to cloud storage: {e}")

    return artifact_name


def _get_survey_file(artifact_name: str) -> bytes:
    """
    Read saved survey file artifact from surveys_storage, matching exact name, prefix, or suffix.
    If missing locally (e.g. after container restart), attempts recovery from Cloud Object Storage.
    """
    clean_name = Path(artifact_name).name
    target_path = settings.SURVEYS_DIR / clean_name
    if target_path.exists() and target_path.is_file():
        with open(target_path, "rb") as f:
            return f.read()

    # Search local disk by filename suffix or pattern
    candidates = sorted(settings.SURVEYS_DIR.glob(f"*_{clean_name}"), key=os.path.getmtime, reverse=True)
    if not candidates:
        candidates = sorted(settings.SURVEYS_DIR.glob(f"*{clean_name}*"), key=os.path.getmtime, reverse=True)

    if candidates and candidates[0].is_file():
        with open(candidates[0], "rb") as fp:
            return fp.read()

    # Attempt recovery from durable Cloud Storage if configured
    try:
        from app.services.storage import get_storage
        storage = get_storage()
        for candidate_key in [f"surveys/{clean_name}", clean_name]:
            try:
                content = storage.get(candidate_key)
                if content:
                    # Self-heal local cache
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(target_path, "wb") as f:
                        f.write(content)
                    return content
            except Exception:
                pass
    except Exception as e:
        logger.warning(f"Error checking cloud storage for survey artifact '{artifact_name}': {e}")

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Survey file artifact '{artifact_name}' not found on storage."
    )


@router.post("/upload-qq")
async def upload_questionnaire(
    file: UploadFile = File(..., description="Questionnaire file (.pdf, .docx, .xlsx, .txt)")
):
    """
    Upload and parse Questionnaire (QQ) reference document.
    Extracts questions, question numbers, wording, types, options, duration, and routing.
    """
    filename = file.filename or "questionnaire.pdf"
    lower = filename.lower()
    allowed = (".pdf", ".docx", ".doc", ".xlsx", ".xls", ".txt", ".md")
    if not any(lower.endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported format for Questionnaire. Please upload PDF, DOCX, XLSX, or TXT."
        )

    try:
        content = await file.read(settings.UPLOAD_MAX_SIZE_BYTES + 1)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to read file: {e}")

    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty (0 bytes).")
    if len(content) > settings.UPLOAD_MAX_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 10 MB limit.")

    file_hash = compute_file_hash(content)
    artifact_name = _save_survey_file(content, "qq", file_hash, filename)

    extraction = parse_questionnaire(content, filename)

    return {
        "status": "success",
        "filename": filename,
        "file_hash": file_hash,
        "artifact_name": artifact_name,
        "document_format": extraction.document_format,
        "unreadable_or_scanned": extraction.unreadable_or_scanned,
        "raw_text_length": extraction.raw_text_length,
        "expected_duration_min_minutes": extraction.expected_duration_min_minutes,
        "expected_duration_max_minutes": extraction.expected_duration_max_minutes,
        "page_timer_threshold_seconds": extraction.page_timer_threshold_seconds,
        "questions_count": len(extraction.questions),
        "total_questions_detected": len(extraction.questions),
        "file_size_bytes": len(content),
        "questions": [
            {
                "question_number": q.question_number,
                "question_text": q.question_text,
                "question_type": q.question_type,
                "options": q.options,
                "matrix_items": q.matrix_items,
                "qc_instructions": q.qc_instructions,
                "routing_logic": q.routing_logic,
                "screening_criteria": q.screening_criteria
            }
            for q in extraction.questions
        ],
        "notes": extraction.extraction_notes
    }


@router.post("/upload-raw")
async def upload_raw_respondent_data(
    file: UploadFile = File(..., description="Raw respondent data file (.csv, .xlsx)")
):
    """
    Upload raw respondent dataset (CSV or Excel).
    Inspects sheet titles, metadata rows, and provides immediate data preview.
    """
    filename = file.filename or "respondents.csv"
    lower = filename.lower()
    allowed = (".csv", ".xlsx", ".xls")
    if not any(lower.endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unsupported format for respondent data. Please upload CSV or XLSX."
        )

    try:
        content = await file.read(settings.UPLOAD_MAX_SIZE_BYTES + 1)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Failed to read file: {e}")

    if len(content) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty (0 bytes).")
    if len(content) > settings.UPLOAD_MAX_SIZE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="File exceeds 10 MB limit.")

    file_hash = compute_file_hash(content)
    artifact_name = _save_survey_file(content, "raw", file_hash, filename)

    preview = preview_survey_data(content, filename)
    preview["file_hash"] = file_hash
    preview["artifact_name"] = artifact_name

    return preview


@router.post("/preview")
def preview_sheet_data(
    artifact_name: str = Form(...),
    filename: str = Form(...),
    sheet_name: str | None = Form(None),
    header_row_index: int = Form(0)
):
    """Re-render dataset preview when user selects a different worksheet or header row."""
    content = _get_survey_file(artifact_name)
    preview = preview_survey_data(
        content=content,
        filename=filename,
        sheet_name=sheet_name,
        header_row_index=header_row_index
    )
    preview["artifact_name"] = artifact_name
    return preview


@router.post("/suggest-mapping")
async def generate_suggested_mappings(request: Request):
    """
    Generate question-to-column mappings by correlating QQ questions
    with raw dataset headers. Supports JSON body or Form data.
    """
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)

    raw_artifact_name = body.get("raw_artifact_name") or body.get("raw_filename")
    raw_filename = body.get("raw_filename") or (Path(raw_artifact_name).name if raw_artifact_name else "raw_data.csv")
    sheet_name = body.get("sheet_name")
    header_row_index = int(body.get("header_row_index") or 0)
    qq_artifact_name = body.get("qq_artifact_name") or body.get("qq_filename")
    qq_filename = body.get("qq_filename")

    if not raw_artifact_name:
        raise HTTPException(status_code=400, detail="raw_filename or raw_artifact_name is required.")

    raw_content = _get_survey_file(raw_artifact_name)
    headers, _, _ = parse_respondent_records(
        raw_content, raw_filename, sheet_name=sheet_name, header_row_index=header_row_index
    )

    questions: list[ParsedQuestion] = []
    if qq_artifact_name:
        try:
            qq_content = _get_survey_file(qq_artifact_name)
            extraction = parse_questionnaire(qq_content, qq_filename or "questionnaire.txt")
            questions = extraction.questions
        except Exception as e:
            logger.warning(f"Could not parse questionnaire for mapping: {e}")

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
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
    else:
        form = await request.form()
        body = dict(form)

    raw_artifact_name = body.get("raw_artifact_name") or body.get("raw_filename")
    raw_filename = body.get("raw_filename") or (Path(raw_artifact_name).name if raw_artifact_name else "raw_data.csv")
    sheet_name = body.get("sheet_name")
    header_row_index = int(body.get("header_row_index") or 0)
    qq_artifact_name = body.get("qq_artifact_name") or body.get("qq_filename")
    qq_filename = body.get("qq_filename")
    audit_name = body.get("audit_name") or "Survey Data Scrubbing Audit"

    if not raw_artifact_name:
        raise HTTPException(status_code=400, detail="raw_filename or raw_artifact_name is required.")

    raw_content = _get_survey_file(raw_artifact_name)
    raw_file_hash = body.get("raw_file_hash") or compute_file_hash(raw_content)

    qq_file_hash = body.get("qq_file_hash")
    qq_parsed_rules: dict[str, Any] = {}
    parsed_questions: list[ParsedQuestion] = []
    if qq_artifact_name:
        try:
            qq_content = _get_survey_file(qq_artifact_name)
            if not qq_file_hash:
                qq_file_hash = compute_file_hash(qq_content)
            extraction = parse_questionnaire(qq_content, qq_filename or "questionnaire.txt")
            parsed_questions = extraction.questions
            if extraction.expected_duration_min_minutes:
                qq_parsed_rules["expected_duration_min_minutes"] = extraction.expected_duration_min_minutes
            if extraction.expected_duration_max_minutes:
                qq_parsed_rules["expected_duration_max_minutes"] = extraction.expected_duration_max_minutes
            if extraction.page_timer_rules:
                qq_parsed_rules["page_timer_rules"] = extraction.page_timer_rules
            qq_parsed_rules["duration_source"] = f"Questionnaire Specification ({qq_filename or 'QQ'})"
        except Exception as e:
            logger.warning(f"Error reading QQ artifact: {e}")

    confirmed_mappings = body.get("confirmed_mappings")
    if confirmed_mappings is None:
        mappings_json = body.get("mappings_json")
        if mappings_json:
            try:
                confirmed_mappings = json.loads(mappings_json)
            except Exception:
                confirmed_mappings = []
        else:
            confirmed_mappings = []

    timing_rules = body.get("timing_rules")
    if timing_rules is None:
        timing_rules_json = body.get("timing_rules_json")
        if timing_rules_json:
            try:
                timing_rules = json.loads(timing_rules_json)
            except Exception:
                timing_rules = {}
        else:
            timing_rules = {}

    final_timing_rules = {**qq_parsed_rules, **timing_rules}
    if "min_expected_duration_min" in body:
        final_timing_rules["expected_duration_min_minutes"] = float(body["min_expected_duration_min"])
        final_timing_rules["duration_source"] = "Project Custom Configuration"
    if "max_expected_duration_min" in body:
        final_timing_rules["expected_duration_max_minutes"] = float(body["max_expected_duration_min"])
        final_timing_rules["duration_source"] = "Project Custom Configuration"
    if "speeding_concern_threshold_min" in body:
        final_timing_rules["speeding_threshold_seconds"] = float(body["speeding_concern_threshold_min"]) * 60.0
    if "page_timer_threshold_sec" in body:
        final_timing_rules["page_timer_threshold_seconds"] = float(body["page_timer_threshold_sec"])
    if "straightline_min_statements" in body:
        final_timing_rules["straightlining_min_grid_size"] = int(body["straightline_min_statements"])
    if "reject_speeding_under_2min" in body:
        final_timing_rules["reject_speeding_under_2min"] = bool(body["reject_speeding_under_2min"])
    if "reject_straightlining" in body:
        final_timing_rules["reject_straightlining"] = bool(body["reject_straightlining"])

    headers, records, resp_id_col = parse_respondent_records(
        raw_content, raw_filename, sheet_name=sheet_name, header_row_index=header_row_index
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

    if not confirmed_mappings:
        confirmed_mappings = mapping_calc.get("mappings", [])

    # Execute QC engine
    audit_summary = run_survey_qc_audit(
        records=records,
        mappings=confirmed_mappings,
        system_columns=system_cols,
        timer_columns=timer_cols,
        resp_id_col=resp_id_col,
        timing_rules=final_timing_rules
    )

    duration = time.time() - start_time

    # Persist SurveyAuditRun
    audit_run = SurveyAuditRun(
        audit_name=audit_name,
        qq_filename=qq_filename,
        qq_file_hash=qq_file_hash,
        raw_filename=raw_filename,
        raw_file_hash=raw_file_hash,
        selected_sheet=sheet_name,
        header_row_index=header_row_index,
        status="completed",
        total_respondents=audit_summary.total_respondents,
        accepted_count=audit_summary.accepted_count,
        rejected_count=audit_summary.rejected_count,
        review_required_count=audit_summary.review_required_count,
        speeding_count=audit_summary.speeding_count,
        straightlining_count=audit_summary.straightlining_count,
        suspicious_text_count=audit_summary.suspicious_text_count,
        confirmed_duplicates_count=audit_summary.confirmed_duplicates_count,
        potential_duplicates_count=audit_summary.potential_duplicates_count,
        routing_violations_count=audit_summary.routing_violations_count,
        screener_violations_count=audit_summary.screener_violations_count,
        contradiction_count=audit_summary.contradiction_count,
        incomplete_count=audit_summary.incomplete_count,
        timestamp_mismatch_count=audit_summary.timestamp_mismatch_count,
        checks_summary_json=json.dumps(audit_summary.checks_coverage),
        timing_rules_json=timing_rules_json,
        started_at=utc_now(),
        completed_at=utc_now(),
        duration_seconds=duration
    )
    db.add(audit_run)
    db.flush()

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
        "strict_accounting_verified": (audit.total_respondents == audit.accepted_count + audit.rejected_count + audit.review_required_count),
        "qc_coverage": coverage,
        "audit": audit
    }


@router.get("/audits/{audit_id}/respondents")
def get_survey_respondents(
    audit_id: int,
    disposition: str | None = Query(None, description="Filter: ACCEPTED, REJECTED, REVIEW_REQUIRED"),
    search: str | None = Query(None, description="Search respondent ID or text"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db)
):
    """Paginated list of respondents with raw values, disposition, and flags."""
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

    stmt = stmt.order_by(SurveyRespondent.row_number.asc()).offset((page - 1) * page_size).limit(page_size)
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
            "duration_seconds": r.exported_duration_seconds or r.calculated_duration_seconds,
            "is_speeding": any("speed" in f.issue_type.lower() for f in (r.findings or [])) or any("speed" in str(x).lower() for x in (reasons + flags)),
            "is_straightlining": any("straight" in f.issue_type.lower() for f in (r.findings or [])) or any("straight" in str(x).lower() or "identical" in str(x).lower() for x in (reasons + flags)),
            "is_gibberish": any("gibberish" in f.issue_type.lower() for f in (r.findings or [])) or any("gibberish" in str(x).lower() or "suspicious" in str(x).lower() for x in (reasons + flags)),
            "is_duplicate": any("duplicate" in f.issue_type.lower() for f in (r.findings or [])) or any("duplicate" in str(x).lower() for x in (reasons + flags)),
            "findings_count": len(r.findings) if r.findings else 0
        })

    return serialized_items


@router.get("/audits/{audit_id}/findings", response_model=list[SurveyQcFindingItem])
def get_survey_findings(
    audit_id: int,
    issue_type: str | None = Query(None),
    severity: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db)
):
    """Retrieve detailed QC findings for an audit run."""
    stmt = select(SurveyQcFinding).where(SurveyQcFinding.audit_id == audit_id)
    if issue_type:
        stmt = stmt.where(SurveyQcFinding.issue_type == issue_type)
    if severity:
        stmt = stmt.where(SurveyQcFinding.severity == severity.upper())

    stmt = stmt.order_by(desc(SurveyQcFinding.id)).limit(limit)
    return list(db.scalars(stmt).all())


@router.post("/audits/{audit_id}/review/{respondent_key}")
def record_manual_review_decision(
    audit_id: int,
    respondent_key: str,
    review_req: SurveyReviewDecisionRequest,
    db: Session = Depends(get_db)
):
    """
    Record human reviewer decision on a Review Required or contested respondent.
    Updates disposition, audit counts, and audit trail.
    """
    stmt = select(SurveyRespondent).where(
        SurveyRespondent.audit_id == audit_id,
        SurveyRespondent.respondent_key == respondent_key
    )
    resp = db.scalars(stmt).first()
    if not resp:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Respondent '{respondent_key}' not found in audit #{audit_id}."
        )

    audit = db.get(SurveyAuditRun, audit_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit #{audit_id} not found.")

    old_disp = resp.disposition
    new_disp = review_req.decision.upper().strip()
    if new_disp not in ("ACCEPTED", "REJECTED", "REVIEW_REQUIRED"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Invalid decision.")

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
    effective_notes = review_req.effective_notes

    resp.disposition = new_disp
    resp.manual_decision = new_disp
    resp.reviewer_notes = effective_notes
    resp.reviewed_by = effective_rev
    resp.reviewed_at = utc_now()

    # Update associated findings
    for f in resp.findings:
        f.final_decision = new_disp

    db.commit()

    return {
        "status": "success",
        "message": f"Respondent '{respondent_key}' updated to {new_disp}.",
        "disposition": new_disp,
        "reviewed_by": effective_rev,
        "reviewer_notes": effective_notes,
        "audit_counts": {
            "accepted": audit.accepted_count,
            "rejected": audit.rejected_count,
            "review_required": audit.review_required_count
        }
    }


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
    audit = db.get(SurveyAuditRun, audit_id)
    if not audit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Audit #{audit_id} not found.")

    respondents = list(db.scalars(
        select(SurveyRespondent).where(SurveyRespondent.audit_id == audit_id).order_by(SurveyRespondent.row_number.asc())
    ).all())

    findings = list(db.scalars(
        select(SurveyQcFinding).where(SurveyQcFinding.audit_id == audit_id).order_by(SurveyQcFinding.id.asc())
    ).all())

    mappings = list(db.scalars(
        select(SurveyQuestionMapping).where(SurveyQuestionMapping.audit_id == audit_id).order_by(SurveyQuestionMapping.id.asc())
    ).all())

    excel_buffer = generate_survey_audit_workbook(audit, respondents, findings, mappings)

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
    if not settings.ALLOW_DESTRUCTIVE_RESET:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Destructive survey reset is currently disabled by configuration."
        )

    if not survey_reset_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A survey reset operation is already in progress."
        )

    try:
        count_findings = db.query(SurveyQcFinding).count()
        count_respondents = db.query(SurveyRespondent).count()
        count_mappings = db.query(SurveyQuestionMapping).count()
        count_audits = db.query(SurveyAuditRun).count()

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

        # Also purge from Cloud Storage if configured
        try:
            from app.services.storage import get_storage
            storage = get_storage()
            if settings.STORAGE_BACKEND != "local" and settings.is_s3_configured:
                remote_keys = storage.list_keys(prefix="surveys/")
                for k in remote_keys:
                    try:
                        storage.delete(k)
                        deleted_files += 1
                    except Exception:
                        pass
        except Exception as e:
            logger.warning(f"Failed to clear cloud storage survey files: {e}")

        return {
            "status": "success",
            "message": "Survey scrubbing data and files have been permanently cleared.",
            "deleted_counts": {
                "audits": count_audits,
                "respondents": count_respondents,
                "findings": count_findings,
                "mappings": count_mappings,
                "files": deleted_files
            },
            "etl_records_preserved": True
        }
    finally:
        survey_reset_lock.release()
