"""
Test Quality Scorer.

Scores each generated test file on 4 dimensions:
  1. Assertion strength  (static analysis of test code)
  2. Branch coverage     (from coverage JSON report if available)
  3. Mutation kill rate  (from MutationReport if available)
  4. Flakiness penalty   (from flakiness detector scan)

Outputs a TestQualityScore with a letter grade.
"""
from __future__ import annotations
import ast
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from core.models.quality import TestQualityScore

if TYPE_CHECKING:
    from core.models.mutation import MutationReport

logger = logging.getLogger(__name__)


class QualityScorer:
    def score_file(
        self,
        test_file: str,
        source_file: str,
        workspace_dir: str,
        mutation_report: "MutationReport | None" = None,
        flakiness_score: float = 0.0,
        coverage_json_path: str | None = None,
    ) -> TestQualityScore:
        test_path = Path(test_file)
        if not test_path.exists():
            logger.warning("[quality] test file not found: %s", test_file)
            return TestQualityScore.compute(
                test_file=test_file, source_file=source_file,
                assertion_score=0, branch_coverage=0,
                mutation_kill_rate=0, flakiness_penalty=flakiness_score,
            )

        code = test_path.read_text(encoding="utf-8", errors="ignore")
        language = "python" if test_file.endswith(".py") else "typescript"

        assertion_score = self._score_assertions(code, language)
        branch_coverage = self._read_branch_coverage(source_file, coverage_json_path)
        mutation_kill_rate = (
            mutation_report.mutation_score if mutation_report else 0.0
        )

        score = TestQualityScore.compute(
            test_file=test_file,
            source_file=source_file,
            assertion_score=assertion_score,
            branch_coverage=branch_coverage,
            mutation_kill_rate=mutation_kill_rate,
            flakiness_penalty=flakiness_score,
        )
        logger.info("[quality] %s grade=%s composite=%.1f",
                    Path(test_file).name, score.grade, score.composite_score)
        return score

    # ── Assertion strength ───────────────────────────────────────────────────

    def _score_assertions(self, code: str, language: str) -> float:
        if language == "python":
            return self._score_python_assertions(code)
        return self._score_typescript_assertions(code)

    def _score_python_assertions(self, code: str) -> float:
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return 0.0

        test_functions = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test")
        ]
        if not test_functions:
            return 0.0

        total_assertions = sum(
            1 for n in ast.walk(tree)
            if isinstance(n, ast.Assert)
            or (isinstance(n, ast.Expr)
                and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Attribute)
                and n.value.func.attr.startswith("assert"))
        )
        assertions_per_test = total_assertions / len(test_functions)
        # 4 assertions/test → perfect 100
        return min(100.0, assertions_per_test * 25)

    def _score_typescript_assertions(self, code: str) -> float:
        test_count = len(re.findall(r"\bit\s*\(|test\s*\(", code))
        if test_count == 0:
            return 0.0
        assertion_count = len(re.findall(r"\bexpect\s*\(", code))
        assertions_per_test = assertion_count / test_count
        return min(100.0, assertions_per_test * 25)

    # ── Branch coverage ──────────────────────────────────────────────────────

    def _read_branch_coverage(
        self, source_file: str, coverage_json_path: str | None
    ) -> float:
        if not coverage_json_path:
            return 0.0
        try:
            import json
            data = json.loads(Path(coverage_json_path).read_text())
            # pytest-cov JSON format
            for key, file_data in data.get("files", {}).items():
                if source_file in key or Path(source_file).name in key:
                    summary = file_data.get("summary", {})
                    pct = summary.get("percent_covered", 0)
                    return float(pct)
            # jest coverage-summary.json format
            for key, file_data in data.items():
                if source_file in key or Path(source_file).name in key:
                    branches = file_data.get("branches", {})
                    pct = branches.get("pct", 0)
                    return float(pct)
        except Exception as exc:
            logger.debug("[quality] coverage read failed: %s", exc)
        return 0.0
