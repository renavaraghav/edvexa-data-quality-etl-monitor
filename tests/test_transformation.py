import pytest
from app.services.transformation import (
    clean_string,
    normalize_name,
    normalize_email,
    normalize_currency,
    parse_and_normalize_date,
    parse_float,
    parse_int,
)

def test_clean_string():
    assert clean_string("  hello world  ") == "hello world"
    assert clean_string("") is None
    assert clean_string("   ") is None
    assert clean_string(None) is None
    assert clean_string("NULL") is None
    assert clean_string("N/A") is None
    assert clean_string("NaN") is None

def test_normalize_name():
    assert normalize_name("  john   doe  ") == "John Doe"
    assert normalize_name("ALICE SMITH") == "Alice Smith"
    assert normalize_name("   ") is None
    assert normalize_name(None) is None

def test_normalize_email():
    assert normalize_email("  User.Name@Example.COM ") == "user.name@example.com"
    assert normalize_email("") is None
    assert normalize_email(None) is None

def test_normalize_currency():
    assert normalize_currency("  usd ") == "USD"
    assert normalize_currency("eur") == "EUR"
    assert normalize_currency("") is None

def test_parse_and_normalize_date():
    # ISO date
    date_str, ok = parse_and_normalize_date("2024-05-12")
    assert ok is True
    assert date_str == "2024-05-12"

    # US format MM/DD/YYYY
    date_str, ok = parse_and_normalize_date("02/28/2024")
    assert ok is True
    assert date_str == "2024-02-28"

    # Datetime with timestamp
    date_str, ok = parse_and_normalize_date("2024-03-15 14:30:00")
    assert ok is True
    assert date_str == "2024-03-15"

    # Invalid calendar dates
    _, ok = parse_and_normalize_date("2023-02-29")  # 2023 is not a leap year
    assert ok is False

    _, ok = parse_and_normalize_date("2024-13-45")
    assert ok is False

    _, ok = parse_and_normalize_date("not-a-date")
    assert ok is False

    _, ok = parse_and_normalize_date("")
    assert ok is False

def test_parse_float():
    val, ok = parse_float("1250.50")
    assert ok is True
    assert val == 1250.50

    # Currency symbol and comma
    val, ok = parse_float(" $1,450.75 ")
    assert ok is True
    assert val == 1450.75

    # Negative float
    val, ok = parse_float("-45.90")
    assert ok is True
    assert val == -45.90

    # Invalid floats
    _, ok = parse_float("abc")
    assert ok is False

    _, ok = parse_float("NaN")
    assert ok is False

    _, ok = parse_float("")
    assert ok is False

def test_parse_int():
    val, ok = parse_int("45")
    assert ok is True
    assert val == 45

    val, ok = parse_int(" 1,000 ")
    assert ok is True
    assert val == 1000

    val, ok = parse_int("12.0")
    assert ok is True
    assert val == 12

    # Reject non-integers
    _, ok = parse_int("12.75")
    assert ok is False

    _, ok = parse_int("hello")
    assert ok is False
