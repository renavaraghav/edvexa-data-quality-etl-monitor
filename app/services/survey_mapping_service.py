"""
Question-to-Column Mapping Service.
Establishes intelligent mapping between Questionnaire (QQ) questions
and raw respondent data columns.
Supports single-column questions, multi-column matrix/grid questions,
and system audit columns (timestamps, timers, respondent identifiers).
Ensures non-question metadata/instructions are excluded from forced mappings.
Provides clear evidence supporting each suggested correlation.
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
        "timer_columns": list[str],
        "unmapped_columns": list[str],
        "coverage_summary": dict
      }
    """
    columns_by_code: dict[str, list[str]] = {}
    for column in raw_columns:
        columns_by_code.setdefault(column.lower(), []).append(column)
    question_codes = {
        str(q.get("question_number", "") if isinstance(q, dict) else q.question_number).strip().casefold()
        for q in questions
    }
    used_columns: set[str] = set()
    fallback_pool = [column for column in raw_columns if column.casefold() not in question_codes]

    # 1. Identify System Telemetry Columns
    system_cols: dict[str, str | None] = {cat: None for cat in SYSTEM_COLUMN_CATEGORIES}
    for cat, candidates in SYSTEM_COLUMN_CATEGORIES.items():
        for cand in candidates:
            if cand in columns_by_code:
                column = columns_by_code[cand][-1]
                system_cols[cat] = column
                used_columns.add(column)
                break

    # Also detect page / question timers
    timer_columns: list[str] = []
    for col in raw_columns:
        low = col.lower()
        if re.search(r"first[ _-]*click|last[ _-]*click|click[ _-]*count", low):
            used_columns.add(col)
            continue
        if "timer" in low or "page submit" in low or "seconds_spent" in low:
            timer_columns.append(col)
            used_columns.add(col)

    mappings: list[dict[str, Any]] = []

    # 2. Map Each Extracted Questionnaire Question
    for q in questions:
        if isinstance(q, dict):
            q_num = str(q.get("question_number", "")).strip()
            q_text = str(q.get("question_text", "")).strip()
            q_type = str(q.get("question_type", "")).strip()
            qc_inst = q.get("qc_instructions")
            screening_criteria = q.get("screening_criteria")
            routing_logic = q.get("routing_logic")
            min_age = q.get("min_age")
            max_age = q.get("max_age")
            page_timer_min_sec = q.get("page_timer_min_seconds")
            options = q.get("options", [])
            matrix_items = q.get("matrix_items", [])
        else:
            q_num = q.question_number.strip()
            q_text = q.question_text.strip()
            q_type = q.question_type.strip()
            qc_inst = getattr(q, "qc_instructions", None)
            screening_criteria = getattr(q, "screening_criteria", None)
            routing_logic = getattr(q, "routing_logic", None)
            min_age = getattr(q, "min_age", None)
            max_age = getattr(q, "max_age", None)
            page_timer_min_sec = getattr(q, "page_timer_min_seconds", None)
            options = getattr(q, "options", [])
            matrix_items = getattr(q, "matrix_items", [])

        q_lower = q_num.lower()
        text_lower = q_text.lower()

        # Non-question content (metadata / display text instructions)
        if q_type in ("metadata", "instruction", "display_text"):
            evidence = "Non-question content (instruction / metadata block) — excluded from audit column mapping"
            mappings.append({
                "question_number": q_num,
                "question_text": q_text,
                "question_type": q_type,
                "matched_column": None,
                "mapped_columns": [],
                "confidence": "UNMAPPED",
                "mapping_status": "SKIPPED",
                "evidence": evidence,
                "mapping_evidence": evidence,
                "qc_instructions": qc_inst,
                "screening_criteria": screening_criteria,
                "routing_logic": routing_logic,
                "min_age": min_age,
                "max_age": max_age,
                "page_timer_min_seconds": page_timer_min_sec,
                "options": options,
                "matrix_items": matrix_items
            })
            continue

        matched_cols: list[str] = []
        confidence = "LOW"
        evidence_parts: list[str] = []
        fallback_columns = [column for column in fallback_pool if column not in used_columns]

        # A. Exact Question Code Match
        # Prefer an exact question code over prefix matches for other questions.
        exact_matches = [
            column for column in columns_by_code.get(q_lower, ()) if column not in used_columns
        ]
        if exact_matches:
            matched_cols.extend(exact_matches)
            confidence = "HIGH"
            evidence_parts.append(f"Exact question code match on '{exact_matches[0]}'")

        # B. Multi-column Matrix / Grid or Multi-select Match
        # E.g. Q.Rating matches Q.Rating_1 and Q.Rating_2.
        q_nospace = q_num.replace(" ", "")
        grid_cols = [
            col for col in fallback_columns
            if (re.match(rf"^{re.escape(q_num)}[_\-\.\s][0-9a-zA-Z_]+$", col, re.IGNORECASE)
                or (q_nospace != q_num and re.match(rf"^{re.escape(q_nospace)}[_\-\.\s][0-9a-zA-Z_]+$", col, re.IGNORECASE))
                )
            and not col.lower().endswith(("_timer_first click", "_timer_last click", "_timer_page submit", "_timer_click count"))
        ]
        if grid_cols and (not exact_matches or q_type in {"matrix_grid", "multi_choice_grid", "multiple_choice"}):
            matched_cols = sorted(grid_cols)
            confidence = "HIGH"
            evidence_parts.append(f"Matched {len(grid_cols)} matrix/statement columns ({grid_cols[0]} ... {grid_cols[-1]})")

        # C. Loop / Piped Question Match (e.g. 1_Q.Satisfaction and 2_Q.Satisfaction)
        loop_cols = []
        if not matched_cols:
            loop_cols = [
                col for col in fallback_columns
                if re.match(rf"^\d+_{re.escape(q_num)}(?:_\w+)?$", col, re.IGNORECASE)
            ]
            if loop_cols:
                matched_cols = sorted(loop_cols)
                confidence = "HIGH"
                evidence_parts.append(f"Matched {len(loop_cols)} piped loop columns ({loop_cols[0]} ... {loop_cols[-1]})")

        # D. Semantic suggestions from common demographic and rating labels
        if not matched_cols:
            code_words = re.findall(r"[a-z0-9]+", q_lower)
            if "age" in code_words or re.search(r"\b(?:age|how old)\b", text_lower):
                age_cols = [c for c in fallback_columns if c.lower() in {"age", "demo_age", "q_age", "q.age"}]
                if age_cols:
                    matched_cols = [age_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched demographic age column '{age_cols[0]}'")
            elif "gender" in q_lower or (len(text_lower) > 5 and "gender" in text_lower):
                gender_cols = [c for c in fallback_columns if c.lower() in {"gender", "sex", "demo_gender", "q.gender"}]
                if gender_cols:
                    matched_cols = [gender_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched demographic gender column '{gender_cols[0]}'")
            elif "nps" in q_lower or ("recommend" in text_lower and "scale of 0-10" in text_lower):
                nps_cols = [c for c in fallback_columns if any(k in c.lower() for k in ("nps", "recommend"))]
                if nps_cols:
                    matched_cols = [nps_cols[0]]
                    confidence = "MEDIUM"
                    evidence_parts.append(f"Matched NPS candidate column '{nps_cols[0]}'")

        # E. Fallback: Fuzzy Token Overlap (low confidence, explicit evidence, never forced)
        if not matched_cols and len(text_lower) > 15:
            words = [
                w for w in re.findall(r"[a-zA-Z]{5,}", text_lower)
                if w not in ("which", "would", "please", "select", "following", "think", "about", "describe", "answer")
            ]
            best_cand = None
            best_score = 0
            for col in fallback_columns:
                c_low = col.lower()
                matches_count = sum(1 for w in words if w in c_low)
                if matches_count > best_score:
                    best_score = matches_count
                    best_cand = col
            if best_cand and best_score >= 2:
                matched_cols = [best_cand]
                confidence = "LOW"
                evidence_parts.append(f"Fuzzy token overlap ({best_score} tokens) with '{best_cand}' (Auditor verification required)")

        matched_cols = [column for column in matched_cols if column not in used_columns]
        if not matched_cols:
            confidence = "UNMAPPED"
            mapping_status = "UNMAPPED"
            evidence = "No matching raw data columns found for this question. Available for manual selection."
        else:
            mapping_status = "SUGGESTED"
            evidence = "; ".join(evidence_parts)
            for mc in matched_cols:
                used_columns.add(mc)

        primary_matched_column = matched_cols[0] if matched_cols else None

        mappings.append({
            "question_number": q_num,
            "question_text": q_text,
            "question_type": q_type,
            "grouped_columns": bool(grid_cols or loop_cols),
            "matched_column": primary_matched_column,
            "mapped_columns": matched_cols,
            "confidence": confidence,
            "mapping_status": mapping_status,
            "evidence": evidence,
            "mapping_evidence": evidence,
            "qc_instructions": qc_inst,
            "screening_criteria": screening_criteria,
            "routing_logic": routing_logic,
            "min_age": min_age,
            "max_age": max_age,
            "age_countries": getattr(q, "age_countries", []),
            "eligibility_source": "Questionnaire Specification" if isinstance(q, ParsedQuestion) else None,
            "page_timer_min_seconds": page_timer_min_sec,
            "options": options,
            "matrix_items": matrix_items,
            "parent_question": getattr(q, "parent_question", None),
            "external_reference": getattr(q, "external_reference", None),
            "option_codes": getattr(q, "option_codes", {}),
            "source_line": getattr(q, "source_line", None),
            "matrix_columns": getattr(q, "matrix_columns", []),
            "follow_up_fields": getattr(q, "follow_up_fields", []),
        })

    # Unmapped Raw Columns
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
