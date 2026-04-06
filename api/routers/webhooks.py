"""
GitHub Webhook receiver — event-driven pipeline trigger.

Replaces polling with instant dispatch on pull_request events.
Validates HMAC-SHA256 signatures for security.
"""
from __future__ import annotations
import hashlib
import hmac
import logging
import os
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.database import get_db
from api.models.schemas import TriggerRequest, TriggerResponse
from api.services import run_service

router = APIRouter()
logger = logging.getLogger(__name__)

# ── In-memory webhook event log (last 100 events for debugging) ─────────────
_webhook_log: deque[dict] = deque(maxlen=100)


@router.post("/webhooks/github", status_code=202)
async def github_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Receives GitHub pull_request webhook events.

    Configure in GitHub → Settings → Webhooks:
      Payload URL:    https://your-host/api/webhooks/github
      Content-type:  application/json
      Events:        Pull requests
      Secret:        value of GITHUB_WEBHOOK_SECRET env var
    """
    # ── 1. Read raw body before any parsing ─────────────────────────────────
    body = await request.body()

    # ── 2. HMAC-SHA256 signature validation ─────────────────────────────────
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    if secret:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig_header, expected):
            logger.warning("Webhook signature mismatch — rejecting")
            raise HTTPException(status_code=403, detail="Invalid signature")
    else:
        logger.warning("GITHUB_WEBHOOK_SECRET not set — skipping signature validation")

    # ── 3. Parse payload ─────────────────────────────────────────────────────
    import json
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = request.headers.get("X-GitHub-Event", "")
    action = payload.get("action", "")

    _webhook_log.append({
        "received_at": datetime.utcnow().isoformat(),
        "event": event_type,
        "action": action,
        "repo": (payload.get("repository") or {}).get("full_name"),
    })

    # ── 4. Filter: only handle pull_request events ───────────────────────────
    if event_type != "pull_request":
        logger.debug("Webhook: ignoring event_type=%s", event_type)
        return {"status": "ignored", "reason": f"event type '{event_type}' not handled"}

    if action not in ("opened", "synchronize", "reopened"):
        logger.debug("Webhook: ignoring action=%s", action)
        return {"status": "ignored", "reason": f"action '{action}' not handled"}

    pr = payload.get("pull_request", {})
    repo = (payload.get("repository") or {}).get("full_name", "")

    # ── 5. Skip draft PRs ────────────────────────────────────────────────────
    if pr.get("draft") is True:
        logger.info("Webhook: skipping draft PR #%s", pr.get("number"))
        return {"status": "skipped", "reason": "draft PR"}

    # ── 6. Skip [WIP] PRs ───────────────────────────────────────────────────
    title = pr.get("title", "")
    if "[WIP]" in title or "[wip]" in title.lower():
        logger.info("Webhook: skipping WIP PR #%s", pr.get("number"))
        return {"status": "skipped", "reason": "WIP PR"}

    pr_number = pr.get("number")
    branch = (pr.get("head") or {}).get("ref", "")
    commit_sha = (pr.get("head") or {}).get("sha", "")

    if not all([repo, pr_number, branch, commit_sha]):
        raise HTTPException(status_code=400, detail="Missing required PR fields")

    # ── 7. Idempotency: skip if run already exists for this commit ───────────
    if await run_service.run_exists_for_commit(db, commit_sha):
        logger.info("Webhook: run already exists for commit %s — skipping", commit_sha[:8])
        return {"status": "already_queued", "commit_sha": commit_sha}

    # ── 8. Build changed files list from GitHub payload ──────────────────────
    changed_files: list[str] = []
    try:
        from core.github_client import GitHubClient
        gh = GitHubClient(repo=repo)
        pr_files = gh.get_pr_files(pr_number)
        changed_files = [f["filename"] for f in pr_files]
    except Exception as exc:
        logger.warning("Webhook: could not fetch PR file list (%s) — proceeding without", exc)

    # ── 9. Schedule pipeline ─────────────────────────────────────────────────
    req = TriggerRequest(
        repo=repo,
        pr_number=pr_number,
        branch=branch,
        commit_sha=commit_sha,
        changed_files=changed_files,
    )
    run = await run_service.create_run(db, req)

    from api.routers.runs import _schedule_pipeline
    _schedule_pipeline(run.id, req)

    logger.info("Webhook: queued run %s for PR #%s (%s@%s)",
                run.id, pr_number, repo, commit_sha[:8])
    return TriggerResponse(job_id=run.id)


@router.get("/webhooks/log")
async def webhook_log():
    """Returns the last 100 received webhook events (for debugging)."""
    return list(_webhook_log)
