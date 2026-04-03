from fastapi import APIRouter, HTTPException, Query
from api.models.schemas import PullRequest
from core.github_client import GitHubClient

router = APIRouter()


@router.get("/pulls", response_model=list[PullRequest])
async def get_pull_requests(
    repo: str | None = Query(default=None, description="GitHub repo in owner/name format. Defaults to GITHUB_REPO env var."),
    state: str = Query(default="open", pattern="^(open|closed|all)$", description="Filter by PR state: open, closed, or all"),
):
    """Return pull requests for a GitHub repository."""
    client = GitHubClient(repo=repo)
    if not client.repo:
        raise HTTPException(status_code=400, detail="No repo specified and GITHUB_REPO env var is not set")
    try:
        return client.get_prs(state=state)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"GitHub API error: {exc}")
