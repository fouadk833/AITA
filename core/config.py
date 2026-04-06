"""
Per-repo AITA configuration loaded from aita.yml at the workspace root.
Falls back to safe defaults when no config file is present.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MutationConfig(BaseModel):
    enabled: bool = True
    threshold: float = 65.0          # minimum mutation score (0-100) to pass
    max_mutants_per_file: int = 50
    operators: list[str] = Field(default_factory=lambda: ["AOR", "ROR", "LCR"])
    timeout_seconds: int = 30        # per-mutant timeout


class GenerationConfig(BaseModel):
    unit: bool = True
    integration: bool = True
    e2e: bool = True
    output_dir: str = "__aita_tests__"
    max_retries: int = 3             # self-healing retries before quarantine


class CoverageThresholds(BaseModel):
    lines: float = 80.0
    branches: float = 70.0
    functions: float = 80.0


class RiskConfig(BaseModel):
    critical_paths: list[str] = Field(
        default_factory=lambda: ["auth", "payment", "billing", "security", "crypto", "token", "password"]
    )
    high_paths: list[str] = Field(
        default_factory=lambda: ["api", "service", "model", "controller", "router", "middleware"]
    )
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "criticality": 0.35,
            "complexity": 0.25,
            "historical": 0.20,
            "change_size": 0.20,
        }
    )


class FlakinessConfig(BaseModel):
    quarantine_threshold: float = 70.0   # score above this → quarantine + regenerate
    warning_threshold: float = 40.0      # score above this → add warning comment
    max_flaky_tests: int = 5             # fail pipeline if more than N flaky tests
    extra_risky_patterns: list[str] = Field(default_factory=list)


class AITAConfig(BaseModel):
    version: str = "1"
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    coverage: CoverageThresholds = Field(default_factory=CoverageThresholds)
    mutation: MutationConfig = Field(default_factory=MutationConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    flakiness: FlakinessConfig = Field(default_factory=FlakinessConfig)
    exclude_paths: list[str] = Field(default_factory=list)
    jira_enabled: bool = True
    webhook_skip_draft: bool = True
    webhook_skip_wip: bool = True       # skip PRs with [WIP] in title

    @classmethod
    def load(cls, workspace_dir: str) -> "AITAConfig":
        """Load aita.yml / .aita.yml from workspace_dir, fall back to defaults."""
        for name in ("aita.yml", ".aita.yml", "aita.yaml", ".aita.yaml"):
            config_path = Path(workspace_dir) / name
            if config_path.exists():
                try:
                    import yaml
                    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                    cfg = cls.model_validate(raw or {})
                    logger.info("AITAConfig: loaded from %s", config_path)
                    return cfg
                except Exception as exc:
                    logger.warning("AITAConfig: failed to parse %s (%s) — using defaults", config_path, exc)
        logger.info("AITAConfig: no aita.yml found in %s — using defaults", workspace_dir)
        return cls()

    def is_excluded(self, file_path: str) -> bool:
        """Return True if the file matches any exclude_paths glob pattern."""
        from fnmatch import fnmatch
        return any(fnmatch(file_path, pattern) for pattern in self.exclude_paths)
