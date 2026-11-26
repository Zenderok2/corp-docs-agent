from docx import Document
import pymupdf

from corp_docs_agent.ingestion.files import chunk_offsets, parse_file
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
