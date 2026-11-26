from collections import Counter
from dataclasses import dataclass
from datetime import date
import json
from math import ceil
import re
import sqlite3

from corp_docs_agent.persistence.store import CorpusVersion, json_dump
from corp_docs_agent.retrieval.terms import concepts, fts_query, index_text, query_terms, scope_key


CANDIDATE_LIMIT = 10
RUBLE_AMOUNT = r"(?P<sum>\d{1,3}(?:\s\d{3})+|\d+(?:[.,]\d{1,2})?)\s*(?:руб(?:лей|ля|ль|\.)?|₽)(?![а-я])"
QUESTION_AMOUNT = re.compile(RUBLE_AMOUNT, re.IGNORECASE)
LIMIT_AMOUNT = re.compile(r"\b(?P<operator>не более|не менее|свыше|до|от)\s+" + RUBLE_AMOUNT, re.IGNORECASE)


@dataclass(frozen=True)
class Candidate:
    id: str
    block_id: str
    start: int
    text: str
    locator: dict
    version: CorpusVersion
    missing_scope: dict[str, str]


def normalized(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def mentioned_departments(text: str, labels: set[str]) -> list[str]:
    text_words = f" {index_text(text)} "
    named_aliases = {"диб", "уиб"}.intersection(concepts(text))
    return [label for label in sorted(labels) if (
        f" {scope_key(label)} " in text_words or scope_key(label) in named_aliases
    )]


def eligible_versions(
    versions: dict[str, CorpusVersion], as_of: date, context: dict, document_ids: list[str],
) -> tuple[dict[str, CorpusVersion], dict[str, dict]]:
    date_matches = {}
    missing = {}
    for version in versions.values():
        if not version.applies_on(as_of):
            continue
        scope = {key: value for key, value in version.metadata["scope"].items() if value}
        for key, value in scope.items():
            compare = scope_key if key == "department" else normalized
            if context.get(key) and compare(context[key]) != compare(value):
                break
        else:
            date_matches[version.id] = version
            missing[version.id] = {key: value for key, value in scope.items() if not context.get(key)}

    superseded = set()
    for version in date_matches.values():
        if not missing[version.id]:
            superseded.update(version.metadata["supersedes"])
    return {
        key: version for key, version in date_matches.items()
        if key not in superseded and (not document_ids or version.document_id in document_ids)
    }, missing


def lexical_ids(conn: sqlite3.Connection, terms: list[str], versions: dict, limit: int = 50) -> list[str]:
    if not terms or not versions:
        return []
    rows = conn.execute(
        """
        SELECT c.id FROM chunks_fts AS f
        JOIN chunks c ON c.id = f.chunk_id
        JOIN blocks b ON b.id = c.block_id
        WHERE chunks_fts MATCH ? AND b.version_id IN (SELECT value FROM json_each(?))
        ORDER BY bm25(chunks_fts, 0, 3.0, 1.25, 0.45), b.ordinal, c.id
        LIMIT ?
        """,
        (fts_query(terms), json_dump(list(versions)), limit),
    )
    return [row["id"] for row in rows]


def load_candidates(
    conn: sqlite3.Connection, ids: list[str], versions: dict[str, CorpusVersion], missing_scope: dict,
) -> list[Candidate]:
    if not ids:
        return []
    rows = {
        row["id"]: row for row in conn.execute(
            """SELECT c.*, b.text, b.version_id, b.locator
            FROM chunks c JOIN blocks b ON c.block_id = b.id
            WHERE c.id IN (SELECT value FROM json_each(?))""",
            (json_dump(ids),),
        )
    }
    candidates = []
    for chunk_id in ids:
        row = rows.get(chunk_id)
        if row is None or row["version_id"] not in versions:
            continue
        key = row["version_id"]
        candidates.append(Candidate(
            id=chunk_id,
            block_id=row["block_id"],
            start=row["start_offset"],
            text=row["text"][row["start_offset"]:row["end_offset"]],
            locator=json.loads(row["locator"]),
            version=versions[key],
            missing_scope=missing_scope[key],
        ))
    return candidates


def rank_ids(lexical: list[str], semantic: list[str]) -> list[str]:
    score: Counter = Counter()
    for weight, ids in ((1.2, lexical), (1.0, semantic)):
        for position, chunk_id in enumerate(ids, 1):
            score[chunk_id] += weight / (20 + position)
    return [chunk_id for chunk_id, _ in score.most_common()]


def expand_evidence(
    conn: sqlite3.Connection, primary: list[Candidate], versions: dict[str, CorpusVersion],
    missing_scope: dict, terms: list[str],
) -> list[Candidate]:
    if not primary:
        return []
    related = {
        related_id for item in primary[:4] for related_id in item.version.metadata["related_to"]
        if related_id in versions
    }
    links = lexical_ids(conn, terms, {key: versions[key] for key in sorted(related)}, limit=3)
    anchor_ids = [candidate.block_id for candidate in primary[:3]]
    neighbors = [
        row["id"] for row in conn.execute(
            """SELECT c.id FROM blocks anchor
            JOIN blocks neighbor ON neighbor.version_id = anchor.version_id
              AND neighbor.ordinal BETWEEN anchor.ordinal - 1 AND anchor.ordinal + 1
              AND neighbor.ordinal != anchor.ordinal
            JOIN chunks c ON c.block_id = neighbor.id AND c.start_offset = 0
            WHERE anchor.id IN (SELECT value FROM json_each(?))
            GROUP BY c.id
            ORDER BY neighbor.ordinal, c.id LIMIT 6""",
            (json_dump(anchor_ids),),
        )
    ]
    additional = load_candidates(conn, list(dict.fromkeys(links + neighbors)), versions, missing_scope)
    primary_ids = {candidate.id for candidate in primary}
    return [
        item for item in additional
        if item.id not in primary_ids and (item.id in links or set(concepts(item.text)).intersection(terms))
    ]


def select_evidence(primary: list[Candidate], adjacent: list[Candidate], anchors: list[Candidate]) -> list[Candidate]:
    version_counts: Counter = Counter()
    visited_blocks = set()
    selected = []
    for candidate in anchors + primary[:5] + adjacent[:3] + primary[5:] + adjacent[3:]:
        if candidate.block_id in visited_blocks or version_counts[candidate.version.id] == 4:
            continue
        visited_blocks.add(candidate.block_id)
        version_counts[candidate.version.id] += 1
        selected.append(candidate)
        if len(selected) == CANDIDATE_LIMIT:
            break
    return selected


def search_terms(question: str, turns: list[str], context: dict) -> list[str]:
    scope = [value for value in context.values() if isinstance(value, str)]
    departments = context.get("departments") or []
    return query_terms(" ".join([question, *turns, *scope, *departments]))


def amount_applies(question: str, passage: str) -> bool:
    """Exclude an explicitly incompatible single ruble limit in the same contract rule."""
    if "договор" not in question.casefold() or "договор" not in passage.casefold():
        return True
    asked = list(QUESTION_AMOUNT.finditer(question))
    limits = list(LIMIT_AMOUNT.finditer(passage))
    if len(asked) != 1 or len(limits) != 1:
        return True

    def number(match: re.Match) -> float:
        return float("".join(match["sum"].split()).replace(",", "."))

    rule = limits[0]
    amount, limit = number(asked[0]), number(rule)
    operator = rule["operator"].casefold()
    if operator == "до":
        # At the boundary, preserve the passage when inclusion is implicit.
        return amount <= limit
    if operator == "не более":
        return amount <= limit
    if operator in {"от", "не менее"}:
        return amount >= limit
    return amount > limit


def topic_supported(question: str, passages: list[str], *, semantic_match: bool = False) -> bool:
    if semantic_match:
        return True
    terms = {term for term in query_terms(question) if not term.isdecimal()}
    support = terms.intersection({token for passage in passages for token in concepts(passage)})
    return bool(terms) and len(support) >= min(len(terms), max(2, ceil(len(terms) * .5)))
