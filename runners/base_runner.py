from __future__ import annotations
import subprocess
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RunResult:
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    output: str
    error: Optional[str] = None
    exit_code: int = 0


class BaseRunner:
    def run(self, test_path: str, **kwargs) -> RunResult:
        raise NotImplementedError

    def _exec(self, cmd: list[str], cwd: str = ".", shell: bool = False, env=None) -> tuple[str, str, int]:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=300, shell=shell, env=env)
        return result.stdout, result.stderr, result.returncode
