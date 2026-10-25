import pytest
from app.services.validation import (
    validate_customer_row,
    validate_sale_row,
    validate_inventory_row,
    is_valid_email_format,
)
from app.services.csv_import import parse_and_validate_csv

# --- Customer Validation Tests ---

def test_valid_customer_row():
    raw = {
        "customer_id": "  CUST-001  ",
        "name": "jane doe",
        "email": "JANE.DOE@EXAMPLE.COM",
        "phone": "555-0199",
        "created_at": "2024-01-15",
    }
    result = validate_customer_row(raw)
    assert result.is_valid is True
    assert result.cleaned_data["customer_id"] == "CUST-001"
    assert result.cleaned_data["name"] == "Jane Doe"
    assert result.cleaned_data["email"] == "jane.doe@example.com"
    assert result.cleaned_data["created_at"] == "2024-01-15"
    assert len(result.errors) == 0

def test_customer_missing_required_fields():
    raw = {
        "customer_id": "",
        "name": "",
        "email": "",
        "created_at": "",
    }
    result = validate_customer_row(raw)
    assert result.is_valid is False
    codes = [e.error_code for e in result.errors]
    assert codes.count("MISSING_REQUIRED_FIELD") == 4

def test_customer_invalid_email():
    raw = {
        "customer_id": "CUST-002",
        "name": "Bob",
        "email": "plainaddress-without-at",
        "created_at": "2024-01-15",
    }
    result = validate_customer_row(raw)
    assert result.is_valid is False
    assert result.errors[0].error_code == "INVALID_EMAIL_FORMAT"
    assert result.errors[0].field_name == "email"

def test_customer_invalid_date():
    raw = {
        "customer_id": "CUST-003",
        "name": "Charlie",
        "email": "charlie@example.com",
        "created_at": "2023-02-29",  # Invalid calendar date
    }
    result = validate_customer_row(raw)
    assert result.is_valid is False
    assert result.errors[0].error_code == "INVALID_DATE_FORMAT"
    assert result.errors[0].field_name == "created_at"

# --- Sales Validation Tests ---

def test_valid_sale_row():
    raw = {
        "sale_id": "SALE-101",
        "customer_id": "CUST-001",
        "amount": "149.99",
        "sale_date": "2024-02-10",
        "currency": "usd",
    }
    result = validate_sale_row(raw)
    assert result.is_valid is True
    assert result.cleaned_data["sale_id"] == "SALE-101"
    assert result.cleaned_data["amount"] == 149.99
    assert result.cleaned_data["currency"] == "USD"

def test_sale_negative_amount():
    raw = {
        "sale_id": "SALE-102",
        "customer_id": "CUST-001",
        "amount": "-50.00",
        "sale_date": "2024-02-10",
        "currency": "USD",
    }
    result = validate_sale_row(raw)
    assert result.is_valid is False
    assert any(e.error_code == "NEGATIVE_VALUE" for e in result.errors)

def test_sale_unsupported_currency():
    raw = {
        "sale_id": "SALE-103",
        "customer_id": "CUST-001",
        "amount": "100.00",
        "sale_date": "2024-02-10",
        "currency": "DOGECOIN",
    }
    result = validate_sale_row(raw)
    assert result.is_valid is False
    assert any(e.error_code == "INVALID_CURRENCY" for e in result.errors)

# --- Inventory Validation Tests ---

def test_valid_inventory_row():
    raw = {
        "product_id": "PROD-10",
        "product_name": "gaming monitor",
        "quantity": "25",
        "unit_price": "299.99",
        "updated_at": "2024-03-01",
    }
    result = validate_inventory_row(raw)
    assert result.is_valid is True
    assert result.cleaned_data["product_id"] == "PROD-10"
    assert result.cleaned_data["product_name"] == "Gaming Monitor"
    assert result.cleaned_data["quantity"] == 25
    assert result.cleaned_data["unit_price"] == 299.99

def test_inventory_negative_quantity_and_price():
    raw = {
        "product_id": "PROD-11",
        "product_name": "Widget",
        "quantity": "-10",
        "unit_price": "-5.50",
        "updated_at": "2024-03-01",
    }
    result = validate_inventory_row(raw)
    assert result.is_valid is False
    neg_errors = [e for e in result.errors if e.error_code == "NEGATIVE_VALUE"]
    assert len(neg_errors) == 2  # Both quantity and price rejected

# --- CSV Import & Structural Validation Tests ---

def test_csv_import_empty_file():
    res = parse_and_validate_csv(b"", "empty.csv", "customers")
    assert res.is_valid is False
    assert "empty" in res.error_message.lower()

def test_csv_import_missing_columns():
    csv_bytes = b"customer_id,name\nCUST-01,Alice"
    res = parse_and_validate_csv(csv_bytes, "customers.csv", "customers")
    assert res.is_valid is False
    assert "missing required column" in res.error_message.lower()

def test_csv_import_duplicate_headers():
    csv_bytes = b"customer_id,name,email,created_at,name\nCUST-01,Alice,alice@ex.com,2024-01-01,Alice2"
    res = parse_and_validate_csv(csv_bytes, "customers.csv", "customers")
    assert res.is_valid is False
    assert "duplicate column" in res.error_message.lower()

def test_csv_import_utf8_bom():
    # UTF-8 BOM is \xef\xbb\xbf
    bom_csv = "\ufeffcustomer_id,name,email,created_at\nCUST-01,Alice,alice@example.com,2024-01-01".encode("utf-8")
    res = parse_and_validate_csv(bom_csv, "customers.csv", "customers")
    assert res.is_valid is True
    assert res.headers[0] == "customer_id"
    assert len(res.rows) == 1
