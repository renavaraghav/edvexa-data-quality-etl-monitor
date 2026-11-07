"""
Question-to-Column Mapping Service.
Establishes intelligent mapping between Questionnaire (QQ) questions
and raw respondent data columns.
Supports single-column questions, multi-column matrix/grid questions,
and system audit columns (timestamps, timers, respondent identifiers).
"""

import logging
import re
from typing import Any
from app.services.survey_qq_parser import ParsedQuestion

logger = logging.getLogger("edvexa.surveys.mapping")


SYSTEM_COLUMN_CATEGORIES = {
    "respondent_id": ["responseid", "respondentid", "rid", "respid", "id", "caseid", "sys_resp_num", "participant_id"],
    "start_time": ["startdate", "start_date", "start_time", "starttime", "begintime"],
    "end_time": ["enddate", "end_date", "end_time", "endtime", "completed_time", "recordeddate"],
    "duration": ["duration (in seconds)", "duration_seconds", "duration", "duration (seconds)", "time_spent", "length_of_interview"],
    "ip_address": ["ipaddress", "ip_address", "ip", "client_ip"],
}


def suggest_mappings(
    questions: list[ParsedQuestion],
    raw_columns: list[str]
) -> dict[str, Any]:
    """
    Generate question-to-column mappings with confidence scoring and evidence.
    Returns:
      {
        "mappings": list[dict],
        "system_columns": dict,
        "unmapped_columns": list[str],
        "coverage_summary": dict
      }
    """
    col_map = {col.lower(): col for col in raw_columns}
    used_columns: set[str] = set()

    # 1. Identify System Telemetry Columns
    system_cols: dict[str, str | None] = {cat: None for cat in SYSTEM_COLUMN_CATEGORIES}
    for cat, candidates in SYSTEM_COLUMN_CATEGORIES.items():
        for cand in candidates:
            if cand in col_map:
                system_cols[cat] = col_map[cand]
                used_columns.add(col_map[cand])
                break

    # Also detect page / question timers
    timer_columns: list[str] = []
    for col in raw_columns:
        low = col.lower()
        if "timer" in low or "page submit" in low or "seconds_spent" in low:
            timer_columns.append(col)
            used_columns.add(col)

    mappings: list[dict[str, Any]] = []

    # 2. Map Each Extracted Questionnaire Question
    for q in questions:
        q_num = q.question_number.strip()
        clean_qnum = re.sub(r"^[\[\(\s]*(?:question|q|s|d)[\s\._\-]*", "", q_num, flags=re.IGNORECASE).strip()
        q_lower = q_num.lower()
        text_lower = q.question_text.lower()

        matched_cols: list[str] = []
        confidence = "LOW"
        evidence_parts: list[str] = []

        # A. Exact or Prefix Question Number Match
        # E.g. Q1 matches 'Q1', 'q_1', 'q1_1'
        exact_matches = [
            col for col in raw_columns
            if col.lower() == q_lower or col.lower() == f"q{clean_qnum}".lower() or col.lower() == f"s{clean_qnum}".lower()
        ]
        if exact_matches:
            matched_cols.extend(exact_matches)
            confidence = "HIGH"
            evidence_parts.append(f"Exact question code match on '{exact_matches[0]}'")

        # B. Multi-column Matrix / Grid or Multi-select Match
        # E.g. Q5 matches 'Q5_1', 'Q5_2', 'Q5_r1', 'Q5_c1', 'FactorAsso1_...'
        if q.question_type in ("matrix_grid", "multiple_choice") or not matched_cols:
            grid_cols = [
                col for col in raw_columns
                if re.match(rf"^{re.escape(q_num)}[_\-\.][0-9a-zA-Z_]+$", col, re.IGNORECASE)
                or (clean_qnum and re.match(rf"^q?{re.escape(clean_qnum)}[_\-\.][0-9a-zA-Z_]+$", col, re.IGNORECASE))
            ]
            if grid_cols:
                matched_cols = sorted(grid_cols)
                confidence = "HIGH"
                evidence_parts.append(f"Matched {len(grid_cols)} matrix/statement columns ({grid_cols[0]} ... {grid_cols[-1]})")

        # C. Semantic / Keyword Match for Demographics, Open-ends, and Brand recall
        if not matched_cols:
            if "age" in text_lower:
                age_cols = [c for c in raw_columns if any(k in c.lower() for k in ["age", "demo_age", "q_age"])]
                if age_cols:
                    matched_cols = [age_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched demographic age column '{age_cols[0]}'")
            elif "gender" in text_lower:
                gender_cols = [c for c in raw_columns if any(k in c.lower() for k in ["gender", "sex", "demo_gender"])]
                if gender_cols:
                    matched_cols = [gender_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched demographic gender column '{gender_cols[0]}'")
            elif "brand" in text_lower or "which brand" in text_lower or "oe_brand" in q_lower:
                brand_cols = [c for c in raw_columns if any(k in c.lower() for k in ["oe_brand", "brand", "ortho_brand", "open_brand"])]
                if brand_cols:
                    matched_cols = [brand_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched open-ended brand column '{brand_cols[0]}'")
            elif "recommend" in text_lower or "nps" in text_lower:
                nps_cols = [c for c in raw_columns if any(k in c.lower() for k in ["nps", "recommend", "nps_score"])]
                if nps_cols:
                    matched_cols = [nps_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched NPS / recommendation column '{nps_cols[0]}'")
            elif "orthodontic" in text_lower or "ortho" in text_lower:
                ortho_cols = [c for c in raw_columns if any(k in c.lower() for k in ["ortho", "consider", "treatment"])]
                if ortho_cols:
                    matched_cols = [ortho_cols[0]]
                    confidence = "MEDIUM"
                    evidence_parts.append(f"Matched keyword 'ortho' to column '{ortho_cols[0]}'")

        # D. Fuzzy Substring Match as Fallback
        if not matched_cols:
            # Tokenize question text
            words = [w for w in re.findall(r"[a-zA-Z]{4,}", text_lower) if w not in ("what", "which", "would", "please", "select", "following")]
            best_cand = None
            best_score = 0
            for col in raw_columns:
                c_low = col.lower()
                matches_count = sum(1 for w in words if w in c_low)
                if matches_count > best_score:
                    best_score = matches_count
                    best_cand = col
            if best_cand and best_score >= 1:
                matched_cols = [best_cand]
                confidence = "MEDIUM" if best_score >= 2 else "LOW"
                evidence_parts.append(f"Fuzzy token overlap ({best_score} tokens) with '{best_cand}'")

        if not matched_cols:
            confidence = "UNMAPPED"
            mapping_status = "UNMAPPED"
            evidence = "No matching raw data columns found for this question. Checks for this question will be reported as limited."
        else:
            mapping_status = "SUGGESTED"
            evidence = "; ".join(evidence_parts)
            for mc in matched_cols:
                used_columns.add(mc)

        effective_type = q.question_type
        if len(matched_cols) > 1:
            effective_type = "matrix_grid"
        elif any(k in q.question_text.lower() for k in ["brand", "which brand", "what brand", "specify", "comments", "feedback"]):
            effective_type = "open_ended"

        mappings.append({
            "question_number": q.question_number,
            "question_text": q.question_text,
            "question_type": effective_type,
            "mapped_columns": matched_cols,
            "confidence": confidence,
            "mapping_status": mapping_status,
            "mapping_evidence": evidence,
            "qc_instructions": q.qc_instructions,
            "screening_criteria": q.screening_criteria,
            "routing_logic": q.routing_logic,
            "min_age": getattr(q, "min_age", None),
            "max_age": getattr(q, "max_age", None),
            "page_timer_min_seconds": getattr(q, "page_timer_min_seconds", None),
            "options": q.options,
            "matrix_items": q.matrix_items
        })

    # Unmapped Raw Columns (variables in dataset that were not linked to a QQ question)
    unmapped_raw = [col for col in raw_columns if col not in used_columns]

    total_qq = len(questions)
    mapped_qq = sum(1 for m in mappings if m["mapped_columns"])
    unmapped_qq = total_qq - mapped_qq

    return {
        "mappings": mappings,
        "system_columns": system_cols,
        "timer_columns": timer_columns,
        "unmapped_columns": unmapped_raw,
        "coverage_summary": {
            "total_questions": total_qq,
            "mapped_questions": mapped_qq,
            "unmapped_questions": unmapped_qq,
            "total_raw_columns": len(raw_columns),
            "mapped_raw_columns": len(used_columns),
            "unmapped_raw_columns": len(unmapped_raw)
        }
    }
