from dataclasses import replace
from datetime import date
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from corp_docs_agent.api.main import create_app
from corp_docs_agent.core.settings import Settings
from corp_docs_agent.ingestion.catalog import import_catalog
from corp_docs_agent.persistence.store import Store, visible_versions
from corp_docs_agent.providers.llama import Coverage, Decision, Finding, Findings, TableCoverage, Verification
from corp_docs_agent.retrieval.search import eligible_versions, lexical_ids
from corp_docs_agent.retrieval.terms import query_terms


SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples" / "catalog.json"
CONTRACT_QUESTION = "Кто подписывает агентский договор на 400 000 рублей и что проверяет инициатор?"
GAP_QUESTION = "Как оформить гостевой пропуск в офис на выходные?"
LICENSE_QUESTION = "Какую лицензию выдают внешнему подрядчику и на какой срок?"


class ScriptedChat:
    def ready(self):
        return True

    def decide(self, data):
        selected = []
        for source in data["sources"]:
            if source["text"].startswith("4.2."):
                selected.append(source["ref"])
            if data["context"]["legal_entity"] == "Маяк Север" and "500 000 рублей включительно" in source["text"]:
                selected.append(source["ref"])
        outcome = "answered" if selected and "агентский договор" in data["question"] else "not_found"
        return Decision(outcome=outcome, evidence=selected if outcome == "answered" else [], clarification=""), {"chat_calls": 1}


@pytest.fixture
def settings(tmp_path):
    settings = Settings(tmp_path, tmp_path / "app.sqlite3", staff_code="staff-test", quality_code="quality-test")
    report = import_catalog(settings, SAMPLES)
    assert len(report) == 6 and all(item["status"] == "imported" for item in report)
    return settings


def wait_question(client, id):
    deadline = monotonic() + 5
    while monotonic() < deadline:
        response = client.get(f"/api/v1/questions/{id}")
        assert response.status_code == 200, response.json()
        found = response.json()
        if found["status"] not in {"running", "queued"}:
            return found
        sleep(.02)
    pytest.fail("The question queue did not complete the test job")


def test_department_variants_complex_projects_and_glossary(settings, tmp_path):
    restricted = tmp_path / "private.md"
    restricted.write_text("РП согласует план проекта за пять рабочих дней.", encoding="utf-8")
    manifest = tmp_path / "project-catalog.json"
    manifest.write_text(json.dumps({"sources": [{
        "path": restricted.name, "key": "private", "version": "test", "title": "Другой центр",
        "kind": "regulations", "status": "published", "scope": {"department": "Другой центр"}, "groups": ["staff"],
    }]}), encoding="utf-8")
    assert import_catalog(settings, manifest)[0]["status"] == "imported"

    class ProjectChat(ScriptedChat):
        def decide(self, data, *, timeout=None):
            selected = [source["ref"] for source in data["sources"] if "согласует план проекта" in source["text"]]
            assert isinstance(timeout, float) and data["focus_department"] == data["context"]["department"]
            assert len({source["scope"]["department"] for source in data["sources"] if source["scope"]}) == 1
            return Decision(outcome="answered", evidence=selected, clarification=""), {"chat_calls": 1}

    question = "В какой срок проджект-менеджер согласует план проекта?"
    with TestClient(create_app(settings, ProjectChat())) as client:
        client.get("/api/v1/session")
        first = client.post("/api/v1/questions", json={"question": question}).json()["id"]
        result = wait_question(client, first)["result"]
        assert result["outcome"] == "needs_clarification"
        names = ["Дирекция информационной безопасности", "Инфраструктура"]
        assert result["clarification"]["options"] == names
        assert result["usage"]["chat_calls"] == 0
        assert not result["quality_case"]
        assert [group["name"] for group in result["departments"]] == names
        by_id = {citation["id"]: citation["quote"] for citation in result["citations"]}
        for group, expected in zip(result["departments"], ["две недели", "три рабочих дня"], strict=True):
            assert expected in by_id[group["citation_ids"][0]]

        both = client.post(f"/api/v1/questions/{first}/follow-ups", json={
            "answer": "Комплексный проект", "context": {"departments": ["ДИБ", "Инфраструктура"]},
        }).json()["id"]
        result = wait_question(client, both)["result"]
        assert result["outcome"] == "answered"
        assert len(result["departments"]) == 2
        assert len({citation_id for group in result["departments"] for citation_id in group["citation_ids"]}) == 2
        assert len(result["findings"]) == 2

        unsupported = client.post("/api/v1/questions", json={
            "question": question, "context": {"departments": ["Инфраструктура", "Отдел снабжения"]},
        }).json()["id"]
        limited = wait_question(client, unsupported)["result"]
        assert limited["outcome"] == "partial"
        assert {item["name"]: len(item["citation_ids"]) for item in limited["departments"]} == {
            "Инфраструктура": 1, "Отдел снабжения": 0,
        }

        unknown = first
        for outcome in ["needs_clarification", "partial"]:
            unknown = client.post(f"/api/v1/questions/{unknown}/follow-ups", json={
                "answer": "Подразделение неизвестно",
            }).json()["id"]
            unresolved = wait_question(client, unknown)["result"]
            assert unresolved["outcome"] == outcome
        assert unresolved["message_code"] == "clarification_exhausted"
        assert unresolved["quality_case"]["status"] == "pending"


@pytest.mark.parametrize(
    ("engineering_timing", "has_comparison"),
    [
        ("за 10 календарных дней до согласования сметы", True),
        ("за 10 календарных дней", False),
        ("за 10 календарных дней до согласования сметы и за 12 календарных дней до начала работ", False),
    ],
    ids=["comparison", "missing_anchor", "multiple_deadlines"],
)
def test_complex_project_compares_deadlines_from_confirmed_sources(settings, tmp_path, engineering_timing, has_comparison):
    implementation = "Дирекция внедрения"
    engineering = "Проектное бюро"
    condition = "При нарушении срока её возвращают на согласование."
    rules = {
        implementation: f"1.2. Заявка направляется не менее чем за 4 рабочих дня до начала работ. {condition}",
        engineering: f"Расчёт направляется {engineering_timing}.",
    }
    sources = []
    for index, (department, rule) in enumerate(rules.items(), 1):
        path = tmp_path / f"deadline-{index}.md"
        path.write_text(rule, encoding="utf-8")
        sources.append({
            "path": path.name, "key": path.stem, "version": "test", "title": f"Сроки — {department}",
            "kind": "regulations", "status": "published", "groups": ["demo"], "scope": {"department": department},
        })
    manifest = tmp_path / "deadline-catalog.json"
    manifest.write_text(json.dumps({"sources": sources}), encoding="utf-8")
    assert all(item["status"] == "imported" for item in import_catalog(settings, manifest))

    class DeadlineChat(ScriptedChat):
        def decide(self, data, *, timeout=None):
            rule = rules[data["focus_department"]]
            ref = next(source["ref"] for source in data["sources"] if source["text"] == rule)
            return Decision(outcome="answered", evidence=[ref], clarification=""), {}

    with TestClient(create_app(settings, DeadlineChat())) as client:
        client.get("/api/v1/session")
        question_id = client.post("/api/v1/questions", json={
            "question": "Когда направляется заявка или расчёт?",
            "context": {"departments": [implementation, engineering]},
        }).json()["id"]
        result = wait_question(client, question_id)["result"]
        assert result["outcome"] == "answered"
        assert [citation["quote"] for citation in result["citations"]] == list(rules.values())
        if not has_comparison:
            assert result["department_comparison"] == []
            return
        assert result["department_comparison"] == [
            {
                "department": implementation, "action": "Заявка направляется",
                "deadline": "не менее чем за 4 рабочих дня", "anchor": "до начала работ", "condition": condition,
                "citation_id": result["departments"][0]["citation_ids"][0],
            },
            {
                "department": engineering, "action": "Расчёт направляется",
                "deadline": "за 10 календарных дней", "anchor": "до согласования сметы", "condition": None,
                "citation_id": result["departments"][1]["citation_ids"][0],
            },
        ]
        for row in result["department_comparison"]:
            citation = client.get(f"/api/v1/citations/{row['citation_id']}")
            assert citation.status_code == 200
            assert citation.json()["quote"] == rules[row["department"]]


def test_explicit_clause_retrieval_uses_each_allowed_department(settings, tmp_path):
    from docx import Document

    rules = {
        "Дирекция внедрения": "8.4. Работы до договора согласует руководитель проекта.",
        "Проектное бюро": "8.4. Работы до договора согласует технический директор.",
    }
    entries = []
    for index, (department, rule) in enumerate([*rules.items(), ("Проектное бюро", "8.4. Закрытая редакция.")]):
        path = tmp_path / f"clause-{index}.docx"
        document = Document()
        for number in range(1, 7):
            document.add_paragraph(f"2.{number}. Комитет согласует начало работ до подписания договора. См. пункт 8.4.")
        document.add_paragraph(rule)
        document.save(path)
        entries.append({
            "path": path.name, "key": path.stem, "version": "test", "title": path.stem,
            "kind": "regulations", "status": "published", "scope": {"department": department},
            "groups": ["staff" if index == len(rules) else "demo"],
        })
    manifest = tmp_path / "clauses.json"
    manifest.write_text(json.dumps({"sources": entries}), encoding="utf-8")
    assert all(item["status"] == "imported" for item in import_catalog(settings, manifest))

    class ClauseChat(ScriptedChat):
        def decide(self, data, *, timeout=None):
            first = data["sources"][0]
            assert first["clause_number"] == "8.4"
            assert first["text"] == rules[data["focus_department"]]
            assert all(source["title"] != "clause-2" for source in data["sources"])
            return Decision(outcome="answered", evidence=[first["ref"]], clarification=""), {}

    with TestClient(create_app(settings, ClauseChat())) as client:
        client.get("/api/v1/session")
        question_id = client.post("/api/v1/questions", json={
            "question": "В длинном обсуждении сложного комплексного проекта возник вопрос. "
                        "Кто согласует начало работ до подписания договора по одному и тому же пункту 8.4?",
            "context": {"departments": list(rules)},
        }).json()["id"]
        result = wait_question(client, question_id)["result"]
        assert result["outcome"] == "answered"
        assert [citation["quote"] for citation in result["citations"]] == list(rules.values())


@pytest.mark.parametrize(
    ("question", "selected_paragraphs", "expected_count"),
    [
        ("Сколько дней закладывать на точную и бюджетную оценку?", (0, 1, 2), 3),
        ("Какой срок рекомендован для точной оценки?", (1,), 2),
    ],
    ids=["complete_clause", "selected_list_item"],
)
def test_numbered_docx_keeps_the_intro_and_selected_deadlines(
    settings, tmp_path, question, selected_paragraphs, expected_count,
):
    from docx import Document

    rule = [
        "1.1. Приведённые ниже сроки рекомендованы:",
        "Для точной оценки — 5 рабочих дней;",
        "Для бюджетной оценки — 3 рабочих дня.",
    ]
    document = Document()
    document.add_paragraph("1. Обработка заявок")
    for paragraph in rule:
        document.add_paragraph(paragraph)
    document.add_paragraph("1.2. Точную оценку передают менеджеру после проверки.")
    document.add_paragraph("1.3. Дни работы по точной оценке отражают в журнале.")
    document.add_paragraph("1.4. Бюджетную оценку прикладывают к заявке.")
    path = tmp_path / "estimates.docx"
    document.save(path)
    manifest = tmp_path / "estimate-catalog.json"
    manifest.write_text(json.dumps({"sources": [{
        "path": path.name, "key": "estimates", "version": "test", "title": "Порядок работы",
        "kind": "regulations", "status": "published", "groups": ["demo"],
    }]}), encoding="utf-8")
    assert import_catalog(settings, manifest)[0]["status"] == "imported"

    class EstimateChat(ScriptedChat):
        def decide(self, data):
            rule_sources = {
                source["text"]: source for source in data["sources"] if source.get("clause_number") == "1.1"
            }
            for index in selected_paragraphs:
                if index:
                    assert rule_sources[rule[index]]["clause_intro"] == rule[0]
            return Decision(
                outcome="answered", evidence=[rule_sources[rule[index]]["ref"] for index in selected_paragraphs],
                clarification="",
            ), {}

    with TestClient(create_app(settings, EstimateChat())) as client:
        client.get("/api/v1/session")
        question_id = client.post("/api/v1/questions", json={
            "question": question,
            "document_ids": ["estimates"],
        }).json()["id"]
        result = wait_question(client, question_id)["result"]
        assert result["outcome"] == "answered"
        assert [citation["quote"] for citation in result["citations"]] == rule[:expected_count]
        assert all(citation["locator"]["clause_number"] == "1.1" for citation in result["citations"])


@pytest.mark.parametrize(("statement", "approve", "checked"), [
    ("Внешнему подрядчику выдают гостевую лицензию на срок договора и не более 90 календарных дней.", True, True),
    ("Подрядчику выдают гостевую лицензию на 120 календарных дней.", True, False),
    ("Подрядчику выдают гостевую лицензию на 90 рабочих дней.", True, False),
    ("Гостевая лицензия подрядчика действует не более 90 календарных дней.", True, False),
    ("Подрядчику выдают гостевую лицензию на срок договора.", True, False),
    ("Штатному сотруднику выдают гостевую лицензию на срок договора и не более 90 календарных дней.", False, True),
])
def test_findings_require_grounding_and_independent_verification(settings, tmp_path, statement, approve, checked):
    document = tmp_path / "license.md"
    document.write_text(
        "3.2. Внешнему подрядчику выдают гостевую лицензию «Орбита Про» на срок договора, но на срок "
        "не более 90 календарных дней. Владелец договора отправляет заявку на выдачу гостевой лицензии "
        "администратору системы и указывает дату окончания работ подрядчика.", encoding="utf-8",
    )
    manifest = tmp_path / "license-catalog.json"
    manifest.write_text(json.dumps({"sources": [{
        "path": document.name, "key": "long-test-license", "version": "test", "title": "Тестовая лицензия",
        "kind": "product_licensing", "status": "published", "publication_confirmed_on": "2026-11-26", "groups": ["demo"],
    }]}), encoding="utf-8")
    assert import_catalog(settings, manifest)[0]["status"] == "imported"

    class LicenseChat(ScriptedChat):
        def decide(self, data):
            ref = next(item["ref"] for item in data["sources"] if item["text"].startswith("3.2."))
            return Decision(outcome="answered", evidence=[ref], clarification=""), {"chat_calls": 1, "prompt_tokens": 40}

        def summarize(self, question, citations, timeout):
            assert question == LICENSE_QUESTION and len(citations) == 1
            return Findings(findings=[Finding(text=statement, refs=[1])]), {"prompt_tokens": 10}

        def verify(self, findings, timeout):
            assert len(findings) == 1 and findings[0]["text"] == statement
            assert "срок договора" in findings[0]["citations"][0]["quote"]
            return Verification(supported=[1] if approve else []), {"prompt_tokens": 20}

    with TestClient(create_app(settings, LicenseChat())) as client:
        client.get("/api/v1/session")
        request = {"question": LICENSE_QUESTION, "document_ids": ["long-test-license"], "as_of": "2026-11-26"}
        id = client.post("/api/v1/questions", json=request).json()["id"]
        result = wait_question(client, id)["result"]
        citation, = result["citations"]
        assert "на срок договора" in citation["quote"] and "не более 90 календарных дней" in citation["quote"]
        assert result["usage"]["chat_calls"] == (3 if checked else 2)
        assert result["usage"]["prompt_tokens"] == (70 if checked else 50)
        assert result["findings"] == ([{"text": statement, "citation_ids": [citation["id"]]}] if approve and checked else [])
        assert citation["effective_from"] is None and citation["publication_confirmed_on"] == "2026-11-26"
        if approve and checked:
            previous = client.post("/api/v1/questions", json={**request, "as_of": "2026-11-25"}).json()["id"]
            earlier = wait_question(client, previous)["result"]
            assert earlier["outcome"] == "partial" and earlier["message_code"] == "unknown_publication"
            assert earlier["findings"] == []


def test_clear_out_of_scope_requests_do_not_escalate(settings):
    with TestClient(create_app(settings, ScriptedChat())) as client:
        client.get("/api/v1/session")
        for outside in ("Какой курс доллара сейчас?", "Какая премия за мою сделку?", "Сделай краткое резюме созвона."):
            id = client.post("/api/v1/questions", json={"question": outside}).json()["id"]
            result = wait_question(client, id)["result"]
            assert result["outcome"] == "out_of_scope"
            assert result["quality_case"] is None and result["usage"]["chat_calls"] == 0
        id = client.post("/api/v1/questions", json={"question": "Как рассчитать премию за сделку по регламенту?"}).json()["id"]
        assert wait_question(client, id)["result"]["outcome"] == "not_found"


@pytest.mark.parametrize("complete", [True, False])
def test_partial_answer_uses_selected_evidence_for_coverage_review(settings, complete):
    class CoverageChat(ScriptedChat):
        def decide(self, data):
            ref = next(source["ref"] for source in data["sources"] if source["text"].startswith("3.2."))
            return Decision(outcome="partial", evidence=[ref], clarification=""), {"chat_calls": 1}

        def check_coverage(self, data, timeout):
            assert data["question"] == LICENSE_QUESTION and len(data["citations"]) == 1
            assert "не более 90 календарных дней" in data["citations"][0]["text"]
            assert 12 < timeout <= 30
            return Coverage(complete=complete), {"chat_calls": 1}

    with TestClient(create_app(settings, CoverageChat())) as client:
        client.get("/api/v1/session")
        question_id = client.post("/api/v1/questions", json={"question": LICENSE_QUESTION}).json()["id"]
        result = wait_question(client, question_id)["result"]
        assert result["outcome"] == ("answered" if complete else "partial")
        assert bool(result["quality_case"]) is not complete
        assert result["usage"]["chat_calls"] == 2


@pytest.mark.parametrize("review", ["both_cells", "omitted_cell", "invalid_reference"])
def test_partial_table_answer_reviews_and_validates_relevant_cells(settings, tmp_path, review):
    from docx import Document
    from docx.oxml import OxmlElement

    required = "Итоговое технико-коммерческое предложение (ТКП) или конкурсная заявка."
    optional = "Конкурсное техническое задание (ТЗ)."
    document = Document()
    document.add_paragraph("1. Содержание карточки проекта")
    document.add_paragraph("1.1. В карточку включают документы из перечня.")
    table = document.add_table(rows=2, cols=2)
    table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    table.cell(0, 0).text = "Обязательно"
    table.cell(0, 1).text = "При наличии"
    table.cell(1, 0).text = required
    table.cell(1, 1).text = optional
    other = document.add_table(rows=2, cols=1)
    other.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    other.cell(0, 0).text = "Данные по согласованию"
    other.cell(1, 0).text = "ТКП и ТЗ передают на экспертизу проекта."
    path = tmp_path / "project-card.docx"
    document.save(path)
    manifest = tmp_path / "table-catalog.json"
    manifest.write_text(json.dumps({"sources": [{
        "path": path.name, "key": "project-card", "version": "test", "title": "Порядок открытия проекта",
        "kind": "regulations", "status": "published", "groups": ["demo"],
    }]}), encoding="utf-8")
    assert import_catalog(settings, manifest)[0]["status"] == "imported"

    class TableChat(ScriptedChat):
        def decide(self, data):
            ref = next(source["ref"] for source in data["sources"] if source["text"] == required)
            return Decision(outcome="partial", evidence=[ref], clarification=""), {}

        def check_table_coverage(self, data, timeout):
            assert {cell["text"]: cell["column_header"] for cell in data["cells"]} == {
                required: "Обязательно", optional: "При наличии",
            }
            assert 12 < timeout <= 30
            refs = {cell["text"]: cell["ref"] for cell in data["cells"]}
            evidence = [refs[required]]
            if review == "both_cells":
                evidence.append(refs[optional])
            elif review == "invalid_reference":
                evidence.append(len(refs) + 1)
            return TableCoverage(complete=True, evidence=evidence), {}

    with TestClient(create_app(settings, TableChat())) as client:
        client.get("/api/v1/session")
        question_id = client.post("/api/v1/questions", json={
            "question": "Обязательны ли ТКП и ТЗ в карточке проекта?",
            "document_ids": ["project-card"],
        }).json()["id"]
        result = wait_question(client, question_id)["result"]
        complete = review == "both_cells"
        assert result["outcome"] == ("answered" if complete else "partial")
        expected = [("Обязательно", required)]
        if complete:
            expected.append(("При наличии", optional))
        assert [(item["locator"]["column_header"], item["quote"]) for item in result["citations"]] == expected
        assert bool(result["quality_case"]) is not complete
        assert result["usage"]["chat_calls"] == 2


def test_dialog_citations_and_practice_survive_restart(settings):
    with TestClient(create_app(settings, ScriptedChat())) as client:
        assert client.get("/api/v1/session").json()["visible_versions"] == 6
        request = {"question": CONTRACT_QUESTION, "as_of": "2026-11-26"}
        response = client.post("/api/v1/questions", json=request, headers={"Idempotency-Key": "first-turn"})
        assert response.status_code == 202
        first = response.json()["id"]
        repeat = client.post("/api/v1/questions", json=request, headers={"Idempotency-Key": "first-turn"})
        assert repeat.json()["id"] == first
        incompatible = client.post(
            "/api/v1/questions", json={"question": GAP_QUESTION}, headers={"Idempotency-Key": "first-turn"},
        )
        assert incompatible.status_code == 409
        initial = wait_question(client, first)["result"]
        assert initial["outcome"] == "needs_clarification"
        assert set(initial["clarification"]["options"]) == {"Маяк Восток", "Маяк Север"}
        follow_up = client.post(f"/api/v1/questions/{first}/follow-ups", json={
            "answer": "Маяк Север", "context": {"legal_entity": "Маяк Север"},
        })
        answer_id = follow_up.json()["id"]
        answer = wait_question(client, answer_id)["result"]
        assert answer["outcome"] == "answered"
        assert len({source["document_version_id"] for source in answer["citations"]}) == 2
        citation = answer["citations"][0]
        detail = client.get(citation["source_url"]).json()
        assert detail["quote"] == citation["quote"]
        assert detail["locator"]["line_start"] > 0

        note = {"citation_id": citation["id"], "situation": "Проверяли новый договор", "action_taken": "Сверили полномочия"}
        report = client.post(f"/api/v1/questions/{answer_id}/practice-reports", json=note)
        assert report.status_code == 201 and report.json()["status"] == "pending_review"
        assert client.post(f"/api/v1/questions/{answer_id}/practice-reports", json=note).json()["id"] == report.json()["id"]
        assert client.get("/api/v1/quality/practices").status_code == 403
        assert wait_question(client, answer_id)["result"]["practices"] == []
        saved_cookie = client.cookies.get("corp_docs_session")

    with TestClient(create_app(settings, ScriptedChat())) as restarted:
        restarted.cookies.set("corp_docs_session", saved_cookie)
        assert wait_question(restarted, answer_id)["result"]["citations"][0]["id"] == citation["id"]
        reviewer_login = restarted.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
        assert reviewer_login.status_code == 200
        items = restarted.get("/api/v1/quality/practices").json()["items"]
        assert items[0]["id"] == report.json()["id"]
        assert restarted.put(f"/api/v1/quality/practices/{items[0]['id']}", json={"decision": "approve"}).status_code == 200
        restarted.cookies.clear()
        restarted.cookies.set("corp_docs_session", saved_cookie)
        assert wait_question(restarted, answer_id)["result"]["practices"][0]["situation"] == note["situation"]


def test_gap_approved_answer_and_withdrawal(settings):
    with TestClient(create_app(settings, ScriptedChat())) as client:
        client.get("/api/v1/session")
        original = client.post("/api/v1/questions", json={"question": GAP_QUESTION}).json()["id"]
        result = wait_question(client, original)["result"]
        assert result["outcome"] == "not_found"
        case_id = result["quality_case"]["id"]
        request, = result["quality_case"]["mail"]
        mail_url = f"/api/v1/quality/mail/{request['id']}/eml"
        message = BytesParser(policy=policy.default).parsebytes(client.get(mail_url).content)
        assert message["To"] == "quality@corp-docs.test"
        assert message["Cc"] == request["cc"]
        assert GAP_QUESTION in message.get_content() and "Причина" in message.get_content()
        employee_cookie = client.cookies.get("corp_docs_session")
        case_url = f"/api/v1/quality/cases/{case_id}"
        assert client.put(case_url, json={"decision": "dismiss"}).status_code == 403
        client.cookies.clear()
        client.get("/api/v1/session")
        assert client.get(mail_url).status_code == 404
        assert client.post("/api/v1/session", json={"role": "quality", "code": "invalid"}).status_code == 401
        client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
        assert client.get("/api/v1/quality/cases").json()["items"][0]["id"] == case_id
        knowledge = "Согласуйте гостевой пропуск с администратором офиса."
        publication = {
            "decision": "publish", "question": GAP_QUESTION, "answer": knowledge,
            "aliases": ["Как попасть в офис в выходной день?"],
            "in_reply_to": request["id"],
        }
        publish = client.put(case_url, json=publication)
        assert publish.status_code == 200
        assert client.put(case_url, json=publication).json() == publish.json()
        reply_id = publish.json()["mail_id"]
        reply = BytesParser(policy=policy.default).parsebytes(client.get(f"/api/v1/quality/mail/{reply_id}/eml").content)
        assert reply["In-Reply-To"] == message["Message-ID"]
        assert reply["To"] == message["Cc"] and reply["From"] == message["To"]
        assert reply.get_content().strip() == knowledge
        corrected = "Перед выходными согласуйте заявку на гостевой пропуск с администратором офиса."
        assert client.put(case_url, json={**publication, "answer": corrected}).status_code == 409
        correction = client.put(case_url, json={**publication, "answer": corrected, "in_reply_to": reply_id})
        assert correction.status_code == 200
        quality_cookie = client.cookies.get("corp_docs_session")
        client.cookies.clear()
        client.cookies.set("corp_docs_session", employee_cookie)
        thread = wait_question(client, original)["result"]["quality_case"]["mail"]
        assert len(thread) == 3 and thread[-1]["body"] == corrected
        client.cookies.clear()
        client.get("/api/v1/session")
        new = client.post("/api/v1/questions", json={"question": "Как попасть в офис в выходной день?"}).json()["id"]
        reused = wait_question(client, new)["result"]
        assert reused["outcome"] == "quality_answer"
        assert reused["quality_answer"]["answer"] == corrected
        assert reused["citations"] == []
        next_cookie = client.cookies.get("corp_docs_session")
        client.cookies.clear()
        client.cookies.set("corp_docs_session", quality_cookie)
        assert client.put(case_url, json={"decision": "withdraw"}).status_code == 200
        client.cookies.clear()
        client.cookies.set("corp_docs_session", next_cookie)
        assert client.get(f"/api/v1/questions/{new}").status_code == 409


@pytest.fixture
def quality_deadlines(settings, tmp_path):
    rules = {
        "request-deadline": "Заявка направляется не менее чем за 4 рабочих дня до начала работ.",
        "other-deadline": "Заявка направляется не менее чем за 2 рабочих дня до начала работ.",
        "later-deadline": "Заявка направляется за 6 рабочих дней до начала работ.",
    }
    sources = []
    for key, rule in rules.items():
        path = tmp_path / f"{key}.txt"
        path.write_text(rule, encoding="utf-8")
        sources.append({
            "path": path.name, "key": key, "version": "test", "title": key, "kind": "regulations",
            "status": "published", "groups": ["demo"],
            **({"effective_from": "2026-12-01"} if key == "later-deadline" else {}),
        })
    manifest = tmp_path / "quality-deadlines.json"
    manifest.write_text(json.dumps({"sources": sources}), encoding="utf-8")
    assert all(item["status"] == "imported" for item in import_catalog(settings, manifest))

    class RulesChat(ScriptedChat):
        outcome = "not_found"
        chosen = []

        def decide(self, data):
            refs = [source["ref"] for source in data["sources"] if source["text"] in self.chosen]
            return Decision(outcome=self.outcome, evidence=refs, clarification=""), {}

    request = {
        "question": "Когда направляется заявка и кто утверждает заявку?", "as_of": "2026-11-27",
        "document_ids": list(rules),
    }
    return RulesChat(), request, rules, manifest


def test_quality_reply_cannot_override_conflicting_regulations(settings, quality_deadlines):
    chat, request, rules, _ = quality_deadlines
    with TestClient(create_app(settings, chat)) as client:
        client.get("/api/v1/session")
        employee = client.cookies.get("corp_docs_session")
        original = client.post("/api/v1/questions", json=request).json()["id"]
        previous_case = wait_question(client, original)["result"]["quality_case"]["id"]
        client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
        approval = {"decision": "publish", "question": request["question"], "answer": rules["other-deadline"]}
        assert client.put(f"/api/v1/quality/cases/{previous_case}", json=approval).status_code == 200

        client.cookies.clear()
        client.cookies.set("corp_docs_session", employee)
        chat.outcome = "conflict"
        chat.chosen = [rules["request-deadline"], rules["other-deadline"]]
        latest = client.post("/api/v1/questions", json=request).json()["id"]
        result = wait_question(client, latest)["result"]
        assert result["outcome"] == "conflict" and result["quality_answer"] is None
        assert {item["quote"] for item in result["citations"]} == set(chat.chosen)
        case = result["quality_case"]
        assert case["status"] == "pending" and len(case["mail"]) == 1

        client.cookies.clear()
        assert client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"}).status_code == 200
        items = client.get("/api/v1/quality/cases").json()["items"]
        item = next(item for item in items if item["id"] == case["id"])
        assert item["publication_block"] == "quality_source_conflict"
        refused = client.put(f"/api/v1/quality/cases/{case['id']}", json=approval)
        assert refused.status_code == 409 and refused.json()["detail"] == "quality_source_conflict"


def test_quality_supplement_must_match_current_norms(settings, quality_deadlines):
    chat, request, rules, manifest = quality_deadlines
    chat.outcome = "partial"
    chat.chosen = [rules["request-deadline"]]
    with TestClient(create_app(settings, chat)) as client:
        def ask(payload=request):
            question_id = client.post("/api/v1/questions", json=payload).json()["id"]
            return question_id, wait_question(client, question_id)["result"]

        client.get("/api/v1/session")
        employee = client.cookies.get("corp_docs_session")
        original, result = ask()
        assert result["outcome"] == "partial"
        case_id = result["quality_case"]["id"]
        case_url = f"/api/v1/quality/cases/{case_id}"
        client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
        approval = {"decision": "publish", "question": request["question"], "answer": rules["other-deadline"]}
        refused = client.put(case_url, json=approval)
        assert refused.status_code == 409 and refused.json()["detail"] == "quality_deadline_changed"
        approval["answer"] = "Полномочия на утверждение заявки уточните у руководителя проекта."
        assert client.put(case_url, json=approval).status_code == 200

        client.cookies.clear()
        client.cookies.set("corp_docs_session", employee)
        reused, result = ask()
        assert result["outcome"] == "partial" and result["quality_answer"]["answer"] == approval["answer"]
        # Simulate a conflicting publication persisted before deadline validation existed.
        with Store(settings.database).connect(write=True) as conn:
            conn.execute("UPDATE quality_cases SET answer = ? WHERE id = ?", (rules["other-deadline"], case_id))
        assert wait_question(client, original)["result"]["quality_answer"] is None
        assert ask()[1]["quality_answer"] is None
        with Store(settings.database).connect(write=True) as conn:
            conn.execute("UPDATE quality_cases SET answer = ? WHERE id = ?", (approval["answer"], case_id))
        assert ask({**request, "as_of": "2026-11-28"})[1]["quality_answer"] is not None
        assert ask({**request, "as_of": "2026-12-02"})[1]["quality_answer"] is None
        chat.chosen = [rules["other-deadline"]]
        assert ask()[1]["quality_answer"] is None

        new_catalog = json.loads(manifest.read_text(encoding="utf-8"))
        new_catalog["sources"][0]["title"] = "Обновлённые правила подачи заявки"
        manifest.write_text(json.dumps(new_catalog), encoding="utf-8")
        assert import_catalog(settings, manifest)[0]["status"] == "updated"
        chat.chosen = [rules["request-deadline"]]
        previous = wait_question(client, original)["result"]
        assert previous["corpus_changed"] and previous["quality_answer"] is None
        assert [entry["direction"] for entry in previous["quality_case"]["mail"]] == ["request", "reply"]
        assert client.get(f"/api/v1/questions/{reused}").status_code == 409

        _, new_result = ask()
        assert new_result["quality_answer"] is None and new_result["quality_case"]["status"] == "pending"
        client.cookies.clear()
        assert client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"}).status_code == 200
        refused = client.put(case_url, json=approval)
        assert refused.status_code == 409 and refused.json()["detail"] == "quality_corpus_changed"
        replacement = {**approval, "answer": "Уточните полномочия на утверждение у владельца процесса."}
        new_case_url = f"/api/v1/quality/cases/{new_result['quality_case']['id']}"
        assert client.put(new_case_url, json=replacement).status_code == 200


def test_reported_claim_keeps_verified_deadline_as_an_answer(settings, quality_deadlines):
    _, request, rules, _ = quality_deadlines
    rule = rules["request-deadline"]
    question = "Мне сказали, что заявка на запуск работ обязательно направляется за две недели до начала работ. Это верно?"

    class ClaimChat(ScriptedChat):
        def decide(self, data):
            assert data["question"] == question
            ref = next(item["ref"] for item in data["sources"] if item["text"] == rule)
            return Decision(outcome="partial", evidence=[ref], clarification=""), {}

        def check_coverage(self, data, timeout):
            assert data["question"] == question
            assert [item["text"] for item in data["citations"]] == [rule]
            return Coverage(complete=True), {}

    with TestClient(create_app(settings, ClaimChat())) as client:
        client.get("/api/v1/session")
        submitted = client.post("/api/v1/questions", json={
            **request, "question": question, "document_ids": ["request-deadline"],
        })
        assert submitted.status_code == 202
        result = wait_question(client, submitted.json()["id"])["result"]
        assert result["outcome"] == "answered" and result["quality_case"] is None
        assert [item["quote"] for item in result["citations"]] == [rule]


def test_pending_mail_is_migrated_once(settings):
    with TestClient(create_app(settings, ScriptedChat())) as client:
        client.get("/api/v1/session")
        question_id = client.post("/api/v1/questions", json={"question": GAP_QUESTION}).json()["id"]
        case = wait_question(client, question_id)["result"]["quality_case"]
    with Store(settings.database).connect(write=True) as conn:
        conn.execute("DROP TABLE quality_mail")
        conn.execute("PRAGMA user_version = 1")
    for _ in range(2):
        with TestClient(create_app(settings, ScriptedChat())) as restarted:
            restarted.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
            item, = restarted.get("/api/v1/quality/cases").json()["items"]
            assert item["id"] == case["id"]
            migrated, = item["mail"]
            assert GAP_QUESTION in migrated["body"]


def test_restricts_corpus_before_lexical_search_and_respects_supersession(settings, tmp_path):
    (tmp_path / "staff.txt").write_text("Закрытый специальный термин Термохолдинг", encoding="utf-8")
    (tmp_path / "new.txt").write_text("Новая редакция правил регистрации договора", encoding="utf-8")
    catalog = tmp_path / "extra.json"
    catalog.write_text(json.dumps({"sources": [
        {"path": "staff.txt", "key": "private-terms", "version": "1", "title": "Внутренние термины",
         "kind": "regulations", "groups": ["staff"]},
        {"path": "new.txt", "key": "demo-contracts", "version": "2", "title": "Новая редакция",
         "kind": "regulations", "status": "published", "effective_from": "2027-01-01",
         "groups": ["demo"], "supersedes": ["demo-contracts@2026.1"]},
    ]}, ensure_ascii=False), encoding="utf-8")
    assert all(item["status"] == "imported" for item in import_catalog(settings, catalog))
    with Store(settings.database).connect() as conn:
        visible = visible_versions(conn, "demo")
        before, _ = eligible_versions(visible, date(2026, 11, 26), {}, [])
        after, _ = eligible_versions(visible, date(2027, 1, 1), {}, [])
        assert "demo-contracts@2026.1" in before and "demo-contracts@2026.1" not in after
        assert "demo-contracts@2" in after
        assert lexical_ids(conn, query_terms("Термохолдинг"), before) == []
        assert lexical_ids(conn, query_terms("Термохолдинг"), visible_versions(conn, "staff"))
    with TestClient(create_app(replace(settings), ScriptedChat())) as client:
        client.get("/api/v1/session")
        response = client.post(
            "/api/v1/questions", json={"question": "Что означает Термохолдинг?", "document_ids": ["private-terms"]},
        )
        assert response.status_code == 404
        assert client.get("/api/v1/document-versions/private-terms@1/content").status_code == 404
