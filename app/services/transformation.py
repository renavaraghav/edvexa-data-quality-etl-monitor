import re
from datetime import datetime
import math
from typing import Any

DATE_FORMATS = [
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m-%d-%Y",
    "%d-%m-%Y",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
]

def clean_string(value: Any) -> str | None:
    """Trim leading/trailing whitespace and normalize empty/blank strings to None."""
    if value is None:
        return None
    val_str = str(value).strip()
    if not val_str or val_str.lower() in {"nan", "none", "null", "n/a", "na"}:
        return None
    return val_str

def normalize_name(value: Any) -> str | None:
    """Normalize customer and product names: trim whitespace and apply title capitalization."""
    cleaned = clean_string(value)
    if cleaned is None:
        return None
    # Collapse multiple whitespace characters into a single space
    collapsed = re.sub(r"\s+", " ", cleaned)
    # Title-case each word while preserving internal alphanumeric casing where appropriate
    return collapsed.title()

def normalize_email(value: Any) -> str | None:
    """Normalize email: trim whitespace and convert to lowercase."""
    cleaned = clean_string(value)
    if cleaned is None:
        return None
    return cleaned.lower()

def normalize_currency(value: Any) -> str | None:
    """Normalize currency code: trim whitespace and convert to uppercase."""
    cleaned = clean_string(value)
    if cleaned is None:
        return None
    return cleaned.upper()

def parse_and_normalize_date(value: Any) -> tuple[str | None, bool]:
    """
    Attempt to parse and normalize a date string into ISO YYYY-MM-DD format.
    Returns:
        (iso_date_string, is_valid_boolean)
    """
    cleaned = clean_string(value)
    if cleaned is None:
        return (None, False)
    
    # Direct match check for YYYY-MM-DD with calendar validation
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", cleaned):
        try:
            dt = datetime.strptime(cleaned, "%Y-%m-%d")
            return (dt.strftime("%Y-%m-%d"), True)
        except ValueError:
            return (None, False)
            
    for fmt in DATE_FORMATS:
        try:
            dt = datetime.strptime(cleaned, fmt)
            return (dt.strftime("%Y-%m-%d"), True)
        except (ValueError, TypeError):
            continue
            
    return (None, False)

def parse_float(value: Any) -> tuple[float | None, bool]:
    """
    Attempt to parse float value, rejecting NaN and Inf.
    Returns:
        (float_value, is_valid_boolean)
    """
    cleaned = clean_string(value)
    if cleaned is None:
        return (None, False)
    
    # Strip optional currency symbols or commas in formatted numbers
    cleaned_num = cleaned.replace(",", "").replace("$", "").replace("€", "").replace("£", "")
    try:
        val = float(cleaned_num)
        if math.isnan(val) or math.isinf(val):
            return (None, False)
        return (val, True)
    except (ValueError, TypeError):
        return (None, False)

def parse_int(value: Any) -> tuple[int | None, bool]:
    """
    Attempt to parse integer value.
    Returns:
        (int_value, is_valid_boolean)
    """
    cleaned = clean_string(value)
    if cleaned is None:
        return (None, False)
    
    cleaned_num = cleaned.replace(",", "")
    try:
        # Check if float before int, e.g. "12.0"
        val_f = float(cleaned_num)
        if math.isnan(val_f) or math.isinf(val_f):
            return (None, False)
        if val_f.is_integer():
            return (int(val_f), True)
        return (None, False)
    except (ValueError, TypeError):
        return (None, False)
