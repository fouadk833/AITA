from __future__ import annotations
import logging
import os
from github import Github, GithubException
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class GitHubClient:
    def __init__(self, token: str | None = None, repo: str | None = None):
        retry = Retry(
            total=5,
            backoff_factor=1,          # waits 1s, 2s, 4s, 8s, 16s between attempts
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["GET", "POST"],
        )
        self._gh = Github(token or os.environ.get("GITHUB_TOKEN"), retry=retry)
        self.repo: str = repo or os.environ.get("GITHUB_REPO", "")
        logger.info("GitHubClient ready — repo=%s", self.repo or "(none)")

    def _resolve_repo(self, repo: str | None) -> str:
        return repo or self.repo

    def get_pr_diff(self, pr_number: int, repo: str | None = None) -> list[dict]:
        logger.info("GitHub get_pr_diff — repo=%s pr=%s", self._resolve_repo(repo), pr_number)
        pr = self._gh.get_repo(self._resolve_repo(repo)).get_pull(pr_number)
        return [
            {
                "filename": f.filename,
                "patch": f.patch or "",
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
            }
            for f in pr.get_files()
        ]

    def get_file_content(self, file_path: str, ref: str = "main", repo: str | None = None) -> str:
        try:
            content = self._gh.get_repo(self._resolve_repo(repo)).get_contents(file_path, ref=ref)
            if isinstance(content, list):
                return ""
            return content.decoded_content.decode("utf-8")
        except GithubException:
            return ""

    def post_pr_comment(self, pr_number: int, body: str, repo: str | None = None) -> None:
        self._gh.get_repo(self._resolve_repo(repo)).get_pull(pr_number).create_issue_comment(body)

    def get_changed_files(self, pr_number: int, repo: str | None = None) -> list[str]:
        pr = self._gh.get_repo(self._resolve_repo(repo)).get_pull(pr_number)
        return [f.filename for f in pr.get_files()]

    def get_pr_branch(self, pr_number: int, repo: str | None = None) -> str:
        """Return the head branch name of a pull request."""
        pr = self._gh.get_repo(self._resolve_repo(repo)).get_pull(pr_number)
        return pr.head.ref

    def list_branches(self, repo: str | None = None) -> list[str]:
        """Return all branch names for a GitHub repository."""
        return [b.name for b in self._gh.get_repo(self._resolve_repo(repo)).get_branches()]

    def list_open_prs(self, repo: str | None = None) -> list[dict]:
        """Return open PRs with the fields needed to trigger a pipeline run."""
        logger.info("GitHub list_open_prs — repo=%s", self._resolve_repo(repo))
        gh_repo = self._gh.get_repo(self._resolve_repo(repo))
        result = []
        for pr in gh_repo.get_pulls(state="open", sort="updated", direction="desc"):
            logger.info("  Found open PR #%s — %s [%s]", pr.number, pr.title, pr.head.ref)
            result.append({
                "pr_number": pr.number,
                "branch": pr.head.ref,
                "commit_sha": pr.head.sha,
                "changed_files": [f.filename for f in pr.get_files()],
            })
        return result

    def get_commit_message(self, sha: str, repo: str | None = None) -> str:
        """Return the full commit message for a given SHA."""
        logger.info("GitHub get_commit_message — repo=%s sha=%s", self._resolve_repo(repo), sha[:12])
        commit = self._gh.get_repo(self._resolve_repo(repo)).get_commit(sha)
        msg = commit.commit.message
        logger.info("Commit message: %s", msg[:120].replace("\n", " "))
        return msg

    def get_prs(self, repo: str | None = None, state: str = "open") -> list[dict]:
        """Return pull requests with full metadata for display."""
        gh_repo = self._gh.get_repo(self._resolve_repo(repo))
        result = []
        for pr in gh_repo.get_pulls(state=state, sort="updated", direction="desc"):
            result.append({
                "number": pr.number,
                "title": pr.title,
                "state": pr.state,
                "branch": pr.head.ref,
                "base_branch": pr.base.ref,
                "commit_sha": pr.head.sha,
                "author": pr.user.login,
                "url": pr.html_url,
                "created_at": pr.created_at.isoformat(),
                "updated_at": pr.updated_at.isoformat(),
                "changed_files": pr.changed_files,
                "additions": pr.additions,
                "deletions": pr.deletions,
                "draft": pr.draft,
            })
        return result
