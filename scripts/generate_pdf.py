"""
Generate a publication-grade PDF version of TECHNICAL_NOTES.md using markdown and Microsoft Edge headless.
Produces TECHNICAL_NOTES.pdf with:
- Full document content (all sections, tables, code blocks, credits)
- Clean, searchable, selectable vector text
- Elegant typography, table formatting, and page numbering
- Clickable hyperlinks
"""

import os
import re
import subprocess
import sys
from pathlib import Path
import markdown

BASE_DIR = Path(__file__).resolve().parent.parent
MD_PATH = BASE_DIR / "TECHNICAL_NOTES.md"
HTML_PATH = BASE_DIR / "TECHNICAL_NOTES.html"
PDF_PATH = BASE_DIR / "TECHNICAL_NOTES.pdf"

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

def find_edge_path():
    for p in EDGE_PATHS:
        if os.path.exists(p):
            return p
    return None

def markdown_to_html(md_text: str) -> str:
    # Convert KaTeX / LaTeX display math $$...$$ and inline $...$ to readable HTML blocks before markdown parsing
    def replace_display_math(match):
        formula = match.group(1).strip()
        # Clean common latex commands for clean print presentation
        clean = formula.replace(r"\text{", "").replace("}", "")
        clean = clean.replace(r"\dfrac{", "(").replace(r"\frac{", "(")
        clean = clean.replace(r"\times", "×")
        clean = clean.replace(r"\ge", "≥").replace(r"\le", "≤")
        return f'<div class="math-block"><code>{clean}</code></div>'

    # Display math $$ ... $$
    processed = re.sub(r"\$\$(.*?)\$\$", replace_display_math, md_text, flags=re.DOTALL)
    
    # Inline math $ ... $
    def replace_inline_math(match):
        formula = match.group(1).strip()
        clean = formula.replace(r"\text{", "").replace("}", "")
        clean = clean.replace(r"\ge", "≥").replace(r"\le", "≤")
        return f'<span class="math-inline"><code>{clean}</code></span>'

    processed = re.sub(r"(?<!\$)\$(?!\$)(.*?)\$", replace_inline_math, processed)

    # Convert alert callouts > [!NOTE] to styled callout divs
    def replace_alerts(text):
        pattern = r"> \[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*\n((?:> .*\n?)+)"
        def alert_replacer(m):
            alert_type = m.group(1).upper()
            content = "\n".join(line[2:] if line.startswith("> ") else (line[1:] if line.startswith(">") else line) for line in m.group(2).splitlines())
            return f'<div class="callout callout-{alert_type.lower()}"><div class="callout-title">{alert_type}</div><div class="callout-body">{content}</div></div>\n'
        return re.sub(pattern, alert_replacer, text)

    processed = replace_alerts(processed)

    html_body = markdown.markdown(
        processed,
        extensions=[
            "extra",
            "tables",
            "fenced_code",
            "toc",
            "nl2br",
        ],
    )
    return html_body

CSS_STYLES = """
@page {
    size: A4;
    margin: 20mm 18mm 22mm 18mm;
    @bottom-right {
        content: "Page " counter(page) " of " counter(pages);
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 8.5pt;
        color: #718096;
    }
    @bottom-left {
        content: "EDVEXA Data Quality & ETL Monitor — Technical Notes";
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        font-size: 8.5pt;
        color: #718096;
    }
}

body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.55;
    color: #1a202c;
    background-color: #ffffff;
    margin: 0;
    padding: 0;
}

h1 {
    font-size: 20pt;
    font-weight: 700;
    color: #0f172a;
    border-bottom: 2pt solid #0f172a;
    padding-bottom: 6pt;
    margin-top: 0;
    margin-bottom: 12pt;
    page-break-after: avoid;
}

h2 {
    font-size: 14pt;
    font-weight: 700;
    color: #1e293b;
    border-bottom: 1pt solid #cbd5e1;
    padding-bottom: 4pt;
    margin-top: 18pt;
    margin-bottom: 8pt;
    page-break-after: avoid;
}

h3 {
    font-size: 11.5pt;
    font-weight: 600;
    color: #334155;
    margin-top: 14pt;
    margin-bottom: 6pt;
    page-break-after: avoid;
}

h4 {
    font-size: 10.5pt;
    font-weight: 600;
    color: #475569;
    margin-top: 10pt;
    margin-bottom: 4pt;
    page-break-after: avoid;
}

p {
    margin-top: 0;
    margin-bottom: 8pt;
    text-align: justify;
}

a {
    color: #2563eb;
    text-decoration: none;
}

a:hover {
    text-decoration: underline;
}

strong {
    color: #0f172a;
    font-weight: 600;
}

hr {
    border: none;
    border-top: 1pt solid #e2e8f0;
    margin: 14pt 0;
}

ul, ol {
    margin-top: 0;
    margin-bottom: 8pt;
    padding-left: 20pt;
}

li {
    margin-bottom: 3pt;
}

blockquote {
    margin: 8pt 0;
    padding: 6pt 12pt;
    background-color: #f8fafc;
    border-left: 3pt solid #64748b;
    color: #334155;
    font-style: italic;
}

code {
    font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
    font-size: 8.8pt;
    background-color: #f1f5f9;
    color: #0f172a;
    padding: 1.5pt 3pt;
    border-radius: 3pt;
    border: 0.5pt solid #e2e8f0;
}

pre {
    font-family: Consolas, "Liberation Mono", Menlo, Courier, monospace;
    font-size: 8.2pt;
    line-height: 1.4;
    background-color: #f8fafc;
    border: 1pt solid #e2e8f0;
    border-radius: 4pt;
    padding: 8pt 10pt;
    margin: 8pt 0;
    overflow-x: auto;
    page-break-inside: avoid;
    white-space: pre-wrap;
    word-wrap: break-word;
}

pre code {
    background-color: transparent;
    padding: 0;
    border: none;
    font-size: inherit;
    color: #1e293b;
}

table {
    width: 100%;
    border-collapse: collapse;
    margin: 10pt 0;
    font-size: 8.8pt;
    page-break-inside: avoid;
}

th, td {
    padding: 5pt 7pt;
    border: 0.5pt solid #cbd5e1;
    text-align: left;
    vertical-align: top;
}

th {
    background-color: #f1f5f9;
    font-weight: 600;
    color: #0f172a;
    border-bottom: 1.5pt solid #94a3b8;
}

tr:nth-child(even) td {
    background-color: #f8fafc;
}

.math-block {
    margin: 8pt 0;
    padding: 6pt 10pt;
    background-color: #f8fafc;
    border-left: 3pt solid #3b82f6;
    border-radius: 2pt;
    font-family: Consolas, monospace;
    font-size: 9pt;
    color: #1e3a8a;
    page-break-inside: avoid;
}

.math-inline code {
    background-color: #eff6ff;
    color: #1d4ed8;
    border-color: #bfdbfe;
}

.callout {
    margin: 10pt 0;
    padding: 8pt 12pt;
    border-radius: 4pt;
    border-left: 4pt solid #3b82f6;
    background-color: #eff6ff;
    page-break-inside: avoid;
}

.callout-title {
    font-weight: 700;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.5pt;
    margin-bottom: 4pt;
    color: #1d4ed8;
}

.callout-body {
    font-size: 9.5pt;
    color: #1e293b;
}

.doc-header-table {
    width: 100%;
    margin-bottom: 14pt;
    border: none;
    font-size: 9.5pt;
}

.doc-header-table td {
    border: none;
    padding: 2pt 4pt;
    background: transparent !important;
}

.badge {
    display: inline-block;
    padding: 1pt 5pt;
    font-size: 8pt;
    font-weight: 600;
    border-radius: 3pt;
    background-color: #e2e8f0;
    color: #334155;
}
"""

def generate_pdf():
    print(f"Reading {MD_PATH}...")
    with open(MD_PATH, "r", encoding="utf-8") as f:
        md_text = f.read()

    print("Converting Markdown to HTML...")
    html_content = markdown_to_html(md_text)

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <title>EDVEXA Data Quality & ETL Monitor — Technical Notes</title>
    <style>
{CSS_STYLES}
    </style>
</head>
<body>
{html_content}
</body>
</html>
"""

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"[OK] Generated HTML: {HTML_PATH} ({len(full_html)} bytes)")

    edge_bin = find_edge_path()
    if not edge_bin:
        print("[ERROR] Microsoft Edge executable not found.")
        sys.exit(1)

    print(f"Invoking Microsoft Edge headless: {edge_bin}...")
    cmd = [
        edge_bin,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={PDF_PATH}",
        str(HTML_PATH),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Edge returned exit code {result.returncode}: {result.stderr}")
        sys.exit(result.returncode)

    if not PDF_PATH.exists():
        print("[ERROR] PDF file was not created.")
        sys.exit(1)

    file_size = PDF_PATH.stat().st_size
    print(f"[SUCCESS] Generated PDF: {PDF_PATH} ({file_size:,} bytes)")

    # Clean up intermediate HTML
    if HTML_PATH.exists():
        HTML_PATH.unlink()

    return PDF_PATH, file_size

if __name__ == "__main__":
    generate_pdf()
