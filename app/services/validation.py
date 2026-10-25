import re
from dataclasses import dataclass
from typing import Any
from app.config import settings
from app.services.transformation import (
    clean_string,
    normalize_name,
    normalize_email,
    normalize_currency,
    parse_and_normalize_date,
    parse_float,
    parse_int,
)

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+$"
)

DATASET_REQUIRED_COLUMNS: dict[str, list[str]] = {
    "customers": ["customer_id", "name", "email", "created_at"],
    "sales": ["sale_id", "customer_id", "amount", "sale_date", "currency"],
    "inventory": ["product_id", "product_name", "quantity", "unit_price", "updated_at"],
}

DATASET_PRIMARY_KEYS: dict[str, str] = {
    "customers": "customer_id",
    "sales": "sale_id",
    "inventory": "product_id",
}

@dataclass
class RowValidationError:
    field_name: str
    error_code: str
    error_message: str
    original_value: str | None

@dataclass
class ValidationResult:
    is_valid: bool
    cleaned_data: dict[str, Any] | None
    errors: list[RowValidationError]

def is_valid_email_format(email: str) -> bool:
    """Validate email structure using standard pattern."""
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_REGEX.match(email))

def validate_customer_row(raw_row: dict[str, Any]) -> ValidationResult:
    """Validate and clean a single customer record."""
    errors: list[RowValidationError] = []
    
    # 1. Customer ID
    raw_cust_id = raw_row.get("customer_id")
    cleaned_cust_id = clean_string(raw_cust_id)
    if not cleaned_cust_id:
        errors.append(RowValidationError(
            field_name="customer_id",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Customer ID is required and cannot be blank.",
            original_value=str(raw_cust_id) if raw_cust_id is not None else None
        ))
        
    # 2. Name
    raw_name = raw_row.get("name")
    cleaned_name = normalize_name(raw_name)
    if not cleaned_name:
        errors.append(RowValidationError(
            field_name="name",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Customer name is required and cannot be blank.",
            original_value=str(raw_name) if raw_name is not None else None
        ))
        
    # 3. Email
    raw_email = raw_row.get("email")
    cleaned_email = normalize_email(raw_email)
    if not cleaned_email:
        errors.append(RowValidationError(
            field_name="email",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Email address is required and cannot be blank.",
            original_value=str(raw_email) if raw_email is not None else None
        ))
    elif not is_valid_email_format(cleaned_email):
        errors.append(RowValidationError(
            field_name="email",
            error_code="INVALID_EMAIL_FORMAT",
            error_message=f"'{cleaned_email}' is not a valid email address.",
            original_value=str(raw_email)
        ))
        
    # 4. Created At
    raw_date = raw_row.get("created_at")
    cleaned_date, date_valid = parse_and_normalize_date(raw_date)
    if not clean_string(raw_date):
        errors.append(RowValidationError(
            field_name="created_at",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Creation date is required.",
            original_value=str(raw_date) if raw_date is not None else None
        ))
    elif not date_valid:
        errors.append(RowValidationError(
            field_name="created_at",
            error_code="INVALID_DATE_FORMAT",
            error_message=f"'{raw_date}' could not be parsed as a valid calendar date.",
            original_value=str(raw_date)
        ))
        
    # 5. Phone (Optional)
    cleaned_phone = clean_string(raw_row.get("phone"))

    if errors:
        return ValidationResult(is_valid=False, cleaned_data=None, errors=errors)
        
    return ValidationResult(
        is_valid=True,
        cleaned_data={
            "customer_id": cleaned_cust_id,
            "name": cleaned_name,
            "email": cleaned_email,
            "phone": cleaned_phone,
            "created_at": cleaned_date,
        },
        errors=[]
    )

def validate_sale_row(raw_row: dict[str, Any]) -> ValidationResult:
    """Validate and clean a single sales record."""
    errors: list[RowValidationError] = []

    # 1. Sale ID
    raw_sale_id = raw_row.get("sale_id")
    cleaned_sale_id = clean_string(raw_sale_id)
    if not cleaned_sale_id:
        errors.append(RowValidationError(
            field_name="sale_id",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Sale ID is required and cannot be blank.",
            original_value=str(raw_sale_id) if raw_sale_id is not None else None
        ))

    # 2. Customer ID
    raw_cust_id = raw_row.get("customer_id")
    cleaned_cust_id = clean_string(raw_cust_id)
    if not cleaned_cust_id:
        errors.append(RowValidationError(
            field_name="customer_id",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Associated Customer ID is required.",
            original_value=str(raw_cust_id) if raw_cust_id is not None else None
        ))

    # 3. Amount
    raw_amount = raw_row.get("amount")
    parsed_amount, amount_valid = parse_float(raw_amount)
    if clean_string(raw_amount) is None:
        errors.append(RowValidationError(
            field_name="amount",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Sales amount is required.",
            original_value=str(raw_amount) if raw_amount is not None else None
        ))
    elif not amount_valid:
        errors.append(RowValidationError(
            field_name="amount",
            error_code="INVALID_NUMERIC_VALUE",
            error_message=f"'{raw_amount}' is not a valid numeric sales amount.",
            original_value=str(raw_amount)
        ))
    elif parsed_amount < 0.0:
        errors.append(RowValidationError(
            field_name="amount",
            error_code="NEGATIVE_VALUE",
            error_message=f"Sales amount cannot be negative (got {parsed_amount}).",
            original_value=str(raw_amount)
        ))

    # 4. Sale Date
    raw_date = raw_row.get("sale_date")
    cleaned_date, date_valid = parse_and_normalize_date(raw_date)
    if not clean_string(raw_date):
        errors.append(RowValidationError(
            field_name="sale_date",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Sale date is required.",
            original_value=str(raw_date) if raw_date is not None else None
        ))
    elif not date_valid:
        errors.append(RowValidationError(
            field_name="sale_date",
            error_code="INVALID_DATE_FORMAT",
            error_message=f"'{raw_date}' could not be parsed as a valid calendar date.",
            original_value=str(raw_date)
        ))

    # 5. Currency
    raw_curr = raw_row.get("currency")
    cleaned_curr = normalize_currency(raw_curr)
    if not cleaned_curr:
        errors.append(RowValidationError(
            field_name="currency",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Currency code is required.",
            original_value=str(raw_curr) if raw_curr is not None else None
        ))
    elif cleaned_curr not in settings.SUPPORTED_CURRENCIES:
        supported_str = ", ".join(sorted(settings.SUPPORTED_CURRENCIES))
        errors.append(RowValidationError(
            field_name="currency",
            error_code="INVALID_CURRENCY",
            error_message=f"Currency '{cleaned_curr}' is unsupported. Supported: {supported_str}.",
            original_value=str(raw_curr)
        ))

    if errors:
        return ValidationResult(is_valid=False, cleaned_data=None, errors=errors)

    return ValidationResult(
        is_valid=True,
        cleaned_data={
            "sale_id": cleaned_sale_id,
            "customer_id": cleaned_cust_id,
            "amount": round(parsed_amount, 2),
            "sale_date": cleaned_date,
            "currency": cleaned_curr,
        },
        errors=[]
    )

def validate_inventory_row(raw_row: dict[str, Any]) -> ValidationResult:
    """Validate and clean a single inventory record."""
    errors: list[RowValidationError] = []

    # 1. Product ID
    raw_prod_id = raw_row.get("product_id")
    cleaned_prod_id = clean_string(raw_prod_id)
    if not cleaned_prod_id:
        errors.append(RowValidationError(
            field_name="product_id",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Product ID is required and cannot be blank.",
            original_value=str(raw_prod_id) if raw_prod_id is not None else None
        ))

    # 2. Product Name
    raw_name = raw_row.get("product_name")
    cleaned_name = normalize_name(raw_name)
    if not cleaned_name:
        errors.append(RowValidationError(
            field_name="product_name",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Product name is required and cannot be blank.",
            original_value=str(raw_name) if raw_name is not None else None
        ))

    # 3. Quantity
    raw_qty = raw_row.get("quantity")
    parsed_qty, qty_valid = parse_int(raw_qty)
    if clean_string(raw_qty) is None:
        errors.append(RowValidationError(
            field_name="quantity",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Quantity is required.",
            original_value=str(raw_qty) if raw_qty is not None else None
        ))
    elif not qty_valid:
        errors.append(RowValidationError(
            field_name="quantity",
            error_code="INVALID_NUMERIC_VALUE",
            error_message=f"'{raw_qty}' is not a valid integer quantity.",
            original_value=str(raw_qty)
        ))
    elif parsed_qty < 0:
        errors.append(RowValidationError(
            field_name="quantity",
            error_code="NEGATIVE_VALUE",
            error_message=f"Quantity cannot be negative (got {parsed_qty}).",
            original_value=str(raw_qty)
        ))

    # 4. Unit Price
    raw_price = raw_row.get("unit_price")
    parsed_price, price_valid = parse_float(raw_price)
    if clean_string(raw_price) is None:
        errors.append(RowValidationError(
            field_name="unit_price",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Unit price is required.",
            original_value=str(raw_price) if raw_price is not None else None
        ))
    elif not price_valid:
        errors.append(RowValidationError(
            field_name="unit_price",
            error_code="INVALID_NUMERIC_VALUE",
            error_message=f"'{raw_price}' is not a valid numeric price.",
            original_value=str(raw_price)
        ))
    elif parsed_price < 0.0:
        errors.append(RowValidationError(
            field_name="unit_price",
            error_code="NEGATIVE_VALUE",
            error_message=f"Unit price cannot be negative (got {parsed_price}).",
            original_value=str(raw_price)
        ))

    # 5. Updated At
    raw_date = raw_row.get("updated_at")
    cleaned_date, date_valid = parse_and_normalize_date(raw_date)
    if not clean_string(raw_date):
        errors.append(RowValidationError(
            field_name="updated_at",
            error_code="MISSING_REQUIRED_FIELD",
            error_message="Update timestamp date is required.",
            original_value=str(raw_date) if raw_date is not None else None
        ))
    elif not date_valid:
        errors.append(RowValidationError(
            field_name="updated_at",
            error_code="INVALID_DATE_FORMAT",
            error_message=f"'{raw_date}' could not be parsed as a valid calendar date.",
            original_value=str(raw_date)
        ))

    if errors:
        return ValidationResult(is_valid=False, cleaned_data=None, errors=errors)

    return ValidationResult(
        is_valid=True,
        cleaned_data={
            "product_id": cleaned_prod_id,
            "product_name": cleaned_name,
            "quantity": parsed_qty,
            "unit_price": round(parsed_price, 2),
            "updated_at": cleaned_date,
        },
        errors=[]
    )

def validate_row_by_dataset(dataset_type: str, raw_row: dict[str, Any]) -> ValidationResult:
    """Dispatch row validation according to dataset type."""
    if dataset_type == "customers":
        return validate_customer_row(raw_row)
    elif dataset_type == "sales":
        return validate_sale_row(raw_row)
    elif dataset_type == "inventory":
        return validate_inventory_row(raw_row)
    else:
        return ValidationResult(
            is_valid=False,
            cleaned_data=None,
            errors=[
                RowValidationError(
                    field_name="dataset_type",
                    error_code="UNSUPPORTED_DATASET",
                    error_message=f"Dataset '{dataset_type}' is unsupported.",
                    original_value=dataset_type
                )
            ]
        )
