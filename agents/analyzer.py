from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import git

logger = logging.getLogger(__name__)


@dataclass
class FileChange:
    path: str
    language: str
    change_type: str  # 'added' | 'modified' | 'deleted' | 'renamed'
    diff: str
    full_content: str
    functions_changed: list[str] = field(default_factory=list)


_LANGUAGE_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
}

_FUNCTION_PATTERNS = {
    "python": re.compile(r"^\+\s*(?:async\s+)?def\s+(\w+)\s*\(", re.MULTILINE),
    "typescript": re.compile(r"^\+\s*(?:async\s+)?(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\()", re.MULTILINE),
    "javascript": re.compile(r"^\+\s*(?:async\s+)?(?:function\s+(\w+)|(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\()", re.MULTILINE),
}


class AnalyzerAgent:
    def analyze_repo(self, repo_path: str, base_sha: str, head_sha: str) -> list[FileChange]:
        repo = git.Repo(repo_path)
        base = repo.commit(base_sha)
        head = repo.commit(head_sha)
        diffs = base.diff(head)

        changes: list[FileChange] = []
        for diff in diffs:
            path = diff.b_path or diff.a_path
            lang = self.detect_language(path)
            if lang == "unknown":
                continue

            change_type = (
                "added" if diff.new_file else
                "deleted" if diff.deleted_file else
                "renamed" if diff.renamed_file else
                "modified"
            )

            diff_text = diff.diff.decode("utf-8", errors="ignore") if diff.diff else ""

            full_content = ""
            if not diff.deleted_file and diff.b_blob:
                full_content = diff.b_blob.data_stream.read().decode("utf-8", errors="ignore")

            functions = self.extract_changed_functions(diff_text, lang)

            changes.append(FileChange(
                path=path,
                language=lang,
                change_type=change_type,
                diff=diff_text,
                full_content=full_content,
                functions_changed=functions,
            ))

        return changes

    def analyze_from_github(self, pr_number: int, commit_sha: str, github_client) -> list[FileChange]:
        """Fetch PR diffs and file content from GitHub and return FileChange objects."""
        logger.info("Analyzer.analyze_from_github — pr=%s sha=%s", pr_number, commit_sha[:12])
        raw_diffs = github_client.get_pr_diff(pr_number)
        logger.info("PR #%s — %d changed file(s) from GitHub", pr_number, len(raw_diffs))
        changes: list[FileChange] = []
        for d in raw_diffs:
            path = d["filename"]
            lang = self.detect_language(path)
            if lang == "unknown":
                logger.debug("Skipping unsupported file: %s", path)
                continue
            change_type = d["status"]  # 'added' | 'modified' | 'removed' | 'renamed'
            if change_type == "removed":
                change_type = "deleted"
            diff_text = d.get("patch", "")
            full_content = ""
            if change_type != "deleted":
                full_content = github_client.get_file_content(path, ref=commit_sha)
            functions = self.extract_changed_functions(diff_text, lang)
            logger.info("  [%s] %s lang=%s functions=%s content_chars=%d",
                        change_type, path, lang, functions or "[]", len(full_content))
            changes.append(FileChange(
                path=path,
                language=lang,
                change_type=change_type,
                diff=diff_text,
                full_content=full_content,
                functions_changed=functions,
            ))
        logger.info("Analyzer done — %d actionable file(s)", len(changes))
        return changes

    def analyze_files(self, file_paths: list[str], repo_root: str = ".") -> list[FileChange]:
        """Analyze local files directly (without git diff — used for initial indexing)."""
        changes: list[FileChange] = []
        for path in file_paths:
            abs_path = Path(repo_root) / path
            if not abs_path.exists():
                continue
            lang = self.detect_language(path)
            if lang == "unknown":
                continue
            content = abs_path.read_text(encoding="utf-8", errors="ignore")
            changes.append(FileChange(
                path=path,
                language=lang,
                change_type="modified",
                diff="",
                full_content=content,
            ))
        return changes

    def get_current_branch(self, repo_path: str = ".") -> str:
        """Return the active branch name of a local git repository."""
        repo = git.Repo(repo_path)
        return repo.active_branch.name

    def list_local_branches(self, repo_path: str = ".") -> list[str]:
        """Return all local branch names in a git repository."""
        repo = git.Repo(repo_path)
        return [b.name for b in repo.branches]

    def detect_language(self, file_path: str) -> str:
        return _LANGUAGE_MAP.get(Path(file_path).suffix.lower(), "unknown")

    def extract_changed_functions(self, diff: str, language: str) -> list[str]:
        pattern = _FUNCTION_PATTERNS.get(language)
        if not pattern:
            return []
        names: list[str] = []
        for match in pattern.finditer(diff):
            # Groups vary by pattern; find the first non-None group
            name = next((g for g in match.groups() if g), None)
            if name:
                names.append(name)
        return list(dict.fromkeys(names))  # deduplicate preserving order
