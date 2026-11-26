from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterator
from zipfile import ZipFile


FORMATS = frozenset({".pdf", ".docx", ".txt", ".md"})
MAX_CHARACTERS = 3_000_000
MAX_PAGES = 1_200
CHUNK_SIZE = 660
CHUNK_OVERLAP = 90
DOCX_LOCATOR_REVISION = 2
NUMBERED_SECTION = re.compile(r"^([1-9]\d{0,2})\.[ \t\u00a0]+[^\n]{3,157}$")
NUMBERED_CLAUSE = re.compile(r"^([1-9]\d*(?:\.\d+)+)\.?(?=\s|$)")


class ImportFailure(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Block:
    text: str
    locator: dict


def pdf_blocks(path: Path, warnings: list) -> Iterator[Block]:
    import pymupdf

    with pymupdf.open(path) as document:
        if document.needs_pass:
            raise ImportFailure("encrypted_pdf")
        if len(document) > MAX_PAGES:
            raise ImportFailure("too_many_pages")
        for number, page in enumerate(document, 1):
            found_text = False
            for x0, y0, x1, y1, text, _ordinal, block_type in page.get_text("blocks", sort=True):
                if block_type == 0 and text.strip():
                    found_text = True
                    yield Block(text.strip(), {
                        "type": "pdf",
                        "page_number": number,
                        "bbox": [round(value, 2) for value in (x0, y0, x1, y1)],
                    })
            if not found_text:
                warnings.append({"code": "page_has_no_text", "page": number})


def docx_blocks(path: Path) -> Iterator[Block]:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    with ZipFile(path) as archive:
        files = archive.infolist()
        if len(files) > 2_000 or sum(member.file_size for member in files) > 70 * 1024 * 1024:
            raise ImportFailure("docx_uncompressed_size_limit")
    document = Document(path)
    numbered_sections = set()
    pending_section = None
    for index, paragraph in enumerate(document.paragraphs, 1):
        text = paragraph.text.strip()
        if heading := NUMBERED_SECTION.fullmatch(text):
            pending_section = (index, heading[1])
        elif clause := NUMBERED_CLAUSE.match(text):
            if pending_section and clause[1].split(".", 1)[0] == pending_section[1]:
                numbered_sections.add(pending_section[0])
            pending_section = None

    headings: list[tuple[int, str]] = []
    clause_number = None
    paragraph_index = 0
    table_index = 0

    def context() -> dict:
        locator = {"section_path": [text for _, text in headings]}
        if clause_number:
            locator["clause_number"] = clause_number
        return locator

    def table_blocks(table: Table, table_path: list[dict]) -> Iterator[Block]:
        seen_cells = set()
        column_headers = {}
        for row_index, row in enumerate(table.rows, 1):
            header_row = bool(row._tr.xpath("./w:trPr/w:tblHeader"))
            for column_index, cell in enumerate(row.cells, row.grid_cols_before + 1):
                if header_row and cell.text.strip():
                    column_headers[column_index] = " ".join(cell.text.split())[:160]
                if cell._tc in seen_cells:
                    continue
                seen_cells.add(cell._tc)
                cell_path = table_path + [{"row_index": row_index, "column_index": column_index}]
                cell_paragraph = 0
                nested_table = 0
                for item in cell.iter_inner_content():
                    if isinstance(item, Paragraph):
                        cell_paragraph += 1
                        if item.text.strip():
                            locator = {
                                "type": "docx_table_cell", **context(),
                                "table_path": cell_path,
                                "paragraph_index": cell_paragraph,
                            }
                            if not header_row and column_index in column_headers:
                                locator["column_header"] = column_headers[column_index]
                            yield Block(item.text.strip(), locator)
                    else:
                        nested_table += 1
                        yield from table_blocks(item, cell_path + [{"table_index": nested_table}])

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            paragraph_index += 1
            text = item.text.strip()
            if not text:
                continue
            style = item.style.style_id if item.style is not None else ""
            match = re.fullmatch(r"(?:Heading|Заголовок)([1-9])", style, re.IGNORECASE)
            if match or paragraph_index in numbered_sections:
                level = int(match[1]) if match else 1
                headings = [(old_level, value) for old_level, value in headings if old_level < level]
                headings.append((level, text[:160]))
                clause_number = None
            elif clause := NUMBERED_CLAUSE.match(text):
                clause_number = clause[1]
            yield Block(text, {
                "type": "docx_paragraph", **context(),
                "paragraph_index": paragraph_index,
            })
        elif isinstance(item, Table):
            table_index += 1
            yield from table_blocks(item, [{"table_index": table_index}])


def text_blocks(path: Path) -> Iterator[Block]:
    section: list[str] = []
    pending: list[str] = []
    start = 1
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines() + [""], 1):
        if line.strip():
            if not pending:
                start = number
            pending.append(line)
            continue
        if not pending:
            continue
        if path.suffix.lower() == ".md" and pending[0].startswith("# "):
            section = [pending[0].removeprefix("# ").strip()[:160]]
        yield Block("\n".join(pending).strip(), {
            "type": "text", "line_start": start, "line_end": number - 1, "section_path": section,
        })
        pending = []


def parse_file(path: Path) -> tuple[list[Block], list]:
    warnings: list = []
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        parsed = pdf_blocks(path, warnings)
    elif suffix == ".docx":
        parsed = docx_blocks(path)
    elif suffix in FORMATS:
        parsed = text_blocks(path)
    else:
        raise ImportFailure("unsupported_format")
    output: list[Block] = []
    characters = 0
    try:
        for block in parsed:
            characters += len(block.text)
            if characters > MAX_CHARACTERS or len(output) >= 30_000:
                raise ImportFailure("extracted_text_size_limit")
            output.append(block)
    except ImportFailure:
        raise
    except ImportError:
        raise ImportFailure("install_importers") from None
    except Exception:
        raise ImportFailure("cannot_parse_file") from None
    if not output:
        raise ImportFailure("needs_ocr" if suffix == ".pdf" and warnings else "no_text")
    return output, warnings


def chunk_offsets(text: str) -> Iterator[tuple[int, int]]:
    start = 0
    while start < len(text):
        end = min(len(text), start + CHUNK_SIZE)
        if end < len(text):
            boundary = max(text.rfind(" ", start + CHUNK_SIZE // 2, end), text.rfind("\n", start + CHUNK_SIZE // 2, end))
            if boundary != -1:
                end = boundary
        yield start, end
        if end == len(text):
            break
        start = end - CHUNK_OVERLAP
        while start < end and not text[start - 1].isspace():
            start += 1
        while start < end and text[start].isspace():
            start += 1
