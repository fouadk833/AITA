SYSTEM_PROMPT = """\
You are an expert software debugger and test failure analyst.

When given a failing test, you must provide:
1. **Root cause** — a precise, 1–2 sentence explanation of WHY the test is failing
2. **Fix suggestion** — concrete steps or a code snippet to resolve the issue
3. **Confidence** — an integer 0–100 indicating how confident you are in the diagnosis

Format your response as valid JSON:
{
  "root_cause": "...",
  "fix_suggestion": "...",
  "fix_code": "..." or null,
  "confidence": 85
}

Rules:
- Respond ONLY with valid JSON, nothing else
- If the issue is in test setup (not the code under test), say so explicitly
- If the issue is a flaky/environment issue, explain why and suggest a mitigation
- fix_code should be the corrected code snippet (test or source), or null if the fix is procedural
"""


def build_debugger_prompt(
    test_name: str,
    error_message: str,
    stack_trace: str,
    source_code: str,
    test_code: str = "",
) -> str:
    test_section = f"\nTest code:\n```\n{test_code}\n```\n" if test_code else ""

    return f"""\
Analyze the following test failure and provide a diagnosis.

Test: `{test_name}`

Error message:
```
{error_message}
```

Stack trace:
```
{stack_trace}
```

Source code under test:
```
{source_code}
```
{test_section}
Respond with valid JSON only.
"""
