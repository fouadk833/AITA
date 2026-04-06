# AITA — AI-powered Automated Testing Agent

## Overview

AITA (AI Test Agent) automatically generates, runs, and self-heals tests for every GitHub pull request. When a PR is opened, AITA fetches the diff, classifies the risk, generates unit/integration/E2E tests using an LLM, runs them, and posts a detailed report — all without manual intervention.

---

## System Architecture

```
GitHub PR
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│                    FastAPI Backend                        │
│  POST /api/runs/trigger  →  LangGraph Pipeline           │
│  WebSocket /ws/{run_id}  →  live log streaming           │
└──────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────── LangGraph Pipeline ──────────────────────────────┐
│                                                                              │
│  fetch_jira → analyze → risk_score → clone_repo → setup_workspace           │
│       │                                                                      │
│       └──→ generate_unit ──→ generate_integration ──→ generate_e2e          │
│                 │  (abort on LLM error)                                      │
│                 ▼                                                            │
│            run_tests ◄──────────────────────────┐                           │
│                 │                               │                           │
│         failed? │ (max 3 attempts)         node_heal                        │
│                 │                               │                           │
│          mutation_test → score_quality → debug → reporter → cleanup         │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
    │
    ▼
┌──────────────────────────────────┐
│  React Dashboard                 │
│  • Live run log (SSE/WebSocket)  │
│  • Test results & quality grade  │
│  • AI failure analysis           │
└──────────────────────────────────┘
```

---

## Agent Pipeline — Step by Step

### 1. `fetch_jira` — Jira Context Fetcher

**What it does:**
- Reads the commit message of the PR head commit
- Extracts a Jira ticket ID using a regex pattern (e.g. `KAN-123`, `PROJ-42`)
- Fetches the ticket from Jira: summary, description, acceptance criteria
- If no ticket is found or Jira is unreachable, continues gracefully with `jira_ticket=None`

**Why it matters:**
The Jira ticket is injected into the unit test prompt. Acceptance criteria are turned into test cases, ensuring generated tests map directly to business requirements.

**Output:** `state["jira_ticket"]` — dict with `id`, `summary`, `description`, `acceptance_criteria`

---

### 2. `analyze` — Code Analyzer Agent (`agents/analyzer.py`)

**What it does:**
- Calls `GitHubClient.get_pr_diff()` to fetch the list of changed files and their diffs
- For each file:
  - Detects language from extension (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`)
  - Skips test files and unsupported formats
  - Runs **AST analysis** (`core/ast_analyzer.py`) to extract:
    - Function names, class names, call graph, imports
    - Cyclomatic complexity score
  - Falls back to regex if AST parsing fails
  - Fetches the full file content at the PR commit SHA
- Maps GitHub change status (`added`, `modified`, `removed`, `renamed`) to `ChangeType`

**Key data structure:** `FileChange`
```python
@dataclass
class FileChange:
    path: str                     # relative path from repo root
    language: str                 # "python" | "typescript" | "javascript"
    change_type: ChangeType       # ADDED | MODIFIED | DELETED | RENAMED
    diff: str                     # raw unified diff patch
    full_content: str             # full source file at PR head
    functions_changed: list[str]  # function/method names touched
    classes_changed: list[str]
    complexity_score: float       # cyclomatic complexity
    additions: int
    deletions: int
```

**Output:** `state["file_changes"]` — list of `FileChange` objects

---

### 3. `risk_score` — Risk Scorer (`agents/risk_scorer.py`)

**What it does:**
- Scores every non-deleted file on a composite risk scale
- Factors:
  - **Complexity** — cyclomatic complexity from AST analysis
  - **Change size** — additions + deletions
  - **Historical failures** — how often tests for this file failed in the past (SQLite DB)
  - **Critical paths** — configurable path patterns (e.g. `auth/`, `payments/`) that always get high risk
- Outputs a `FileRisk` with a **tier**: `low`, `medium`, `high`, or `critical`

**Why it matters:**
The tier controls how many tests the LLM generates:

| Tier | Minimum tests | Instructions |
|------|---------------|--------------|
| `critical` | 15 | All branches, security edge cases, concurrent access |
| `high` | 10 | All branches, error handling, integration contracts |
| `medium` | 6 | Happy path + 2 edge cases |
| `low` | 3 | Main contract + one error case |

**Output:** `state["risk_scores"]` — dict of `file_path → FileRisk`

---

### 4. `clone_repo` — Repository Cloner

**What it does:**
- Runs `git clone --branch <branch> --depth 1` into a temporary directory (`C:\Temp\aita_XXXXX`)
- The branch name comes from the PR head branch (e.g. `KAN-2`)
- Uses `GITHUB_TOKEN` for authentication on private repos

**Output:** `state["workspace_dir"]` — path to the cloned repo

---

### 5. `setup_workspace` — Dependency Installer

**What it does:**
- Installs Python dependencies: reads `requirements.txt` or `pyproject.toml`, installs with `pip` into `.ws_deps/` to avoid polluting the global environment
- Installs Node dependencies: runs `npm install` if `package.json` exists
- Ensures Jest + ts-jest are available for TypeScript test execution:
  - Checks if `node_modules/.bin/jest` already exists
  - If not, installs `jest`, `ts-jest`, `@types/jest`, `typescript`
- Writes an AITA Jest config (`jest.aita.config.js`) if no native Jest config exists:

```js
module.exports = {
  testEnvironment: 'node',
  preset: 'ts-jest',
  transform: { '^.+\.(ts|tsx)$': ['ts-jest', { diagnostics: false }] },
  moduleDirectories: ['node_modules', '<rootDir>'],
  globals: { 'ts-jest': { tsconfig: { strict: false, esModuleInterop: true } } },
};
```

---

### 6. `generate_unit` — Unit Test Generator (`agents/unit_generator.py`)

**What it does:**
- For each non-deleted file change, calls the LLM to generate unit tests
- Builds a prompt that includes:
  - The full source code of the file
  - The language and framework (`jest` for TS/JS, `pytest` for Python)
  - The **correct relative import path** from the test file's location to the source
  - Risk-tier depth instructions (e.g. "generate minimum 15 tests")
  - Jira acceptance criteria (if available)
  - Vector store context: similar tests from previous runs retrieved via ChromaDB
- Streams the LLM response token-by-token (live preview in dashboard)
- Validates and saves the test to: `__aita_tests__/{frontend|backend}/unit/{stem}.test.ts`
- Skips saving if the LLM returns an empty response
- On LLM error: sets `state["error"]` and aborts the pipeline (skips to reporter)

**Prompt structure:**
```
[System] You are a senior QA engineer...
[User]   Generate unit tests for client/src/lib/utils/matchScore.ts
         Import path: ../../../client/src/lib/utils/matchScore
         Risk: LOW — generate minimum 3 tests
         Code: <full source>
         Requirements: cover all exports, branches, edge cases...
```

**Output:** `state["generated_tests"]["unit"]` — list of absolute test file paths

---

### 7. `generate_integration` — Integration Test Generator

**What it does:**
- Scans changed files for API/service patterns: paths containing `controller`, `router`, `route`, `endpoint`, or `service`
- Generates integration tests only for those files
- Uses a separate prompt tuned for HTTP-level testing (supertest, httpx, etc.)

**Output:** `state["generated_tests"]["integration"]`

---

### 8. `generate_e2e` — E2E Test Generator

**What it does:**
- Scans TypeScript files for UI component patterns: paths containing `page`, `component`, `view`, or `screen`
- Generates Playwright/Cypress E2E tests only for those files

**Output:** `state["generated_tests"]["e2e"]`

---

### 9. `run_tests` — Test Runner

**What it does:**
- Runs each generated test file through the appropriate runner:
  - `.test.ts` / `.test.js` → **JestRunner** (`runners/jest_runner.py`)
  - `test_*.py` / `*_test.py` → **PytestRunner** (`runners/pytest_runner.py`)
- For Jest: uses `npx jest <relative-path> --json` (relative path avoids Windows 8.3 short-name regex mismatch)
- Collects: `passed`, `failed`, `skipped`, `duration_seconds`, failure details
- Detects suite-level execution errors (`testExecError`) — e.g. "Cannot find module X" — and counts them as failures
- Streams stdout/stderr to the dashboard in real time

**Output:** `state["run_results"]` — `{ passed, failed, skipped, duration_seconds, failures: [...] }`

---

### 10. `node_heal` — Self-Healing Agent (max 3 attempts)

**What it does:**
- Triggered when `run_results.failed > 0` and `heal_count < 3`
- For each failing test (cap: 5 per attempt):
  1. Calls `DebuggerAgent` to analyze the failure and produce a `root_cause` + `fix_suggestion`
  2. Re-generates the test with the error context injected into the prompt
  3. Overwrites the previous test file
  4. Indexes the failure pattern in ChromaDB for future runs
- After healing, routes back to `run_tests` for re-execution
- After 3 attempts with no improvement, proceeds to reporting

**Healing prompt injection:**
```
⚠️ SELF-HEALING MODE — A previous attempt failed. Fix the issues below:
Error: Cannot find module '../utils/matchScore'
Root cause: incorrect relative import path
Fix suggestion: use '../../../client/src/lib/utils/matchScore'
```

---

### 11. `mutation_test` — Mutation Testing Agent (`agents/mutation_agent.py`)

**What it does:**
- Only runs on Python files (uses Python's `ast` module for AST mutation)
- Applies 4 mutation operator classes:
  - **AOR** (Arithmetic Operator Replacement): `+` ↔ `-`, `*` ↔ `/`
  - **ROR** (Relational Operator Replacement): `>` ↔ `>=`, `==` ↔ `!=`, etc.
  - **LCR** (Logical Connector Replacement): `and` ↔ `or`
  - **SDL** (Statement Deletion): removes individual statements
- For each mutant: re-runs the generated tests
- Computes **mutation score** = killed mutants / total mutants × 100%
- Fails the pipeline if score < configured threshold

**Output:** `state["mutation_reports"]` — dict of `file_path → MutationReport`

---

### 12. `score_quality` — Quality Scorer (`agents/quality_scorer.py`)

**What it does:**
- Scores each generated test file on 4 dimensions:

| Dimension | How measured |
|-----------|-------------|
| **Assertion strength** | Static analysis: counts `expect`, `assert`, `assertEqual` calls; penalizes tests with no assertions |
| **Branch coverage** | Reads coverage JSON report if available |
| **Mutation kill rate** | From `MutationReport` |
| **Flakiness penalty** | Static scan for `setTimeout`, `Date.now()`, random calls, hardcoded timestamps |

- Combines into a composite score (0–100) and assigns a letter grade (A–F)

**Output:** `state["quality_scores"]` — dict of `test_file → TestQualityScore`

---

### 13. `debug` — Failure Debugger (`agents/debugger.py`)

**What it does:**
- Runs when there are still failing tests after self-healing exhaustion
- Calls the LLM with the test name, error message, stack trace, and source code
- Expects a structured JSON response:
```json
{
  "root_cause": "Import path resolves to wrong file",
  "fix_suggestion": "Change import to '../../../utils/matchScore'",
  "fix_code": "import { calculateMatchScore } from '../../../utils/matchScore';",
  "confidence": 87
}
```
- Confidence score: 🔴 < 40%, 🟡 < 75%, 🟢 ≥ 75%

**Output:** `state["debug_results"]`

---

### 14. `reporter` — Report Builder (`agents/reporter.py`)

**What it does:**
- Assembles a Markdown PR comment with:
  - Summary table (total/passed/failed/skipped/pass-rate/duration)
  - Coverage table (if available)
  - Test quality scores table (grade, assertion score, branch coverage, mutation kill rate)
  - Mutation testing results per file
  - Collapsible failure analysis sections with root cause + fix suggestions

**Output:** `state["report"]` — Markdown string, posted to GitHub PR

---

### 15. `cleanup` — Workspace Cleaner

**What it does:**
- Removes the temporary clone directory (`shutil.rmtree`)
- Always runs, even when earlier nodes error

---

## LLM Backends

Configure via `LLM_BACKEND` in `.env`:

| Backend | Env var | Default model | Notes |
|---------|---------|---------------|-------|
| `openai` | `OPENAI_API_KEY` | `gpt-4o-mini` | Best balance of cost/quality |
| `anthropic` | `ANTHROPIC_API_KEY` | `claude-sonnet-4-6` | Strongest for code |
| `gemini` | `GEMINI_API_KEY` | `gemini-2.0-flash` | Fast, free tier available |
| `ollama` | — | `phi3:medium` | Local, no API cost, needs `ollama serve` |

All backends support streaming (tokens appear live in the dashboard). Connection errors retry up to 3× with exponential backoff.

---

## Key Configuration Files

| File | Purpose |
|------|---------|
| `.env` | API keys, backend selection, model names, DB/Chroma paths |
| `aita.yml` (optional) | Per-repo overrides: risk weights, critical paths, mutation threshold |
| `jest.aita.config.js` | Auto-generated Jest config for workspaces without one |

---

## Data Flow Summary

```
PR diff (GitHub API)
    │
    ├─ FileChange objects (path, language, functions, complexity, full source)
    │       │
    │       ├─ RiskScorer → tier (low/medium/high/critical)
    │       │
    │       └─ UnitGeneratorAgent
    │               │  prompt = source + import path + risk depth + Jira ACs
    │               └─ LLM (OpenAI / Claude / Gemini / Ollama)
    │                       │
    │                       └─ test code → saved to __aita_tests__/
    │                                               │
    │                               JestRunner / PytestRunner
    │                                               │
    │                               ┌───────────────┴──────────────┐
    │                           passed ✅                     failed ❌
    │                               │                              │
    │                           QualityScorer              HealAgent (×3)
    │                           MutationAgent                      │
    │                               │                         DebuggerAgent
    │                               └──────────┬────────────────────┘
    │                                          │
    │                                     ReporterAgent
    │                                          │
    └──────────────────────────────── Markdown PR Comment
```

---

## Directory Structure

```
project_intern/
├── agents/
│   ├── analyzer.py          # Diff parsing, AST analysis, FileChange extraction
│   ├── debugger.py          # LLM-powered failure root-cause analysis
│   ├── flakiness_detector.py # Static scan for non-deterministic patterns
│   ├── mutation_agent.py    # AST-level mutation testing (Python only)
│   ├── orchestrator.py      # LangGraph pipeline definition & graph wiring
│   ├── quality_scorer.py    # 4-dimension test quality scoring
│   ├── reporter.py          # Markdown PR comment builder
│   ├── risk_scorer.py       # Composite risk scoring & depth instructions
│   └── unit_generator.py   # LLM prompt building & test file saving
├── core/
│   ├── ast_analyzer.py      # Tree-sitter AST parsing
│   ├── config.py            # AITAConfig from aita.yml
│   ├── github_client.py     # GitHub REST API wrapper
│   ├── jira_client.py       # Jira REST API wrapper
│   ├── llm_client.py        # Unified LLM interface (OpenAI/Anthropic/Gemini/Ollama)
│   ├── vector_store.py      # ChromaDB: test relationships & failure patterns
│   └── prompts/
│       ├── unit_test_prompt.py   # Unit test prompt builder
│       └── debugger_prompt.py    # Debugger prompt builder
├── runners/
│   ├── base_runner.py       # RunResult dataclass, _exec wrapper
│   ├── jest_runner.py       # TypeScript/JS test execution via npx jest
│   └── pytest_runner.py     # Python test execution via pytest
├── api/
│   ├── main.py              # FastAPI app, startup, middleware
│   └── routers/
│       ├── runs.py          # POST /runs/trigger, GET /runs/{id}
│       ├── ws.py            # WebSocket live log streaming
│       └── webhooks.py      # GitHub webhook receiver
└── dashboard/               # React frontend
    └── src/
        ├── pages/RunDetail.tsx   # Live run view
        └── hooks/useRunWebSocket.ts
```
