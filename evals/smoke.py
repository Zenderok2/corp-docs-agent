"""Check the synthetic corpus through a running HTTP service using its configured providers."""

import argparse
import json
import os
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import httpx


CONTRACT = "Кто подписывает агентский договор на 400 000 рублей и что проверяет инициатор?"
LICENSE = "Какую лицензию выдают внешнему подрядчику и на какой срок?"
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
    report[name] = {
        "ok": outcome in expected and set(required_versions).issubset(versions) and verified
        and all(marker in quote_text for marker in text_markers),
        "outcome": outcome,
        "error": result.get("error_code"),
        "sources": sorted(versions),
        "usage": result.get("usage", {}),
        "search": result.get("trace", [{}])[0],
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
        gap = wait(client, "/questions", {"question": GAP, "as_of": DATE})
        expected_gap = {"not_found", "quality_answer"} if verify_quality else {"not_found"}
        assess(client, report, "gap", gap, expected_gap)

        if verify_quality and report["gap"]["ok"]:
            if gap["result"]["outcome"] == "quality_answer":
                report["quality_reuse"] = {"ok": True, "already_reviewed": True}
            else:
                case = gap["result"]["quality_case"]
                if not case:
                    report["quality_reuse"] = {"ok": False, "error": "missing_case"}
                    return report
                call(client, "POST", "/session", json={"role": "quality", "code": os.environ["QUALITY_ACCESS_CODE"]})
                call(client, "PUT", "/quality/cases/" + case["id"], json={
                    "decision": "publish", "question": GAP,
                    "answer": "Демонстрационный ответ. Согласуйте гостевой пропуск с администратором офиса.",
                })
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
