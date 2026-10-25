import re
from pathlib import Path

pdf_path = Path("TECHNICAL_NOTES.pdf")
assert pdf_path.exists(), "PDF does not exist"
size = pdf_path.stat().st_size
print(f"File size: {size:,} bytes")

with open(pdf_path, "rb") as f:
    content = f.read()

# In PDF, pages are marked by /Type /Page
pages = re.findall(rb"/Type\s*/Page\b", content)
print(f"Detected page count: {len(pages)}")

# Try installing pypdf to inspect extracted text cleanly
try:
    import pypdf
except ImportError:
    import subprocess, sys
    subprocess.run([sys.executable, "-m", "pip", "install", "pypdf"], capture_output=True)
    import pypdf

reader = pypdf.PdfReader(str(pdf_path))
num_pages = len(reader.pages)
print(f"pypdf confirmed page count: {num_pages}")

full_text = ""
for idx, page in enumerate(reader.pages):
    full_text += f"\n--- Page {idx+1} ---\n" + page.extract_text()

checks = [
    "Raghav Kumar",
    "Software Engineer",
    "Architecture Decisions",
    "ETL Workflow",
    "Idempotency",
    "Antigravity",
    "CodeZero",
    "Credits & Tools Used",
]

print("\n--- Section Presence Verification ---")
for c in checks:
    present = c.lower() in full_text.lower()
    print(f"[{'PASS' if present else 'FAIL'}] '{c}' found in extracted PDF text")

# Check that the old title is NOT present
old_title = "Senior Full-Stack Python Engineer & Data Platform Architect"
old_present = old_title.lower() in full_text.lower()
print(f"[{'PASS' if not old_present else 'FAIL'}] Inaccurate seniority title absent: {not old_present}")

# Print first 400 chars and last 600 chars of extracted text
print("\n--- PDF Start Preview ---")
print(full_text[:400])
print("\n--- PDF End Preview ---")
print(full_text[-600:])
