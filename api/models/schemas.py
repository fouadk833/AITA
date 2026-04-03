from __future__ import annotations
import json
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, field_validator


class TestRun(BaseModel):
    id: str
    repo: str
    pr_number: int
    branch: str
    commit_sha: str
    status: Literal["running", "passed", "failed", "error"]
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    created_at: datetime
    error_message: Optional[str] = None
    generated_tests: Optional[list[str]] = None
    debug_results: Optional[list[dict]] = None
    report: Optional[str] = None
    jira_task_id: Optional[str] = None
    console_output: Optional[list[dict]] = None

    model_config = {"from_attributes": True}

    @field_validator("generated_tests", "debug_results", "console_output", mode="before")
    @classmethod
    def parse_json(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v


class CoverageReport(BaseModel):
    service: str
    timestamp: datetime
    lines: float
    branches: float
    functions: float
    statements: float

    model_config = {"from_attributes": True}


class FlakinessScore(BaseModel):
    test_name: str
    file_path: str
    score: float
    failure_count: int
    run_count: int
    last_seen: datetime

    model_config = {"from_attributes": True}


class AgentStatus(BaseModel):
    name: str
    status: Literal["idle", "running", "error"]
    last_run: datetime
    current_task: Optional[str] = None


class PullRequest(BaseModel):
    number: int
    title: str
    state: str
    branch: str
    base_branch: str
    commit_sha: str
    author: str
    url: str
    created_at: str
    updated_at: str
    changed_files: int
    additions: int
    deletions: int
    draft: bool


class TriggerRequest(BaseModel):
    pr_number: int
    branch: str
    commit_sha: str
    repo: Optional[str] = None
    changed_files: list[str] = []


class TriggerResponse(BaseModel):
    job_id: str
