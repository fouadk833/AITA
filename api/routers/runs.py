import asyncio
import os
import json
import time
import traceback
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from api.db.database import get_db
from api.models.schemas import TestRun, TriggerRequest, TriggerResponse
from api.services import run_service
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Registry of active pipeline tasks, keyed by run_id.
# Enables cancellation when a run is deleted.
_running_tasks: dict[str, asyncio.Task] = {}


def _schedule_pipeline(run_id: str, req: TriggerRequest) -> None:
    """Start the pipeline as a tracked asyncio task so it can be cancelled."""
    task = asyncio.create_task(_run_pipeline(run_id, req))
    _running_tasks[run_id] = task
    task.add_done_callback(lambda _: _running_tasks.pop(run_id, None))


@router.get("/runs", response_model=list[TestRun])
async def get_runs(db: AsyncSession = Depends(get_db)):
    runs = await run_service.list_runs(db)
    return runs


@router.get("/runs/{run_id}", response_model=TestRun)
async def get_run(run_id: str, db: AsyncSession = Depends(get_db)):
    run = await run_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.post("/trigger", response_model=TriggerResponse, status_code=202)
async def trigger_run(req: TriggerRequest, db: AsyncSession = Depends(get_db)):
    run = await run_service.create_run(db, req)
    _schedule_pipeline(run.id, req)
    return TriggerResponse(job_id=run.id)


async def _run_pipeline(run_id: str, req: TriggerRequest):
    """Background task: runs the AI pipeline and persists full results."""
    from api.db.database import SessionLocal
    from api.services import run_service as svc
    from api.ws_manager import manager as ws_manager

    live_console_logs: list[dict] = []
    latest_result = {"passed": 0, "failed": 0, "skipped": 0, "duration_seconds": 0.0}
    flush_interval = max(1.0, float(os.environ.get("AITA_RUN_PROGRESS_FLUSH_SECONDS", "2")))
    last_flush_at = 0.0

    def _event_to_console_log(event: dict) -> dict | None:
        etype = event.get("type")
        if etype == "test_log":
            return {
                "source": event.get("source", "test"),
                "stdout": event.get("stdout", "") or "",
                "stderr": event.get("stderr", "") or "",
                "passed": int(event.get("passed", 0) or 0),
                "failed": int(event.get("failed", 0) or 0),
                "skipped": int(event.get("skipped", 0) or 0),
                "exit_code": int(event.get("exit_code", 0) or 0),
            }
        if etype == "progress":
            node = str(event.get("node") or "pipeline")
            status = str(event.get("status") or "started")
            message = str(event.get("message") or status)
            return {
                "source": f"[{node}]",
                "stdout": "",
                "stderr": f"{status.upper()}: {message}",
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "exit_code": 1 if status == "error" else 0,
            }
        if etype == "error":
            return {
                "source": "[pipeline]",
                "stdout": "",
                "stderr": str(event.get("message") or "Pipeline error"),
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "exit_code": 1,
            }
        return None

    async def _flush_progress(force: bool = False) -> None:
        nonlocal last_flush_at
        if not force and (time.monotonic() - last_flush_at) < flush_interval:
            return
        last_flush_at = time.monotonic()
        try:
            async with SessionLocal() as db:
                await svc.update_run(
                    db,
                    run_id,
                    passed=latest_result["passed"],
                    failed=latest_result["failed"],
                    skipped=latest_result["skipped"],
                    duration_seconds=latest_result["duration_seconds"],
                    console_output=json.dumps(live_console_logs[-500:]),
                )
        except Exception as exc:
            logger.debug("Progress flush failed for run %s: %s", run_id, exc)

    async def on_event(event: dict) -> None:
        await ws_manager.broadcast(run_id, event)
        entry = _event_to_console_log(event)
        if entry is not None:
            live_console_logs.append(entry)

        if event.get("type") == "run_result":
            latest_result["passed"] = int(event.get("passed", 0) or 0)
            latest_result["failed"] = int(event.get("failed", 0) or 0)
            latest_result["skipped"] = int(event.get("skipped", 0) or 0)
            latest_result["duration_seconds"] = float(event.get("duration", 0.0) or 0.0)

        if entry is not None or event.get("type") == "run_result":
            await _flush_progress()

    logger.info("Pipeline starting — run_id=%s repo=%s pr=%s branch=%s sha=%s",
                run_id, req.repo, req.pr_number, req.branch, req.commit_sha[:12] if req.commit_sha else "?")
    try:
        from agents.orchestrator import run_pipeline  # type: ignore
        final_state = await run_pipeline(
            {
                "repo": req.repo,
                "pr_number": req.pr_number,
                "branch": req.branch,
                "commit_sha": req.commit_sha,
                "changed_files": req.changed_files,
            },
            on_event=on_event,
        )

        run_results  = final_state.get("run_results") or {}
        generated    = final_state.get("generated_tests") or {}
        debug_raw    = final_state.get("debug_results") or []
        console_logs = final_state.get("console_logs") or live_console_logs
        report       = final_state.get("report")
        error_msg    = final_state.get("error")

        all_tests = []
        for paths in generated.values():
            all_tests.extend(paths)

        debug_clean = [
            {
                "test_name":      d.get("test_name"),
                "root_cause":     d.get("root_cause"),
                "fix_suggestion": d.get("fix_suggestion"),
                "fix_code":       d.get("fix_code"),
                "confidence":     d.get("confidence"),
            }
            for d in debug_raw
        ]

        total_failed = run_results.get("failed", 0)
        status = "error" if error_msg else ("failed" if total_failed > 0 else "passed")

        jira_ticket = final_state.get("jira_ticket")
        logger.info(
            "Pipeline complete — run_id=%s status=%s passed=%d failed=%d skipped=%d duration=%.2fs jira=%s tests=%d",
            run_id, status,
            run_results.get("passed", 0), total_failed, run_results.get("skipped", 0),
            run_results.get("duration_seconds", 0.0),
            jira_ticket.get("id") if jira_ticket else "none",
            len(all_tests),
        )
        async with SessionLocal() as db:
            await svc.update_run(
                db, run_id,
                status=status,
                passed=run_results.get("passed", 0),
                failed=total_failed,
                skipped=run_results.get("skipped", 0),
                duration_seconds=run_results.get("duration_seconds", 0.0),
                error_message=error_msg,
                generated_tests=json.dumps(all_tests),
                debug_results=json.dumps(debug_clean),
                console_output=json.dumps(console_logs),
                report=report,
                jira_task_id=jira_ticket.get("id") if jira_ticket else None,
            )
        await on_event({"type": "complete", "status": status, "report": report or ""})
        ws_manager.clear_buffer(run_id)
    except asyncio.CancelledError:
        logger.info("Pipeline cancelled — run_id=%s", run_id)
        ws_manager.clear_buffer(run_id)
        raise
    except Exception as exc:
        logger.error("Pipeline failed for run %s: %s", run_id, exc, exc_info=True)
        trace = traceback.format_exc()
        error_payload = f"{exc}\n\n{trace}"[:16000]
        async with SessionLocal() as db:
            await svc.update_run(
                db,
                run_id,
                status="error",
                error_message=error_payload,
                passed=latest_result["passed"],
                failed=latest_result["failed"],
                skipped=latest_result["skipped"],
                duration_seconds=latest_result["duration_seconds"],
                console_output=json.dumps(live_console_logs[-500:]),
            )
        await on_event({"type": "error", "message": str(exc)})
        ws_manager.clear_buffer(run_id)


@router.post("/runs/{run_id}/restart", response_model=TriggerResponse, status_code=202)
async def restart_run(run_id: str, db: AsyncSession = Depends(get_db)):
    """Re-execute a failed or interrupted run using the same PR metadata."""
    run = await run_service.get_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status == "running":
        raise HTTPException(status_code=409, detail="Run is already in progress")
    await run_service.update_run(
        db, run_id,
        status="running",
        passed=0, failed=0, skipped=0,
        duration_seconds=0.0,
        error_message=None,
        generated_tests=None,
        debug_results=None,
        console_output=None,
        report=None,
    )
    req = TriggerRequest(
        repo=run.repo,
        pr_number=run.pr_number,
        branch=run.branch,
        commit_sha=run.commit_sha,
    )
    _schedule_pipeline(run_id, req)
    logger.info("Restarting run %s for PR #%s", run_id, run.pr_number)
    return TriggerResponse(job_id=run_id)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(run_id: str, db: AsyncSession = Depends(get_db)):
    task = _running_tasks.pop(run_id, None)
    if task and not task.done():
        task.cancel()
        logger.info("Cancelled background pipeline for run %s", run_id)
    deleted = await run_service.delete_run(db, run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Run not found")


@router.post("/runs/sync", response_model=list[TriggerResponse], status_code=202)
async def sync_prs(
    db: AsyncSession = Depends(get_db),
    repo: str | None = None,
):
    """Fetch all open PRs from a GitHub repo and queue a test run for each one.
    Uses the GITHUB_REPO env var if repo is not specified."""
    from core.github_client import GitHubClient
    client = GitHubClient(repo=repo)
    if not client.repo:
        raise HTTPException(status_code=400, detail="No repo specified and GITHUB_REPO env var is not set")
    logger.info("sync_prs — fetching open PRs for repo=%s", client.repo)
    try:
        open_prs = client.list_open_prs()
    except Exception as exc:
        logger.error("sync_prs — GitHub API error: %s", exc)
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")
    logger.info("sync_prs — found %d open PR(s)", len(open_prs))

    responses: list[TriggerResponse] = []
    for pr in open_prs:
        if await run_service.run_exists_for_commit(db, pr["commit_sha"]):
            logger.info("Skipping PR #%s — run already exists for commit %s", pr["pr_number"], pr["commit_sha"])
            continue
        req = TriggerRequest(
            repo=client.repo,
            pr_number=pr["pr_number"],
            branch=pr["branch"],
            commit_sha=pr["commit_sha"],
            changed_files=pr["changed_files"],
        )
        run = await run_service.create_run(db, req)
        _schedule_pipeline(run.id, req)
        responses.append(TriggerResponse(job_id=run.id))
        logger.info("Queued run %s for PR #%s", run.id, pr["pr_number"])

    return responses
