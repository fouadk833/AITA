from __future__ import annotations
from typing import Literal
from pydantic import BaseModel


class TestQualityScore(BaseModel):
    test_file: str
    source_file: str
    assertion_score: float          # 0-100
    branch_coverage: float          # 0-100
    mutation_kill_rate: float        # 0-100  (0 if mutation disabled)
    flakiness_penalty: float         # 0-100  (higher = more flaky)
    composite_score: float           # weighted final
    grade: Literal["A", "B", "C", "D", "F"]

    @classmethod
    def compute(
        cls,
        test_file: str,
        source_file: str,
        assertion_score: float,
        branch_coverage: float,
        mutation_kill_rate: float,
        flakiness_penalty: float,
    ) -> "TestQualityScore":
        # Weights: assertion 30%, branch 30%, mutation 25%, stability 15%
        composite = (
            assertion_score   * 0.30
            + branch_coverage * 0.30
            + mutation_kill_rate * 0.25
            + (100 - flakiness_penalty) * 0.15
        )
        composite = round(min(100.0, max(0.0, composite)), 1)
        grade: Literal["A", "B", "C", "D", "F"] = (
            "A" if composite >= 90 else
            "B" if composite >= 75 else
            "C" if composite >= 60 else
            "D" if composite >= 45 else
            "F"
        )
        return cls(
            test_file=test_file,
            source_file=source_file,
            assertion_score=round(assertion_score, 1),
            branch_coverage=round(branch_coverage, 1),
            mutation_kill_rate=round(mutation_kill_rate, 1),
            flakiness_penalty=round(flakiness_penalty, 1),
            composite_score=composite,
            grade=grade,
        )
