import logging
import os
import uuid
from datetime import datetime
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.models import TestRunModel
from api.models.schemas import TriggerRequest

logger = logging.getLogger(__name__)


async def list_runs(db: AsyncSession, limit: int = 50) -> list[TestRunModel]:
    logger.debug("list_runs — limit=%d", limit)
    result = await db.execute(
        select(TestRunModel).order_by(desc(TestRunModel.created_at)).limit(limit)
    )
    runs = list(result.scalars().all())
    logger.debug("list_runs — returned %d run(s)", len(runs))
    return runs


async def create_run(db: AsyncSession, req: TriggerRequest) -> TestRunModel:
    run_id = str(uuid.uuid4())
    logger.info("create_run — id=%s repo=%s pr=%s branch=%s sha=%s",
                run_id,
                req.repo or os.environ.get("GITHUB_REPO", ""),
                req.pr_number, req.branch,
                (req.commit_sha or "")[:12])
    run = TestRunModel(
        id=run_id,
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
    logger.info("create_run — committed id=%s", run.id)
    return run


async def update_run(db: AsyncSession, run_id: str, **kwargs) -> TestRunModel | None:
    _loggable = {k: v for k, v in kwargs.items()
                 if k not in ("console_output", "generated_tests", "debug_results", "report")}
    logger.info("update_run — id=%s fields=%s", run_id, list(_loggable.keys()))
    logger.debug("update_run — id=%s values=%s", run_id, _loggable)
    result = await db.execute(select(TestRunModel).where(TestRunModel.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        logger.warning("update_run — run not found: %s", run_id)
        return None
    for key, value in kwargs.items():
        setattr(run, key, value)
    await db.commit()
    await db.refresh(run)
    logger.info("update_run — committed id=%s status=%s", run.id, run.status)
    return run


async def get_run(db: AsyncSession, run_id: str) -> TestRunModel | None:
    logger.debug("get_run — id=%s", run_id)
    result = await db.execute(select(TestRunModel).where(TestRunModel.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        logger.debug("get_run — not found: %s", run_id)
    else:
        logger.debug("get_run — found id=%s status=%s", run.id, run.status)
    return run


async def mark_stale_runs(db: AsyncSession) -> int:
    """Mark all runs still in 'running' state as 'error' (server was restarted mid-pipeline)."""
    from sqlalchemy import update
    logger.info("mark_stale_runs — scanning for stale running runs")
    result = await db.execute(
        update(TestRunModel)
        .where(TestRunModel.status == "running")
        .values(status="error", error_message="Server was restarted while this run was in progress.")
    )
    await db.commit()
    count = result.rowcount
    if count:
        logger.warning("mark_stale_runs — marked %d stale run(s) as error", count)
    else:
        logger.info("mark_stale_runs — no stale runs found")
    return count


async def delete_run(db: AsyncSession, run_id: str) -> bool:
    logger.info("delete_run — id=%s", run_id)
    result = await db.execute(select(TestRunModel).where(TestRunModel.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        logger.warning("delete_run — run not found: %s", run_id)
        return False
    await db.delete(run)
    await db.commit()
    logger.info("delete_run — deleted id=%s", run_id)
    return True


async def run_exists_for_commit(db: AsyncSession, commit_sha: str) -> bool:
    """Return True only if a healthy run (running or passed) already exists for this commit."""
    logger.debug("run_exists_for_commit — sha=%s", commit_sha[:12])
    result = await db.execute(
        select(TestRunModel.id).where(
            TestRunModel.commit_sha == commit_sha,
            TestRunModel.status.in_(["running", "passed"]),
        ).limit(1)
    )
    exists = result.scalar_one_or_none() is not None
    logger.debug("run_exists_for_commit — sha=%s exists=%s", commit_sha[:12], exists)
    return exists
