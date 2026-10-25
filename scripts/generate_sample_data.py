"""
Generate synthetic sample CSV files for testing the Edvexa Data Quality & ETL Monitor platform.
Includes a documented mixture of:
- Clean valid records
- Inconsistent whitespace and casing
- Invalid email formats
- Missing required fields
- Unparseable or impossible calendar dates
- Prohibited negative numbers
- In-file duplicate primary business keys
"""

from pathlib import Path
import csv

BASE_DIR = Path(__file__).resolve().parent.parent
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
SAMPLE_DATA_DIR.mkdir(parents=True, exist_ok=True)

def generate_customers_csv():
    customers_path = SAMPLE_DATA_DIR / "customers.csv"
    headers = ["customer_id", "name", "email", "phone", "created_at"]
    rows = [
        # 1. Valid clean records with normalization tests
        ["CUST-001", "  alexander pierce  ", "ALEX.PIERCE@EXAMPLE.COM", "+1-555-0101", "2024-01-15"],
        ["CUST-002", "Elena Rostova", "elena.rostova@acme.org", "555-0102", "02/18/2024"],
        ["CUST-003", "Marcus Aurelius Vance", "m.vance@rome.io", "+44-20-7946-0919", "2023-11-30"],
        ["CUST-004", "Sophia Lin", "sophia.lin@techcorp.com", "", "2024-03-01"],
        ["CUST-005", "David Kim", "dkim@startup.co", "555-0199", "2024-02-14"],

        # 2. In-file Duplicate primary key (CUST-001 already appeared above)
        ["CUST-001", "Alexander Duplicate", "alex.duplicate@example.com", "555-9999", "2024-01-16"],

        # 3. Invalid email formats
        ["CUST-006", "Brian BadEmail", "plainaddress", "555-0106", "2024-01-20"],
        ["CUST-007", "Clara MissingAt", "clara.example.com", "555-0107", "2024-01-22"],

        # 4. Missing required fields
        ["", "Missing ID User", "noid@example.com", "555-0108", "2024-01-25"],
        ["CUST-008", "", "noname@example.com", "555-0109", "2024-01-26"],
        ["CUST-009", "Missing Email User", "", "555-0110", "2024-01-27"],

        # 5. Invalid calendar dates
        ["CUST-010", "Felix LeapDayInvalid", "felix@leap.com", "555-0111", "2023-02-29"],
        ["CUST-011", "George BadDateString", "george@baddate.com", "555-0112", "not-a-date"],
    ]

    with open(customers_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"[OK] Generated {customers_path} ({len(rows)} records)")

    # Also generate specialized test sets
    valid_path = SAMPLE_DATA_DIR / "customers_valid.csv"
    with open(valid_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows[:5])
    print(f"[OK] Generated {valid_path} (5 clean valid records)")

    invalid_path = SAMPLE_DATA_DIR / "customers_invalid.csv"
    with open(invalid_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows[6:])
    print(f"[OK] Generated {invalid_path} (7 invalid records)")

    dup_path = SAMPLE_DATA_DIR / "customers_duplicates.csv"
    with open(dup_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows([rows[0], rows[5]])  # CUST-001 repeated
    print(f"[OK] Generated {dup_path} (2 records with duplicate key CUST-001)")

def generate_sales_csv():
    sales_path = SAMPLE_DATA_DIR / "sales.csv"
    headers = ["sale_id", "customer_id", "amount", "sale_date", "currency"]
    rows = [
        # 1. Clean valid records
        ["SALE-1001", "CUST-001", "1250.50", "2024-02-01", "USD"],
        ["SALE-1002", "CUST-002", "89.99", "2024-02-05", "EUR"],
        ["SALE-1003", "CUST-003", "4500.00", "02/10/2024", "GBP"],
        ["SALE-1004", "CUST-004", " 320.75 ", "2024-02-15", " usd "],
        ["SALE-1005", "CUST-005", "15000", "2024-02-20", "JPY"],

        # 2. In-file duplicate key
        ["SALE-1001", "CUST-001", "999.00", "2024-02-22", "USD"],

        # 3. Negative sales amounts (Prohibited)
        ["SALE-1006", "CUST-002", "-150.00", "2024-02-23", "USD"],

        # 4. Non-numeric amount
        ["SALE-1007", "CUST-003", "one-thousand", "2024-02-24", "USD"],

        # 5. Unsupported currency code
        ["SALE-1008", "CUST-004", "500.00", "2024-02-25", "BITCOIN"],

        # 6. Missing required fields
        ["", "CUST-005", "100.00", "2024-02-26", "USD"],
        ["SALE-1009", "", "200.00", "2024-02-27", "USD"],

        # 7. Invalid date
        ["SALE-1010", "CUST-001", "300.00", "2024-13-45", "USD"],
    ]

    with open(sales_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"[OK] Generated {sales_path} ({len(rows)} records)")

    valid_sales_path = SAMPLE_DATA_DIR / "sales_valid.csv"
    with open(valid_sales_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows[:5])
    print(f"[OK] Generated {valid_sales_path} (5 clean valid records)")

    invalid_sales_path = SAMPLE_DATA_DIR / "sales_invalid.csv"
    with open(invalid_sales_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows[6:])
    print(f"[OK] Generated {invalid_sales_path} (6 invalid records)")

def generate_inventory_csv():
    inventory_path = SAMPLE_DATA_DIR / "inventory.csv"
    headers = ["product_id", "product_name", "quantity", "unit_price", "updated_at"]
    rows = [
        # 1. Clean valid records
        ["PROD-5001", "  ultrawide 34-inch monitor  ", "45", "499.99", "2024-03-01"],
        ["PROD-5002", "Ergonomic Mechanical Keyboard", "120", "89.50", "2024-03-02"],
        ["PROD-5003", "Precision Wireless Mouse", "250", "45.00", "03/05/2024"],
        ["PROD-5004", "USB-C Dual 4K Docking Station", "30", "159.00", "2024-03-10"],
        ["PROD-5005", "Noise Cancelling Headphones", "65", "199.95", "2024-03-12"],

        # 2. In-file duplicate key
        ["PROD-5001", "Duplicate Monitor", "10", "450.00", "2024-03-13"],

        # 3. Prohibited negative quantities and prices
        ["PROD-5006", "Defective Stock", "-5", "20.00", "2024-03-14"],
        ["PROD-5007", "Zero Worth Item", "10", "-50.00", "2024-03-15"],

        # 4. Non-integer quantity
        ["PROD-5008", "Fractional Box", "12.75", "15.00", "2024-03-16"],

        # 5. Missing required identifiers or names
        ["", "Nameless Product", "50", "10.00", "2024-03-17"],
        ["PROD-5009", "", "100", "5.00", "2024-03-18"],

        # 6. Invalid calendar date
        ["PROD-5010", "Time Traveler Hardware", "10", "100.00", "2024-99-99"],
    ]

    with open(inventory_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"[OK] Generated {inventory_path} ({len(rows)} records)")

    valid_inv_path = SAMPLE_DATA_DIR / "inventory_valid.csv"
    with open(valid_inv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows[:5])
    print(f"[OK] Generated {valid_inv_path} (5 clean valid records)")

    invalid_inv_path = SAMPLE_DATA_DIR / "inventory_invalid.csv"
    with open(invalid_inv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows[6:])
    print(f"[OK] Generated {invalid_inv_path} (6 invalid records)")

if __name__ == "__main__":
    generate_customers_csv()
    generate_sales_csv()
    generate_inventory_csv()
