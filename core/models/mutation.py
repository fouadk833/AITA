from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel


class MutantRecord(BaseModel):
    mutant_id: str                  # "{file_path}::{line}::{operator}"
    file_path: str
    line_number: int
    operator: str                   # AOR | ROR | LCR | UOI | SDL
    original_token: str
    mutant_token: str
    status: Literal["killed", "survived", "timeout", "error"] = "survived"
    killing_test: Optional[str] = None


class MutationReport(BaseModel):
    source_file: str
    total_mutants: int
    killed: int
    survived: int
    timed_out: int
    mutation_score: float           # killed / max(1, total - timed_out)  0-100
    mutants: list[MutantRecord] = []
    passed_threshold: bool = True
    threshold_used: float = 65.0
