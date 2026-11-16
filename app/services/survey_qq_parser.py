"""
Questionnaire (QQ) Reference Document Extraction Service.
Extracts questions, question numbers, wording, question types, response options,
matrix statement items, timing instructions, screener criteria, and skip logic.
Supports PDF, DOCX, XLSX, and TXT/MD formats.
Handles scanned or unreadable documents honestly and transparently.
"""

import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
import openpyxl
from app.services.survey_upload_limits import (
    MAX_EXCEL_SECONDS, SurveyUploadLimit, check_excel_time, check_openxml_size, validate_xlsx,
)

_pdf_workers = threading.BoundedSemaphore(2)

logger = logging.getLogger("edvexa.surveys.qq_parser")


@dataclass
class ParsedQuestion:
    question_number: str
    question_text: str
    question_type: str  # single_choice, multiple_choice, matrix_grid, open_ended, numeric, rating_scale, screener, timer, metadata, instruction
    options: list[str] = field(default_factory=list)
    matrix_items: list[str] = field(default_factory=list)
    qc_instructions: str | None = None
    routing_logic: str | None = None
    screening_criteria: str | None = None
    min_age: float | None = None
    max_age: float | None = None
    page_timer_min_seconds: float | None = None
    suggested_columns: list[str] = field(default_factory=list)
    confidence: str = "MEDIUM"
    mapping_evidence: str | None = None
    source_line: int | None = None
    source_text: str = ""
    exclusion_reason: str | None = None
    parent_question: str | None = None
    external_reference: str | None = None
    option_codes: dict[str, str] = field(default_factory=dict)
    follow_up_fields: list[dict[str, str]] = field(default_factory=list)
    matrix_columns: list[str] = field(default_factory=list)
    age_countries: list[str] = field(default_factory=list)



@dataclass
class QuestionnaireExtractionResult:
    questions: list[ParsedQuestion] = field(default_factory=list)
    excluded_blocks: list[ParsedQuestion] = field(default_factory=list)
    expected_duration_min_minutes: float | None = None
    expected_duration_max_minutes: float | None = None
    page_timer_threshold_seconds: float | None = None
    page_timer_rules: dict[str, float] = field(default_factory=dict)
    expected_duration_target_minutes: float | None = None
    duration_source: str = "Not configured"
    screener_rules: list[dict[str, Any]] = field(default_factory=list)
    skip_logic_rules: list[dict[str, Any]] = field(default_factory=list)
    unreadable_or_scanned: bool = False
    extraction_notes: list[str] = field(default_factory=list)
    raw_text_length: int = 0
    document_format: str = "unknown"


# Strict list of prefixes/lines that represent non-question questionnaire content
NON_QUESTION_STARTS = (
    "start of block",
    "end of block",
    "page break",
    "wave ",
    "notes for",
    "notes:",
    "quota:",
    "quotas:",
    "quota -",
    "dynata:",
    "qual2585",
    "for qc purposes",
    "show respondent progress",
    "program embedded variable",
    "populate month/year",
    "needs to be set up",
    "for example,",
    "parent pre-screener (",
    "ask parents of",
    "please handover the computer",
    "validate q.",
    "validate ",
    "note, geo-targeting",
    "show n and %",
    "show mean and median",
    "show nets:",
    "detractors:",
    "detractors (",
    "passives:",
    "promoters:",
    "show net promoter score",
    "add up the total responses",
    "to get the percentage",
    "now, subtract the percentage",
    "display in applicable countries",
    "display if us is not selected",
    "skip to: end of block and terminate",
    "see global excel",
    "show per global excel",
    "single choice drop down",
    "single choice",
    "single select",
    "multi choice grid",
    "single choice grid",
    "multiple choice",
    "multi-select",
    "open end",
    "numeric oe",
    "oe numeric",
    "display text",
    "randomize list",
    "no tables",
    "pn:",
    "programmer note:",
    "insert relevant language",
    "show all countries listed",
    "recommended view in web layout",
)

# Common response options / choices / anchors that must NEVER be parsed as question identifiers
FORBIDDEN_QUESTION_CODES = {
    "DYNATA", "QUAL2585", "QUOTA", "QUOTAS", "ST", "DETRACTORS", "PASSIVES", "PROMOTERS",
    "NET", "NOTES", "SHOW", "DISPLAY", "SKIP", "TERMINATE", "VALIDATE", "INSERT", "PROGRAM",
    "POPULATE", "YES", "NO", "LARGE", "SMALL", "FAKE", "OTHER", "NONE", "STATEMENT", "TOTAL",
    "AGE", "GENDER", "FEMALE", "MALE", "TEEN", "ADULT", "COUNTRY", "CITY", "STATE", "ENGLISH"
}


def extract_text_from_pdf(content: bytes) -> tuple[str, bool]:
    """Use a bounded worker so a PDF timeout stops parsing, even off the event loop."""
    if not _pdf_workers.acquire(blocking=False):
        raise SurveyUploadLimit("PDF processing is busy. Retry the upload shortly.")
    try:
        response = subprocess.run(
            [sys.executable, "-I", "-B", str(Path(__file__).with_name("survey_pdf_worker.py"))],
            input=content, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
            env={key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TMP", "TEMP") if key in os.environ},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise SurveyUploadLimit("PDF processing exceeded the time limit.") from exc
    finally:
        _pdf_workers.release()
    if response.returncode == 2:
        raise SurveyUploadLimit("PDF exceeds the page, expanded stream, or text limit.")
    if response.returncode != 0:
        return "", True
    text = response.stdout.decode("utf-8")
    return text, len(text) < 50


def extract_text_from_docx(content: bytes) -> tuple[str, bool]:
    """
    Extract text and table contents from DOCX using standard library zipfile and XML.
    Requires no external packages; 100% reliable across all environments.
    """
    try:
        stream = io.BytesIO(content)
        with zipfile.ZipFile(stream, "r") as docx_zip:
            check_openxml_size(docx_zip)
            if "word/document.xml" not in docx_zip.namelist():
                return "", True
            xml_content = docx_zip.read("word/document.xml")
            tree = ET.fromstring(xml_content)
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            
            paragraphs = []
            for p in tree.iterfind(".//w:p", ns):
                texts = [node.text for node in p.iterfind(".//w:t", ns) if node.text]
                if texts:
                    paragraphs.append("".join(texts))
            
            full_text = "\n".join(paragraphs).strip()
            is_empty = len(full_text) < 20
            return full_text, is_empty
    except SurveyUploadLimit:
        raise
    except Exception as e:
        logger.warning(f"DOCX extraction error: {e}")
        return "", True


def extract_text_from_xlsx(content: bytes) -> tuple[str, bool]:
    """Extract questions or text from XLSX questionnaire / codebook."""
    deadline = time.monotonic() + MAX_EXCEL_SECONDS
    validate_xlsx(content, deadline)
    wb = None
    try:
        stream = io.BytesIO(content)
        wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        lines = []
        for sheet in wb.worksheets:
            sheet.reset_dimensions()
            lines.append(f"--- Sheet: {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                check_excel_time(deadline)
                non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if non_empty:
                    if len(non_empty) >= 2 and re.match(r"^[\[\(]?[QSD0-9a-zA-Z_]+", non_empty[0], re.IGNORECASE):
                        lines.append(f"{non_empty[0]}. {' | '.join(non_empty[1:])}")
                    else:
                        lines.append(" | ".join(non_empty))
        full_text = "\n".join(lines).strip()
        return full_text, len(full_text) < 30
    except SurveyUploadLimit:
        raise
    except Exception as e:
        logger.warning(f"XLSX questionnaire extraction error: {e}")
        return "", True
    finally:
        if wb:
            wb.close()


def extract_questionnaire_text(content: bytes, filename: str) -> tuple[str, str, bool]:
    """Extract text from questionnaire file based on extension."""
    lower_name = filename.lower()
    if lower_name.endswith(".pdf"):
        text, is_scanned = extract_text_from_pdf(content)
        return text, "pdf", is_scanned
    elif lower_name.endswith(".docx") or lower_name.endswith(".doc"):
        text, is_empty = extract_text_from_docx(content)
        return text, "docx", is_empty
    elif lower_name.endswith(".xlsx") or lower_name.endswith(".xls"):
        text, is_empty = extract_text_from_xlsx(content)
        return text, "xlsx", is_empty
    else:
        try:
            text = content.decode("utf-8-sig")
            return text, "txt", False
        except UnicodeDecodeError:
            try:
                text = content.decode("latin-1")
                return text, "txt", False
            except Exception:
                return "", "unknown", True


def parse_questionnaire(content: bytes, filename: str) -> QuestionnaireExtractionResult:
    """
    Parse a questionnaire reference document and interpret survey structure.
    Extracts questions, question numbers, wording, types, options, duration, and routing.
    Strictly distinguishes genuine question identifiers from headings, panel notes,
    quota instructions, response options, and scoring definitions.
    """
    raw_text, doc_format, unreadable = extract_questionnaire_text(content, filename)
    result = QuestionnaireExtractionResult(
        document_format=doc_format,
        unreadable_or_scanned=unreadable,
        raw_text_length=len(raw_text)
    )

    if unreadable or len(raw_text) < 30:
        result.unreadable_or_scanned = True
        result.extraction_notes.append(
            f"The questionnaire file '{filename}' appears to be a scanned image or empty. "
            "Text could not be extracted automatically. QC checks relying on QQ logic will report limitations."
        )
        return result

    result.extraction_notes.append(
        f"Successfully extracted {len(raw_text):,} characters from {doc_format.upper()} questionnaire."
    )

    # Normalize inline question patterns (e.g. "Study Header. Q1. Age? Terminate if under 18.")
    raw_text = re.sub(r'([.?!;])\s+((?:Q\.|(?:PS|QS|Q|S|D|DEMO|OE)\d+[\.:\-\)\s]))', r'\1\n\2', raw_text)

    # 1. Detect Overall Expected Duration
    # Examples: "7-10 minutes", "7 to 15 minutes", "take around 7-10 minutes", "LOI: 12 mins"
    duration_patterns = [
        r"(?:expected|average|approximate|target|survey)?\s*(?:duration|length|loi|time|take)\s*(?:is|of|around|about|:)?\s*(\d+)\s*(?:-|to|–)\s*(\d+)\s*(?:min|mins|minute|minutes)",
        r"(\d+)\s*(?:-|to|–)\s*(\d+)\s*(?:min|mins|minute|minutes)\s*(?:survey|duration|length|to complete)",
        r"(?:duration|length|loi)\s*(?:is|of|around|about|:)?\s*(\d+)\s*(?:min|mins|minute|minutes)"
    ]
    for pat in duration_patterns:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            if len(m.groups()) == 2 and m.group(1) and m.group(2):
                result.expected_duration_min_minutes = float(m.group(1))
                result.expected_duration_max_minutes = float(m.group(2))
                result.duration_source = "Questionnaire Specification"
                result.extraction_notes.append(
                    f"Identified expected survey duration: {result.expected_duration_min_minutes:.0f}–{result.expected_duration_max_minutes:.0f} minutes (from QQ text)."
                )
                break
            elif len(m.groups()) >= 1 and m.group(1):
                mid = float(m.group(1))
                result.expected_duration_target_minutes = mid
                result.duration_source = "Questionnaire target (not a cutoff)"
                result.extraction_notes.append(
                    f"Identified expected survey duration: ~{mid:.0f} minutes (from QQ text)."
                )
                break

    # 2. Parse individual questions, keeping timing instructions in their source scope.
    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    q_candidates: list[tuple[str, int, str]] = []

    for idx, line in enumerate(lines):
        l_low = line.lower()
        if any(l_low.startswith(p) for p in NON_QUESTION_STARTS):
            continue

        # Reject lines that are instructions or qc checks
        if "qc check =" in l_low or "validate" in l_low and "geo-ip" in l_low:
            continue

        q_id: str | None = None

        # Pattern 1: Q.<Identifier> (e.g. Q.Country, Q.Age, Q.PastTreatment, Q.Order Purchase)
        m1 = re.match(r"^(Q[._][A-Za-z0-9_][A-Za-z0-9_\s/-]*?)(?:[\.:\-\)]|\s*$)", line)
        if m1:
            cand = m1.group(1).strip()
            # Exclude intro banner text like Q.IntroThanks
            if "introthanks" not in cand.lower() and " qc check" not in cand.lower():
                q_id = cand

        # Pattern 2: PS1, S1, D1, Q1, OE1, etc. (Letters followed by digits)
        if not q_id:
            m2 = re.match(r"^((?:PS|QS|Q|S|D|DEMO|OE)\d+[a-zA-Z]?(?:_\d+)?)(?:[\.:\-\)\|\s]|$)", line)
            if m2:
                q_id = m2.group(1).strip()

        # Pattern 3: Question 1, Screener 1, Demographics 1
        if not q_id:
            m3 = re.match(r"^((?:Question|Screener|Demo|Demographic)\s+\d+[a-zA-Z]?)(?:[\.:\-\)\|\s]|$)", line, re.IGNORECASE)
            if m3:
                q_id = m3.group(1).strip()

        # Pattern 4: OE_<Name>
        if not q_id:
            m4 = re.match(r"^(OE_[A-Za-z0-9_]+)(?:[\.:\-\)\|\s]|$)", line)
            if m4:
                q_id = m4.group(1).strip()

        if q_id:
            # Validate against forbidden identifiers
            clean_token = re.sub(r"[^A-Za-z0-9]", "", q_id).upper()
            if clean_token in FORBIDDEN_QUESTION_CODES:
                continue
            q_candidates.append((q_id, idx, line))

    # Deduplicate candidates by line index
    unique_candidates: list[tuple[str, int, str]] = []
    seen_indices = set()
    for qid, s_idx, l_str in q_candidates:
        if s_idx not in seen_indices:
            seen_indices.add(s_idx)
            unique_candidates.append((qid, s_idx, l_str))

    # An unqualified timer declaration in the survey header can apply to every page.
    # A declaration inside a question block is never promoted to a survey-wide rule.
    header_end = unique_candidates[0][1] if unique_candidates else 0
    header_timers = {value for line in lines[:header_end] if (value := _timer_threshold(line)) is not None}
    if len(header_timers) == 1:
        result.page_timer_threshold_seconds = header_timers.pop()
        result.extraction_notes.append(
            f"Identified survey-wide page timer threshold: {result.page_timer_threshold_seconds:g} seconds (from QQ header)."
        )

    # Process each question block
    for i, (qid, start_idx, line_str) in enumerate(unique_candidates):
        end_idx = unique_candidates[i + 1][1] if i + 1 < len(unique_candidates) else len(lines)
        block_lines = lines[start_idx:end_idx]

        parsed = _interpret_question_block(qid, line_str, block_lines)
        parsed.source_line = start_idx + 1
        parsed.source_text = "\n".join(block_lines)
        if parsed.question_type in {"metadata", "instruction", "display_text"}:
            parsed.exclusion_reason = "Derived or embedded field" if parsed.question_type == "metadata" else "Respondent instructions or display text"
            result.excluded_blocks.append(parsed)
            continue
        # A handover block can embed multiple vendor-specific respondent prompts.
        if re.search(r"(?i)handover|hand over", parsed.source_text):
            prompts = [(n, line) for n, line in enumerate(block_lines) if line.endswith("?")]
            if prompts:
                parsed.question_type = "instruction"
                parsed.exclusion_reason = "Handover wrapper; actual prompts are recorded separately"
                result.excluded_blocks.append(parsed)
                for ordinal, (n, line) in enumerate(prompts, 1):
                    limit = prompts[ordinal][0] if ordinal < len(prompts) else len(block_lines)
                    choices = [value for value in block_lines[n + 1:limit] if len(value) < 30 and not value.startswith("[")]
                    result.questions.append(ParsedQuestion(
                        f"{qid}.{ordinal}", line, "screener", options=choices,
                        source_line=start_idx+n+1, source_text="\n".join(block_lines[n:limit]), parent_question=qid))
                continue
        result.questions.append(parsed)

        if parsed.screening_criteria:
            result.screener_rules.append({
                "question_number": parsed.question_number,
                "rule": parsed.screening_criteria
            })
        if parsed.routing_logic:
            result.skip_logic_rules.append({
                "question_number": parsed.question_number,
                "rule": parsed.routing_logic
            })
        if parsed.page_timer_min_seconds:
            result.page_timer_rules[parsed.question_number] = parsed.page_timer_min_seconds

    result.extraction_notes.append(
        f"Extracted {len(result.questions)} structured questions from the questionnaire."
    )
    return result


def _timer_threshold(text, question=None):
    pattern = r"(?:global\s+)?(?:page|screen|question)\s+(?:submit\s+)?timer(?:\s+(?:threshold|minimum|cutoff|rule))?\s*(?:is|of|:)?\s*(\d{1,5})\s*(?:seconds?|secs?|s)"
    if question:
        pattern += rf"(?:\s+for\s+(?:page\s+)?{re.escape(question)}(?:\s+only)?)?"
    match = re.fullmatch(pattern + r"\.?", text.strip(), re.I)
    return float(match[1]) if match and int(match[1]) > 0 else None


def _age_rule(lines):
    limits = set()
    pattern = (
        r"terminate if (?:age is )?(under|below|over|above)\s+(\d{1,3})"
        r"(?:\s+or\s+(?:age is )?(over|above)\s+(\d{1,3}))?"
        r"(?:\s+(?:in\s+(?:the\s+)?|for\s+(?:the\s+)?)(u\.?s\.?a?\.?|united states(?: of america)?)(?:\s+respondents)?(?:\s+only)?)?\.?"
    )
    instructions = [*lines, *(line.rsplit("?", 1)[1] for line in lines if "?" in line)]
    instructions.extend(s for line in lines for s in re.findall(r"\[([^\]]+)\]", line))
    for text in instructions:
        match = re.fullmatch(pattern, text.strip(), re.I)
        if not match:
            continue
        lower = match[1].lower() in {"under", "below"}
        minimum = int(match[2]) if lower else None
        maximum = (int(match[4]) if match[4] else None) if lower else int(match[2])
        if any(value > 130 for value in (minimum, maximum) if value is not None):
            continue
        if minimum is not None and maximum is not None and maximum < minimum:
            continue
        limits.add((minimum, maximum, "US" if match[5] else None))
    return limits.pop() if len(limits) == 1 else (None, None, None)


def _interpret_question_block(qid: str, header_line: str, block_lines: list[str]) -> ParsedQuestion:
    """Interpret question wording, type, options, screening, and routing from a block."""
    wording = ""
    # Check if the header line itself contained question wording after the question code
    # e.g. "Q1. Have you ever considered orthodontic treatment?"
    after_id = header_line[len(qid):].lstrip(".:-)|\t ")
    if after_id and not any(after_id.lower().startswith(p) for p in NON_QUESTION_STARTS):
        wording = after_id

    # Determine Question Classification & Type
    q_lower = qid.lower()
    full_block_text = " ".join(block_lines)
    full_block_lower = full_block_text.lower()

    # Distinguish metadata & instruction display blocks from substantive questions
    if any(k in q_lower for k in ["monthyear", "wave", "embedded", "tracking"]) or "program embedded variable" in full_block_lower:
        q_type = "metadata"
    elif any(k in q_lower for k in ["starttext", "blocktext", "profilingintro"]) or "congratulations, you have qualified" in full_block_lower:
        q_type = "instruction"
    elif ("past_current_usage_net" in q_lower or
          (len(block_lines) > 1 and all(re.match(r"(?:recent past|current) user \(q[.]",
                line, re.I) for line in block_lines[1:]))):
        q_type = "metadata"
    elif qid.upper().startswith("S") or qid.upper().startswith("PS"):
        q_type = "screener"
    elif "nps" in q_lower or "recommend" in q_lower and "scale of 0-10" in full_block_lower:
        q_type = "rating_scale"
    elif any(k in full_block_lower for k in ["open-end", "open end", "text box", "please specify", "in your own words", "oe_brand"]):
        q_type = "open_ended"
    elif any(k in full_block_lower for k in ["matrix", "grid", "rate each", "how much do you agree", "strongly agree"]):
        q_type = "matrix_grid"
    elif any(k in full_block_lower for k in ["select all that apply", "multi-select", "multiselect", "which of the following have you", "please check all"]):
        q_type = "multiple_choice"
    elif any(k in full_block_lower for k in ["how old", "enter age", "oe numeric", "numeric oe", "numeric"]):
        q_type = "numeric"
    else:
        q_type = "single_choice"

    type_labels = (
        ("multi_choice_grid", r"multi(?:ple)? choice grid"),
        ("matrix_grid", r"single choice grid|matrix(?: / grid)?|grid"),
        ("multiple_choice", r"multiple choice|multi-select|multi select"),
        ("numeric", r"oe numeric|numeric oe"),
        ("open_ended", r"open[ -]end(?:ed)?(?: text)?"),
        ("single_choice", r"single choice(?: drop down)?|single select"),
    )
    for explicit_type, pattern in type_labels:
        if q_type not in {"metadata", "instruction", "screener", "rating_scale"} and any(
            re.match(r"^(?:" + pattern + r")(?:$|\s|(?=[A-Z][a-z]))", line, re.I)
            for line in block_lines[1:4]
        ):
            q_type = explicit_type
            break
    if q_type not in {"metadata", "instruction"} and re.search(r"how important are the following factors", full_block_lower):
        q_type = "matrix_grid"
    prompt = next((line for line in block_lines[1:] if line.endswith("?") and
                   not line.lower().startswith(NON_QUESTION_STARTS)), None)
    if not wording and prompt:
        wording = prompt

    if q_type == "single_choice" and not any(re.match(r"single (?:choice|select)", line, re.I) for line in block_lines[1:4]) and re.match(r"what brand\b", wording, re.I):
        q_type = "open_ended"
    if q_type == "single_choice" and sum(bool(re.match(r"^(?:Statement|Row|Item)\s+\w+\s*[:.)-]", line, re.I)) for line in block_lines) >= 2:
        q_type = "matrix_grid"

    options: list[str] = []
    matrix_items: list[str] = []
    option_codes: dict[str, str] = {}
    external_ref = next((l for l in block_lines if re.search(r"global .*excel|global excel", l, re.I)), None)
    screening_criteria: str | None = None
    routing_logic: str | None = None
    min_age: float | None = None
    max_age: float | None = None
    page_timer_min_seconds: float | None = None

    for bl in block_lines[1:]:
        bl_low = bl.lower()

        # Check screening instructions
        if any(term in bl_low for term in ["terminate if", "screen out", "validate q."]):
            screening_criteria = bl
        elif any(term in bl_low for term in ["skip to:", "skip to ", "go to "]):
            routing_logic = bl

        # Check for wording if not already found
        if not wording:
            if not any(bl_low.startswith(p) for p in NON_QUESTION_STARTS) and not bl.isupper() and len(bl) > 10:
                wording = bl

        typed_prompt = re.match(r"^(?:MULTIPLE CHOICE|SINGLE CHOICE)([A-Z].{10,})$", bl)
        if typed_prompt and not after_id:
            wording = typed_prompt[1]
        item = re.match(r"^(?:Statement|Row|Item)\s+(?:\d+|[A-Z])\s*[:.)-]\s*(.+)$", bl, re.I)
        if item and q_type in {"matrix_grid", "multi_choice_grid"}:
            matrix_items.append(item[1])
            continue

        # Options collection
        if bl != wording and not any(bl_low.startswith(p) for p in NON_QUESTION_STARTS):
            # Numbered / bulleted options
            opt_m = re.match(r"^(?:[0-9]{1,2}[\.\)]|[a-zA-Z][\.\)]|[\-•\*])\s*(.+)", bl)
            if opt_m:
                opt_str = opt_m.group(1).strip()
                if opt_str and len(opt_str) < 120 and not any(k in opt_str.lower() for k in ["skip to:", "quota:"]):
                    code = re.match(r"^(\d+|[A-Za-z])[.)]", bl)
                    options.append(f"{code[1]}. {opt_str}" if code else opt_str)
                    if code:
                        option_codes[code[1]] = opt_str
            elif q_type == "single_choice" and len(bl) < 60 and not bl.isupper():
                if not any(k in bl_low for k in ["skip to:", "quota:"]):
                    options.append(bl)

    # Clean wording of question type brackets
    if wording:
        wording = re.sub(r"\[(?:Select|Single|Multi|Matrix|Grid|Open|Numeric|Rating)[^\]]*\]", "", wording, flags=re.IGNORECASE).strip()

    age_question = bool(re.search(r"\bage\b|how old", wording or qid, re.I))
    age_country = None
    if age_question:
        min_age, max_age, age_country = _age_rule(block_lines)

    follow_ups = []
    for line in block_lines[1:]:
        match = re.match(r"^(.+?)\s*\((please specify|if so, [^)]+)\)(?:\s*_+)?(?:\s*\[anchor\])?$", line, re.I)
        if match:
            follow_ups.append({"option": match[1].strip(), "prompt": match[2], "type": "open_ended"})
    matrix_columns = []
    qc_notes = [line for line in block_lines if re.search(r"\bQC CHECK\b", line, re.I)]
    if q_type in {"matrix_grid", "multi_choice_grid"}:
        first = block_lines.index(wording) + 1 if wording in block_lines else 1
        before_items = True
        for line in block_lines[first:]:
            low = line.lower()
            if re.match(r"^(?:show (?:n|net|mean)|page break|manual qc|qc check|create embedded|ask if)", low):
                break
            if low.startswith("[show responses"):
                matrix_items.append(line)
                break
            if re.match(r"^(?:PART\s+\d+|STATEMENTS|ROWS|ATTRIBUTES)\b", line, re.I):
                before_items = False
                continue
            if low.startswith(("randomize", "split into", "you may select", "show attributes")):
                continue
            if before_items and re.match(r"^(?:extremely|somewhat|neither|not at all|very |moderately |slightly |dislike|like |had this|currently undergoing|never had|[0-9]+[.)])", low):
                matrix_columns.append(line)
                continue
            if matrix_columns and line != wording and not low.startswith(NON_QUESTION_STARTS):
                before_items = False
                if line not in matrix_items:
                    matrix_items.append(line)
    if external_ref and (wording == "[Survey end]" or wording.lower().startswith("skip to")):
        wording = ""

    # Page timer for this specific question/page
    timer_instructions = [*block_lines, *(s for line in block_lines for s in re.findall(r"\[([^\]]+)\]", line))]
    timer_values = {value for text in timer_instructions if (value := _timer_threshold(text, qid)) is not None}
    if len(timer_values) == 1:
        page_timer_min_seconds = timer_values.pop()

    return ParsedQuestion(
        question_number=qid,
        question_text=wording or qid,
        question_type=q_type,
        options=options,
        option_codes=option_codes,
        external_reference=external_ref,
        matrix_items=matrix_items,
        qc_instructions="\n".join(qc_notes) or None,
        follow_up_fields=follow_ups,
        matrix_columns=matrix_columns,
        routing_logic=routing_logic,
        screening_criteria=screening_criteria,
        min_age=min_age,
        age_countries=[age_country] if age_country else [],
        max_age=max_age,
        page_timer_min_seconds=page_timer_min_seconds
    )
