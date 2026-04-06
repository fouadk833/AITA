from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class FileRisk(BaseModel):
    file_path: str
    complexity_score: float         # from AST cyclomatic complexity, 0-100
    change_size_score: float        # additions + deletions, normalized 0-100
    historical_failure_rate: float  # 0-100: failures/runs for tests on this file
    criticality_score: float        # path heuristic, 0-100
    composite_risk: float           # weighted sum
    tier: Literal["critical", "high", "medium", "low"]

    @classmethod
    def compute(
        cls,
        file_path: str,
        complexity: float,
        change_size: int,
        historical_failures: int,
        historical_runs: int,
        critical_paths: list[str],
        high_paths: list[str],
        weights: dict[str, float],
    ) -> "FileRisk":
        path_lower = file_path.lower()

        criticality = (
            100.0 if any(kw in path_lower for kw in critical_paths) else
            70.0  if any(kw in path_lower for kw in high_paths) else
            30.0
        )
        complexity_score = min(100.0, complexity * 5)
        change_size_score = min(100.0, change_size / 5)
        historical = (historical_failures / max(1, historical_runs)) * 100

        composite = (
            criticality           * weights.get("criticality", 0.35)
            + complexity_score    * weights.get("complexity",  0.25)
            + historical          * weights.get("historical",  0.20)
            + change_size_score   * weights.get("change_size", 0.20)
        )
        composite = round(min(100.0, composite), 1)

        tier: Literal["critical", "high", "medium", "low"] = (
            "critical" if composite >= 80 else
            "high"     if composite >= 60 else
            "medium"   if composite >= 40 else
            "low"
        )
        return cls(
            file_path=file_path,
            complexity_score=round(complexity_score, 1),
            change_size_score=round(change_size_score, 1),
            historical_failure_rate=round(historical, 1),
            criticality_score=criticality,
            composite_risk=composite,
            tier=tier,
        )
