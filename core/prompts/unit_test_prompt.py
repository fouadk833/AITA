"""
Prompt builder for AITA unit-test generation agent.

Responsibilities:
- Assemble system + user prompts for the LLM
- Validate inputs before prompt assembly
- Manage token budget awareness
- Support self-healing (re-generation from prior failures)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import PurePosixPath

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants & config
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert QA engineer who writes production-quality automated tests.
You have deep knowledge of Jest (TypeScript/JavaScript) and pytest (Python).

═══════════════════════════════════════════════════════
CRITICAL RULES — violating any of these causes test failure
═══════════════════════════════════════════════════════

JEST / TYPESCRIPT rules:
1. `describe`, `it`, `test`, `expect`, `beforeEach`, `afterEach`, `jest` are GLOBAL — NEVER import them.
   ✗ WRONG:  import { describe, it, expect } from 'jest'
   ✗ WRONG:  import { describe, it, expect } from 'vitest'
   ✓ CORRECT: // no import needed — globals provided by Jest runtime

2. Always use the EXACT relative import path given in the prompt. Never use bare module paths.
   ✗ WRONG:  import { fn } from 'src/utils/fn'
   ✗ WRONG:  import { fn } from './fn'
   ✓ CORRECT: import { fn } from '../../../src/utils/fn'

3. Use `toEqual` (deep equality) for objects and arrays — NOT `toBe`.
   `toBe` uses reference equality (===) and fails for objects.
   ✓ expect(result).toEqual({ score: 42, label: 'good' })

4. `jest.mock(...)` must be called at the TOP LEVEL (module scope), BEFORE describe blocks.
   ✓ jest.mock('axios');
   ✓ const mockFn = jest.fn();

5. Clear mocks in `beforeEach`:
   beforeEach(() => { jest.clearAllMocks(); });

6. Async tests: use `async/await` inside `it()` callbacks.
   ✓ it('fetches data', async () => { const result = await fn(); ... });

7. Testing thrown errors:
   ✓ expect(() => fn(bad)).toThrow('Expected message');
   ✓ await expect(asyncFn(bad)).rejects.toThrow('Expected message');

8. Snapshots: use `toMatchSnapshot()` ONLY when a function returns a complex object
   with many fields — not for simple primitives or booleans.

PYTEST / PYTHON rules:
1. One import per symbol — never duplicate imports.
2. Use `pytest.raises` for exception testing.
3. Use fixtures for shared setup (`@pytest.fixture`).
4. Each test function name: `test_<what>_<condition>_<expectation>`.

UNIVERSAL rules:
- Output ONLY the code block. Zero prose outside it.
- Every test must be independently runnable — no shared mutable state.
- Arrange / Act / Assert pattern — three logical sections per test.
- Never test implementation details — test observable behaviour.
"""

# ---------------------------------------------------------------------------
# Framework config
# ---------------------------------------------------------------------------

_FRAMEWORK_EXTENSION: dict[str, str] = {
    "pytest": "python",
    "jest": "typescript",
    "vitest": "typescript",
}

_SUPPORTED_FRAMEWORKS = frozenset(_FRAMEWORK_EXTENSION.keys())

# Token budget
_CHARS_PER_TOKEN = 3.5
_DEFAULT_MAX_PROMPT_TOKENS = 120_000
_DEFAULT_MAX_CONTEXT_TOKENS = 30_000
_JIRA_DESCRIPTION_LIMIT = 800

DEFAULT_TEST_ROOT = "__aita_tests__"

# ---------------------------------------------------------------------------
# Few-shot examples (appended to system prompt for the active framework)
# ---------------------------------------------------------------------------

_JEST_EXAMPLE = """\

═══════════════════════════════════════════════════════
REFERENCE EXAMPLE — correct Jest/TypeScript test structure
═══════════════════════════════════════════════════════
```typescript
// ── imports ──────────────────────────────────────────
import { calculateScore } from '../../../src/utils/score';

// ── module-level mocks (before describe) ─────────────
jest.mock('../../../src/api/client');
const mockFetch = jest.fn();

// ── test suite ───────────────────────────────────────
describe('calculateScore', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('returns correct score for valid inputs', () => {
    // Arrange
    const input = { value: 10, weight: 2 };
    // Act
    const result = calculateScore(input);
    // Assert
    expect(result).toEqual({ score: 20, grade: 'B' });
  });

  it('matches snapshot for complex output', () => {
    const result = calculateScore({ value: 100, weight: 1 });
    expect(result).toMatchSnapshot();
  });

  it('throws RangeError when value is negative', () => {
    expect(() => calculateScore({ value: -1, weight: 1 }))
      .toThrow(RangeError);
  });

  it('returns null for undefined input', () => {
    expect(calculateScore(undefined as any)).toBeNull();
  });

  it('handles zero weight without division error', () => {
    const result = calculateScore({ value: 5, weight: 0 });
    expect(result.score).toBe(0);
  });
});
```
"""

_PYTEST_EXAMPLE = """\

═══════════════════════════════════════════════════════
REFERENCE EXAMPLE — correct pytest structure
═══════════════════════════════════════════════════════
```python
import pytest
from src.utils.score import calculate_score

@pytest.fixture
def valid_input():
    return {"value": 10, "weight": 2}

def test_calculate_score_valid_input_returns_correct_score(valid_input):
    result = calculate_score(valid_input)
    assert result["score"] == 20
    assert result["grade"] == "B"

def test_calculate_score_negative_value_raises_range_error():
    with pytest.raises(RangeError, match="value must be non-negative"):
        calculate_score({"value": -1, "weight": 1})

def test_calculate_score_none_input_returns_none():
    assert calculate_score(None) is None

def test_calculate_score_zero_weight_returns_zero_score():
    result = calculate_score({"value": 5, "weight": 0})
    assert result["score"] == 0
```
"""


_SYSTEM_PROMPT_COMPACT = """\
You are an expert QA engineer. Generate valid, runnable unit tests only.

STRICT RULES for Jest/TypeScript:
- describe/it/expect/jest are GLOBAL — NEVER import them from any package
- Use the EXACT import path given — no other path
- toEqual for objects, toBe for primitives
- jest.mock() at module top level before describe
- clearAllMocks() in beforeEach
- async it() for async functions

STRICT RULES for pytest:
- pytest.raises for exceptions
- One assert per logical check
- Fixture for shared setup

Output ONLY the code block. No prose.
"""


def get_system_prompt(framework: str, lightweight: bool = False) -> str:
    """Return the system prompt. Lightweight models get a compact version without examples."""
    if lightweight:
        return _SYSTEM_PROMPT_COMPACT
    if framework == "pytest":
        return SYSTEM_PROMPT + _PYTEST_EXAMPLE
    return SYSTEM_PROMPT + _JEST_EXAMPLE


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class PromptBuildError(Exception):
    """Raised when prompt assembly fails validation."""


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptInputs:
    """Validated, immutable inputs for prompt assembly."""

    code: str
    file_path: str
    language: str
    framework: str
    ext: str
    context: str
    jira_ticket: dict | None
    depth_instruction: str | None
    heal_context: str | None
    functions: list[str] = field(default_factory=list)
    classes: list[str] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    call_graph: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        code: str,
        file_path: str,
        language: str,
        framework: str,
        context: str = "",
        jira_ticket: dict | None = None,
        depth_instruction: str | None = None,
        heal_context: str | None = None,
        functions: list[str] | None = None,
        classes: list[str] | None = None,
        imports: list[str] | None = None,
        call_graph: dict[str, list[str]] | None = None,
    ) -> PromptInputs:
        errors: list[str] = []

        if not code or not code.strip():
            errors.append("Source code is empty")
        if not file_path or not file_path.strip():
            errors.append("file_path is empty")
        if not language or not language.strip():
            errors.append("language is empty")
        if framework not in _SUPPORTED_FRAMEWORKS:
            errors.append(
                f"Unsupported framework '{framework}'. "
                f"Expected one of: {', '.join(sorted(_SUPPORTED_FRAMEWORKS))}"
            )

        if jira_ticket:
            for key in ("id", "summary", "description"):
                if key not in jira_ticket:
                    errors.append(f"jira_ticket missing required key '{key}'")

        if errors:
            raise PromptBuildError("; ".join(errors))

        ext = _FRAMEWORK_EXTENSION.get(framework, language)

        return cls(
            code=code,
            file_path=file_path.strip(),
            language=language.strip().lower(),
            framework=framework,
            ext=ext,
            context=context,
            jira_ticket=jira_ticket,
            depth_instruction=depth_instruction,
            heal_context=heal_context,
            functions=functions or [],
            classes=classes or [],
            imports=imports or [],
            call_graph=call_graph or {},
        )


# ---------------------------------------------------------------------------
# Token budget helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


def _trim_context(context: str, max_tokens: int = _DEFAULT_MAX_CONTEXT_TOKENS) -> str:
    if not context:
        return context
    estimated = _estimate_tokens(context)
    if estimated <= max_tokens:
        return context

    logger.warning("Context exceeds budget (%d > %d tokens). Trimming.", estimated, max_tokens)
    max_chars = int(max_tokens * _CHARS_PER_TOKEN)
    half = max_chars // 2
    return (
        context[:half]
        + "\n\n/* ... context trimmed for token budget ... */\n\n"
        + context[-half:]
    )


# ---------------------------------------------------------------------------
# Import path resolution
# ---------------------------------------------------------------------------


def _resolve_test_dir(language: str, test_root: str = DEFAULT_TEST_ROOT) -> str:
    sub = "backend" if language == "python" else "frontend"
    return f"{test_root}/{sub}/unit"


def _relative_import_path(
    file_path: str,
    language: str,
    test_root: str = DEFAULT_TEST_ROOT,
) -> str:
    test_dir = _resolve_test_dir(language, test_root)
    source_no_ext = str(PurePosixPath(file_path).with_suffix(""))
    rel = os.path.relpath(source_no_ext, test_dir).replace("\\", "/")
    if not rel.startswith("."):
        rel = "./" + rel
    return rel


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_import_hint(file_path: str, language: str, rel_import: str) -> str:
    if language == "python":
        module_dotpath = (
            PurePosixPath(file_path).with_suffix("").__str__().replace("/", ".")
        )
        return (
            f"Import the module under test as: `from {module_dotpath} import ...`\n"
            f"Fallback relative import: `from {rel_import} import ...`\n"
        )
    return (
        f"EXACT import to use (copy verbatim): `import {{ ... }} from '{rel_import}';`\n"
        f"⚠ DO NOT use any other path. DO NOT import jest globals (describe/it/expect).\n"
    )


def _build_ast_context(
    functions: list[str],
    classes: list[str],
    imports: list[str],
    call_graph: dict[str, list[str]],
    language: str,
) -> str:
    """Build a structured AST summary to tell the LLM exactly what to test and mock."""
    lines: list[str] = []

    if functions:
        lines.append(f"Exported functions to test: {', '.join(f'`{f}`' for f in functions)}")

    if classes:
        lines.append(f"Exported classes to test: {', '.join(f'`{c}`' for c in classes)}")

    if imports:
        # Surface only external/non-relative imports — these are candidates for mocking
        external = [imp for imp in imports if "from '" not in imp or not imp.split("from '")[1].startswith(".")]
        if external:
            lines.append("External dependencies (mock these in tests):")
            for imp in external[:8]:
                lines.append(f"  • {imp.strip()}")

    if call_graph:
        lines.append("Call graph (function → what it calls — mock the callees):")
        for fn, callees in list(call_graph.items())[:6]:
            if callees:
                lines.append(f"  • `{fn}` calls: {', '.join(f'`{c}`' for c in callees[:5])}")

    if not lines:
        return ""

    return "\n📊 Source Code Analysis:\n" + "\n".join(lines) + "\n"


def _build_snapshot_hint(functions: list[str], language: str) -> str:
    """Suggest snapshot testing when the function likely returns a complex object."""
    if language == "python" or not functions:
        return ""
    return (
        "\n📸 Snapshot testing: for functions that return objects with multiple fields "
        "(not primitives), add one `expect(result).toMatchSnapshot()` test. "
        "This catches unintended structural changes.\n"
    )


def _build_jira_section(jira_ticket: dict | None) -> str:
    if not jira_ticket:
        return ""

    desc = jira_ticket["description"]
    if len(desc) > _JIRA_DESCRIPTION_LIMIT:
        logger.warning(
            "Jira description truncated from %d to %d chars for ticket %s",
            len(desc), _JIRA_DESCRIPTION_LIMIT, jira_ticket["id"],
        )
        desc = desc[:_JIRA_DESCRIPTION_LIMIT] + "…"

    ac = jira_ticket.get("acceptance_criteria", "")
    ac_line = f"Acceptance criteria:\n{ac}" if ac else ""

    return (
        f"\nJira ticket: {jira_ticket['id']} — {jira_ticket['summary']}\n"
        f"Feature description: {desc}\n"
        f"{ac_line}\n"
    )


def _build_heal_section(heal_context: str | None) -> str:
    if not heal_context:
        return ""
    return (
        "\n⚠️ SELF-HEALING MODE — A previous test generation attempt failed.\n"
        "Study the error and fix the root cause:\n"
        f"{heal_context}\n\n"
        "Generate corrected tests that specifically address the error above.\n"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_unit_test_prompt(
    code: str,
    file_path: str,
    language: str,
    framework: str,
    context: str = "",
    jira_ticket: dict | None = None,
    depth_instruction: str | None = None,
    heal_context: str | None = None,
    *,
    functions: list[str] | None = None,
    classes: list[str] | None = None,
    imports: list[str] | None = None,
    call_graph: dict[str, list[str]] | None = None,
    test_root: str = DEFAULT_TEST_ROOT,
    max_prompt_tokens: int = _DEFAULT_MAX_PROMPT_TOKENS,
) -> str:
    """Assemble a validated, budget-aware prompt for the test-generation LLM."""

    inputs = PromptInputs.build(
        code=code,
        file_path=file_path,
        language=language,
        framework=framework,
        context=context,
        jira_ticket=jira_ticket,
        depth_instruction=depth_instruction,
        heal_context=heal_context,
        functions=functions,
        classes=classes,
        imports=imports,
        call_graph=call_graph,
    )

    trimmed_context = _trim_context(inputs.context)
    context_section = (
        f"\nRelated test patterns from codebase:\n```{inputs.ext}\n{trimmed_context}\n```\n"
        if trimmed_context else ""
    )

    jira_section = _build_jira_section(inputs.jira_ticket)
    heal_section = _build_heal_section(inputs.heal_context)
    depth_section = f"\n{inputs.depth_instruction}\n" if inputs.depth_instruction else ""
    rel_import = _relative_import_path(inputs.file_path, inputs.language, test_root)
    import_hint = _build_import_hint(inputs.file_path, inputs.language, rel_import)
    ast_context = _build_ast_context(
        inputs.functions, inputs.classes, inputs.imports, inputs.call_graph, inputs.language
    )
    snapshot_hint = _build_snapshot_hint(inputs.functions, inputs.language)

    ac_requirement = (
        "6. Validate each acceptance criterion from the Jira ticket with a dedicated test\n"
        if inputs.jira_ticket else ""
    )

    prompt = f"""\
Generate expert-level unit tests for the {inputs.language} code below using {inputs.framework}.

━━━ Target file ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
File: `{inputs.file_path}`
{import_hint}
{ast_context}{snapshot_hint}{jira_section}{depth_section}{heal_section}
━━━ Source code to test ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```{inputs.ext}
{inputs.code}
```
{context_section}
━━━ Requirements ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. Test every exported function / class listed above
2. One test per logical branch (if/else, try/catch, ternary)
3. Edge cases: null, undefined, empty string, 0, empty array, very large values
4. Mock every external import listed in the analysis above
5. Group tests in `describe` blocks (Jest) or classes (pytest)
{ac_requirement}
━━━ Output format ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Return ONLY a single ```{inputs.ext} code block. No markdown, no explanation outside it.
"""

    total_tokens = _estimate_tokens(get_system_prompt(inputs.framework) + prompt)
    if total_tokens > max_prompt_tokens:
        raise PromptBuildError(
            f"Assembled prompt is ~{total_tokens} tokens, "
            f"exceeding the {max_prompt_tokens} token budget."
        )

    logger.debug("Prompt assembled: ~%d tokens", total_tokens)
    return prompt
