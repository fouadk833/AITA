SYSTEM_PROMPT = """\
You are a senior QA engineer specializing in automated test generation.

Rules:
- Generate ONLY valid, runnable test code — no prose explanations outside code blocks
- Cover: happy path, edge cases, error handling, null/undefined inputs, boundary values
- Use descriptive test names: test_<what>_when_<condition>_should_<expectation>
- Mock external dependencies (databases, HTTP, file system) appropriately
- Each test must be fully independent and deterministic
- Do not import the module under test twice — keep imports at the top
- For async functions, use the appropriate async test patterns for the framework
"""

_FRAMEWORK_EXTENSION = {
    "pytest": "python",
    "jest": "typescript",
    "vitest": "typescript",
}


def build_unit_test_prompt(
    code: str,
    file_path: str,
    language: str,
    framework: str,
    context: str = "",
    jira_ticket: dict | None = None,
) -> str:
    ext = _FRAMEWORK_EXTENSION.get(framework, language)
    context_section = f"\nRelated code for context:\n```{ext}\n{context}\n```\n" if context else ""

    jira_section = ""
    if jira_ticket:
        ac = jira_ticket.get("acceptance_criteria", "")
        jira_section = f"""\

Jira ticket: {jira_ticket['id']} — {jira_ticket['summary']}
Feature description: {jira_ticket['description'][:800]}
{"Acceptance criteria:" + chr(10) + ac if ac else ""}
"""

    return f"""\
Generate comprehensive unit tests for the following {language} code using {framework}.

File under test: `{file_path}`
{jira_section}{context_section}
Code to test:
```{ext}
{code}
```

Requirements:
1. Cover all exported functions/classes/methods
2. Include at least one test per branch (if/else, try/catch)
3. Test edge cases: empty inputs, null/undefined, max values, empty collections
4. Mock all I/O, network calls, and database access
5. Use `describe` blocks to group related tests (Jest/Vitest) or classes (pytest)
{"6. Ensure tests validate the acceptance criteria listed above" if jira_ticket else ""}

Return a single ```{ext} code block containing all tests. No explanation outside the block.
"""
