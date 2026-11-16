"""Bounds for survey document parsing, including sparse worksheets."""

import io
import posixpath
import time
import zipfile

from defusedxml.ElementTree import fromstring, iterparse
from openpyxl.utils.cell import range_boundaries, coordinate_to_tuple

MAX_ROWS = 50_000
MAX_COLUMNS = 1_024
MAX_CELLS = 1_000_000
MAX_EXPANDED_BYTES = 48 * 1024 * 1024
MAX_MEMBER_BYTES = 16 * 1024 * 1024
MAX_EXCEL_SECONDS = 8


class SurveyUploadLimit(ValueError):
    pass


def check_excel_time(deadline):
    if time.monotonic() > deadline:
        raise SurveyUploadLimit("Worksheet processing exceeded the time limit.")


def check_openxml_size(archive):
    members = archive.infolist()
    if (len(members) > 2_000 or sum(item.file_size for item in members) > MAX_EXPANDED_BYTES
            or any(item.file_size > MAX_MEMBER_BYTES for item in members)):
        raise SurveyUploadLimit("Expanded document exceeds safe processing limits.")


def _worksheet_bounds(stream, deadline):
    largest_row = largest_column = actual_cells = 0
    row_number = column_number = 0
    for event, node in iterparse(stream, events=("start", "end")):
        check_excel_time(deadline)
        if event == "end":
            node.clear()
            continue
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "dimension" and node.get("ref"):
            _, _, column, row = range_boundaries(node.attrib["ref"])
            largest_row = max(largest_row, row or 0)
            largest_column = max(largest_column, column or 0)
        elif tag == "row":
            row_number = int(node.get("r", str(row_number + 1)))
            largest_row = max(largest_row, row_number)
            column_number = 0
        elif tag == "c":
            actual_cells += 1
            if "r" in node.attrib:
                row, column_number = coordinate_to_tuple(node.attrib["r"])
                largest_row = max(largest_row, row)
            else:
                column_number += 1
            largest_column = max(largest_column, column_number)
        if largest_row > MAX_ROWS or largest_column > MAX_COLUMNS:
            raise SurveyUploadLimit(f"Worksheet exceeds the limit of {MAX_ROWS:,} rows or {MAX_COLUMNS:,} columns.")
        if max(actual_cells, largest_row * largest_column) > MAX_CELLS:
            raise SurveyUploadLimit(f"Worksheet exceeds the {MAX_CELLS:,} cell processing limit, including sparse cells.")
    return max(actual_cells, largest_row * largest_column)


def validate_xlsx(content, deadline):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        check_openxml_size(archive)
        relationships = fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        worksheets = []
        for relation in relationships:
            if relation.get("Type", "").endswith("/worksheet"):
                if relation.get("TargetMode") == "External":
                    raise ValueError("External worksheets are unsupported.")
                target = relation.attrib["Target"]
                worksheets.append(target.lstrip("/") if target.startswith("/") else posixpath.normpath(f"xl/{target}"))
        if not worksheets or len(worksheets) > 32:
            raise SurveyUploadLimit("Upload a workbook with 1 to 32 internal worksheets.")
        total_cells = 0
        for path in worksheets:
            with archive.open(path) as stream:
                total_cells += _worksheet_bounds(stream, deadline)
            if total_cells > MAX_CELLS:
                raise SurveyUploadLimit(f"Workbook exceeds the {MAX_CELLS:,} cell processing limit.")
