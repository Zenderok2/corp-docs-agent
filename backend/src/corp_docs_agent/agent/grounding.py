from decimal import Decimal
import re

from corp_docs_agent.providers.llama import Finding
from corp_docs_agent.retrieval.search import Candidate
from corp_docs_agent.retrieval.terms import WORD, index_text, words


NUMBERS = re.compile(r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?")
CLAUSE_NUMBER = re.compile(r"^(?:\d{1,3}(?:\.\d{1,3})*\.|\d{1,3}\)|•)\s+")
RELATIVE_OFFSET = re.compile(r"\bза \d", re.IGNORECASE)
BEFORE_DEADLINE = re.compile(
    r"(?P<action>[^.!?;]+?) "
    r"(?P<deadline>(?:не (?:менее|более|позднее|ранее) чем |как (?:минимум|максимум) )?"
    r"за \d+(?:[.,]\d+)? (?:(?:рабоч|календарн)\w+ )?"
    r"(?:час(?:а|ов)?|день|дня|дней|недел(?:ю|и|ь)|месяц(?:а|ев)?)) "
    r"(?P<anchor>до [^.!?;]+)(?:\.(?: (?P<condition>(?:при|если|в случае|иначе)\b [^.!?]+[.!?]?))?)?",
    re.IGNORECASE,
)
CONDITIONS = [re.compile(pattern, re.IGNORECASE) for pattern in (
    r"\bрабоч\w* (?:д[не]\w*|час\w*)\b",
    r"\bкалендарн\w* (?:д[не]\w*|недел\w*)\b",
    r"\bнедел\w*\b",
    r"\bмесяц\w*\b",
    r"\bруб(?:[.\s]|л\w*\b)|₽",
    r"\bпроцент\w*\b|%",
)]
QUALIFIED_CLAUSE = re.compile(
    r"\b(?:на\s+(?:срок|период)(?!\s+(?:не\b|до\b|от\b))"
    r"|не\s+(?:более|менее|больше|меньше|выше|ниже|ранее|позднее)"
    r"|(?:только|лишь)\s+(?:при|если|для|после|до)"
    r"|при\s+(?:условии|наличии|отсутствии)|в\s+(?:случае|течение)"
    r"|с\s+момента|за\s+исключением|если|кроме|до|после)\b"
    r"[^\n]+?(?=[.!?;](?:\s|$)|\n|\s+(?:но|однако)\s+|$)",
    re.IGNORECASE,
)
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


def parse_before_deadline(quote: str) -> dict[str, str | None] | None:
    """Split an explicit deadline before an event while retaining any trailing condition."""
    text = CLAUSE_NUMBER.sub("", " ".join(quote.split()))
    match = BEFORE_DEADLINE.fullmatch(text)
    if match is None or any(RELATIVE_OFFSET.search(match[field]) for field in ("action", "anchor")):
        return None
    return match.groupdict()


def changes_explicit_deadline(quote: str, reply: str) -> bool:
    """Detect a changed amount when a reply restates the same explicit deadline."""
    original = parse_before_deadline(quote.casefold().replace("ё", "е"))
    proposed = parse_before_deadline(reply.casefold().replace("ё", "е"))
    if not original or not proposed:
        return False
    if any(original[key] != proposed[key] for key in ("action", "anchor", "condition")):
        return False
    before, after = original["deadline"], proposed["deadline"]
    if NUMBERS.sub("#", before) != NUMBERS.sub("#", after):
        return False
    amounts = (Decimal(NUMBERS.search(value)[0].replace(",", ".")) for value in (before, after))
    left, right = amounts
    return left != right


def concise_excerpts(evidence: list[tuple[Candidate, int, int]]) -> list[dict]:
    excerpts = []
    for index, (source, _, _) in enumerate(evidence):
        clause = CLAUSE_NUMBER.sub("", source.text) if source.start == 0 else source.text
        text = " ".join(clause.split())
        if text and (column := source.locator.get("column_header")):
            text = f"{column} — {text}"
        if source.missing_scope or not text or len(text) > 195:
            return []
        excerpts.append({"text": text, "refs": [index]})
    return excerpts


def table_abbreviations_covered(question: str, cells: list[Candidate], selected: list[Candidate]) -> bool:
    abbreviations = {
        token.casefold() for cell in cells for token in WORD.findall(cell.text)
        if len(token) > 1 and token.isupper()
    }
    requested = set(words(question)) & abbreviations
    return requested.issubset(words(" ".join(cell.text for cell in selected)))


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
        if any(source.missing_scope or source.locator.get("column_header") for source in passages):
            return [], []
        text = " ".join(finding.text.split())
        originals = "\n".join(source.text for source in passages)
        if not literals(text).issubset(literals(originals)):
            return [], []
        if any(pattern.search(text) and not pattern.search(originals) for pattern in CONDITIONS):
            return [], []
        # Every explicit limitation must remain visible next to a shortened claim.
        # The verifier alone can accept a statement that is true but incomplete.
        normalized = f" {index_text(text)} "
        if any(f" {index_text(match[0])} " not in normalized for match in QUALIFIED_CLAUSE.finditer(originals)):
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
