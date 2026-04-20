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
import re
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

3. NEVER mock the module you are testing. Only mock its EXTERNAL DEPENDENCIES (e.g. axios, databases, other services).
   ✗ WRONG:  import { calculateScore } from '../../../src/utils/score';
             jest.mock('../../../src/utils/score');   // ← NEVER do this — you just mocked the thing you're testing!
   ✓ CORRECT: import { calculateScore } from '../../../src/utils/score';
              jest.mock('axios');                     // ← mock dependencies of calculateScore, not calculateScore itself

4. Use `jest.clearAllMocks()` ALWAYS — NEVER just `clearAllMocks()` alone (it does not exist globally and throws ReferenceError):
   ✗ WRONG:  beforeEach(() => { clearAllMocks(); });
   ✓ CORRECT: beforeEach(() => { jest.clearAllMocks(); });

5. Use `toEqual` (deep equality) for objects and arrays — NOT `toBe`.
   `toBe` uses reference equality (===) and fails for objects.
   ✓ expect(result).toEqual({ score: 42, label: 'good' })

6. `jest.mock(...)` must be called at the TOP LEVEL (module scope), BEFORE describe blocks.
   ✓ jest.mock('axios');
   ✓ const mockFn = jest.fn();

7. TypeScript types/interfaces: ONLY use types that you explicitly import. Do NOT reference types from the module under test unless you add a named import for them.
   ✗ WRONG:  const x: MyType = { ... }   // if MyType was never imported
   ✓ CORRECT: import { calculateScore, type MyType } from '../../../src/utils/score';

8. Async tests: use `async/await` inside `it()` callbacks.
   ✓ it('fetches data', async () => { const result = await fn(); ... });

9. Testing thrown errors:
   ✓ expect(() => fn(bad)).toThrow('Expected message');
   ✓ await expect(asyncFn(bad)).rejects.toThrow('Expected message');

10. Snapshots: use `toMatchSnapshot()` ONLY when a function returns a complex object
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
- ALWAYS compute expected values from the actual source code constants/formulas — never guess or approximate.
  If the source has weights, thresholds, or formulas, manually evaluate them for your test inputs.
- ONLY test edge cases (null, undefined, out-of-range) when the source code EXPLICITLY handles them
  with a guard (e.g. `if (x == null)`, `try/catch`, `?? default`). If there is no guard in the source,
  do NOT write a test for that edge case — the function will crash and the test will fail.
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
import axios from 'axios';  // only imported if calculateScore uses it

// ── mock DEPENDENCIES (NOT the module under test) ────
// ✓ We mock axios because calculateScore calls it internally.
// ✗ We do NOT mock '../../../src/utils/score' — that is the code we are testing.
jest.mock('axios');
const mockedAxios = axios as jest.Mocked<typeof axios>;

// ── test suite ───────────────────────────────────────
describe('calculateScore', () => {
  beforeEach(() => {
    jest.clearAllMocks();  // always jest.clearAllMocks(), never clearAllMocks()
  });

  it('returns correct score for valid inputs', () => {
    // Arrange
    const input = { value: 10, weight: 2 };
    // Act
    const result = calculateScore(input);
    // Assert
    expect(result).toEqual({ score: 20, grade: 'B' });
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
- NEVER mock the file you are testing. Only mock its dependencies (e.g. axios, database)
- ALWAYS write jest.clearAllMocks() — NEVER just clearAllMocks() (throws ReferenceError)
- toEqual for objects, toBe for primitives
- jest.mock() at module top level before describe
- Only use TypeScript types you explicitly import
- COMPUTE expected values from the source constants/formulas — never guess them
- ONLY test null/undefined/edge inputs when the source explicitly guards against them

STRICT RULES for pytest:
- pytest.raises for exceptions
- One assert per logical check
- Fixture for shared setup
- Compute expected values from source code, never guess
- Only test None/empty inputs when the source has an explicit guard for them

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
        f"⚠ DO NOT call jest.mock('{rel_import}') — that is the file you are testing, not a dependency.\n"
        f"   Only use jest.mock() for its external dependencies (e.g. axios, database clients).\n"
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
        # Surface only genuinely external imports — exclude relative imports (start with . or ..)
        # and the source file itself. These are candidates for mocking.
        external = [
            imp for imp in imports
            if not re.search(r"""from\s+['"]\.""", imp)  # not relative
        ]
        if external:
            lines.append("External dependencies (mock ONLY these — not the module under test):")
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
3. Edge cases: ONLY test inputs that the source code explicitly handles (e.g. null checks, try/catch, default values).
   Do NOT pass null, undefined, or invalid inputs unless the source has an explicit guard — the function will crash.
4. Mock every external import listed in the analysis above
5. Group tests in `describe` blocks (Jest) or classes (pytest)
6. ⚠️ CRITICAL — COMPUTE expected values from the SOURCE CODE above, do NOT guess:
   - Read every constant, weight, threshold, and formula in the source
   - Manually calculate the exact output for each test input using those values
   - Write the computed result as the expected value in the assertion
   - Example: if source has `score = Math.round((a*0.35 + b*0.45 + c*0.20)*100)`,
     then for a=0.5, b=0.6, c=0.7 compute: round((0.5*0.35+0.6*0.45+0.7*0.20)*100) = round(57.5) = 58
     and write: expect(result.score).toBe(58)
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
