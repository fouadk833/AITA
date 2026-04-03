from fastapi import APIRouter, HTTPException, Query

router = APIRouter()


def _get_analyzer():
    from agents.analyzer import AnalyzerAgent
    return AnalyzerAgent()


def _get_github_client():
    from core.github_client import GitHubClient
    return GitHubClient()


@router.get("/branches/local", response_model=list[str])
async def get_local_branches(repo_path: str = Query(default=".", description="Path to the local git repository")):
    """List all local branches in a git repository on disk."""
    try:
        return _get_analyzer().list_local_branches(repo_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/branches/current", response_model=dict)
async def get_current_branch(repo_path: str = Query(default=".", description="Path to the local git repository")):
    """Return the currently active branch."""
    try:
        branch = _get_analyzer().get_current_branch(repo_path)
        return {"branch": branch}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/branches/remote", response_model=list[str])
async def get_remote_branches(repo: str | None = Query(default=None, description="GitHub repo in owner/name format, e.g. org/repo. Defaults to GITHUB_REPO env var.")):
    """List all branches for a GitHub repository (requires GITHUB_TOKEN)."""
    try:
        return _get_github_client().list_branches(repo)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
