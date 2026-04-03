import os
import uuid
from datetime import datetime
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.models import TestRunModel
from api.models.schemas import TriggerRequest


async def list_runs(db: AsyncSession, limit: int = 50) -> list[TestRunModel]:
    result = await db.execute(
        select(TestRunModel).order_by(desc(TestRunModel.created_at)).limit(limit)
    )
    return list(result.scalars().all())


async def create_run(db: AsyncSession, req: TriggerRequest) -> TestRunModel:
    run = TestRunModel(
        id=str(uuid.uuid4()),
        repo=req.repo or os.environ.get("GITHUB_REPO", ""),
        pr_number=req.pr_number,
        branch=req.branch,
        commit_sha=req.commit_sha,
        status="running",
        created_at=datetime.utcnow(),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def update_run(db: AsyncSession, run_id: str, **kwargs) -> TestRunModel | None:
    result = await db.execute(select(TestRunModel).where(TestRunModel.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return None
    for key, value in kwargs.items():
        setattr(run, key, value)
    await db.commit()
    await db.refresh(run)
    return run


async def get_run(db: AsyncSession, run_id: str) -> TestRunModel | None:
    result = await db.execute(select(TestRunModel).where(TestRunModel.id == run_id))
    return result.scalar_one_or_none()


async def mark_stale_runs(db: AsyncSession) -> int:
    """Mark all runs still in 'running' state as 'error' (server was restarted mid-pipeline)."""
    from sqlalchemy import update
    result = await db.execute(
        update(TestRunModel)
        .where(TestRunModel.status == "running")
        .values(status="error", error_message="Server was restarted while this run was in progress.")
    )
    await db.commit()
    return result.rowcount


async def delete_run(db: AsyncSession, run_id: str) -> bool:
    result = await db.execute(select(TestRunModel).where(TestRunModel.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        return False
    await db.delete(run)
    await db.commit()
    return True


async def run_exists_for_commit(db: AsyncSession, commit_sha: str) -> bool:
    """Return True only if a healthy run (running or passed) already exists for this commit."""
    result = await db.execute(
        select(TestRunModel.id).where(
            TestRunModel.commit_sha == commit_sha,
            TestRunModel.status.in_(["running", "passed"]),
        ).limit(1)
    )
    return result.scalar_one_or_none() is not None
