import json

from docx import Document
from docx.oxml import OxmlElement
import pymupdf

from corp_docs_agent.core.settings import Settings
from corp_docs_agent.ingestion.catalog import import_catalog
from corp_docs_agent.ingestion.files import chunk_offsets, parse_file
from corp_docs_agent.persistence.store import Store
from corp_docs_agent.retrieval.search import amount_applies, topic_supported


def test_pdf_page_and_bounding_box_survive_parsing(tmp_path):
    path = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page()
        page = pdf.new_page()
        page.insert_text((72, 72), "Approved source paragraph.")
        pdf.save(path)
    blocks, warnings = parse_file(path)
    assert warnings == [{"code": "page_has_no_text", "page": 1}]
    assert blocks[0].locator["page_number"] == 2
    assert len(blocks[0].locator["bbox"]) == 4
    assert blocks[0].text == "Approved source paragraph."


def test_docx_paragraphs_and_table_cells_have_separate_locators(tmp_path):
    path = tmp_path / "source.docx"
    document = Document()
    document.add_heading("Правила", 1)
    document.add_paragraph("Первый пункт")
    table = document.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Строка"
    table.cell(0, 1).text = "Право подписи"
    document.add_paragraph("Последний абзац")
    document.save(path)
    blocks, warnings = parse_file(path)
    assert not warnings and len(blocks) == 5
    assert blocks[3].locator == {
        "type": "docx_table_cell", "section_path": ["Правила"],
        "table_path": [{"table_index": 1}, {"row_index": 1, "column_index": 2}], "paragraph_index": 1,
    }
    assert blocks[4].locator["paragraph_index"] == 3


def test_docx_numbered_sections_and_marked_columns_upgrade_existing_locations(tmp_path):
    path = tmp_path / "source.docx"
    document = Document()
    document.add_paragraph("1. Состав заявки")
    document.add_paragraph("1.1. Приложите документы к карточке.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    for row, contents in zip(table.rows, [("Обязательно", "При наличии"), ("План проекта", "Пояснение")], strict=True):
        for cell, content in zip(row.cells, contents, strict=True):
            cell.text = content
    document.add_paragraph("2. Проверка заявки")
    document.add_paragraph("2.1. Координатор проверяет карточку.")
    document.save(path)
    blocks, warnings = parse_file(path)
    assert not warnings
    assert blocks[4].text == "План проекта" and blocks[4].locator == {
        "type": "docx_table_cell", "section_path": ["1. Состав заявки"], "clause_number": "1.1",
        "table_path": [{"table_index": 1}, {"row_index": 2, "column_index": 1}],
        "paragraph_index": 1, "column_header": "Обязательно",
    }
    assert blocks[5].locator["column_header"] == "При наличии"
    assert "column_header" not in blocks[2].locator
    assert blocks[-1].locator["section_path"] == ["2. Проверка заявки"]
    assert blocks[-1].locator["paragraph_index"] == 4 and blocks[-1].locator["clause_number"] == "2.1"

    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"sources": [{
        "key": "sample", "version": "current", "path": path.name,
        "title": "Правила", "kind": "regulations", "status": "published", "groups": ["demo"],
    }]}), encoding="utf-8")
    settings = Settings(tmp_path / "runtime", tmp_path / "runtime" / "app.sqlite3")
    store = Store(settings.database)
    assert import_catalog(settings, catalog)[0]["status"] == "imported"
    with store.connect(write=True) as conn:
        conn.execute("UPDATE document_versions SET metadata = json_remove(metadata, '$.locator_revision')")
        conn.execute("UPDATE blocks SET locator = json_remove(locator, '$.column_header', '$.clause_number')")
        conn.execute("UPDATE chunks_fts SET heading = ''")
        chunk_ids = [row["id"] for row in conn.execute("SELECT id FROM chunks ORDER BY id")]
    assert import_catalog(settings, catalog)[0]["status"] == "updated"
    assert import_catalog(settings, catalog)[0]["status"] == "unchanged"
    with store.connect() as conn:
        assert [row["id"] for row in conn.execute("SELECT id FROM chunks ORDER BY id")] == chunk_ids
        assert json.loads(conn.execute("SELECT locator FROM blocks WHERE ordinal = 5").fetchone()[0]) == blocks[4].locator
        assert conn.execute("SELECT count(*) FROM chunks_fts WHERE heading MATCH 'обязательн'").fetchone()[0] == 1


def test_chunk_offsets_cover_the_original_text():
    long_text = "основание " * 100
    offsets = list(chunk_offsets(long_text))
    assert offsets[0][0] == 0 and offsets[-1][-1] == len(long_text)
    assert all(next_start <= end for (_, end), (next_start, _) in zip(offsets, offsets[1:]))


def test_topic_and_contract_limit_do_not_treat_partial_word_matches_as_an_answer():
    assert not topic_supported(
        "Как оформить гостевой пропуск в офис на выходные?",
        ["Для выдачи гостевой лицензии владелец договора оформляет заявку в службу поддержки."],
    )
    question = "Кто подписывает договор на 400 000 рублей?"
    assert not amount_applies(question, "Договор на сумму свыше 500 000 рублей подписывает директор.")
    assert amount_applies(question, "Договор на сумму до 500 000 рублей подписывает заместитель.")
