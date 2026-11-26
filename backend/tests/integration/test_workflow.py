from dataclasses import replace
from datetime import date
import json
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient
import pytest

from corp_docs_agent.api.main import create_app
from corp_docs_agent.core.settings import Settings
from corp_docs_agent.ingestion.catalog import import_catalog
from corp_docs_agent.persistence.store import Store, visible_versions
from corp_docs_agent.providers.llama import Decision
from corp_docs_agent.retrieval.search import eligible_versions, lexical_ids
from corp_docs_agent.retrieval.terms import query_terms


SAMPLES = Path(__file__).resolve().parents[3] / "data" / "samples" / "catalog.json"
CONTRACT_QUESTION = "Кто подписывает агентский договор на 400 000 рублей и что проверяет инициатор?"
GAP_QUESTION = "Как оформить гостевой пропуск в офис на выходные?"


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
    assert len(report) == 4 and all(item["status"] == "imported" for item in report)
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


def test_dialog_citations_and_practice_survive_restart(settings):
    with TestClient(create_app(settings, ScriptedChat())) as client:
        assert client.get("/api/v1/session").json()["visible_versions"] == 4
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
        assert client.put(f"/api/v1/quality/cases/{case_id}", json={"decision": "dismiss"}).status_code == 403
        assert client.post("/api/v1/session", json={"role": "quality", "code": "invalid"}).status_code == 401
        client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
        assert client.get("/api/v1/quality/cases").json()["items"][0]["id"] == case_id
        knowledge = "Согласуйте гостевой пропуск с администратором офиса."
        publish = client.put(f"/api/v1/quality/cases/{case_id}", json={
            "decision": "publish", "question": GAP_QUESTION, "answer": knowledge,
            "aliases": ["Как попасть в офис в выходной день?"],
        })
        assert publish.status_code == 200
        client.delete("/api/v1/session")
        new = client.post("/api/v1/questions", json={"question": "Как попасть в офис в выходной день?"}).json()["id"]
        reused = wait_question(client, new)["result"]
        assert reused["outcome"] == "quality_answer"
        assert reused["quality_answer"]["answer"] == knowledge
        assert reused["citations"] == []
        client.post("/api/v1/session", json={"role": "quality", "code": "quality-test"})
        assert client.put(f"/api/v1/quality/cases/{case_id}", json={"decision": "withdraw"}).status_code == 200
        client.delete("/api/v1/session")
        assert client.get(f"/api/v1/questions/{new}").status_code == 409


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
