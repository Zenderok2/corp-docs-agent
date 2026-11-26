import re

from corp_docs_agent.providers.llama import Finding
from corp_docs_agent.retrieval.search import Candidate


NUMBERS = re.compile(r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?")
CLAUSE_NUMBER = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})*\.|\d{1,3}\)|•)\s+")
CONDITIONS = [re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bрабоч\w* (?:д[не]\w*|час\w*)\b",
    r"\bкалендарн\w* (?:д[не]\w*|недел\w*)\b",
    r"\bнедел\w*\b",
    r"\bмесяц\w*\b",
    r"\bруб(?:[.\s]|л\w*\b)|₽",
    r"\bпроцент\w*\b|%",
)]
MEETING_SUMMARY = re.compile(
    r"\b(?:перескажи|суммируй|подведи итог\w*|сделай [^.!?]{0,30}резюме)\b[^.!?]{0,75}\b(?:встреч|созвон|совещан)\w*\b"
)
INDIVIDUAL_METRIC = re.compile(r"\b(?:марж|преми|вознаграждени|бонус)\w*\b.*\b(?:сделк|продаж)\w*\b")
SPECIFIC_CASE = re.compile(r"\b(?:у меня|мо(?:й|я|и|ей|их|ему|его|ю)|эт(?:ой|ого|от|у)|конкретн\w*|номер)\b|\d{2,}")
EXCHANGE = re.compile(r"\bкурс\w*\s+(?:доллар|евро|валют|юан)\w*\b")
CURRENT = re.compile(r"\b(?:сегодня|сейчас|текущ\w*|актуальн\w*)\b")
POLICY = re.compile(r"\b(?:как|правил\w*|регламент\w*|поряд\w*|положени\w*|формул\w*)\b")


def outside_document_qa(question: str) -> bool:
    text = " ".join(question.casefold().replace("ё", "е").split())
    if MEETING_SUMMARY.search(text):
        return True
    if POLICY.search(text):
        return False
    return bool((INDIVIDUAL_METRIC.search(text) and SPECIFIC_CASE.search(text))
                or (EXCHANGE.search(text) and CURRENT.search(text)))


def concise_excerpts(evidence: list[tuple[Candidate, int, int]]) -> list[dict]:
    excerpts = []
    for index, (source, _, _) in enumerate(evidence):
        clause = CLAUSE_NUMBER.sub("", source.text) if source.start == 0 else source.text
        text = " ".join(clause.split())
        if source.missing_scope or not text or len(text) > 195:
            return []
        excerpts.append({"text": text, "refs": [index]})
    return excerpts


def verification_input(
    proposals: list[Finding], sources: list[Candidate], evidence: list[tuple[Candidate, int, int]],
    scopes: dict[str, list[int]],
) -> tuple[list[dict], list[dict]]:
    if not proposals or not evidence:
        return [], []
    citation_index = {source.id: index for index, (source, _, _) in enumerate(evidence)}
    index_by_ref = {ref: citation_index[source.id] for ref, source in enumerate(sources, 1) if source.id in citation_index}
    safe_findings, checks = [], []
    covered = set()
    for number, finding in enumerate(proposals, 1):
        if any(ref not in index_by_ref for ref in finding.refs):
            return [], []
        indices = list(dict.fromkeys(index_by_ref[ref] for ref in finding.refs))
        applicable = [name for name, refs in scopes.items() if set(indices).issubset(refs)]
        if scopes and not applicable:
            return [], []
        passages = [evidence[index][0] for index in indices]
        if any(source.missing_scope for source in passages):
            return [], []
        text = " ".join(finding.text.split())
        originals = "\n".join(source.text for source in passages)
        if not literals(text).issubset(literals(originals)):
            return [], []
        if any(pattern.search(text) and not pattern.search(originals) for pattern in CONDITIONS):
            return [], []
        covered.update(indices)
        safe_findings.append({"text": text, "refs": indices})
        checks.append({
            "id": number, "text": text, "departments": applicable,
            "citations": [{"scope": source.version.metadata["scope"], "quote": source.text} for source in passages],
        })
    if len(covered) != len(evidence):
        return [], []
    return safe_findings, checks


def literals(text: str) -> set[str]:
    return {"".join(number.split()).replace(",", ".") for number in NUMBERS.findall(text)}
