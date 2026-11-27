"""Run private acceptance cases through a deployed service and record safe diagnostics."""

import argparse
from email import policy
from email.parser import BytesParser
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from smoke import call, wait


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def citation_matches(citation: dict, expected: dict) -> bool:
    if citation["document_version_id"] != expected["version"]:
        return False
    locator = citation["locator"]
    for field in ("clause_number", "column_header"):
        if field in expected and locator.get(field) != expected[field]:
            return False
    quote = normalize(citation["quote"])
    return all(normalize(value) in quote for value in expected.get("contains", []))


def comparison_matches(rows: list[dict], expected: list[dict], citations: dict[str, dict], groups: dict) -> bool:
    by_department = {row["department"]: row for row in rows}
    if len(rows) != len(expected) or set(by_department) != {item["department"] for item in expected}:
        return False
    for item in expected:
        department = item["department"]
        row = by_department[department]
        citation_id = row["citation_id"]
        if citation_id not in citations or citation_id not in groups.get(department, []):
            return False
        if not citation_matches(citations[citation_id], item):
            return False
        for field in ("deadline", "anchor", "condition"):
            if field in item and normalize(row.get(field) or "") != normalize(item[field] or ""):
                return False
    return True


def verify_link(client: httpx.Client, citation: dict) -> bool:
    resolved = call(client, "GET", "/citations/" + citation["id"])
    return all(
        resolved[field] == citation[field]
        for field in ("document_version_id", "block_id", "chunk_id", "locator", "source_hash", "quote")
    )


def verify_mail(client: httpx.Client, case: dict, result: dict) -> bool:
    expected = case.get("quality_mail")
    if expected is None:
        return True
    messages = (result.get("quality_case") or {}).get("mail", [])
    if not expected:
        return not messages
    request = next((item for item in messages if item["direction"] == "request"), None)
    if request is None:
        return False
    download = client.get("/api/v1/quality/mail/" + request["id"] + "/eml")
    if not download.is_success:
        return False
    mail = BytesParser(policy=policy.default).parsebytes(download.content)
    return (
        mail["To"] == "quality@corp-docs.test" and mail["Cc"] == request["cc"]
        and normalize(case["request"]["question"]) in normalize(mail.get_content())
    )


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
    links_ok = all(verify_link(client, citation) for citation in citations)
    mail_ok = verify_mail(client, case, result)
    expected_comparison = case.get("department_comparison")
    comparison_ok = expected_comparison is None or comparison_matches(
        result.get("department_comparison", []), expected_comparison, by_id, groups,
    )
    allowed_versions = case.get("allowed_versions")
    sources_ok = allowed_versions is None or all(item["document_version_id"] in allowed_versions for item in citations)
    trace = {item["step"]: item for item in result.get("trace", [])}
    retrieval = trace.get("retrieval", {})
    outcomes = case["outcomes"]
    return {
        "id": case["id"],
        "ok": result.get("outcome") in outcomes and not missing and links_ok and mail_ok and comparison_ok and sources_ok,
        "status": record["status"], "outcome": result.get("outcome"), "error": result.get("error_code"),
        "expected_outcomes": outcomes, "missing_markers": missing, "links_ok": links_ok, "mail_ok": mail_ok,
        "comparison_ok": comparison_ok, "sources_ok": sources_ok,
        "sources": [{"version": item["document_version_id"], "locator_type": item["locator"]["type"],
                     "clause": item["locator"].get("clause_number")} for item in citations],
        "usage": result.get("usage", {}),
        "timings_ms": {step: trace[step]["duration_ms"] for step in ("retrieval", "assessment", "verification")
                       if step in trace and "duration_ms" in trace[step]},
        "vector_state": retrieval.get("vector_state"),
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
