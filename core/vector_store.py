from __future__ import annotations
import hashlib
import logging
import os
from pathlib import Path
import re

try:
    import chromadb
    from chromadb.utils import embedding_functions
    _CHROMADB_AVAILABLE = True
except Exception:
    chromadb = None
    embedding_functions = None
    _CHROMADB_AVAILABLE = False


logger = logging.getLogger(__name__)


class CodeVectorStore:
    SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}

    def __init__(self, persist_directory: str | None = None):
        persist_dir = persist_directory or os.environ.get("CHROMA_PERSIST_DIR", ".chroma")
        self._use_chromadb = _CHROMADB_AVAILABLE
        self._collection = None
        self._test_rel = None
        self._failures = None

        # In-memory fallback stores used when chromadb is unavailable.
        self._code_docs: dict[str, dict] = {}
        self._test_docs: dict[str, dict] = {}
        self._failure_docs: dict[str, dict] = {}

        if self._use_chromadb:
            try:
                self._client = chromadb.PersistentClient(path=persist_dir)
                self._ef = embedding_functions.DefaultEmbeddingFunction()
                # Collection 1 - source code
                self._collection = self._client.get_or_create_collection(
                    name="codebase",
                    embedding_function=self._ef,
                )
                # Collection 2 - test-to-source relationships
                self._test_rel = self._client.get_or_create_collection(
                    name="test_relationships",
                    embedding_function=self._ef,
                )
                # Collection 3 - failure patterns and their fixes
                self._failures = self._client.get_or_create_collection(
                    name="failure_patterns",
                    embedding_function=self._ef,
                )
            except Exception as exc:
                logger.warning("Chroma init failed, using in-memory fallback: %s", exc)
                self._use_chromadb = False

        if not self._use_chromadb:
            logger.warning("chromadb not available; vector store is running in in-memory fallback mode")

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        parts = [p for p in re.split(r"[^a-zA-Z0-9_]+", text.lower()) if p]
        return list(dict.fromkeys(parts))

    def _search_in_memory(self, store: dict[str, dict], query: str, n_results: int) -> list[dict]:
        query_tokens = self._tokenize(query)
        scored: list[tuple[int, str, str, dict]] = []
        for doc_id, payload in store.items():
            content = payload.get("content", "")
            metadata = payload.get("metadata", {})
            metadata_blob = " ".join(str(v) for v in metadata.values())
            haystack = f"{content} {metadata_blob}".lower()
            score = sum(1 for token in query_tokens if token in haystack)
            if query_tokens and score == 0:
                continue
            scored.append((score, doc_id, content, metadata))

        scored.sort(key=lambda x: (-x[0], x[1]))
        return [
            {"content": content, **metadata}
            for _, _, content, metadata in scored[: max(0, n_results)]
        ]

    # ── Codebase collection ──────────────────────────────────────────────────

    def index_file(self, file_path: str, content: str, metadata: dict | None = None) -> None:
        doc_id = file_path.replace("/", "__").replace("\\", "__")
        merged_meta = {**(metadata or {}), "file_path": file_path}

        if self._use_chromadb and self._collection is not None:
            try:
                self._collection.upsert(
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[merged_meta],
                )
                return
            except Exception as exc:
                logger.warning("Chroma upsert failed for codebase, switching to fallback: %s", exc)
                self._use_chromadb = False

        self._code_docs[doc_id] = {"content": content, "metadata": merged_meta}

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        if self._use_chromadb and self._collection is not None:
            try:
                results = self._collection.query(query_texts=[query], n_results=n_results)
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                return [{"content": doc, **meta} for doc, meta in zip(docs, metas)]
            except Exception as exc:
                logger.warning("Chroma query failed for codebase, switching to fallback: %s", exc)
                self._use_chromadb = False
        return self._search_in_memory(self._code_docs, query, n_results)

    def index_directory(self, directory: str, extensions: list[str] | None = None) -> int:
        exts = set(extensions) if extensions else self.SUPPORTED_EXTENSIONS
        count = 0
        for path in Path(directory).rglob("*"):
            if path.suffix in exts and path.is_file():
                try:
                    content = path.read_text(encoding="utf-8", errors="ignore")
                    self.index_file(str(path), content)
                    count += 1
                except Exception:
                    pass
        return count

    # ── Test relationship collection ─────────────────────────────────────────

    def index_test_relationship(
        self,
        source_file: str,
        test_file: str,
        test_code: str,
        metadata: dict | None = None,
    ) -> None:
        """Index generated test code linked to its source file."""
        doc_id = test_file.replace("/", "__").replace("\\", "__")
        merged_meta = {
            **(metadata or {}),
            "source_file": source_file,
            "test_file": test_file,
        }

        if self._use_chromadb and self._test_rel is not None:
            try:
                self._test_rel.upsert(
                    ids=[doc_id],
                    documents=[test_code],
                    metadatas=[merged_meta],
                )
                return
            except Exception as exc:
                logger.warning("Chroma upsert failed for test relationships, switching to fallback: %s", exc)
                self._use_chromadb = False

        self._test_docs[doc_id] = {"content": test_code, "metadata": merged_meta}

    def search_related_tests(self, source_file: str, n_results: int = 3) -> list[dict]:
        """Retrieve previously generated tests for files similar to source_file."""
        query = f"tests for {source_file}"
        if self._use_chromadb and self._test_rel is not None:
            try:
                results = self._test_rel.query(query_texts=[query], n_results=n_results)
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                return [{"content": doc, **meta} for doc, meta in zip(docs, metas)]
            except Exception as exc:
                logger.warning("Chroma query failed for test relationships, switching to fallback: %s", exc)
                self._use_chromadb = False
        return self._search_in_memory(self._test_docs, query, n_results)

    # ── Failure pattern collection ───────────────────────────────────────────

    def index_failure_pattern(
        self,
        test_name: str,
        error: str,
        root_cause: str,
        fix_suggestion: str,
        metadata: dict | None = None,
    ) -> None:
        """Store a failure and its resolution for future similarity lookup."""
        doc_id = hashlib.sha1(f"{test_name}:{error[:80]}".encode()).hexdigest()
        doc = (
            f"Test: {test_name}\n"
            f"Error: {error[:400]}\n"
            f"Root cause: {root_cause}\n"
            f"Fix: {fix_suggestion}"
        )
        merged_meta = {
            **(metadata or {}),
            "test_name": test_name,
            "root_cause": root_cause,
        }

        if self._use_chromadb and self._failures is not None:
            try:
                self._failures.upsert(
                    ids=[doc_id],
                    documents=[doc],
                    metadatas=[merged_meta],
                )
                return
            except Exception as exc:
                logger.warning("Chroma upsert failed for failure patterns, switching to fallback: %s", exc)
                self._use_chromadb = False

        self._failure_docs[doc_id] = {"content": doc, "metadata": merged_meta}

    def search_failure_patterns(self, query: str, n_results: int = 3) -> list[dict]:
        """Find similar past failures and how they were resolved."""
        if self._use_chromadb and self._failures is not None:
            try:
                results = self._failures.query(query_texts=[query], n_results=n_results)
                docs = results.get("documents", [[]])[0]
                metas = results.get("metadatas", [[]])[0]
                return [{"content": doc, **meta} for doc, meta in zip(docs, metas)]
            except Exception as exc:
                logger.warning("Chroma query failed for failure patterns, switching to fallback: %s", exc)
                self._use_chromadb = False
        return self._search_in_memory(self._failure_docs, query, n_results)
