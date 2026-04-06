from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Protocol

import git

from core.ast_analyzer import ASTAnalyzer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class ChangeType(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"

    @classmethod
    def from_github_status(cls, status: str) -> ChangeType:
        _MAP = {
            "added": cls.ADDED,
            "modified": cls.MODIFIED,
            "removed": cls.DELETED,
            "renamed": cls.RENAMED,
            # GitHub also sends "copied", "changed", "unchanged"
            "copied": cls.ADDED,
            "changed": cls.MODIFIED,
            "unchanged": cls.MODIFIED,
        }
        try:
            return _MAP[status]
        except KeyError:
            logger.warning("Unknown GitHub change status '%s', defaulting to MODIFIED", status)
            return cls.MODIFIED


@dataclass
class FileChange:
    path: str
    language: str
    change_type: ChangeType
    diff: str
    full_content: str
    functions_changed: list[str] = field(default_factory=list)
    classes_changed: list[str] = field(default_factory=list)
    call_graph: dict[str, list[str]] = field(default_factory=dict)
    imports: list[str] = field(default_factory=list)
    complexity_score: float | None = None
    additions: int = 0
    deletions: int = 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}

_TEST_FILE_PATTERNS = re.compile(
    r"([\\/]__tests__[\\/]|\.test\.|\.spec\.|[\\/]test_|_test\.py$)"
)

_FUNCTION_PATTERNS = {
    "python": re.compile(
        r"^\+\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE
    ),
    "typescript": re.compile(
        r"^\+\s*(?:async\s+)?(?:"
        r"function\s+(\w+)"                         # function declarations
        r"|(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\("  # arrow / function expressions
        r"|(\w+)\s*\([^)]*\)\s*(?::\s*\w+)?\s*\{"   # class method declarations
        r")",
        re.MULTILINE,
    ),
    "javascript": re.compile(
        r"^\+\s*(?:async\s+)?(?:"
        r"function\s+(\w+)"
        r"|(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\("
        r"|(\w+)\s*\([^)]*\)\s*\{"
        r")",
        re.MULTILINE,
    ),
}


# ---------------------------------------------------------------------------
# Protocol for AST analysis (testability)
# ---------------------------------------------------------------------------


class ASTAnalyzerProtocol(Protocol):
    def analyze(self, content: str, language: str) -> object: ...


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AnalyzerAgent:
    def __init__(
        self,
        ast_analyzer: ASTAnalyzerProtocol | None = None,
        skip_test_files: bool = True,
    ):
        self._ast = ast_analyzer or ASTAnalyzer()
        self._skip_test_files = skip_test_files

    # -- public entry points ------------------------------------------------

    def analyze_repo(
        self, repo_path: str, base_sha: str, head_sha: str
    ) -> list[FileChange]:
        repo = git.Repo(repo_path)
        base = repo.commit(base_sha)
        head = repo.commit(head_sha)
        diffs = base.diff(head)

        changes: list[FileChange] = []
        for diff in diffs:
            path = diff.b_path or diff.a_path

            if not self._should_process(path):
                continue

            lang = self.detect_language(path)
            change_type = (
                ChangeType.ADDED   if diff.new_file    else
                ChangeType.DELETED if diff.deleted_file else
                ChangeType.RENAMED if diff.renamed_file else
                ChangeType.MODIFIED
            )

            diff_text = self._safe_decode(diff.diff, path) if diff.diff else ""

            full_content = ""
            if change_type != ChangeType.DELETED and diff.b_blob:
                full_content = self._safe_decode(
                    diff.b_blob.data_stream.read(), path
                )

            changes.append(
                self._build_change(
                    path=path,
                    lang=lang,
                    change_type=change_type,
                    diff=diff_text,
                    full_content=full_content,
                )
            )
        return changes

    def analyze_from_github(
        self,
        pr_number: int,
        commit_sha: str,
        github_client,
    ) -> list[FileChange]:
        logger.info(
            "Analyzer.analyze_from_github — pr=%s sha=%s",
            pr_number,
            commit_sha[:12],
        )
        raw_diffs = github_client.get_pr_diff(pr_number)
        logger.info(
            "PR #%s — %d changed file(s) from GitHub", pr_number, len(raw_diffs)
        )

        changes: list[FileChange] = []
        for d in raw_diffs:
            path = d.get("filename")
            if not path:
                logger.warning("Skipping diff entry with no filename: %s", d)
                continue

            if not self._should_process(path):
                continue

            lang = self.detect_language(path)
            change_type = ChangeType.from_github_status(d.get("status", "modified"))

            diff_text = d.get("patch", "")
            full_content = ""
            if change_type != ChangeType.DELETED:
                full_content = github_client.get_file_content(
                    path, ref=commit_sha
                )

            change = self._build_change(
                path=path,
                lang=lang,
                change_type=change_type,
                diff=diff_text,
                full_content=full_content,
                additions=d.get("additions", 0),
                deletions=d.get("deletions", 0),
            )
            logger.info(
                "  [%s] %s lang=%s fns=%s complexity=%s content=%d chars",
                change_type.value,
                path,
                lang,
                change.functions_changed or "[]",
                f"{change.complexity_score:.1f}"
                if change.complexity_score is not None
                else "n/a",
                len(full_content),
            )
            changes.append(change)

        logger.info("Analyzer done — %d actionable file(s)", len(changes))
        return changes

    def analyze_files(
        self, file_paths: list[str], repo_root: str = "."
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        for path in file_paths:
            abs_path = Path(repo_root) / path
            if not abs_path.exists():
                logger.warning("File not found, skipping: %s", abs_path)
                continue
            if not self._should_process(path):
                continue
            lang = self.detect_language(path)
            content = abs_path.read_text(encoding="utf-8", errors="replace")
            changes.append(
                self._build_change(
                    path=path,
                    lang=lang,
                    change_type=ChangeType.MODIFIED,
                    diff="",
                    full_content=content,
                )
            )
        return changes

    # -- internals ----------------------------------------------------------

    def _should_process(self, path: str) -> bool:
        """Return False for unsupported languages and test files."""
        lang = self.detect_language(path)
        if lang == "unknown":
            logger.debug("Skipping unsupported file: %s", path)
            return False
        if self._skip_test_files and _TEST_FILE_PATTERNS.search(path):
            logger.debug("Skipping test file: %s", path)
            return False
        return True

    @staticmethod
    def _safe_decode(data: bytes, path: str) -> str:
        """Decode bytes to str, logging when replacement occurs."""
        text = data.decode("utf-8", errors="replace")
        if "\ufffd" in text:
            logger.warning(
                "File %s contains non-UTF-8 bytes (replaced with U+FFFD)", path
            )
        return text

    def _build_change(
        self,
        path: str,
        lang: str,
        change_type: ChangeType,
        diff: str,
        full_content: str,
        additions: int = 0,
        deletions: int = 0,
    ) -> FileChange:
        functions: list[str] = []
        classes: list[str] = []
        call_graph: dict[str, list[str]] = {}
        imports: list[str] = []
        complexity: float | None = None

        if full_content and change_type != ChangeType.DELETED:
            try:
                ast_result = self._ast.analyze(full_content, lang)
                functions = ast_result.functions
                classes = ast_result.classes
                call_graph = ast_result.call_graph
                imports = ast_result.imports
                complexity = ast_result.complexity
            except Exception as exc:
                logger.debug(
                    "AST analysis failed for %s (%s) — falling back to regex",
                    path,
                    exc,
                )
                functions = self.extract_changed_functions(diff, lang)

        if not functions and diff:
            functions = self.extract_changed_functions(diff, lang)

        return FileChange(
            path=path,
            language=lang,
            change_type=change_type,
            diff=diff,
            full_content=full_content,
            functions_changed=functions,
            classes_changed=classes,
            call_graph=call_graph,
            imports=imports,
            complexity_score=complexity,
            additions=additions,
            deletions=deletions,
        )

    def get_current_branch(self, repo_path: str = ".") -> str:
        repo = git.Repo(repo_path)
        return repo.active_branch.name

    def list_local_branches(self, repo_path: str = ".") -> list[str]:
        repo = git.Repo(repo_path)
        return [b.name for b in repo.branches]

    @staticmethod
    def detect_language(file_path: str) -> str:
        return _LANGUAGE_MAP.get(Path(file_path).suffix.lower(), "unknown")

    @staticmethod
    def extract_changed_functions(diff: str, language: str) -> list[str]:
        pattern = _FUNCTION_PATTERNS.get(language)
        if not pattern:
            return []
        names: list[str] = []
        for match in pattern.finditer(diff):
            name = next((g for g in match.groups() if g), None)
            if name:
                names.append(name)
        return list(dict.fromkeys(names))