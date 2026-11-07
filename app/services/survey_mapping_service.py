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

        # Extract numeric core only if question code is pure Q/S/D followed by digits (e.g. Q1 -> 1, S2 -> 2)
        num_m = re.match(r"^[\[\(\s]*(?:question|q|s|d)[\s\._\-]*(\d+[a-zA-Z]?(?:_\d+)?)$", q_num, flags=re.IGNORECASE)
        clean_qnum = num_m.group(1) if num_m else ""

        matched_cols: list[str] = []
        confidence = "LOW"
        evidence_parts: list[str] = []

        # A. Exact Question Code Match
        # e.g. Q.Country matches 'Q.Country', Q.Age matches 'Q.Age', PS1 matches 'PS1'
        exact_matches = [
            col for col in raw_columns
            if col.lower() == q_lower
            or (clean_qnum and (col.lower() == f"q{clean_qnum}".lower() or col.lower() == f"s{clean_qnum}".lower() or col.lower() == clean_qnum.lower()))
        ]
        if exact_matches:
            matched_cols.extend(exact_matches)
            confidence = "HIGH"
            evidence_parts.append(f"Exact question code match on '{exact_matches[0]}'")

        # B. Multi-column Matrix / Grid or Multi-select Match
        # E.g. Q.PastTreatment matches 'Q.PastTreatment_1' ... 'Q.PastTreatment_13'
        q_nospace = q_num.replace(" ", "")
        grid_cols = [
            col for col in raw_columns
            if (re.match(rf"^{re.escape(q_num)}[_\-\.\s][0-9a-zA-Z_]+$", col, re.IGNORECASE)
                or (q_nospace != q_num and re.match(rf"^{re.escape(q_nospace)}[_\-\.\s][0-9a-zA-Z_]+$", col, re.IGNORECASE))
                or (clean_qnum and re.match(rf"^q?{re.escape(clean_qnum)}[_\-\.][0-9a-zA-Z_]+$", col, re.IGNORECASE)))
            and not col.lower().endswith(("_timer_first click", "_timer_last click", "_timer_page submit", "_timer_click count"))
        ]
        if grid_cols:
            matched_cols = sorted(grid_cols)
            confidence = "HIGH"
            evidence_parts.append(f"Matched {len(grid_cols)} matrix/statement columns ({grid_cols[0]} ... {grid_cols[-1]})")

        # C. Loop / Piped Question Match (e.g. 13_Q.NPS, 14_Q.NPS for Q.NPS)
        if not matched_cols and q_num.startswith("Q."):
            loop_cols = [
                col for col in raw_columns
                if re.match(rf"^\d+_{re.escape(q_num)}(?:_\w+)?$", col, re.IGNORECASE)
                and not any(t in col.lower() for t in ["timer", "submit", "click"])
            ]
            if loop_cols:
                matched_cols = sorted(loop_cols)
                confidence = "HIGH"
                evidence_parts.append(f"Matched {len(loop_cols)} piped loop columns ({loop_cols[0]} ... {loop_cols[-1]})")

        # D. Semantic / Keyword Match for Demographics, Open-ends, and Brand recall
        if not matched_cols:
            if "age" in q_lower or (len(text_lower) > 3 and ("how old" in text_lower or any(w.startswith("age") for w in re.findall(r"\w+", text_lower)))):
                age_cols = [c for c in raw_columns if any(k == c.lower() for k in ["age", "demo_age", "q_age", "q.age"])]
                if age_cols:
                    matched_cols = [age_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched demographic age column '{age_cols[0]}'")
            elif "gender" in q_lower or (len(text_lower) > 5 and "gender" in text_lower):
                gender_cols = [c for c in raw_columns if any(k == c.lower() for k in ["gender", "sex", "demo_gender", "q.gender"])]
                if gender_cols:
                    matched_cols = [gender_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched demographic gender column '{gender_cols[0]}'")
            elif "oe_brand" in q_lower or "unaidedaware" in q_lower or ("brand" in text_lower and "come to mind" in text_lower):
                brand_cols = [c for c in raw_columns if any(k in c.lower() for k in ["oe_brand", "unaidedaware", "open_brand", "q.unaidedaware"])]
                if brand_cols:
                    matched_cols = [brand_cols[0]]
                    confidence = "HIGH"
                    evidence_parts.append(f"Matched open-ended brand column '{brand_cols[0]}'")
            elif "nps" in q_lower or ("recommend" in text_lower and "scale of 0-10" in text_lower):
                nps_cols = [c for c in raw_columns if any(k in c.lower() for k in ["nps", "recommend"] and not any(t in c.lower() for t in ["timer", "submit"]))]
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
            for col in raw_columns:
                if col in used_columns or any(t in col.lower() for t in ["timer", "submit", "click", "start", "end", "date"]):
                    continue
                c_low = col.lower()
                matches_count = sum(1 for w in words if w in c_low)
                if matches_count > best_score:
                    best_score = matches_count
                    best_cand = col
            if best_cand and best_score >= 2:
                matched_cols = [best_cand]
                confidence = "LOW"
                evidence_parts.append(f"Fuzzy token overlap ({best_score} tokens) with '{best_cand}' (Auditor verification required)")

        if not matched_cols:
            confidence = "UNMAPPED"
            mapping_status = "UNMAPPED"
            evidence = "No matching raw data columns found for this question. Available for manual selection."
        else:
            mapping_status = "SUGGESTED"
            evidence = "; ".join(evidence_parts)
            for mc in matched_cols:
                used_columns.add(mc)

        effective_type = q_type
        if len(matched_cols) > 1:
            effective_type = "matrix_grid"
        elif any(k in q_text.lower() for k in ["brand", "which brand", "what brand", "specify", "comments", "feedback"]):
            effective_type = "open_ended"

        primary_matched_column = matched_cols[0] if matched_cols else None

        mappings.append({
            "question_number": q_num,
            "question_text": q_text,
            "question_type": effective_type,
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
            "page_timer_min_seconds": page_timer_min_sec,
            "options": options,
            "matrix_items": matrix_items
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
