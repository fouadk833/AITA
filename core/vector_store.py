from __future__ import annotations
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
        self._collection = self._client.get_or_create_collection(
            name="codebase",
            embedding_function=self._ef,
        )

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
