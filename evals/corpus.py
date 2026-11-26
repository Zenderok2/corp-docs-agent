"""Run private acceptance cases through a deployed service and record safe diagnostics."""

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from smoke import call, wait


def citation_matches(citation: dict, expected: dict) -> bool:
    if citation["document_version_id"] != expected["version"]:
        return False
    locator = citation["locator"]
    for field in ("clause_number", "column_header"):
        if field in expected and locator.get(field) != expected[field]:
            return False
    quote = " ".join(citation["quote"].casefold().split())
    return all(" ".join(value.casefold().split()) in quote for value in expected.get("contains", []))


def check_case(client: httpx.Client, case: dict) -> dict:
    record = wait(client, "/questions", case["request"])
    result = record["result"] or {}
    citations = result.get("citations", [])
    by_id = {citation["id"]: citation for citation in citations}
    groups = {group["name"]: group["citation_ids"] for group in result.get("departments", [])}
    missing = []
    for number, expected in enumerate(case.get("citations", []), 1):
        available = [by_id[key] for key in groups.get(expected["department"], [])] if "department" in expected else citations
        if not any(citation_matches(citation, expected) for citation in available):
            missing.append(number)
    links_ok = all(
        call(client, "GET", "/citations/" + citation["id"])["quote"] == citation["quote"] for citation in citations
    )
    mail_expected = case.get("quality_mail")
    quality = result.get("quality_case") or {}
    mail_ok = mail_expected is None or bool(quality.get("mail")) == mail_expected
    outcomes = case["outcomes"]
    return {
        "id": case["id"], "ok": result.get("outcome") in outcomes and not missing and links_ok and mail_ok,
        "status": record["status"], "outcome": result.get("outcome"), "error": result.get("error_code"),
        "expected_outcomes": outcomes, "missing_markers": missing, "links_ok": links_ok, "mail_ok": mail_ok,
        "sources": [{"version": item["document_version_id"], "locator_type": item["locator"]["type"],
                     "clause": item["locator"].get("clause_number")} for item in citations],
        "usage": result.get("usage", {}),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    address = urlsplit(args.base_url)
    if address.scheme != "https" and address.hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Use HTTPS for a remote service")
    plan = json.loads(args.cases.read_text(encoding="utf-8"))
    report: dict = {"cases": []}
    with httpx.Client(base_url=args.base_url, timeout=15, trust_env=False) as client:
        health = client.get("/health/ready")
        health.raise_for_status()
        report["build_sha"] = health.json()["build_sha"]
        call(client, "GET", "/session")
        demo = {item["id"] for item in call(client, "GET", "/documents")["items"]}
        session = call(client, "POST", "/session", json={"role": "staff", "code": os.environ["STAFF_ACCESS_CODE"]})
        staff = {item["id"] for item in call(client, "GET", "/documents")["items"]}
        required = set(plan["versions"])
        report["access_ok"] = session["role"] == "staff" and required.issubset(staff) and not required.intersection(demo)
        for case in plan["cases"]:
            checked = check_case(client, case)
            report["cases"].append(checked)
            print(f"{case['id']}  {'PASS' if checked['ok'] else 'FAIL'}  {checked['outcome'] or checked['error']}", flush=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if report["access_ok"] and all(case["ok"] for case in report["cases"]) else 1)


if __name__ == "__main__":
    main()
