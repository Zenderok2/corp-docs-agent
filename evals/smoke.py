"""Check the synthetic corpus through a running HTTP service using its configured providers."""

import argparse
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx


CONTRACT = "Кто подписывает агентский договор на 400 000 рублей и что проверяет инициатор?"
LICENSE = "Какую лицензию выдают внешнему подрядчику и на какой срок?"
PROJECT = "В какой срок проджект-менеджер согласует план проекта?"
PROJECT_MARKERS = {"Дирекция информационной безопасности": "две недели", "Инфраструктура": "три рабочих дня"}
GAP = "Как оформить гостевой пропуск в офис на выходные?"
DATE = "2026-11-26"


def call(client, method, path, **kwargs):
    response = client.request(method, "/api/v1" + path, **kwargs)
    response.raise_for_status()
    return response.json()


def wait(client, path, payload):
    created = call(client, "POST", path, json=payload, headers={"Idempotency-Key": uuid4().hex})
    deadline = monotonic() + 190
    while monotonic() < deadline:
        question = call(client, "GET", "/questions/" + created["id"])
        if question["status"] not in {"queued", "running"}:
            return question
        sleep(.4)
    raise RuntimeError("The question did not finish before the smoke test deadline")


def assess(client, report, name, question, expected, required_versions=(), text_markers=()):
    result = question["result"] or {}
    citations = result.get("citations", [])
    versions = {citation["document_version_id"] for citation in citations}
    quote_text = " ".join(citation["quote"] for citation in citations)
    verified = all(
        call(client, "GET", "/citations/" + citation["id"])["quote"] == citation["quote"]
        for citation in citations
    )
    outcome = result.get("outcome")
    findings = result.get("findings", [])
    ids = {citation["id"] for citation in citations}
    grounded = all(finding["citation_ids"] and ids.issuperset(finding["citation_ids"]) for finding in findings)
    report[name] = {
        "ok": outcome in expected and set(required_versions).issubset(versions) and verified and grounded
        and all(marker in quote_text for marker in text_markers),
        "outcome": outcome,
        "error": result.get("error_code"),
        "sources": sorted(versions),
        "findings": len(findings),
        "usage": result.get("usage", {}),
        "search": next(iter(result.get("trace", [])), {}),
    }


def run(base_url: str, verify_quality: bool):
    report = {}
    with httpx.Client(base_url=base_url, timeout=15, trust_env=False) as client:
        health = client.get("/health/ready")
        report["readiness"] = {"ok": health.is_success, **health.json()}
        call(client, "GET", "/session")
        opening = wait(client, "/questions", {"question": CONTRACT, "as_of": DATE})
        assess(client, report, "clarification", opening, {"needs_clarification"})
        if report["clarification"]["ok"]:
            composed = wait(client, "/questions/" + opening["id"] + "/follow-ups", {
                "answer": "Маяк Север", "context": {"legal_entity": "Маяк Север"},
            })
        else:
            composed = wait(client, "/questions", {
                "question": CONTRACT, "as_of": DATE, "context": {"legal_entity": "Маяк Север"},
            })
        assess(client, report, "multiple_documents", composed, {"answered"},
               ("demo-contracts@2026.1", "demo-delegation-north@2026.1"),
               ("проверяет приказ", "директору по продажам", "500 000"))
        licensed = wait(client, "/questions", {"question": LICENSE, "as_of": DATE})
        assess(client, report, "license", licensed, {"answered"}, ("demo-licensing@2026.1",), ("90 календарных дней",))
        project = wait(client, "/questions", {"question": PROJECT, "as_of": DATE})
        assess(client, report, "department_clarification", project, {"needs_clarification"})
        if report["department_clarification"]["ok"]:
            options = project["result"]["clarification"]["options"]
            report["department_clarification"]["ok"] = set(options) == set(PROJECT_MARKERS)
            both = wait(client, "/questions/" + project["id"] + "/follow-ups", {
                "answer": "Комплексный проект", "context": {"departments": list(PROJECT_MARKERS)},
            })
            assess(client, report, "complex_project", both, {"answered"},
                   ("demo-project-infrastructure@2026.1", "demo-project-security@2026.1"), tuple(PROJECT_MARKERS.values()))
            result = both["result"]
            quotes = {citation["id"]: citation["quote"] for citation in result.get("citations", [])}
            groups = {group["name"]: group["citation_ids"] for group in result.get("departments", [])}
            report["complex_project"]["ok"] &= all(
                any(marker in quotes[key] for key in groups.get(department, []))
                for department, marker in PROJECT_MARKERS.items()
            )
        out_of_scope = wait(client, "/questions", {"question": "Какой курс доллара сейчас?", "as_of": DATE})
        assess(client, report, "out_of_scope", out_of_scope, {"out_of_scope"})
        report["out_of_scope"]["ok"] &= not out_of_scope["result"].get("quality_case")
        gap = wait(client, "/questions", {"question": GAP, "as_of": DATE})
        expected_gap = {"not_found", "quality_answer"} if verify_quality else {"not_found"}
        assess(client, report, "gap", gap, expected_gap)

        if verify_quality and report["gap"]["ok"]:
            if gap["result"]["outcome"] == "quality_answer":
                report["quality_reuse"] = {"ok": True, "already_reviewed": True}
            else:
                case = gap["result"]["quality_case"]
                if not case or not case["mail"]:
                    report["quality_reuse"] = {"ok": False, "error": "missing_case"}
                    return report
                initial, = case["mail"]
                download = client.get(f"/api/v1/quality/mail/{initial['id']}/eml")
                download.raise_for_status()
                request_mail = BytesParser(policy=policy.default).parsebytes(download.content)
                report["quality_mail"] = {"ok": request_mail["To"] == "quality@corp-docs.test"
                                          and request_mail["Cc"] == initial["cc"] and GAP in request_mail.get_content()}
                call(client, "POST", "/session", json={"role": "quality", "code": os.environ["QUALITY_ACCESS_CODE"]})
                published = call(client, "PUT", "/quality/cases/" + case["id"], json={
                    "decision": "publish", "question": GAP,
                    "answer": "Демонстрационный ответ. Согласуйте гостевой пропуск с администратором офиса.",
                    "in_reply_to": initial["id"],
                })
                reply = client.get(f"/api/v1/quality/mail/{published['mail_id']}/eml")
                reply.raise_for_status()
                reply_mail = BytesParser(policy=policy.default).parsebytes(reply.content)
                report["quality_mail"]["ok"] &= reply_mail["In-Reply-To"] == request_mail["Message-ID"]
                call(client, "DELETE", "/session")
                reused = wait(client, "/questions", {"question": GAP, "as_of": DATE})
                assess(client, report, "quality_reuse", reused, {"quality_answer"})
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-quality", action="store_true", help="Publish a synthetic answer in a test-only database")
    args = parser.parse_args()
    report = run(args.base_url, args.verify_quality)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    raise SystemExit(0 if all(item["ok"] for item in report.values()) else 1)


if __name__ == "__main__":
    main()
