"""
Risk Scorer — scores each FileChange by complexity, criticality,
change size, and historical failure rate.

Output drives test depth allocation (critical → 15+ tests, low → 3 tests).
"""
from __future__ import annotations
import logging
from typing import TYPE_CHECKING

from core.models.risk import FileRisk

if TYPE_CHECKING:
    from agents.analyzer import FileChange
    from core.config import AITAConfig

logger = logging.getLogger(__name__)


class RiskScorer:
    def score_changes(
        self,
        file_changes: list["FileChange"],
        config: "AITAConfig",
        db_session=None,
    ) -> dict[str, FileRisk]:
        """
        Returns a dict keyed by file_path → FileRisk.
        db_session is optional; when provided, historical failure rates are queried.
        """
        results: dict[str, FileRisk] = {}

        for change in file_changes:
            if change.change_type == "deleted":
                continue

            historical_failures, historical_runs = self._query_history(
                change.path, db_session
            )

            change_size = getattr(change, "additions", 0) + getattr(change, "deletions", 0)

            risk = FileRisk.compute(
                file_path=change.path,
                complexity=getattr(change, "complexity_score", 1.0),
                change_size=change_size,
                historical_failures=historical_failures,
                historical_runs=historical_runs,
                critical_paths=config.risk.critical_paths,
                high_paths=config.risk.high_paths,
                weights=config.risk.weights,
            )
            logger.info("[risk] %s → tier=%s composite=%.1f", change.path, risk.tier, risk.composite_risk)
            results[change.path] = risk

        return results

    def _query_history(self, file_path: str, db_session) -> tuple[int, int]:
        """Returns (failures, runs) for the given source file from the DB."""
        if db_session is None:
            return 0, 0
        try:
            from sqlalchemy import text
            stem = file_path.split("/")[-1].replace(".py", "").replace(".ts", "").replace(".tsx", "")
            result = db_session.execute(
                text(
                    "SELECT COUNT(*) as runs, SUM(CASE WHEN failed > 0 THEN 1 ELSE 0 END) as failures "
                    "FROM test_runs WHERE generated_tests LIKE :pattern"
                ),
                {"pattern": f"%{stem}%"},
            ).fetchone()
            if result:
                return int(result[1] or 0), int(result[0] or 0)
        except Exception as exc:
            logger.debug("[risk] DB query failed for %s: %s", file_path, exc)
        return 0, 0


# ── Depth instructions injected into prompts ───────────────────────────────

DEPTH_INSTRUCTIONS: dict[str, str] = {
    "critical": (
        "RISK LEVEL: CRITICAL\n"
        "Generate MINIMUM 15 test cases.\n"
        "Cover: all branches, boundary values, null/empty inputs, "
        "adversarial inputs (SQL injection, XSS payloads where applicable), "
        "concurrent access scenarios, and security edge cases.\n"
        "Every public function must have at least 3 test cases.\n"
        "Include negative tests for every validation rule."
    ),
    "high": (
        "RISK LEVEL: HIGH\n"
        "Generate MINIMUM 10 test cases.\n"
        "Cover: all branches, boundary values, null/empty inputs, "
        "error handling paths, and integration contracts.\n"
        "Every public function must have at least 2 test cases."
    ),
    "medium": (
        "RISK LEVEL: MEDIUM\n"
        "Generate MINIMUM 6 test cases.\n"
        "Cover: happy path, at least 2 error/edge cases, and null inputs."
    ),
    "low": (
        "RISK LEVEL: LOW\n"
        "Generate MINIMUM 3 test cases.\n"
        "Cover: the main function contract and one error case."
    ),
}
