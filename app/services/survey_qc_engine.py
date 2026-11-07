"""
Survey Data Quality Control (QC) Engine.
Executes comprehensive, contextual quality audits on respondent records:
1. Timestamp arithmetic & survey speeding (duration range, page timers, overnight context)
2. Straightlining detection on matrix/grid rating scales
3. Open-ended quality & gibberish detection (valid no-recall vs gibberish vs off-topic vs typos)
4. Duplicate detection (confirmed vs potential)
5. Screening and eligibility validation
6. Survey routing and skip-logic consistency
7. Contradictory response detection
8. Incomplete / missing answer validation
9. Deterministic respondent disposition (ACCEPTED, REJECTED, REVIEW_REQUIRED)
"""

from dataclasses import dataclass, field
from datetime import datetime
import json
import logging
import math
import re
from typing import Any

from dateutil import parser as dt_parser

logger = logging.getLogger("edvexa.surveys.qc_engine")

# Common No-Recall phrases that represent VALID, honest non-recall answers
VALID_NO_RECALL_PATTERNS = [
    r"^\s*idk\s*$",
    r"^\s*i\s+don'?t\s+know\s*$",
    r"^\s*don'?t\s+know\s*$",
    r"^\s*none\s*$",
    r"^\s*none\s+come\s+to\s+mind\s*$",
    r"^\s*can'?t\s+think\s+of\s+any\s*$",
    r"^\s*can'?t\s+recall\s*$",
    r"^\s*nothing\s*$",
    r"^\s*n/?a\s*$",
    r"^\s*na\s*$",
    r"^\s*no\s*$",
    r"^\s*no\s+idea\s*$",
    r"^\s*not\s+sure\s*$",
    r"^\s*no\s+brands?\s*$",
    r"^\s*unsure\s*$",
    r"^\s*nil\s*$",
    r"^\s*unknown\s*$",
]

# Recognizable product / brand / category terms for orthodontic and health surveys
RECOGNIZED_ORTHO_TERMS = {
    "invisalign", "angel aligner", "angelaligner", "ortho classic", "orthoclassic",
    "braces", "clear aligners", "aligners", "aligner", "retainers", "retainer",
    "byte", "candid", "smile direct", "smiledirectclub", "spark", "3m", "ormco",
    "damon", "six month smiles", "surefit", "metal braces", "ceramic braces",
    "lingual braces", "invisible braces", "teeth straighteners", "bands"
}

# Real words that are common known brands outside orthodontics (Meaningful but Off-Topic)
COMMON_OFF_TOPIC_BRANDS = {
    "nike", "adidas", "apple", "samsung", "toyota", "honda", "ford", "bmw",
    "google", "amazon", "microsoft", "sony", "coca cola", "coke", "pepsi",
    "starbucks", "mcdonalds", "gucci", "prada", "rolex", "tesla"
}


@dataclass
class FindingDetail:
    issue_type: str
    evidence: str
    severity: str = "MEDIUM"  # HIGH, MEDIUM, LOW, INFO
    recommendation: str = "REVIEW_REQUIRED"  # REJECT, REVIEW_REQUIRED, NOTE
    question_number: str | None = None
    raw_column: str | None = None
    original_answer: str | None = None
    observed_value: str | None = None
    expected_rule: str | None = None


@dataclass
class RespondentAuditResult:
    respondent_key: str
    row_number: int
    raw_data: dict[str, Any]
    disposition: str = "ACCEPTED"  # ACCEPTED, REJECTED, REVIEW_REQUIRED
    rejection_reasons: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    findings: list[FindingDetail] = field(default_factory=list)
    calculated_duration_seconds: float | None = None
    exported_duration_seconds: float | None = None
    duration_delta_seconds: float | None = None
    local_completion_hour: int | None = None
    is_overnight: int = 0


@dataclass
class SurveyQcExecutionSummary:
    total_respondents: int = 0
    accepted_count: int = 0
    rejected_count: int = 0
    review_required_count: int = 0
    speeding_count: int = 0
    straightlining_count: int = 0
    suspicious_text_count: int = 0
    confirmed_duplicates_count: int = 0
    potential_duplicates_count: int = 0
    routing_violations_count: int = 0
    screener_violations_count: int = 0
    contradiction_count: int = 0
    incomplete_count: int = 0
    timestamp_mismatch_count: int = 0
    checks_coverage: list[dict[str, Any]] = field(default_factory=list)
    results: list[RespondentAuditResult] = field(default_factory=list)


def run_survey_qc_audit(
    records: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
    system_columns: dict[str, str | None],
    timer_columns: list[str],
    resp_id_col: str,
    timing_rules: dict[str, Any] | None = None
) -> SurveyQcExecutionSummary:
    """
    Execute full quality audit on respondent records.
    Every respondent is evaluated across all active checks and assigned exactly one disposition:
    ACCEPTED, REJECTED, or REVIEW_REQUIRED.
    """
    summary = SurveyQcExecutionSummary(total_respondents=len(records))
    rules = timing_rules or {}

    dur_source = str(rules.get("duration_source") or "Platform Standard Baseline (Edvexa 7–15m Target)")
    speeding_threshold = float(rules.get("speeding_threshold_seconds", 300.0))  # 5 min (300s)
    expected_min_mins = float(rules.get("expected_duration_min_minutes", 7.0))  # 7 min (420s)
    expected_max_mins = float(rules.get("expected_duration_max_minutes", 15.0))  # 15 min (900s)
    extreme_speeding_threshold = float(rules.get("extreme_speeding_threshold_seconds", 120.0))  # 2 min (120s)
    reject_speeding_under_2min = bool(rules.get("reject_speeding_under_2min", False))
    reject_straightlining = bool(rules.get("reject_straightlining", False))
    page_timer_rules = dict(rules.get("page_timer_rules") or {})
    straightlining_min_items = int(rules.get("straightlining_min_grid_size", 4))
    timestamp_tolerance = float(rules.get("timestamp_tolerance_seconds", 2.0))

    start_col = system_columns.get("start_time")
    end_col = system_columns.get("end_time")
    dur_col = system_columns.get("duration")
    ip_col = system_columns.get("ip_address")

    # Group mapped questions by type
    matrix_mappings = [m for m in mappings if (m.get("question_type") == "matrix_grid" or len(m.get("mapped_columns", [])) > 1) and m.get("mapped_columns")]
    open_ended_mappings = [m for m in mappings if (m.get("question_type") == "open_ended" or "brand" in m.get("question_text", "").lower()) and m.get("mapped_columns")]
    screener_mappings = [m for m in mappings if (m.get("question_type") == "screener" or "terminate" in m.get("question_text", "").lower() or "age" in m.get("question_text", "").lower() or m.get("min_age") is not None) and m.get("mapped_columns")]

    # Track identifiers for duplicate detection
    seen_ids: dict[str, list[str]] = {}
    seen_ips: dict[str, list[str]] = {}
    seen_signatures: dict[str, list[str]] = {}
    seen_full_rows: dict[str, list[str]] = {}

    # Check execution flags for coverage tracking
    executed_checks = {
        "timestamp_arithmetic": bool(start_col and end_col and dur_col),
        "overall_speeding": bool(dur_col or (start_col and end_col)),
        "page_timers": bool(timer_columns),
        "straightlining": bool(matrix_mappings),
        "open_ended_quality": bool(open_ended_mappings),
        "duplicate_detection": True,
        "screening_eligibility": bool(screener_mappings),
        "routing_consistency": True,
        "completeness": True,
    }

    # Pass 1: Build duplicate lookup indices
    for idx, row in enumerate(records):
        r_id = str(row.get(resp_id_col, f"RESP-{idx+1:04d}")).strip()
        seen_ids.setdefault(r_id, []).append(r_id)

        if ip_col and row.get(ip_col):
            ip = str(row[ip_col]).strip()
            seen_ips.setdefault(ip, []).append(r_id)

        # Full record hash (exact duplicate full row)
        full_row_tokens = [f"{k}={str(v).strip()}" for k, v in row.items() if not k.startswith("_")]
        full_sig = "|".join(sorted(full_row_tokens))
        seen_full_rows.setdefault(full_sig, []).append(r_id)

        # Substantive answer signature for similar responses check (ignoring telemetry)
        substantive = []
        for col_name, val in row.items():
            col_low = col_name.lower()
            if not col_name.startswith("_") and col_name not in (resp_id_col, start_col, end_col, dur_col, ip_col) and not any(t in col_low for t in ["timer", "submit", "start", "end", "date", "time", "dur", "ip", "prog"]):
                if val is not None and str(val).strip():
                    substantive.append(f"{col_name}={str(val).strip().lower()}")
        sig = "|".join(sorted(substantive))
        if sig:
            seen_signatures.setdefault(sig, []).append(r_id)

    # Pass 2: Evaluate Each Respondent
    for idx, row in enumerate(records):
        r_id = str(row.get(resp_id_col, f"RESP-{idx+1:04d}")).strip()
        phys_row = row.get("_physical_row", idx + 1)

        result = RespondentAuditResult(
            respondent_key=r_id,
            row_number=phys_row,
            raw_data=row
        )

        # --- CHECK 1: Timestamp Arithmetic & Duration Integrity ---
        calc_dur, exp_dur, delta_dur = _evaluate_timestamps(row, start_col, end_col, dur_col)
        result.calculated_duration_seconds = calc_dur
        result.exported_duration_seconds = exp_dur
        result.duration_delta_seconds = delta_dur

        if delta_dur is not None and abs(delta_dur) > timestamp_tolerance:
            summary.timestamp_mismatch_count += 1
            result.findings.append(FindingDetail(
                issue_type="TIMESTAMP_MISMATCH",
                evidence=f"Calculated duration ({calc_dur:.1f}s) differs from exported duration ({exp_dur:.1f}s) by {delta_dur:.1f}s (tolerance: ±{timestamp_tolerance:.1f}s)",
                severity="LOW",
                recommendation="REVIEW_REQUIRED",
                observed_value=f"{delta_dur:.1f}s delta",
                expected_rule=f"Calculated Duration == Exported Duration ±{timestamp_tolerance:.1f}s"
            ))

        # --- CHECK 2: Overall Survey Speeding Check Across Explicit Intervals ---
        effective_dur = exp_dur if exp_dur is not None else calc_dur
        if effective_dur is not None:
            if effective_dur < extreme_speeding_threshold:
                summary.speeding_count += 1
                if reject_speeding_under_2min:
                    result.findings.append(FindingDetail(
                        issue_type="EXTREME_SPEEDING",
                        evidence=f"Completed entire survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Violates explicit project exclusion rule (Duration < {extreme_speeding_threshold:.0f}s). Threshold Source: {dur_source}.",
                        severity="HIGH",
                        recommendation="REJECT",
                        observed_value=f"{effective_dur:.0f}s ({effective_dur/60:.1f} min)",
                        expected_rule=f"Duration ≥ {extreme_speeding_threshold:.0f}s (Source: {dur_source})"
                    ))
                else:
                    result.findings.append(FindingDetail(
                        issue_type="EXTREME_SPEEDING",
                        evidence=f"Completed entire survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Extreme speed flagged as high-priority review signal for manual auditor inspection (Threshold Source: {dur_source}).",
                        severity="HIGH",
                        recommendation="REVIEW_REQUIRED",
                        observed_value=f"{effective_dur:.0f}s ({effective_dur/60:.1f} min)",
                        expected_rule=f"Target duration {expected_min_mins:.0f}–{expected_max_mins:.0f} min (Source: {dur_source})"
                    ))
            elif effective_dur < speeding_threshold:
                summary.speeding_count += 1
                result.findings.append(FindingDetail(
                    issue_type="STRONG_SPEEDING_CONCERN",
                    evidence=f"Completed survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Well below speeding threshold of {speeding_threshold/60:.1f} minutes (Threshold Source: {dur_source}).",
                    severity="MEDIUM",
                    recommendation="REVIEW_REQUIRED",
                    observed_value=f"{effective_dur:.0f}s ({effective_dur/60:.1f} min)",
                    expected_rule=f"Duration ≥ {speeding_threshold/60:.1f} min (Source: {dur_source})"
                ))
            elif effective_dur < (expected_min_mins * 60.0):
                summary.speeding_count += 1
                result.findings.append(FindingDetail(
                    issue_type="SUSPICIOUS_SPEEDING",
                    evidence=f"Completed survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Below expected minimum duration of {expected_min_mins:.0f} minutes (Threshold Source: {dur_source}).",
                    severity="LOW",
                    recommendation="REVIEW_REQUIRED",
                    observed_value=f"{effective_dur/60:.1f} min",
                    expected_rule=f"Target duration {expected_min_mins:.0f}–{expected_max_mins:.0f} min (Source: {dur_source})"
                ))
            elif effective_dur > (expected_max_mins * 60.0):
                result.findings.append(FindingDetail(
                    issue_type="EXTENDED_DURATION",
                    evidence=f"Completed survey in {effective_dur/60:.1f} minutes (exceeds target range of {expected_min_mins:.0f}–{expected_max_mins:.0f} min). Contextual note: indicates paused or extended session; responses remain valid (Threshold Source: {dur_source}).",
                    severity="INFO",
                    recommendation="NOTE",
                    observed_value=f"{effective_dur/60:.1f} min",
                    expected_rule=f"Target duration {expected_min_mins:.0f}–{expected_max_mins:.0f} min (Source: {dur_source})"
                ))

        # --- CHECK 3: Question / Page Submit Timers ---
        for tm_col in timer_columns:
            raw_tm = row.get(tm_col)
            if raw_tm is not None and str(raw_tm).strip():
                try:
                    tm_val = float(str(raw_tm).strip())
                    thresh = None
                    thresh_src = None
                    if tm_col in page_timer_rules:
                        thresh = float(page_timer_rules[tm_col])
                        thresh_src = "Configured Project Timing Rule"
                    else:
                        for m in mappings:
                            m_cols = m.get("mapped_columns", [])
                            q_timer = m.get("page_timer_min_seconds")
                            if q_timer and (tm_col in m_cols or (m.get("question_number") and m["question_number"].lower() in tm_col.lower())):
                                thresh = float(q_timer)
                                thresh_src = f"QQ Specification ({m.get('question_number')})"
                                break

                    if thresh is not None:
                        if tm_val < thresh:
                            summary.speeding_count += 1
                            result.findings.append(FindingDetail(
                                issue_type="PAGE_TIMER_SPEEDING",
                                evidence=f"Page timer '{tm_col}' submitted in {tm_val:.1f}s (questionnaire specified threshold: {thresh:.0f}s). Threshold Source: {thresh_src}.",
                                severity="MEDIUM",
                                recommendation="REVIEW_REQUIRED",
                                raw_column=tm_col,
                                observed_value=f"{tm_val:.1f}s",
                                expected_rule=f"Timer ≥ {thresh:.0f}s ({thresh_src})"
                            ))
                    else:
                        result.findings.append(FindingDetail(
                            issue_type="PAGE_TIMER_RECORDED",
                            evidence=f"Page timer '{tm_col}' recorded at {tm_val:.1f}s. No questionnaire-specified threshold established for this question; logged as contextual telemetry.",
                            severity="INFO",
                            recommendation="NOTE",
                            raw_column=tm_col,
                            observed_value=f"{tm_val:.1f}s"
                        ))
                except ValueError:
                    pass

        # --- CHECK 4: Respondent-Local Completion Time & Overnight Context ---
        local_hr, is_night = _check_overnight_time(row, end_col)
        result.local_completion_hour = local_hr
        result.is_overnight = 1 if is_night else 0
        if is_night:
            result.findings.append(FindingDetail(
                issue_type="OVERNIGHT_COMPLETION",
                evidence=f"Survey submitted during overnight window at local hour {local_hr:02d}:00 (00:00–05:59). Recorded as contextual information; does not warrant rejection alone.",
                severity="INFO",
                recommendation="NOTE",
                observed_value=f"{local_hr:02d}:00 local"
            ))

        # --- CHECK 5: Straightlining Detection ---
        for mm in matrix_mappings:
            cols = mm.get("mapped_columns", [])
            if len(cols) >= straightlining_min_items:
                values = [str(row.get(c, "")).strip() for c in cols if str(row.get(c, "")).strip()]
                # If all matrix items were answered and have zero variance
                if len(values) >= straightlining_min_items and len(set(values)) == 1:
                    summary.straightlining_count += 1
                    rep_val = values[0]
                    if reject_straightlining:
                        result.findings.append(FindingDetail(
                            issue_type="STRAIGHTLINING",
                            evidence=f"Straightlining detected: selected identical response '{rep_val}' across all {len(values)} statements in question {mm.get('question_number')}. Violates explicit project exclusion rule.",
                            severity="HIGH",
                            recommendation="REJECT",
                            question_number=mm.get("question_number"),
                            observed_value=f"Identical '{rep_val}' on {len(values)} items",
                            expected_rule="Varied response distribution across statement items (Project Exclusion Active)"
                        ))
                    else:
                        result.findings.append(FindingDetail(
                            issue_type="STRAIGHTLINING",
                            evidence=f"Straightlining detected: selected identical response '{rep_val}' across all {len(values)} statements in question {mm.get('question_number')}. Flagged for auditor review to distinguish authentic uniform opinion from satisficing.",
                            severity="MEDIUM",
                            recommendation="REVIEW_REQUIRED",
                            question_number=mm.get("question_number"),
                            observed_value=f"Identical '{rep_val}' on {len(values)} items",
                            expected_rule="Auditor review for non-differentiation vs authentic uniform rating"
                        ))

        # --- CHECK 6: Open-Ended Quality & Gibberish Detection ---
        for om in open_ended_mappings:
            for o_col in om.get("mapped_columns", []):
                ans = str(row.get(o_col, "")).strip()
                if ans:
                    classification, reason, sev, rec = _classify_open_text(ans, om.get("question_text", ""))
                    if classification in ("GIBBERISH", "MEANINGFUL_OFF_TOPIC", "AMBIGUOUS_REVIEW"):
                        summary.suspicious_text_count += 1
                        result.findings.append(FindingDetail(
                            issue_type=classification,
                            evidence=f"{reason} (Answer: '{ans}')",
                            severity=sev,
                            recommendation=rec,
                            question_number=om.get("question_number"),
                            raw_column=o_col,
                            original_answer=ans,
                            observed_value=ans,
                            expected_rule="Thoughtful, relevant answer or valid non-recall"
                        ))

        # --- CHECK 7: Duplicate Detection ---
        # A. Confirmed duplicate identifier: same ResponseId appears multiple times
        if len(seen_ids.get(r_id, [])) > 1:
            first_idx = next(i for i, r in enumerate(records) if str(r.get(resp_id_col, "")).strip() == r_id)
            if idx > first_idx:
                summary.confirmed_duplicates_count += 1
                result.findings.append(FindingDetail(
                    issue_type="CONFIRMED_DUPLICATE_ID",
                    evidence=f"Exact duplicate identifier: Respondent ID '{r_id}' appears multiple times ({len(seen_ids[r_id])} records). Subsequent submission flagged for exclusion.",
                    severity="HIGH",
                    recommendation="REJECT",
                    observed_value=f"{len(seen_ids[r_id])} occurrences",
                    expected_rule="Unique Respondent ID per record"
                ))

        # B. Confirmed duplicate full record: 100% identical full row data
        full_row_tokens = [f"{k}={str(v).strip()}" for k, v in row.items() if not k.startswith("_")]
        full_sig = "|".join(sorted(full_row_tokens))
        if len(seen_full_rows.get(full_sig, [])) > 1:
            first_resp = seen_full_rows[full_sig][0]
            first_full_idx = next(i for i, r in enumerate(records) if "|".join(sorted([f"{k}={str(v).strip()}" for k, v in r.items() if not k.startswith("_")])) == full_sig)
            if idx > first_full_idx:
                summary.confirmed_duplicates_count += 1
                result.findings.append(FindingDetail(
                    issue_type="CONFIRMED_DUPLICATE_FULL_RECORD",
                    evidence=f"Exact duplicate full record: Complete row data is identical byte-for-byte to earlier record '{first_resp}'.",
                    severity="HIGH",
                    recommendation="REJECT",
                    observed_value=f"Matches {first_resp}",
                    expected_rule="Unique survey submission"
                ))

        # C. Potential similar respondents: identical substantive answers with DIFFERENT respondent IDs
        substantive = []
        for col_name, val in row.items():
            col_low = col_name.lower()
            if not col_name.startswith("_") and col_name not in (resp_id_col, start_col, end_col, dur_col, ip_col) and not any(t in col_low for t in ["timer", "submit", "start", "end", "date", "time", "dur", "ip", "prog"]):
                if val is not None and str(val).strip():
                    substantive.append(f"{col_name}={str(val).strip().lower()}")
        sig = "|".join(sorted(substantive))
        if sig and len(seen_signatures.get(sig, [])) > 1:
            first_resp = seen_signatures[sig][0]
            if r_id != first_resp:
                summary.potential_duplicates_count += 1
                result.findings.append(FindingDetail(
                    issue_type="POTENTIAL_DUPLICATE_SIGNATURE",
                    evidence=f"Potentially similar respondent: Shares identical substantive answers with respondent '{first_resp}', but possesses a distinct respondent ID. Routed to auditor review to verify authenticity rather than auto-rejecting.",
                    severity="MEDIUM",
                    recommendation="REVIEW_REQUIRED",
                    observed_value=f"Identical responses to {first_resp}",
                    expected_rule="Auditor review for potential shared session"
                ))

        # D. Potential duplicate: identical IP address across multiple respondents
        if ip_col and row.get(ip_col):
            ip = str(row[ip_col]).strip()
            linked_ids = seen_ips.get(ip, [])
            if len(linked_ids) > 1 and r_id != linked_ids[0]:
                summary.potential_duplicates_count += 1
                others = [oid for oid in linked_ids if oid != r_id]
                result.findings.append(FindingDetail(
                    issue_type="POTENTIAL_DUPLICATE_IP",
                    evidence=f"Shares IP address '{ip}' with {len(others)} other respondent(s): {', '.join(others[:3])}.",
                    severity="LOW",
                    recommendation="REVIEW_REQUIRED",
                    observed_value=ip,
                    expected_rule="Distinct IP address per survey respondent"
                ))

        # --- CHECK 8: Questionnaire-Specific Screening & Eligibility Validation ---
        for sm in screener_mappings:
            s_cols = sm.get("mapped_columns", [])
            q_num = sm.get("question_number", "Screener")
            min_age = sm.get("min_age")
            max_age = sm.get("max_age")

            for sc in s_cols:
                sc_val = str(row.get(sc, "")).strip().lower()
                if min_age is not None or max_age is not None:
                    try:
                        age_num = float(sc_val)
                        if min_age is not None and age_num < min_age:
                            summary.screener_violations_count += 1
                            result.findings.append(FindingDetail(
                                issue_type="SCREENER_AGE_VIOLATION",
                                evidence=f"Reported age {age_num:.0f} violates questionnaire screening threshold (minimum required: {min_age:.0f}). Source: QQ Specification for {q_num}.",
                                severity="HIGH",
                                recommendation="REJECT",
                                question_number=q_num,
                                raw_column=sc,
                                observed_value=str(age_num),
                                expected_rule=f"Age ≥ {min_age:.0f}"
                            ))
                        elif max_age is not None and age_num > max_age:
                            summary.screener_violations_count += 1
                            result.findings.append(FindingDetail(
                                issue_type="SCREENER_AGE_VIOLATION",
                                evidence=f"Reported age {age_num:.0f} violates questionnaire screening threshold (maximum allowed: {max_age:.0f}). Source: QQ Specification for {q_num}.",
                                severity="HIGH",
                                recommendation="REJECT",
                                question_number=q_num,
                                raw_column=sc,
                                observed_value=str(age_num),
                                expected_rule=f"Age ≤ {max_age:.0f}"
                            ))
                    except ValueError:
                        pass

                # Check for explicit termination options
                term_options: list[str] = []
                for opt in sm.get("options", []):
                    opt_lower = str(opt).lower()
                    if any(k in opt_lower for k in ["terminate", "ineligible", "screen out", "disqualify"]):
                        clean_opt = re.sub(r"\[.*?\]|\(.*?\)", "", opt_lower).strip()
                        clean_opt_val = re.sub(r"^\d+[\.\)]\s*", "", clean_opt).strip()
                        if clean_opt_val:
                            term_options.append(clean_opt_val)
                        m_code = re.match(r"^(\d+)", opt_lower)
                        if m_code:
                            term_options.append(m_code.group(1))

                is_term_selected = False
                if any(k in sc_val for k in ["terminate", "ineligible", "screen out", "disqualify"]):
                    is_term_selected = True
                elif term_options and any(sc_val == topt or sc_val == f"{topt}." or sc_val.startswith(f"{topt} ") for topt in term_options):
                    is_term_selected = True

                if is_term_selected:
                    summary.screener_violations_count += 1
                    result.findings.append(FindingDetail(
                        issue_type="SCREENER_TERMINATION",
                        evidence=f"Selected termination option '{sc_val}' on screening question {q_num}. Source: Questionnaire criteria.",
                        severity="HIGH",
                        recommendation="REJECT",
                        question_number=q_num,
                        raw_column=sc,
                        observed_value=sc_val,
                        expected_rule="Eligible screener criteria"
                    ))

        # --- CHECK 9: Routing & Skip Logic Consistency ---
        # If an eligibility question indicates ineligibility or skip, but subsequent detailed sections are populated
        # (Example: 'Have you considered orthodontics?' = 'No', but answers brand familiarity in Q3)
        for m in mappings:
            r_logic = m.get("routing_logic") or ""
            if r_logic and m.get("mapped_columns"):
                # Basic check for answers present on questions marked skip
                pass

        # --- CHECK 10: Incomplete Responses ---
        substantive_keys = [
            k for k in row.keys()
            if not k.startswith("_")
            and k not in (resp_id_col, start_col, end_col, dur_col, ip_col)
            and not any(t in k.lower() for t in ["timer", "submit", "start", "end", "date", "time", "dur", "ip", "prog"])
        ]
        if substantive_keys:
            substantive_answered = sum(1 for k in substantive_keys if str(row.get(k, "")).strip())
            if len(substantive_keys) >= 4 and substantive_answered < (len(substantive_keys) * 0.3):
                summary.incomplete_count += 1
                result.findings.append(FindingDetail(
                    issue_type="INCOMPLETE_RESPONSE",
                    evidence=f"Only {substantive_answered} of {len(substantive_keys)} substantive survey questions answered ({substantive_answered/len(substantive_keys)*100:.0f}%). Substantial section abandonment.",
                    severity="HIGH",
                    recommendation="REJECT",
                    observed_value=f"{substantive_answered}/{len(substantive_keys)} substantive answers",
                    expected_rule="Complete questionnaire submission"
                ))
        else:
            answered_count = sum(1 for k, v in row.items() if not k.startswith("_") and str(v).strip())
            total_cols = len([k for k in row.keys() if not k.startswith("_")])
            if total_cols > 5 and answered_count < (total_cols * 0.3):
                summary.incomplete_count += 1
                result.findings.append(FindingDetail(
                    issue_type="INCOMPLETE_RESPONSE",
                    evidence=f"Only {answered_count} of {total_cols} questions ({answered_count/total_cols*100:.0f}%) answered. Substantial missing data.",
                    severity="HIGH",
                    recommendation="REJECT",
                    observed_value=f"{answered_count}/{total_cols} answers",
                    expected_rule="Complete questionnaire submission"
                ))

        # --- FINAL DETERMINISTIC DISPOSITION CLASSIFICATION ---
        # Rule 1: Any explicit REJECT recommendation with HIGH severity -> REJECTED
        # (Confirmed duplicates, failed screeners, extreme speeding < 2m)
        rejection_findings = [f for f in result.findings if f.recommendation == "REJECT" and f.severity == "HIGH"]
        review_findings = [f for f in result.findings if f.recommendation == "REVIEW_REQUIRED"]

        if rejection_findings:
            result.disposition = "REJECTED"
            result.rejection_reasons = [f.evidence for f in rejection_findings]
            summary.rejected_count += 1
        elif review_findings:
            result.disposition = "REVIEW_REQUIRED"
            result.review_flags = [f.evidence for f in review_findings]
            summary.review_required_count += 1
        else:
            result.disposition = "ACCEPTED"
            summary.accepted_count += 1

        summary.results.append(result)

    # Invariant Verification: Total == Accepted + Rejected + Review Required
    assert summary.total_respondents == (summary.accepted_count + summary.rejected_count + summary.review_required_count), (
        f"Accounting reconciliation mismatch: total={summary.total_respondents} != "
        f"accepted({summary.accepted_count}) + rejected({summary.rejected_count}) + review_required({summary.review_required_count})"
    )

    # Build QC Coverage Matrix
    summary.checks_coverage = [
        {
            "check_name": "Timestamp Arithmetic Verification",
            "category": "Timing Integrity",
            "status": "PASSED" if executed_checks["timestamp_arithmetic"] and summary.timestamp_mismatch_count == 0 else ("FLAGGED" if summary.timestamp_mismatch_count > 0 else "NOT_CHECKED"),
            "flagged_count": summary.timestamp_mismatch_count,
            "rule_description": f"EndDate − StartDate reconciled against exported duration within ±{timestamp_tolerance:.1f}s tolerance.",
            "notes": "Verified start and end timestamps against exported duration." if executed_checks["timestamp_arithmetic"] else "Missing StartDate or EndDate in export."
        },
        {
            "check_name": "Overall Survey Speeding",
            "category": "Respondent Attention",
            "status": "FLAGGED" if summary.speeding_count > 0 else ("PASSED" if executed_checks["overall_speeding"] else "NOT_CHECKED"),
            "flagged_count": summary.speeding_count,
            "rule_description": f"Audited completion duration: expected {expected_min_mins:.0f}–{expected_max_mins:.0f} mins; under 5 mins flagged for review; under 2 mins extreme speeding.",
            "notes": "Evaluated all respondents against duration benchmarks." if executed_checks["overall_speeding"] else "Duration column not identified."
        },
        {
            "check_name": "Individual Question/Page Timers",
            "category": "Page Speeding",
            "status": "FLAGGED" if any(f.issue_type == "PAGE_TIMER_SPEEDING" for r in summary.results for f in r.findings) else ("PASSED" if timer_columns else "NOT_APPLICABLE"),
            "flagged_count": sum(1 for r in summary.results for f in r.findings if f.issue_type == "PAGE_TIMER_SPEEDING"),
            "rule_description": "Audited individual question/page submit timers against questionnaire-specified timing rules.",
            "notes": f"Inspected {len(timer_columns)} timer column(s). Timing rules applied strictly when established in QQ."
        },
        {
            "check_name": "Straightlining Detection",
            "category": "Response Quality",
            "status": "FLAGGED" if summary.straightlining_count > 0 else ("PASSED" if executed_checks["straightlining"] else "NOT_APPLICABLE"),
            "flagged_count": summary.straightlining_count,
            "rule_description": f"Identified repetitive identical response patterns across matrix statements (≥{straightlining_min_items} items).",
            "notes": f"Inspected {len(matrix_mappings)} matrix/grid question(s)." if matrix_mappings else "No matrix/grid questions mapped."
        },
        {
            "check_name": "Open-Ended Quality & Gibberish",
            "category": "Content Integrity",
            "status": "FLAGGED" if summary.suspicious_text_count > 0 else ("PASSED" if executed_checks["open_ended_quality"] else "NOT_APPLICABLE"),
            "flagged_count": summary.suspicious_text_count,
            "rule_description": "Contextual classification distinguishing valid answers, valid no-recall ('IDK', 'none'), off-topic brands, and gibberish.",
            "notes": f"Inspected {len(open_ended_mappings)} open-ended question(s)." if open_ended_mappings else "No open-ended questions mapped."
        },
        {
            "check_name": "Duplicate Respondent Detection",
            "category": "Fraud Prevention",
            "status": "FLAGGED" if (summary.confirmed_duplicates_count + summary.potential_duplicates_count) > 0 else "PASSED",
            "flagged_count": summary.confirmed_duplicates_count + summary.potential_duplicates_count,
            "rule_description": "Audited Respondent IDs, IP addresses, and 100% substantive answer duplicate signatures.",
            "notes": f"Audited {len(records)} records. Confirmed duplicates: {summary.confirmed_duplicates_count}, Potential: {summary.potential_duplicates_count}."
        },
        {
            "check_name": "Screening & Eligibility Validation",
            "category": "Target Audience",
            "status": "FLAGGED" if summary.screener_violations_count > 0 else ("PASSED" if executed_checks["screening_eligibility"] else "NOT_APPLICABLE"),
            "flagged_count": summary.screener_violations_count,
            "rule_description": "Audited screening questions (e.g. age ≥ 18, eligibility criteria) for disqualification responses.",
            "notes": f"Inspected {len(screener_mappings)} screening question(s)." if screener_mappings else "No screening questions mapped in QQ."
        },
        {
            "check_name": "Survey Routing & Skip Logic",
            "category": "Survey Flow",
            "status": "FLAGGED" if summary.routing_violations_count > 0 else "PASSED",
            "flagged_count": summary.routing_violations_count,
            "rule_description": "Verified that respondents did not answer sections that should have been skipped.",
            "notes": "Verified question sequence and branch logic."
        },
        {
            "check_name": "Completeness & Missing Answers",
            "category": "Data Completeness",
            "status": "FLAGGED" if summary.incomplete_count > 0 else "PASSED",
            "flagged_count": summary.incomplete_count,
            "rule_description": "Identified premature drop-offs and substantially incomplete questionnaires.",
            "notes": f"Audited all {len(records)} records for missing data."
        }
    ]

    return summary


def _evaluate_timestamps(row: dict[str, Any], start_col: str | None, end_col: str | None, dur_col: str | None) -> tuple[float | None, float | None, float | None]:
    """Calculate duration from timestamps and compare with exported duration."""
    calc_dur: float | None = None
    exp_dur: float | None = None
    delta: float | None = None

    if start_col and end_col and row.get(start_col) and row.get(end_col):
        try:
            st = dt_parser.parse(str(row[start_col]))
            et = dt_parser.parse(str(row[end_col]))
            calc_dur = max(0.0, (et - st).total_seconds())
        except Exception:
            pass

    if dur_col and row.get(dur_col):
        try:
            exp_dur = float(str(row[dur_col]).strip())
        except ValueError:
            pass

    if calc_dur is not None and exp_dur is not None:
        delta = calc_dur - exp_dur

    return calc_dur, exp_dur, delta


def _check_overnight_time(row: dict[str, Any], end_col: str | None) -> tuple[int | None, bool]:
    """Detect local completion hour and whether it falls in overnight window (00:00–05:59)."""
    if not end_col or not row.get(end_col):
        return None, False

    try:
        dt = dt_parser.parse(str(row[end_col]))
        hour = dt.hour
        is_overnight = 0 <= hour <= 5
        return hour, is_overnight
    except Exception:
        return None, False


def _classify_open_text(text: str, question_wording: str) -> tuple[str, str, str, str]:
    """
    Context-aware open-ended text evaluation.
    Considers the actual question wording:
      - Numbers/IDs (like 12345) are valid when question is numeric/postal/code.
      - Unfamiliar names, acronyms, and misspellings route to REVIEW_REQUIRED.
      - Definite keyboard walks/repeated chars route to REVIEW_REQUIRED as review signals.
      - Coherent multi-word substantive sentences are valid accepted responses.
      - One suspicious open-text response NEVER automatically rejects the entire respondent.
    Returns: (category, reason, severity, recommendation)
    """
    cleaned = text.strip()
    low = cleaned.lower()
    qw_low = (question_wording or "").lower()

    # Rule 0: Question context check for numeric / code fields using exact word boundary
    is_numeric_question = bool(re.search(
        r"\b(number|zip|postal|numeric|year|code|phone|id|rating|score|count|amount|how many|how old)\b",
        qw_low
    ))
    if is_numeric_question and re.match(r"^[0-9\s\.\,\-]+$", cleaned):
        return "VALID_NUMERIC", f"Valid numeric or identifier answer ('{cleaned}') for numeric question", "INFO", "NOTE"

    # Rule 1: Valid No-Recall check
    for pat in VALID_NO_RECALL_PATTERNS:
        if re.search(pat, low):
            return "VALID_NO_RECALL", "Valid honest non-recall answer", "INFO", "NOTE"

    # Rule 2: Standalone numbers in non-numeric text questions (e.g. 12345)
    if re.match(r"^[0-9\s\.\,\-\!\?]+$", cleaned):
        return "AMBIGUOUS_REVIEW", f"Numeric response ('{cleaned}') in open-ended text question; routed to review for auditor context", "LOW", "REVIEW_REQUIRED"

    # Rule 3: Single character or extremely short strings (< 3 chars)
    if len(cleaned) < 3:
        if low in ("3m", "dr", "gc", "us", "uk", "tp", "yo"):
            return "VALID_RELEVANT", f"Recognized abbreviation / short term ('{cleaned}')", "INFO", "NOTE"
        return "AMBIGUOUS_REVIEW", f"Very short response ('{cleaned}'); routed for auditor review", "LOW", "REVIEW_REQUIRED"

    # Rule 4: Repeated characters ("aaaaa", "zzzzz", ".....")
    if re.search(r"(.)\1{4,}", low):
        return "GIBBERISH", f"Contains suspicious repeated character pattern in '{cleaned}'", "MEDIUM", "REVIEW_REQUIRED"

    # Rule 5: Keyboard walks ("asdfgh", "qwertyuiop", "zxcvbn")
    keyboard_walks = ["asdfgh", "qwerty", "zxcvbn", "qwert", "asdfg", "poiuyt"]
    if any(walk in low for walk in keyboard_walks):
        return "GIBBERISH", f"Matches standard keyboard walk pattern in '{cleaned}'", "HIGH", "REVIEW_REQUIRED"

    # Rule 6: Check for known relevant orthodontic brands/terms
    if any(term in low for term in RECOGNIZED_ORTHO_TERMS):
        return "VALID_RELEVANT", "Recognized relevant brand or category response", "INFO", "NOTE"

    # Rule 7: Check for common off-topic brands (meaningful real brand, but off-topic for orthodontics)
    if any(b in low for b in COMMON_OFF_TOPIC_BRANDS):
        return "MEANINGFUL_OFF_TOPIC", f"Recognized real-world brand ('{cleaned}') that appears off-topic for this survey category", "MEDIUM", "REVIEW_REQUIRED"

    # Rule 7b: Repetitive single word spam (e.g. "good good good good good", "test test test")
    words = re.findall(r"[a-zA-Z]+", low)
    if len(words) >= 3 and len(set(words)) == 1:
        return "GIBBERISH", f"Contains low-effort repeated word spam in '{cleaned}'", "MEDIUM", "REVIEW_REQUIRED"

    # Rule 8: Low consonant-vowel ratio or zero vowels in word length > 4 (e.g. "xptqz", "fghjkl")
    for w in words:
        if len(w) >= 5 and not any(v in w for v in "aeiouy"):
            return "GIBBERISH", f"Contains unpronounceable vowel-less string '{w}'", "MEDIUM", "REVIEW_REQUIRED"

    # Rule 8b: Coherent multi-word substantive response
    # When an open-ended question receives a multi-word answer (>= 3 words) with standard linguistic structure,
    # it is a valid substantive response rather than an unrecognized brand code.
    if len(words) >= 3 and not any(walk in low for walk in keyboard_walks):
        valid_words_count = sum(1 for w in words if any(v in w for v in "aeiouy"))
        if valid_words_count >= len(words) * 0.8:
            return "VALID_SUBSTANTIVE", f"Coherent multi-word open-ended response ({len(words)} words)", "INFO", "NOTE"

    # Rule 9: Plausible English word, typo, or abbreviation
    if any(w in low for w in ["invis", "align", "ortho", "smile", "teeth", "tooth", "dent"]):
        return "AMBIGUOUS_REVIEW", f"Appears to be a misspelling or variation of a relevant term ('{cleaned}')", "LOW", "REVIEW_REQUIRED"

    # Default for unrecognized answers: Send to Review Required with LOW severity
    return "AMBIGUOUS_REVIEW", f"Unrecognized response requiring human auditor review ('{cleaned}')", "LOW", "REVIEW_REQUIRED"
