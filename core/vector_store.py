from __future__ import annotations
import hashlib
import os
from pathlib import Path
import chromadb
from chromadb.utils import embedding_functions


class CodeVectorStore:
    SUPPORTED_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx"}

    def __init__(self, persist_directory: str | None = None):
        persist_dir = persist_directory or os.environ.get("CHROMA_PERSIST_DIR", ".chroma")
        self._client = chromadb.PersistentClient(path=persist_dir)
        self._ef = embedding_functions.DefaultEmbeddingFunction()
        # Collection 1 — source code
        self._collection = self._client.get_or_create_collection(
            name="codebase",
            embedding_function=self._ef,
        )
        # Collection 2 — test-to-source relationships
        self._test_rel = self._client.get_or_create_collection(
            name="test_relationships",
            embedding_function=self._ef,
        )
        # Collection 3 — failure patterns and their fixes
        self._failures = self._client.get_or_create_collection(
            name="failure_patterns",
            embedding_function=self._ef,
        )

    # ── Codebase collection ──────────────────────────────────────────────────

    def index_file(self, file_path: str, content: str, metadata: dict | None = None) -> None:
        doc_id = file_path.replace("/", "__").replace("\\", "__")
        self._collection.upsert(
            ids=[doc_id],
            documents=[content],
            metadatas=[{**(metadata or {}), "file_path": file_path}],
        )

    def search(self, query: str, n_results: int = 5) -> list[dict]:
        results = self._collection.query(query_texts=[query], n_results=n_results)
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        return [{"content": doc, **meta} for doc, meta in zip(docs, metas)]

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
        self._test_rel.upsert(
            ids=[doc_id],
            documents=[test_code],
            metadatas=[{
                **(metadata or {}),
                "source_file": source_file,
                "test_file": test_file,
            }],
        )

    def search_related_tests(self, source_file: str, n_results: int = 3) -> list[dict]:
        """Retrieve previously generated tests for files similar to source_file."""
        try:
            results = self._test_rel.query(
                query_texts=[f"tests for {source_file}"],
                n_results=n_results,
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [{"content": doc, **meta} for doc, meta in zip(docs, metas)]
        except Exception:
            return []

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
        self._failures.upsert(
            ids=[doc_id],
            documents=[doc],
            metadatas=[{
                **(metadata or {}),
                "test_name": test_name,
                "root_cause": root_cause,
            }],
        )

    def search_failure_patterns(self, query: str, n_results: int = 3) -> list[dict]:
        """Find similar past failures and how they were resolved."""
        try:
            results = self._failures.query(query_texts=[query], n_results=n_results)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [{"content": doc, **meta} for doc, meta in zip(docs, metas)]
        except Exception:
            return []
