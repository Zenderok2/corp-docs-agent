from hashlib import sha256
from math import isfinite
from uuid import uuid4

import httpx

from corp_docs_agent.core.settings import Settings
from corp_docs_agent.persistence.store import Store, corpus_revision, json_dump, now


QUERY_PREFIX = "Instruct: Retrieve corporate procedure passages that directly answer the question.\nQuery: "
BATCH_SIZE = 8


class VectorIndex:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(timeout=httpx.Timeout(18, connect=3.0), trust_env=False)
        self.collections = settings.chroma_url + "/api/v2/tenants/default_tenant/databases/default_database/collections"
        self.profile = sha256(json_dump([
            settings.embedding_profile, settings.embedding_model, settings.embedding_dimensions,
            QUERY_PREFIX, "title-and-chunk-v1",
        ]).encode()).hexdigest()

    def _request(self, method: str, url: str, **kwargs):
        response = self.client.request(method, url, **kwargs)
        response.raise_for_status()
        return response.json()

    def _embed(self, texts: list[str]) -> tuple[list[list[float]], int | None]:
        result = self._request("POST", self.settings.embedding_url + "/embeddings", json={
            "model": self.settings.embedding_model, "input": texts, "encoding_format": "float",
        })
        items = sorted(result["data"], key=lambda item: item["index"])
        vectors = [item["embedding"] for item in items]
        if [item["index"] for item in items] != list(range(len(texts))) or any(
            len(vector) != self.settings.embedding_dimensions
            or not all(type(value) in {int, float} and isfinite(value) for value in vector)
            or not any(vector)
            for vector in vectors
        ):
            raise ValueError("Invalid embedding shape or values")
        tokens = (result.get("usage") or {}).get("prompt_tokens")
        return vectors, tokens if type(tokens) is int and tokens >= 0 else None

    def _generation(self, store: Store, revision: str | None = None):
        with store.connect() as conn:
            current = corpus_revision(conn)
            if revision and revision != current:
                return None
            return conn.execute(
                """SELECT g.* FROM index_generations g
                JOIN app_meta m ON m.key = 'active_generation' AND m.value = g.id
                WHERE g.corpus_revision = ? AND g.profile = ? AND g.dimensions = ?""",
                (current, self.profile, self.settings.embedding_dimensions),
            ).fetchone()

    def ready(self, store: Store) -> bool:
        generation = self._generation(store)
        if generation is None:
            return False
        try:
            status = self.client.get(self.settings.embedding_url.removesuffix("/v1") + "/health", timeout=2)
            return status.is_success and self._request(
                "GET", f"{self.collections}/{generation['collection_id']}/count", timeout=2,
            ) == generation["chunk_count"]
        except (httpx.HTTPError, ValueError):
            return False

    def search(self, store: Store, revision: str, version_ids: list[str], query: str) -> tuple[list[str], set[str], dict, str]:
        generation = self._generation(store, revision)
        if generation is None:
            return [], set(), {}, "index_not_ready"
        usage = {"embedding_calls": 1, "embedding_model": self.settings.embedding_model}
        try:
            vectors, tokens = self._embed([QUERY_PREFIX + query])
            if tokens is not None:
                usage["embedding_tokens"] = tokens
            result = self._request(
                "POST", f"{self.collections}/{generation['collection_id']}/query",
                json={
                    "query_embeddings": vectors,
                    "n_results": min(30, generation["chunk_count"]),
                    "where": {"version_id": {"$in": version_ids}},
                    "include": ["distances"],
                },
            )
            hits = list(zip(result["ids"][0], result["distances"][0], strict=True))
            ids = [item for item, distance in hits if -1e-6 <= distance <= 0.58]
            strong = {item for item, distance in hits if -1e-6 <= distance <= 0.26}
            return ids, strong, usage, "ready"
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, OverflowError):
            return [], set(), usage, "lexical_fallback"

    def rebuild(self, store: Store) -> dict:
        store.initialize()
        with store.connect() as conn:
            revision = corpus_revision(conn)
            rows = conn.execute(
                """SELECT c.id, b.version_id, json_extract(v.metadata, '$.title') AS title,
                substr(b.text, c.start_offset + 1, c.end_offset - c.start_offset) AS text
                FROM chunks c JOIN blocks b ON b.id = c.block_id
                JOIN document_versions v ON v.id = b.version_id
                WHERE json_extract(v.metadata, '$.status') != 'withdrawn'
                ORDER BY c.id"""
            ).fetchall()
        if not rows:
            raise RuntimeError("Cannot index an empty corpus")
        generation = uuid4().hex
        collection_id = None
        try:
            collection = self._request("POST", self.collections, json={
                "name": f"corp-docs-{generation}",
                "configuration": {"hnsw": {"space": "cosine"}},
                "metadata": {"profile": self.profile, "corpus_revision": revision},
            })
            collection_id = collection["id"]
            for offset in range(0, len(rows), BATCH_SIZE):
                batch = rows[offset:offset + BATCH_SIZE]
                embeddings, _ = self._embed([f"{item['title']}\n{item['text']}" for item in batch])
                self._request("POST", f"{self.collections}/{collection_id}/add", json={
                    "ids": [item["id"] for item in batch],
                    "embeddings": embeddings,
                    "metadatas": [{"version_id": item["version_id"]} for item in batch],
                })
            if self._request("GET", f"{self.collections}/{collection_id}/count") != len(rows):
                raise ValueError("Index count mismatch")
            sample = self._request("POST", f"{self.collections}/{collection_id}/get", json={
                "ids": list(dict.fromkeys([rows[0]["id"], rows[-1]["id"]])), "include": ["embeddings"],
            })
            if set(sample["ids"]) != {rows[0]["id"], rows[-1]["id"]} or any(
                len(vector) != self.settings.embedding_dimensions for vector in sample["embeddings"]
            ):
                raise ValueError("Index validation failed")
            with store.connect(write=True) as conn:
                if corpus_revision(conn) != revision:
                    raise ValueError("Corpus changed during indexing")
                conn.execute(
                    "INSERT INTO index_generations VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (generation, revision, self.profile, self.settings.embedding_dimensions, collection_id, len(rows), now()),
                )
                conn.execute(
                    """INSERT INTO app_meta VALUES ('active_generation', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value""", (generation,),
                )
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            if collection_id:
                try:
                    self.client.delete(f"{self.collections}/corp-docs-{generation}")
                except httpx.HTTPError:
                    pass
            raise RuntimeError("Vector rebuild failed; active generation unchanged") from None
        return {"id": generation, "corpus_revision": revision, "chunks": len(rows), "profile": self.profile}
