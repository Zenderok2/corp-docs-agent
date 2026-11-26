import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys
import tempfile

from pydantic import ValidationError

from corp_docs_agent.core.settings import Settings
from corp_docs_agent.domain.models import Catalog, SourceEntry
from corp_docs_agent.ingestion.files import DOCX_LOCATOR_REVISION, FORMATS, ImportFailure, chunk_offsets, parse_file
from corp_docs_agent.persistence.store import Store, corpus_revision, json_dump, now
from corp_docs_agent.retrieval.terms import index_text


def version_id(entry: SourceEntry) -> str:
    return f"{entry.key}@{entry.version}"


def heading_index(locator: dict) -> str:
    headings = locator.get("section_path", []) + [locator.get("column_header", "")]
    return index_text(" ".join(headings))


def import_entry(settings: Settings, store: Store, root: Path, entry: SourceEntry) -> dict:
    reference = version_id(entry)
    try:
        source = (root / entry.path).resolve(strict=True)
        source.relative_to(root)
        suffix = source.suffix.lower()
        if suffix not in FORMATS:
            raise ImportFailure("unsupported_format")
        with source.open("rb") as stream:
            limit = settings.max_upload_mb * 1024 * 1024
            content = stream.read(limit + 1)
    except (ValueError, OSError):
        raise ImportFailure("file_unavailable") from None
    if len(content) > limit:
        raise ImportFailure("file_too_large")
    checksum = sha256(content).hexdigest()
    excluded = {"path"}
    excluded.update(field for field in ("publication_confirmed_on", "revision_label") if getattr(entry, field) is None)
    properties = entry.model_dump(mode="json", exclude=excluded)
    if suffix == ".docx":
        properties["locator_revision"] = DOCX_LOCATOR_REVISION
    metadata = json_dump(properties)
    with store.connect() as conn:
        existing = conn.execute("SELECT * FROM document_versions WHERE id = ?", (reference,)).fetchone()
    if existing and checksum != existing["sha256"]:
        raise ImportFailure("version_content_changed")
    if existing and suffix != existing["file_suffix"]:
        raise ImportFailure("version_format_changed")

    originals = settings.storage / "originals"
    originals.mkdir(parents=True, exist_ok=True)
    original = originals / (checksum + suffix)
    if not original.exists():
        with tempfile.NamedTemporaryFile(dir=originals, suffix=suffix, delete=False) as stream:
            staged = Path(stream.name)
        try:
            staged.write_bytes(content)
            staged.replace(original)
        finally:
            staged.unlink(missing_ok=True)

    if existing and existing["metadata"] == metadata:
        return {"id": reference, "status": "unchanged", "warnings": json.loads(existing["warnings"])}

    upgrade_locators = existing and suffix == ".docx" and json.loads(existing["metadata"]).get("locator_revision") != DOCX_LOCATOR_REVISION
    blocks, warnings = parse_file(original) if not existing or upgrade_locators else ([], json.loads(existing["warnings"]))
    with store.connect(write=True) as conn:
        # Imports are administrative CLI jobs. This guard also covers concurrent invocations.
        confirmed = conn.execute("SELECT sha256, file_suffix, metadata FROM document_versions WHERE id = ?", (reference,)).fetchone()
        if confirmed and (confirmed["sha256"], confirmed["file_suffix"]) != (checksum, suffix):
            raise ImportFailure("version_content_changed")
        if confirmed and confirmed["metadata"] == metadata:
            return {"id": reference, "status": "unchanged", "warnings": warnings}
        conn.execute("INSERT OR IGNORE INTO documents VALUES (?)", (entry.key,))
        if confirmed:
            if upgrade_locators:
                old_blocks = conn.execute("SELECT id, text FROM blocks WHERE version_id = ? ORDER BY ordinal", (reference,)).fetchall()
                if [row["text"] for row in old_blocks] != [block.text for block in blocks]:
                    raise ImportFailure("locator_upgrade_text_changed")
                for row, block in zip(old_blocks, blocks, strict=True):
                    conn.execute("UPDATE blocks SET locator = ? WHERE id = ?", (json_dump(block.locator), row["id"]))
                    conn.execute(
                        "UPDATE chunks_fts SET heading = ? WHERE chunk_id IN (SELECT id FROM chunks WHERE block_id = ?)",
                        (heading_index(block.locator), row["id"]),
                    )
            conn.execute("UPDATE document_versions SET metadata = ? WHERE id = ?", (metadata, reference))
            conn.execute(
                """UPDATE chunks_fts SET title = ? WHERE chunk_id IN (
                    SELECT c.id FROM chunks c JOIN blocks b ON c.block_id = b.id WHERE b.version_id = ?
                )""",
                (index_text(entry.title), reference),
            )
        else:
            conn.execute(
                "INSERT INTO document_versions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (reference, entry.key, checksum, suffix, metadata, json_dump(warnings), now()),
            )
            title = index_text(entry.title)
            for ordinal, block in enumerate(blocks, 1):
                block_id = f"{reference}:{ordinal}"
                conn.execute(
                    "INSERT INTO blocks VALUES (?, ?, ?, ?, ?)",
                    (block_id, reference, ordinal, block.text, json_dump(block.locator)),
                )
                heading = heading_index(block.locator)
                for start, end in chunk_offsets(block.text):
                    chunk_id = f"{block_id}:{start}"
                    conn.execute("INSERT INTO chunks VALUES (?, ?, ?, ?)", (chunk_id, block_id, start, end))
                    conn.execute(
                        "INSERT INTO chunks_fts VALUES (?, ?, ?, ?)",
                        (chunk_id, index_text(block.text[start:end]), heading, title),
                    )
        revision = str(int(corpus_revision(conn)) + 1)
        conn.execute("UPDATE app_meta SET value = ? WHERE key = 'corpus_revision'", (revision,))
    return {"id": reference, "status": "updated" if existing else "imported", "blocks": len(blocks), "warnings": warnings}


def import_catalog(settings: Settings, catalog_path: Path) -> list[dict]:
    catalog_path = catalog_path.resolve()
    catalog = Catalog.model_validate_json(catalog_path.read_bytes())
    store = Store(settings.database)
    store.initialize()
    with store.connect() as conn:
        catalog_sources = {row["id"]: json.loads(row["metadata"]) for row in conn.execute("SELECT id, metadata FROM document_versions")}
    catalog_sources.update({version_id(entry): entry.model_dump(mode="json") for entry in catalog.sources})
    for reference, metadata in catalog_sources.items():
        if set(metadata["supersedes"] + metadata["related_to"]).difference(catalog_sources):
            raise ImportFailure(f"unknown_relation_target:{reference}")

    pending = set(catalog_sources)
    while pending:
        leaves = {reference for reference in pending if not pending.intersection(catalog_sources[reference]["supersedes"])}
        if not leaves:
            raise ImportFailure("cyclic_supersession")
        pending -= leaves

    result = []
    for entry in catalog.sources:
        try:
            result.append(import_entry(settings, store, catalog_path.parent, entry))
        except (ImportFailure, OSError, sqlite3.Error) as error:
            code = error.code if isinstance(error, ImportFailure) else "storage_error"
            result.append({"id": version_id(entry), "status": "failed", "code": code})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the team-controlled document index")
    subcommands = parser.add_subparsers(dest="command", required=True)
    load = subcommands.add_parser("import")
    load.add_argument("catalog", type=Path)
    subcommands.add_parser("rebuild-vectors")
    args = parser.parse_args()
    try:
        settings = Settings.from_env()
        if args.command == "rebuild-vectors":
            from corp_docs_agent.providers.vectors import VectorIndex

            print(json_dump(VectorIndex(settings).rebuild(Store(settings.database))))
            return
        report = import_catalog(settings, args.catalog)
        print(json_dump(report))
        if any(item["status"] == "failed" for item in report):
            sys.exit(1)
    except ValidationError as error:
        print(json_dump({"errors": error.errors(include_input=False, include_url=False, include_context=False)}), file=sys.stderr)
        sys.exit(1)
    except (ImportFailure, OSError, RuntimeError) as error:
        code = error.code if isinstance(error, ImportFailure) else "operation_failed"
        print(json_dump({"error": code}), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
