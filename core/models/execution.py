"""
Execution Engine data models.
"""
from __future__ import annotations
from typing import Literal, Optional
from pydantic import BaseModel, Field


class ExecutionPlan(BaseModel):
    """Fully describes how to install, build, and test a workspace."""
    runtime: Literal["node", "python", "docker"]
    package_manager: str                        # npm | yarn | pnpm | pip | poetry | unknown
    install_command: str
    build_command: str = ""                     # empty if no build step needed
    test_command: str
    requires_services: list[str] = Field(default_factory=list)    # postgres | redis | mongo ...
    env_vars: list[str] = Field(default_factory=list)             # names of required env vars
    docker_supported: bool = False
    monorepo: bool = False
    monorepo_packages: list[str] = Field(default_factory=list)    # sub-package dirs
    test_dirs: list[str] = Field(default_factory=list)
    framework: str = "unknown"                  # jest | vitest | pytest | mocha | unknown
    confidence: float = 1.0                     # 0.0–1.0: certainty that plan is correct
    env_overrides: dict[str, str] = Field(default_factory=dict)   # injected at execution time


class FailureClassification(BaseModel):
    """Typed result of failure analysis produced by FailureClassifier."""
    category: Literal["ENVIRONMENT_ERROR", "CONFIGURATION_ERROR", "TEST_FAILURE", "TIMEOUT", "UNKNOWN"]
    subcategory: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggested_fix: str = ""
    auto_fixable: bool = False


class HealAction(BaseModel):
    """A single auto-fix action applied during the healing loop."""
    action_type: str    # install_dep | inject_env | switch_db | change_command | create_config
    description: str
    details: dict = Field(default_factory=dict)
    success: bool = False


class ExecutionResult(BaseModel):
    """Full outcome of running tests through the execution engine."""
    success: bool
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_seconds: float = 0.0
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    attempt: int = 1
    classification: Optional[FailureClassification] = None
    heal_actions: list[HealAction] = Field(default_factory=list)
    plan_used: Optional[ExecutionPlan] = None


class ExecutionProfile(BaseModel):
    """Persisted, learned execution configuration for a given repository."""
    repo: str
    branch: Optional[str] = None
    runtime: str = "unknown"
    test_command: str = ""
    install_command: str = ""
    env_defaults: dict[str, str] = Field(default_factory=dict)    # var_name → safe default
    requires_services: list[str] = Field(default_factory=list)
    last_success: Optional[str] = None     # ISO datetime string
    run_count: int = 0
    success_count: int = 0
    heal_history: list[dict] = Field(default_factory=list)        # successful HealActions
