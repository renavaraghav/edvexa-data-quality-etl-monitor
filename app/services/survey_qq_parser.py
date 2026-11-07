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
    question_type: str  # single_choice, multiple_choice, matrix_grid, open_ended, numeric, rating_scale, screener, timer, metadata
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
    duration_source: str = "Questionnaire Specification"
    screener_rules: list[dict[str, Any]] = field(default_factory=list)
    skip_logic_rules: list[dict[str, Any]] = field(default_factory=list)
    unreadable_or_scanned: bool = False
    extraction_notes: list[str] = field(default_factory=list)
    raw_text_length: int = 0
    document_format: str = "unknown"


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
        # If document has pages but virtually no text, it is likely a scanned image
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
            # Namespace for WordprocessingML
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
        # Fallback to UTF-8 / Latin-1 plain text
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

    # 1. Detect Overall Expected Duration
    # Examples: "7-15 minutes", "7 to 15 minutes", "approximately 10 minutes", "LOI: 12 mins"
    duration_patterns = [
        r"(?:expected|average|approximate|target|survey)?\s*(?:duration|length|loi|time)\s*(?:is|of|:)?\s*(\d+)\s*(?:-|to)\s*(\d+)\s*(?:min|mins|minute|minutes)",
        r"(\d+)\s*(?:-|to)\s*(\d+)\s*(?:min|mins|minute|minutes)\s*(?:survey|duration|length)",
        r"(?:duration|length|loi)\s*(?:is|of|:)?\s*(\d+)\s*(?:min|mins|minute|minutes)"
    ]
    for pat in duration_patterns:
        m = re.search(pat, raw_text, re.IGNORECASE)
        if m:
            if len(m.groups()) == 2 and m.group(1) and m.group(2):
                result.expected_duration_min_minutes = float(m.group(1))
                result.expected_duration_max_minutes = float(m.group(2))
                result.extraction_notes.append(
                    f"Identified expected survey duration: {result.expected_duration_min_minutes:.0f}–{result.expected_duration_max_minutes:.0f} minutes."
                )
                break
            elif len(m.groups()) >= 1 and m.group(1):
                mid = float(m.group(1))
                result.expected_duration_min_minutes = max(1.0, mid * 0.7)
                result.expected_duration_max_minutes = mid * 1.5
                result.extraction_notes.append(
                    f"Identified expected survey duration: ~{mid:.0f} minutes."
                )
                break

    # 2. Detect Page / Question Timer Thresholds
    timer_pattern = r"(?:page|screen|question|factor)?\s*(?:submit\s+)?timer[^\d]*(\d+)\s*(?:sec|secs|second|seconds)"
    tm = re.search(timer_pattern, raw_text, re.IGNORECASE)
    if tm:
        result.page_timer_threshold_seconds = float(tm.group(1))
        result.extraction_notes.append(
            f"Identified individual page timer threshold: {result.page_timer_threshold_seconds:.0f} seconds."
        )

    # 3. Parse Individual Questions
    # Match patterns like:
    # Q1. What is your age?
    # Q2: Have you considered orthodontic treatment?
    # S1. Are you 18 or older?
    # D1. What is your household income?
    # OE_BRAND. Which brands come to mind?
    # Question 4: How likely are you...
    question_regex = re.compile(
        r"(?:^|\n)\s*(?:(?:\[?(?:Q|S|D|OE|SCREENER|QUESTION|QS|DEMO|Timer)\s*[\._\-]?[0-9a-zA-Z_]+\]?)|(?:Question\s+\d+))[\.:\-\)]\s*(.+?)(?=(?:\n\s*(?:(?:\[?(?:Q|S|D|OE|SCREENER|QUESTION|QS|DEMO|Timer)\s*[\._\-]?[0-9a-zA-Z_]+\]?)|(?:Question\s+\d+))[\.:\-\)])|\Z)",
        re.DOTALL | re.IGNORECASE
    )

    matches = list(question_regex.finditer(raw_text))

    if not matches:
        # Fallback line-by-line heuristic if block regex didn't catch standard markers
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        for line in lines:
            m = re.match(r"^([QSD]\d+|Q[0-9a-zA-Z_]+|Q\.[A-Za-z0-9_]+|Question\s+\d+|Screener\s+\d+|OE_[A-Za-z0-9_]+)[\.:\-\)\|]\s*(.+)", line, re.IGNORECASE)
            if m:
                qnum = m.group(1).strip()
                qbody = m.group(2).strip()
                parsed = _interpret_question_block(qnum, qbody)
                result.questions.append(parsed)
                if parsed.page_timer_min_seconds:
                    result.page_timer_rules[parsed.question_number] = parsed.page_timer_min_seconds
    else:
        for match in matches:
            block = match.group(0).strip()
            # Extract question identifier from start of block
            id_m = re.match(r"^(\[?(?:Q|S|D|OE|SCREENER|QUESTION|QS|DEMO|Timer)\s*[\._\-]?[0-9a-zA-Z_]+\]?|Question\s+\d+)[\.:\-\)]\s*(.*)", block, re.DOTALL | re.IGNORECASE)
            if id_m:
                qnum = id_m.group(1).replace("[", "").replace("]", "").strip()
                qbody = id_m.group(2).strip()
                parsed = _interpret_question_block(qnum, qbody)
                result.questions.append(parsed)
                if parsed.page_timer_min_seconds:
                    result.page_timer_rules[parsed.question_number] = parsed.page_timer_min_seconds

    result.extraction_notes.append(f"Extracted {len(result.questions)} structured questions from the questionnaire.")
    return result


def _interpret_question_block(qnum: str, body: str) -> ParsedQuestion:
    """Interpret question wording, type, response options, and routing logic from text block."""
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    question_text = lines[0] if lines else body[:120]

    # Clean question text if options or instructions follow on same line
    instr_cut = re.split(r"(\[(?:Select|Single|Multi|Matrix|Grid|Open|Numeric|Rating)[^\]]*\])", question_text, flags=re.IGNORECASE)
    if len(instr_cut) > 1:
        question_text = instr_cut[0].strip()

    q_lower = body.lower()
    q_type = "single_choice"
    options: list[str] = []
    matrix_items: list[str] = []
    qc_instructions = None
    routing_logic = None
    screening_criteria = None

    # Determine Question Type
    if any(k in q_lower for k in ["open-end", "open end", "text box", "please specify", "in your own words", "oe_brand", "what brands come to mind", "describe"]):
        q_type = "open_ended"
    elif any(k in q_lower for k in ["matrix", "grid", "rate each", "how much do you agree", "strongly agree", "scale of 1"]):
        q_type = "matrix_grid"
    elif any(k in q_lower for k in ["select all that apply", "multi-select", "multiselect", "which of the following"]):
        q_type = "multiple_choice"
    elif any(k in q_lower for k in ["how old", "enter age", "percentage", "number of", "numeric"]):
        q_type = "numeric"
    elif any(k in q_lower for k in ["nps", "recommend", "scale of 0-10", "scale of 1-10", "rating"]):
        q_type = "rating_scale"
    elif qnum.upper().startswith("S") or "screener" in q_lower or "screening" in q_lower:
        q_type = "screener"

    # Extract options (lines starting with 1., 2., a., -, •, etc.)
    for line in lines[1:]:
        opt_m = re.match(r"^(?:[0-9]{1,2}[\.\)]|[a-zA-Z][\.\)]|[\-•\*])\s*(.+)", line)
        if opt_m:
            options.append(opt_m.group(1).strip())
        elif "terminate if" in line.lower() or "screen out" in line.lower():
            screening_criteria = line.strip()
        elif "skip to" in line.lower() or "ask only if" in line.lower():
            routing_logic = line.strip()

    # Identify matrix statement items if matrix_grid
    if q_type == "matrix_grid":
        for line in lines[1:]:
            if re.match(r"^(?:Statement|\d+[\.\)]|[a-z][\.\)])\s*(.+)", line, re.IGNORECASE):
                stmt_text = re.sub(r"^(?:Statement|\d+[\.\)]|[a-z][\.\)])\s*", "", line, flags=re.IGNORECASE).strip()
                matrix_items.append(stmt_text)

    # Screening instructions
    if "terminate" in q_lower or "must be" in q_lower:
        term_m = re.search(r"(terminate\s+[^\.\n]+|must be\s+[^\.\n]+)", body, re.IGNORECASE)
        if term_m:
            screening_criteria = term_m.group(1).strip()

    # Routing instructions
    if "skip to" in q_lower or "go to" in q_lower:
        skip_m = re.search(r"((?:if|skip|go to)\s+[^\.\n]+(?:skip to|go to|terminate)[^\.\n]+)", body, re.IGNORECASE)
        if skip_m:
            routing_logic = skip_m.group(1).strip()

    min_age: float | None = None
    max_age: float | None = None
    page_timer_min_seconds: float | None = None

    # Age criteria parser
    # 1. "terminate if under 18" -> min_age = 18.0
    # 2. "must be between 12 and 17" -> min_age = 12.0, max_age = 17.0
    # 3. "target ages 12 to 17" or "teen survey ages 12–17" -> min_age = 12.0, max_age = 17.0
    # 4. "must be 18 or older" / "at least 18" -> min_age = 18.0
    # 5. "terminate if over 65" -> max_age = 65.0
    age_range_m = re.search(r"(?:ages?|must\s+be|target)\s*(?:between\s+)?(\d+)\s*(?:to|–|-|and)\s*(\d+)", q_lower)
    if age_range_m:
        min_age = float(age_range_m.group(1))
        max_age = float(age_range_m.group(2))
    else:
        age_under_m = re.search(r"(?:terminate|screen\s*out|disqualify)?\s*(?:if\s+)?(?:age\s+)?(?:under|<)\s*(\d+)", q_lower)
        if age_under_m:
            min_age = float(age_under_m.group(1))

        age_older_m = re.search(r"(?:must\s+be|at\s+least)\s+(\d+)\s*(?:\+|or\s+older|years\s+old\s+or\s+older)", q_lower)
        if age_older_m:
            min_age = float(age_older_m.group(1))

        age_over_m = re.search(r"(?:terminate|screen\s*out|disqualify)?\s*(?:if\s+)?(?:age\s+)?(?:over|>)\s*(\d+)", q_lower)
        if age_over_m:
            max_age = float(age_over_m.group(1))

    # Page timer rule for this specific question/page (e.g. "page submit timer 20 seconds", "timer minimum: 20s")
    pt_m = re.search(r"(?:page\s+timer|submit\s+timer|timer\s+threshold|minimum\s+time|page\s+time|timer)[^\d]*(\d+)\s*(?:s|sec|seconds)", q_lower)
    if pt_m:
        page_timer_min_seconds = float(pt_m.group(1))

    return ParsedQuestion(
        question_number=qnum,
        question_text=question_text,
        question_type=q_type,
        options=options,
        matrix_items=matrix_items,
        qc_instructions=qc_instructions,
        routing_logic=routing_logic,
        screening_criteria=screening_criteria,
        min_age=min_age,
        max_age=max_age,
        page_timer_min_seconds=page_timer_min_seconds
    )
