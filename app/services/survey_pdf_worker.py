"""Standalone PDF text extraction worker. Input and output use pipes."""

import io
import sys

from pypdf import PdfReader, filters
from pypdf.errors import LimitReachedError

MAX_STREAM_BYTES = 4 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_PAGES = 100
MAX_TEXT_CHARS = 1_000_000


def extract(content):
    for name in ("ZLIB_MAX_OUTPUT_LENGTH", "LZW_MAX_OUTPUT_LENGTH", "RUN_LENGTH_MAX_OUTPUT_LENGTH",
                 "JBIG2_MAX_OUTPUT_LENGTH", "MAX_DECLARED_STREAM_LENGTH", "MAX_ARRAY_BASED_STREAM_OUTPUT_LENGTH"):
        setattr(filters, name, MAX_STREAM_BYTES)
    reader = PdfReader(io.BytesIO(content))
    if len(reader.pages) > MAX_PAGES:
        raise LimitReachedError("Page limit")
    parts = []
    total_bytes = total_chars = 0
    for page in reader.pages:
        stream = page.get_contents()
        total_bytes += len(stream.get_data()) if stream is not None else 0
        if total_bytes > MAX_TOTAL_BYTES:
            raise LimitReachedError("Expanded page data limit")
        text = page.extract_text() or ""
        total_chars += len(text)
        if total_chars > MAX_TEXT_CHARS:
            raise LimitReachedError("Extracted text limit")
        parts.append(text)
    return "\n\n".join(parts).strip()


def main():
    try:
        text = extract(sys.stdin.buffer.read())
    except LimitReachedError:
        return 2
    except Exception:
        return 1
    sys.stdout.buffer.write(text.encode("utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
