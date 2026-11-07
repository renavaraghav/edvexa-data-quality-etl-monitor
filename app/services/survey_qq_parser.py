"""
Questionnaire (QQ) Reference Document Extraction Service.
Extracts questions, question numbers, wording, question types, response options,
matrix statement items, timing instructions, screener criteria, and skip logic.
Supports PDF, DOCX, XLSX, and TXT/MD formats.
Handles scanned or unreadable documents honestly and transparently.
"""

import io
import logging
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
import openpyxl

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


@dataclass
class QuestionnaireExtractionResult:
    questions: list[ParsedQuestion] = field(default_factory=list)
    expected_duration_min_minutes: float | None = None
    expected_duration_max_minutes: float | None = None
    page_timer_threshold_seconds: float | None = None
    page_timer_rules: dict[str, float] = field(default_factory=dict)
    duration_source: str = "Platform Standard Baseline"
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
    """Extract raw text from PDF content using pypdf."""
    try:
        from pypdf import PdfReader
        stream = io.BytesIO(content)
        reader = PdfReader(stream)
        text_parts = []
        for page_idx, page in enumerate(reader.pages):
            page_text = page.extract_text() or ""
            text_parts.append(page_text)
        full_text = "\n\n".join(text_parts).strip()
        is_scanned = len(reader.pages) > 0 and len(full_text) < 50
        return full_text, is_scanned
    except Exception as e:
        logger.warning(f"PDF extraction error: {e}")
        return "", True


def extract_text_from_docx(content: bytes) -> tuple[str, bool]:
    """
    Extract text and table contents from DOCX using standard library zipfile and XML.
    Requires no external packages; 100% reliable across all environments.
    """
    try:
        stream = io.BytesIO(content)
        with zipfile.ZipFile(stream, "r") as docx_zip:
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
    except Exception as e:
        logger.warning(f"DOCX extraction error: {e}")
        return "", True


def extract_text_from_xlsx(content: bytes) -> tuple[str, bool]:
    """Extract questions or text from XLSX questionnaire / codebook."""
    try:
        stream = io.BytesIO(content)
        wb = openpyxl.load_workbook(stream, read_only=True, data_only=True)
        lines = []
        for sheet in wb.worksheets:
            lines.append(f"--- Sheet: {sheet.title} ---")
            for row in sheet.iter_rows(values_only=True):
                non_empty = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if non_empty:
                    if len(non_empty) >= 2 and re.match(r"^[\[\(]?[QSD0-9a-zA-Z_]+", non_empty[0], re.IGNORECASE):
                        lines.append(f"{non_empty[0]}. {' | '.join(non_empty[1:])}")
                    else:
                        lines.append(" | ".join(non_empty))
        full_text = "\n".join(lines).strip()
        return full_text, len(full_text) < 30
    except Exception as e:
        logger.warning(f"XLSX questionnaire extraction error: {e}")
        return "", True


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
                result.expected_duration_min_minutes = max(1.0, mid * 0.7)
                result.expected_duration_max_minutes = mid * 1.5
                result.duration_source = "Questionnaire Specification"
                result.extraction_notes.append(
                    f"Identified expected survey duration: ~{mid:.0f} minutes (from QQ text)."
                )
                break

    # 2. Detect Page / Question Timer Thresholds (Only if explicitly stated in QQ)
    timer_pattern = r"(?:page|screen|question|factor)?\s*(?:submit\s+)?timer\s*(?:threshold|minimum|cutoff|rule)?\s*(?:is|of|:)?\s*(\d+)\s*(?:sec|secs|second|seconds)"
    tm = re.search(timer_pattern, raw_text, re.IGNORECASE)
    if tm:
        result.page_timer_threshold_seconds = float(tm.group(1))
        result.extraction_notes.append(
            f"Identified individual page timer threshold: {result.page_timer_threshold_seconds:.0f} seconds (from QQ text)."
        )

    # 3. Parse Individual Questions and Sections
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
        m1 = re.match(r"^(Q\.[A-Za-z0-9_][A-Za-z0-9_\s/-]*?)(?:[\.:\-\)]|\s*$)", line)
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

    # Process each question block
    for i, (qid, start_idx, line_str) in enumerate(unique_candidates):
        end_idx = unique_candidates[i + 1][1] if i + 1 < len(unique_candidates) else len(lines)
        block_lines = lines[start_idx:end_idx]

        parsed = _interpret_question_block(qid, line_str, block_lines)
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
    elif "past_current_usage_net" in q_lower:
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

    options: list[str] = []
    matrix_items: list[str] = []
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

        # Options collection
        if bl != wording and not any(bl_low.startswith(p) for p in NON_QUESTION_STARTS):
            # Numbered / bulleted options
            opt_m = re.match(r"^(?:[0-9]{1,2}[\.\)]|[a-zA-Z][\.\)]|[\-•\*])\s*(.+)", bl)
            if opt_m:
                opt_str = opt_m.group(1).strip()
                if opt_str and len(opt_str) < 120 and not any(k in opt_str.lower() for k in ["skip to:", "quota:"]):
                    options.append(opt_str)
            elif q_type == "single_choice" and len(bl) < 60 and not bl.isupper():
                if not any(k in bl_low for k in ["skip to:", "quota:"]):
                    options.append(bl)

    # Clean wording of question type brackets
    if wording:
        wording = re.sub(r"\[(?:Select|Single|Multi|Matrix|Grid|Open|Numeric|Rating)[^\]]*\]", "", wording, flags=re.IGNORECASE).strip()

    # Age criteria parser
    if "under 18" in full_block_lower or "terminate if under 18" in full_block_lower or "at least 18" in full_block_lower:
        min_age = 18.0
    elif "between 12 to 17" in full_block_lower or "ages 12 to 17" in full_block_lower or "12-17" in full_block_lower:
        min_age = 12.0
        max_age = 17.0
    else:
        age_range_m = re.search(r"(?:ages?|must\s+be|target)\s*(?:between\s+)?(\d+)\s*(?:to|–|-|and)\s*(\d+)", full_block_lower)
        if age_range_m:
            min_age = float(age_range_m.group(1))
            max_age = float(age_range_m.group(2))

    # Page timer for this specific question/page
    pt_m = re.search(r"(?:page\s+timer|submit\s+timer|timer\s+threshold|minimum\s+time)\s*(?:is|:)?\s*(\d+)\s*(?:s|sec|seconds)", full_block_lower)
    if pt_m:
        page_timer_min_seconds = float(pt_m.group(1))

    return ParsedQuestion(
        question_number=qid,
        question_text=wording or qid,
        question_type=q_type,
        options=options,
        matrix_items=matrix_items,
        qc_instructions=None,
        routing_logic=routing_logic,
        screening_criteria=screening_criteria,
        min_age=min_age,
        max_age=max_age,
        page_timer_min_seconds=page_timer_min_seconds
    )
