from __future__ import annotations
import logging
import os
import re
from jira import JIRA, JIRAError

logger = logging.getLogger(__name__)


class JiraClient:
    def __init__(self):
        url = os.environ.get("JIRA_URL")
        email = os.environ.get("JIRA_EMAIL")
        logger.info("JiraClient connecting — url=%s email=%s", url, email)
        self._jira = JIRA(
            server=url,
            basic_auth=(email, os.environ.get("JIRA_API_TOKEN")),
        )
        logger.info("JiraClient connected")

    @staticmethod
    def extract_task_id(text: str) -> str | None:
        """Extract the first Jira-style task ID from a string (e.g. HR-123)."""
        match = re.search(r'\b([A-Z][A-Z0-9]+-\d+)\b', text)
        return match.group(1) if match else None

    def get_ticket(self, task_id: str) -> dict:
        """Fetch Jira ticket and return a structured dict."""
        logger.info("Jira fetching ticket — id=%s", task_id)
        try:
            issue = self._jira.issue(task_id)
        except JIRAError as exc:
            logger.error("Jira ticket not found — id=%s error=%s", task_id, exc.text)
            raise ValueError(f"Jira ticket {task_id} not found: {exc.text}") from exc

        description = issue.fields.description or ""
        ac = self._extract_acceptance_criteria(description)
        ticket = {
            "id": task_id,
            "summary": issue.fields.summary,
            "description": description,
            "issue_type": issue.fields.issuetype.name,
            "status": issue.fields.status.name,
            "acceptance_criteria": ac,
        }
        logger.info(
            "Jira ticket loaded — id=%s type=%s status=%s summary=%s ac_chars=%d",
            task_id, ticket["issue_type"], ticket["status"], ticket["summary"][:60], len(ac)
        )
        return ticket

    @staticmethod
    def _extract_acceptance_criteria(description: str) -> str:
        """Pull out the Acceptance Criteria section if present."""
        lines = description.splitlines()
        ac_lines: list[str] = []
        in_section = False
        for line in lines:
            if re.search(r'acceptance[\s_-]*criteria', line, re.IGNORECASE):
                in_section = True
                continue
            if in_section:
                # Stop at the next heading
                if re.match(r'^#+\s|^\*{2}[^*]+\*{2}\s*$', line) and line.strip():
                    break
                ac_lines.append(line)
        return "\n".join(ac_lines).strip()
