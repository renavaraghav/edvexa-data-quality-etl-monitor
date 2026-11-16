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
from collections import Counter
import re
from typing import Any

from dateutil import parser as dt_parser
from app.services.survey_rules import confirmed, country_matches, resolve_eligibility_rules, resolve_page_rules

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
    resp_id_col: str | None,
    timing_rules: dict[str, Any] | None = None
) -> SurveyQcExecutionSummary:
    """
    Execute full quality audit on respondent records.
    Every respondent is evaluated across all active checks and assigned exactly one disposition:
    ACCEPTED, REJECTED, or REVIEW_REQUIRED.
    """
    summary = SurveyQcExecutionSummary(total_respondents=len(records))
    rules = timing_rules or {}

    dur_source = rules.get("duration_source") or "Not configured"
    sources = rules.get("rule_sources", {})
    speeding_threshold = rules.get("speeding_threshold_seconds")
    expected_min_mins = rules.get("expected_duration_min_minutes")
    expected_max_mins = rules.get("expected_duration_max_minutes")
    extreme_speeding_threshold = rules.get("extreme_speeding_threshold_seconds")
    reject_speeding_under_2min = bool(rules.get("reject_speeding_under_2min", False))
    if reject_speeding_under_2min and (extreme_speeding_threshold is None or not math.isfinite(extreme_speeding_threshold) or extreme_speeding_threshold <= 0):
        raise ValueError("Strict speeding rejection requires an explicit positive cutoff.")
    reject_straightlining = bool(rules.get("reject_straightlining", False))
    if reject_straightlining and any(rules.get(k) is None for k in ("straightline_threshold_pct", "straightlining_min_grid_size")):
        raise ValueError("Strict straightlining requires explicit percentage and minimum size thresholds.")
    page_rules = rules.get("effective_page_rules")
    if page_rules is None:
        page_rules = resolve_page_rules(rules, mappings, timer_columns)
    straightlining_min_items = int(rules.get("straightlining_min_grid_size") or 4)
    straightlining_pct = rules.get("straightline_threshold_pct") or 100
    required_questions = set(rules.get("required_questions", []))
    timestamp_tolerance = float(rules.get("timestamp_tolerance_seconds", 2.0))

    start_col = system_columns.get("start_time")
    end_col = system_columns.get("end_time")
    dur_col = system_columns.get("duration")
    ip_col = system_columns.get("ip_address")

    # Group mapped questions by type
    active = [m for m in mappings if m.get("mapped_columns") and m.get("mapping_status") not in {"SKIPPED", "UNMAPPED"}]
    by_question = {m["question_number"]: m["mapped_columns"] for m in active}
    matrix_mappings = [m for m in active if m.get("question_type") == "matrix_grid"] if rules.get("straightlining_enabled", True) else []
    open_ended_mappings = [m for m in active if m.get("question_type") == "open_ended"] if rules.get("open_text_enabled", True) else []
    eligibility = rules.get("effective_eligibility_rules")
    if eligibility is None:
        eligibility = resolve_eligibility_rules(active, system_columns.get("country"), [], enabled=rules.get("screener_enabled", True))
    configured_routing = rules.get("routing_rules", [])
    routing = [r for r in configured_routing if len(by_question.get(r["control_question"], [])) == 1 and r["skip_question"] in by_question]
    required = required_questions & by_question.keys() if rules.get("completeness_enabled", bool(required_questions)) else set()
    duplicate_enabled = rules.get("duplicate_enabled", True)
    total_tested = timer_tested = routed = complete_tested = eligibility_tested = 0

    # Track identifiers for duplicate detection
    seen_ids: dict[str, list[str]] = {}
    seen_ips: dict[str, list[str]] = {}
    seen_signatures: dict[str, list[str]] = {}
    seen_full_rows: dict[tuple, list[tuple]] = {}

    # Pass 1: Build duplicate lookup indices
    for idx, row in enumerate(records):
        supplied_id = str(row.get(resp_id_col) or "").strip()
        r_id = supplied_id or f"RESP-{row.get('_physical_row', idx + 1):04d}"
        if supplied_id:
            seen_ids.setdefault(supplied_id, []).append(idx)

        if ip_col and row.get(ip_col):
            ip = str(row[ip_col]).strip()
            seen_ips.setdefault(ip, []).append(r_id)

        # Full record hash (exact duplicate full row)
        full_sig = tuple(sorted((k, str(v)) for k, v in row.items() if k != "_physical_row"))
        seen_full_rows.setdefault(full_sig, []).append((idx, r_id))

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
        supplied_id = str(row.get(resp_id_col) or "").strip()
        r_id = supplied_id or f"RESP-{row.get('_physical_row', idx + 1):04d}"
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
        valid_duration = effective_dur is not None and not (start_col and end_col and row.get(start_col) and row.get(end_col) and calc_dur is None) and not (delta_dur is not None and abs(delta_dur) > timestamp_tolerance)
        if valid_duration and any(v is not None for v in (extreme_speeding_threshold, speeding_threshold, expected_min_mins, expected_max_mins)):
            total_tested += 1
            if extreme_speeding_threshold is not None and effective_dur < extreme_speeding_threshold:
                if reject_speeding_under_2min:
                    result.findings.append(FindingDetail(
                        issue_type="EXTREME_SPEEDING",
                        evidence=f"Completed entire survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Violates explicit project exclusion rule (Duration < {extreme_speeding_threshold:.0f}s). Threshold Source: {sources.get('extreme_speeding_threshold_seconds', 'Explicit cutoff')}.",
                        severity="HIGH",
                        recommendation="REJECT",
                        observed_value=f"{effective_dur:.0f}s ({effective_dur/60:.1f} min)",
                        expected_rule=f"Duration ≥ {extreme_speeding_threshold:.0f}s (Source: {sources.get('extreme_speeding_threshold_seconds', 'Explicit cutoff')})"
                    ))
                else:
                    result.findings.append(FindingDetail(
                        issue_type="EXTREME_SPEEDING",
                        evidence=f"Completed entire survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Extreme speed flagged as high-priority review signal for manual auditor inspection (Threshold Source: {sources.get('extreme_speeding_threshold_seconds', 'Explicit cutoff')}).",
                        severity="HIGH",
                        recommendation="REVIEW_REQUIRED",
                        observed_value=f"{effective_dur:.0f}s ({effective_dur/60:.1f} min)",
                        expected_rule=f"Duration ≥ {extreme_speeding_threshold:g}s"
                    ))
            elif speeding_threshold is not None and effective_dur < speeding_threshold:
                result.findings.append(FindingDetail(
                    issue_type="STRONG_SPEEDING_CONCERN",
                    evidence=f"Completed survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Well below speeding threshold of {speeding_threshold/60:.1f} minutes (Threshold Source: {sources.get('speeding_threshold_seconds', 'Configured cutoff')}).",
                    severity="MEDIUM",
                    recommendation="REVIEW_REQUIRED",
                    observed_value=f"{effective_dur:.0f}s ({effective_dur/60:.1f} min)",
                    expected_rule=f"Duration ≥ {speeding_threshold/60:.1f} min (Source: {sources.get('speeding_threshold_seconds', 'Configured cutoff')})"
                ))
            elif expected_min_mins is not None and effective_dur < (expected_min_mins * 60.0):
                result.findings.append(FindingDetail(
                    issue_type="SUSPICIOUS_SPEEDING",
                    evidence=f"Completed survey in {effective_dur:.0f} seconds ({effective_dur/60:.1f} min). Below expected minimum duration of {expected_min_mins:.0f} minutes (Threshold Source: {dur_source}).",
                    severity="LOW",
                    recommendation="REVIEW_REQUIRED",
                    observed_value=f"{effective_dur/60:.1f} min",
                    expected_rule=f"Minimum duration {expected_min_mins:.0f} min (Source: {dur_source})"
                ))
            elif expected_max_mins is not None and effective_dur > (expected_max_mins * 60.0):
                result.findings.append(FindingDetail(
                    issue_type="EXTENDED_DURATION",
                    evidence=f"Completed survey in {effective_dur/60:.1f} minutes (exceeds configured maximum of {expected_max_mins:g} min). Contextual note (Threshold Source: {sources.get('expected_duration_max_minutes', dur_source)}).",
                    severity="INFO",
                    recommendation="NOTE",
                    observed_value=f"{effective_dur/60:.1f} min",
                    expected_rule=f"Maximum duration {expected_max_mins:g} min (Source: {sources.get('expected_duration_max_minutes', dur_source)})"
                ))

        # --- CHECK 3: Question / Page Submit Timers ---
        timer_evaluated = False
        for tm_col in timer_columns:
            raw_tm = row.get(tm_col)
            if raw_tm is not None and str(raw_tm).strip():
                try:
                    tm_val = float(str(raw_tm).strip())
                    if not math.isfinite(tm_val) or tm_val < 0:
                        continue
                    rule = page_rules.get(tm_col)
                    if rule:
                        thresh, thresh_src = rule["threshold"], rule["source"]
                        timer_evaluated = True
                        if tm_val < thresh:
                            if rule["action"] == "REJECT":
                                result.findings.append(FindingDetail(
                                    issue_type="PAGE_TIMER_SPEEDING",
                                    evidence=f"Page timer '{tm_col}' submitted in {tm_val:.1f}s (specified threshold: {thresh:.0f}s). Violates strict page timer rejection rule. Threshold Source: {thresh_src}.",
                                    severity="HIGH",
                                    recommendation="REJECT",
                                    raw_column=tm_col,
                                    observed_value=f"{tm_val:.1f}s",
                                    expected_rule=f"Timer ≥ {thresh:.0f}s ({thresh_src})"
                                ))
                            else:
                                result.findings.append(FindingDetail(
                                    issue_type="PAGE_TIMER_SPEEDING",
                                    evidence=f"Page timer '{tm_col}' submitted in {tm_val:.1f}s (specified threshold: {thresh:.0f}s). Flagged as review signal. Threshold Source: {thresh_src}.",
                                    severity="MEDIUM",
                                    recommendation="REVIEW_REQUIRED",
                                    raw_column=tm_col,
                                    observed_value=f"{tm_val:.1f}s",
                                    expected_rule=f"Timer ≥ {thresh:.0f}s ({thresh_src})"
                                ))
                    else:
                        result.findings.append(FindingDetail(
                            issue_type="PAGE_TIMER_RECORDED",
                            evidence=f"Page timer '{tm_col}' recorded at {tm_val:.1f}s. No applicable confirmed threshold; logged as contextual telemetry.",
                            severity="INFO",
                            recommendation="NOTE",
                            raw_column=tm_col,
                            observed_value=f"{tm_val:.1f}s"
                        ))
                except ValueError:
                    pass

        timer_tested += int(timer_evaluated)

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
                if len(values) >= straightlining_min_items and (modal := Counter(values).most_common(1)[0])[1] * 100 >= straightlining_pct * len(values):
                    summary.straightlining_count += 1
                    rep_val, frequency = modal
                    if reject_straightlining and confirmed(mm):
                        result.findings.append(FindingDetail(
                            issue_type="STRAIGHTLINING",
                            evidence=f"Straightlining detected: selected identical response '{rep_val}' on {frequency} of {len(values)} answered statements in question {mm.get('question_number')}. Violates explicit project exclusion rule.",
                            severity="HIGH",
                            recommendation="REJECT",
                            question_number=mm.get("question_number"),
                            observed_value=f"Identical '{rep_val}' on {frequency}/{len(values)} items",
                            expected_rule="Varied response distribution across statement items (Project Exclusion Active)"
                        ))
                    else:
                        result.findings.append(FindingDetail(
                            issue_type="STRAIGHTLINING",
                            evidence=f"Straightlining detected: selected identical response '{rep_val}' on {frequency} of {len(values)} answered statements in question {mm.get('question_number')}. Flagged for auditor review to distinguish authentic uniform opinion from satisficing.",
                            severity="MEDIUM",
                            recommendation="REVIEW_REQUIRED",
                            question_number=mm.get("question_number"),
                            observed_value=f"Identical '{rep_val}' on {frequency}/{len(values)} items",
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
        if duplicate_enabled and supplied_id and len(seen_ids.get(r_id, [])) > 1:
            first_idx = seen_ids[r_id][0]
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
        full_sig = tuple(sorted((k, str(v)) for k, v in row.items() if k != "_physical_row"))
        if duplicate_enabled and len(seen_full_rows.get(full_sig, [])) > 1:
            first_full_idx, first_resp = seen_full_rows[full_sig][0]
            if idx > first_full_idx:
                summary.confirmed_duplicates_count += 1
                result.findings.append(FindingDetail(
                    issue_type="CONFIRMED_DUPLICATE_FULL_RECORD",
                    evidence=f"Exact duplicate full record: Complete row data is identical in all imported fields to earlier record '{first_resp}'.",
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
        if duplicate_enabled and sig and len(seen_signatures.get(sig, [])) > 1:
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
            if duplicate_enabled and len(linked_ids) > 1 and r_id != linked_ids[0]:
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

        # --- CHECK 8: Confirmed eligibility rules and their recorded scope ---
        checked_eligibility = False
        for rule in eligibility:
            scope = rule["scope"]
            column, question = scope["column"], scope["question_number"]
            if scope.get("countries") and not any(
                country_matches(row.get(scope["country_column"], ""), country)
                for country in scope["countries"]
            ):
                continue
            value = str(row.get(column, "")).strip()
            if not value:
                continue
            source = rule["source"]
            minimum, maximum = rule["min_age"], rule["max_age"]
            age = None
            if minimum is not None or maximum is not None:
                try:
                    candidate = float(value)
                    if math.isfinite(candidate) and 0 <= candidate <= 130:
                        age = candidate
                except ValueError:
                    pass
            if age is not None:
                checked_eligibility = True
                below_minimum = minimum is not None and age < minimum
                above_maximum = maximum is not None and age > maximum
                violation = minimum if below_minimum else maximum if above_maximum else None
                if violation is not None:
                    comparison = "≥" if below_minimum else "≤"
                    condition = f" for {'/'.join(scope['countries'])}" if scope.get("countries") else ""
                    result.findings.append(FindingDetail(
                        issue_type="SCREENER_AGE_VIOLATION", severity="HIGH", recommendation=rule["action"],
                        question_number=question, raw_column=column, observed_value=str(age),
                        evidence=f"Reported age {age:g} violates age eligibility for {question}{condition}. Source: {source}.",
                        expected_rule=f"Age {comparison} {violation:g} years{condition}",
                    ))
            if rule["termination_values"]:
                checked_eligibility = True
                if value.casefold() in rule["termination_values"]:
                    result.findings.append(FindingDetail(
                        issue_type="SCREENER_TERMINATION", severity="HIGH", recommendation=rule["action"],
                        question_number=question, raw_column=column, observed_value=value,
                        evidence=f"Selected explicit termination option '{value}' for {question}. Source: {source}.",
                        expected_rule="Eligible screener option",
                    ))
        eligibility_tested += int(checked_eligibility)

        skipped = set()
        applicability_unknown = set()
        routing_evaluated = False
        for rule in routing:
            control = str(row.get(by_question[rule["control_question"]][0], "")).strip()
            if not control:
                applicability_unknown.add(rule["skip_question"])
                continue
            routing_evaluated = True
            if control.casefold() == rule["equals"].casefold():
                target = rule["skip_question"]
                skipped.add(target)
                if any(str(row.get(c, "")).strip() for c in by_question[target]):
                    result.findings.append(FindingDetail(
                        issue_type="ROUTING_VIOLATION", question_number=target, severity="MEDIUM",
                        recommendation="REVIEW_REQUIRED",
                        evidence=f"{target} was answered despite an explicit skip rule triggered by {rule['control_question']}.",
                        expected_rule=f"Skip {target} when {rule['control_question']} equals {rule['equals']}"))
        routed += int(routing_evaluated)
        eligible = required - skipped - applicability_unknown
        # With unresolved routing, required fields may not apply to this respondent.
        if eligible and not (configured_routing and len(routing) != len(configured_routing)):
            complete_tested += 1
            answered = sum(any(str(row.get(c, "")).strip() for c in by_question[q]) for q in eligible)
            threshold = rules.get("completeness_threshold_pct") or 100
            if answered * 100 < threshold * len(eligible):
                result.findings.append(FindingDetail(
                    issue_type="INCOMPLETE_RESPONSE", severity="MEDIUM", recommendation="REVIEW_REQUIRED",
                    evidence=f"Answered {answered}/{len(eligible)} confirmed applicable required questions.",
                    expected_rule=f"At least {threshold:g}% of applicable required questions answered"))

        # --- FINAL DETERMINISTIC DISPOSITION CLASSIFICATION ---
        # Rule 1: Any explicit REJECT recommendation with HIGH severity -> REJECTED
        # (Confirmed duplicates, failed screeners, extreme speeding < 2m)
        rejection_findings = [f for f in result.findings if f.recommendation == "REJECT" and f.severity == "HIGH"]
        review_findings = [f for f in result.findings if f.recommendation == "REVIEW_REQUIRED"]

        result.review_flags = [f.evidence for f in review_findings]
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

    groups = [
        ("Timestamp Arithmetic Verification", "Timing Integrity", ("TIMESTAMP_MISMATCH",), "timestamp_mismatch_count",
         sum(r.duration_delta_seconds is not None for r in summary.results), f"Timestamp tolerance: {timestamp_tolerance:g}s."),
        ("Overall Survey Speeding", "Respondent Attention", ("EXTREME_SPEEDING", "STRONG_SPEEDING_CONCERN", "SUSPICIOUS_SPEEDING"), None,
         total_tested, f"Seconds: extreme={extreme_speeding_threshold}, concern={speeding_threshold}; expected minutes={expected_min_mins}–{expected_max_mins}."),
        ("Individual Question/Page Timers", "Page Speeding", ("PAGE_TIMER_SPEEDING",), None,
         timer_tested, f"Applicable thresholds on {len(page_rules)} mapped timer column(s)."),
        ("Straightlining Detection", "Response Quality", ("STRAIGHTLINING",), "straightlining_count",
         sum(any(sum(bool(str(r.raw_data.get(c, "")).strip()) for c in m["mapped_columns"]) >= straightlining_min_items for m in matrix_mappings)
             for r in summary.results), f"Same response on at least {straightlining_pct:g}% of at least {straightlining_min_items} rating items."),
        ("Open-Ended Quality & Gibberish", "Content Integrity", ("GIBBERISH", "AMBIGUOUS_REVIEW", "MEANINGFUL_OFF_TOPIC"), "suspicious_text_count",
         sum(any(str(r.raw_data.get(c, "")).strip() for m in open_ended_mappings for c in m["mapped_columns"]) for r in summary.results),
         "Review suspicious or ambiguous responses."),
        ("Confirmed Duplicates", "Fraud Prevention", ("CONFIRMED_DUPLICATE_ID", "CONFIRMED_DUPLICATE_FULL_RECORD"), "confirmed_duplicates_count",
         len(records) if duplicate_enabled else 0, "Subsequent exact record or respondent ID matches."),
        ("Potential Duplicates", "Fraud Prevention", ("POTENTIAL_DUPLICATE_IP", "POTENTIAL_DUPLICATE_SIGNATURE"), "potential_duplicates_count",
         len(records) if duplicate_enabled else 0, "Shared IP or substantive answers are review signals."),
        ("Screening & Eligibility Validation", "Target Audience", ("SCREENER_AGE_VIOLATION", "SCREENER_TERMINATION"), "screener_violations_count",
         eligibility_tested, "Explicit eligibility criteria on confirmed mappings, within their applicable scope."),
        ("Survey Routing & Skip Logic", "Survey Flow", ("ROUTING_VIOLATION",), "routing_violations_count",
         routed, "Only explicit structured rules with mapped inputs are evaluated."),
        ("Contradictory Responses", "Survey Flow", (), "contradiction_count", 0, "No contradiction rule is implemented."),
        ("Completeness & Missing Answers", "Data Completeness", ("INCOMPLETE_RESPONSE",), "incomplete_count",
         complete_tested, "Only confirmed applicable required questions are evaluated."),
    ]
    issue_counts = Counter(f.issue_type for r in summary.results for f in r.findings)
    issue_sets = [set(f.issue_type for f in r.findings) for r in summary.results]
    speeding_issues = {"PAGE_TIMER_SPEEDING", "EXTREME_SPEEDING", "STRONG_SPEEDING_CONCERN", "SUSPICIOUS_SPEEDING"}
    summary.speeding_count = sum(bool(speeding_issues & actual) for actual in issue_sets)
    for name, category, issues, counter, evaluated, description in groups:
        flags = sum(bool(set(issues) & actual) for actual in issue_sets)
        if counter:
            setattr(summary, counter, flags)
        summary.checks_coverage.append({
            "check_name": name, "category": category,
            "status": "FLAGGED" if flags else "PASSED" if evaluated == len(records) and evaluated else "PARTIALLY_CHECKED" if evaluated else "NOT_CHECKED",
            "flagged_count": flags, "finding_count": sum(issue_counts[i] for i in issues),
            "rule_description": description, "notes": f"Evaluated {evaluated}/{len(records)} respondents." if evaluated else "No applicable rule or valid mapped data."
        })

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
            delta = (et - st).total_seconds()
            calc_dur = delta if delta >= 0 else None
        except Exception:
            pass

    if dur_col and row.get(dur_col):
        try:
            value = float(str(row[dur_col]).strip())
            exp_dur = value if math.isfinite(value) and value >= 0 else None
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
    if any(k in qw_low for k in ("orthodont", "teeth straight", "aligner")) and any(term in low for term in RECOGNIZED_ORTHO_TERMS):
        return "VALID_RELEVANT", "Recognized relevant brand or category response", "INFO", "NOTE"

    # Rule 7: Check for common off-topic brands (meaningful real brand, but off-topic for orthodontics)
    if any(k in qw_low for k in ("orthodont", "teeth straight", "aligner")) and any(b in low for b in COMMON_OFF_TOPIC_BRANDS):
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
