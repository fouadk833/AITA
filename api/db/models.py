from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column
from api.db.database import Base


class TestRunModel(Base):
    __tablename__ = "test_runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    repo: Mapped[str] = mapped_column(String, default="")
    pr_number: Mapped[int] = mapped_column(Integer)
    branch: Mapped[str] = mapped_column(String)
    commit_sha: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="running")
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_tests: Mapped[str | None] = mapped_column(Text, nullable=True)   # JSON list of file paths
    debug_results: Mapped[str | None] = mapped_column(Text, nullable=True)     # JSON list of {test_name, root_cause, fix_suggestion}
    report: Mapped[str | None] = mapped_column(Text, nullable=True)            # full PR comment markdown
    jira_task_id: Mapped[str | None] = mapped_column(String, nullable=True)    # e.g. KAN-42
    console_output: Mapped[str | None] = mapped_column(Text, nullable=True)    # JSON list of {source, content, passed, failed, exit_code}


class CoverageModel(Base):
    __tablename__ = "coverage_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service: Mapped[str] = mapped_column(String)
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    lines: Mapped[float] = mapped_column(Float, default=0.0)
    branches: Mapped[float] = mapped_column(Float, default=0.0)
    functions: Mapped[float] = mapped_column(Float, default=0.0)
    statements: Mapped[float] = mapped_column(Float, default=0.0)


class FlakinessModel(Base):
    __tablename__ = "flakiness_scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    test_name: Mapped[str] = mapped_column(String)
    file_path: Mapped[str] = mapped_column(String)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    run_count: Mapped[int] = mapped_column(Integer, default=0)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
