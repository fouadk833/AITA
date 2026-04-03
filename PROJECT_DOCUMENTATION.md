# AITA — Autonomous Intelligence Testing Agent

## Overview

**AITA** (Autonomous Intelligence Testing Agent) is an AI-powered testing platform that automatically generates, runs, analyzes, and reports tests across the full testing pyramid (unit, integration, E2E). Built on Claude AI (Anthropic), LangGraph, and Python/FastAPI, it acts as a dedicated QA layer that continuously validates code changes triggered by GitHub pull requests.

**Key Goals:**
- Automate test generation for unit, integration, and E2E scenarios
- Integrate seamlessly into existing CI/CD pipelines (GitHub Actions)
- Detect and manage flaky tests automatically
- Provide dashboards and insights into test quality over time

**Success Metrics:**
- Test generation time per PR: < 3 minutes
- Generated test pass rate: > 85% on first run
- Coverage improvement per sprint: +5% minimum
- Flaky test detection rate: > 90%

---

## Architecture Overview

```
GitHub Pull Request
        │
        ▼
GitHub Actions Workflow
        │
        ▼
POST /api/trigger  ──────────────────────────────┐
        │                                        │
        ▼                                        ▼
LangGraph Orchestrator               React Dashboard (port 5173)
        │
  ┌─────┴─────────────────────────┐
  │  Agent State Machine          │
  │  fetch_jira → analyze →       │
  │  clone_repo → setup →         │
  │  generate_unit →              │
  │  generate_integration →       │
  │  generate_e2e →               │
  │  run_tests → [debug] →        │
  │  reporter → cleanup           │
  └─────┬─────────────────────────┘
        │
   ┌────┴────┐
   │         │
   ▼         ▼
SQLite    GitHub PR Comment
(aita.db)
```

---

## Project Structure

```
project_intern/
├── agents/                     # Multi-agent AI orchestration
│   ├── orchestrator.py         # LangGraph state machine
│   ├── analyzer.py             # Git diff / file change extraction
│   ├── unit_generator.py       # Claude-powered unit test generation
│   ├── integration_generator.py# API/service integration test generation
│   ├── e2e_generator.py        # Playwright E2E test generation
│   ├── debugger.py             # AI root cause analysis for failures
│   └── reporter.py             # GitHub PR comment formatter
│
├── core/                       # Integrations and utilities
│   ├── llm_client.py           # Anthropic / Ollama LLM abstraction
│   ├── vector_store.py         # ChromaDB semantic search
│   ├── github_client.py        # GitHub API wrapper
│   ├── jira_client.py          # Jira API wrapper
│   └── prompts/                # Claude prompt templates
│       ├── unit_test_prompt.py
│       ├── integration_test_prompt.py
│       ├── e2e_test_prompt.py
│       └── debugger_prompt.py
│
├── api/                        # FastAPI REST backend
│   ├── main.py                 # App setup, CORS, lifespan
│   ├── db/
│   │   ├── database.py         # SQLAlchemy async engine
│   │   └── models.py           # ORM models (TestRun, Coverage, Flakiness)
│   ├── models/
│   │   └── schemas.py          # Pydantic request/response schemas
│   ├── routers/                # Route handlers
│   │   ├── runs.py             # /api/runs, /api/trigger, /api/runs/sync
│   │   ├── agents.py           # /api/agents/status
│   │   ├── coverage.py         # /api/coverage
│   │   ├── flakiness.py        # /api/flakiness
│   │   ├── branches.py         # /api/branches
│   │   └── pulls.py            # /api/pulls
│   └── services/
│       └── run_service.py      # Database CRUD operations
│
├── runners/                    # Test execution wrappers
│   ├── base_runner.py          # RunResult dataclass
│   ├── jest_runner.py          # Jest --json execution
│   └── pytest_runner.py        # pytest --json-report execution
│
├── dashboard/                  # React + Vite frontend
│   ├── src/
│   │   ├── App.tsx             # Router setup
│   │   ├── api/client.ts       # Axios API client
│   │   ├── pages/              # Dashboard, Runs, Coverage, Flakiness, Branches
│   │   └── components/         # AgentStatusCard, TestRunTable, Charts, etc.
│   └── package.json
│
├── quality/                    # Quality gates
│   ├── strategy.md             # Test pyramid ratios (70/20/10)
│   ├── thresholds.json         # Coverage minimums, flakiness limits
│   └── pact/                   # Contract testing (placeholder)
│
├── infra/
│   ├── docker-compose.test.yml # PostgreSQL, Redis, API containers
│   └── .github/workflows/
│       └── ai-test-agent.yml   # GitHub Actions trigger workflow
│
├── requirements.txt            # Python dependencies
└── prompt.md                   # Original project specification
```

---

## Modules

### `agents/orchestrator.py`

Creates and executes a **LangGraph state machine**. The graph is built with named nodes connected by edges, including a conditional branch that routes to the debugger only when tests fail.

**Agent State (`AgentState` TypedDict):**

| Field | Type | Description |
|-------|------|-------------|
| `repo` | str | `org/repo` identifier |
| `pr_number` | int | GitHub PR number |
| `branch` | str | Head branch name |
| `commit_sha` | str | Head commit SHA |
| `file_changes` | list | Parsed `FileChange` objects |
| `jira_ticket` | dict | Jira issue details (optional) |
| `workspace_dir` | str | Ephemeral clone path |
| `generated_tests` | list | Paths to generated test files |
| `run_results` | dict | Execution metrics |
| `debug_results` | list | Failure analyses |
| `report` | str | Final markdown report |

**Graph Flow:**
```
fetch_jira → analyze → clone_repo → setup_workspace
  → generate_unit → generate_integration → generate_e2e
  → run_tests → [failures?] debug → reporter → cleanup → END
```

---

### `agents/analyzer.py`

Extracts meaningful context from code changes.

**`FileChange` Dataclass:**
- `path`, `language`, `change_type` (added / modified / deleted / renamed)
- `diff` — raw unified diff
- `full_content` — complete file at HEAD
- `functions_changed` — functions with added lines (regex-extracted)

**Language Support:** `.py`, `.ts`, `.tsx`, `.js`, `.jsx`

**Methods:**
- `analyze_repo()` — local git diffs between base/head commits
- `analyze_from_github()` — GitHub API diffs for a PR
- `detect_language()` — maps file extension to language string
- `extract_changed_functions()` — regex-based function name extraction

---

### `agents/unit_generator.py`

Generates unit tests for changed functions/classes.

- **Python** → `pytest`
- **TypeScript/JavaScript** → `vitest`
- Semantic search on vector store retrieves related existing tests as context
- Output path: `tests/{frontend|backend}/unit/{stem}.test.{ext}`

---

### `agents/integration_generator.py`

Generates API/service integration tests.

- **NestJS controllers** → `jest + supertest`
- **FastAPI routers** → `pytest + httpx`
- Detects API patterns (`controller`, `router`, `endpoint`, `service`)
- Parses OpenAPI specs when available
- Output path: `tests/backend/{nestjs|fastapi}/{stem}.test.ts|_test.py`

---

### `agents/e2e_generator.py`

Generates Playwright E2E tests for React components.

- Infers routes from file paths (e.g. `src/pages/Login.tsx` → `/login`)
- Covers happy path and error states
- Output path: `tests/frontend/e2e/{stem}.spec.ts`

---

### `agents/debugger.py`

AI-powered root cause analysis of test failures.

**`DebugResult` Dataclass:**
- `test_name`, `root_cause`, `fix_suggestion`, `fix_code`, `confidence` (0–100)

Outputs structured JSON with a concrete fix suggestion and optional fix code snippet.

---

### `agents/reporter.py`

Formats test results into GitHub PR comments.

- `build_pr_comment()` — markdown table (passed/failed/skipped/rate), coverage, expandable failure details
- `build_summary_json()` — JSON for API responses

---

### `core/llm_client.py`

Unified LLM interface selectable via `LLM_BACKEND` env var.

| Backend | Model | Notes |
|---------|-------|-------|
| `anthropic` | `claude-sonnet-4-6` | Official SDK, API key auth |
| `ollama` | `gemma3` | HTTP calls to local Ollama server |

**Methods:** `generate()`, `generate_async()`, `extract_code_block()`

---

### `core/vector_store.py`

ChromaDB-backed semantic search over the codebase.

- Collection: `"codebase"`
- `index_file()` / `index_directory()` — add files to the vector DB
- `search(query, n_results)` — returns top-N semantically similar chunks
- Used by test generators to retrieve relevant existing tests as few-shot context

---

### `core/github_client.py`

PyGithub wrapper with retry logic (5 attempts, 1s–16s exponential backoff).

| Method | Description |
|--------|-------------|
| `get_pr_diff()` | Fetch file diffs for a PR |
| `get_file_content()` | Read file at a specific ref |
| `post_pr_comment()` | Post markdown comment on PR |
| `get_changed_files()` | List filenames changed in PR |
| `get_prs()` | PRs with full metadata |
| `list_open_prs()` | Fetch all open PRs |
| `get_commit_message()` | Full commit message text |

---

### `core/jira_client.py`

Jira REST API integration.

- `extract_task_id(text)` — regex parse ticket ID (e.g. `HR-123`) from commit message
- `get_ticket(task_id)` — fetch issue details
- `_extract_acceptance_criteria()` — parse AC section from description
- Output used by unit/integration generators to include AC context in Claude prompts

---

### `core/prompts/`

Claude prompt templates enforcing output constraints:

| File | Generated For | Key Rules |
|------|--------------|-----------|
| `unit_test_prompt.py` | Unit tests | Valid code only, edge cases, deterministic; pytest or Vitest examples |
| `integration_test_prompt.py` | Integration tests | 200/400/401/403/404 coverage; setup/teardown required |
| `e2e_test_prompt.py` | Playwright tests | Use `getByRole/Label/TestId` selectors; `await expect()` after each action |
| `debugger_prompt.py` | Failure analysis | Structured JSON output: root_cause, fix_suggestion, fix_code, confidence |

---

### `runners/`

| Runner | Execution | Output Parsing |
|--------|-----------|----------------|
| `jest_runner.py` | `jest --json` | Parses JSON report |
| `pytest_runner.py` | `pytest --json-report` | Writes/reads/deletes temp JSON report |

Both return a `RunResult` dataclass: `passed`, `failed`, `skipped`, `duration_seconds`, `output`, `error`, `exit_code`.

---

### `api/main.py`

FastAPI application with:
- CORS enabled
- Lifespan: creates DB tables on startup
- Mounts all routers under `/api`
- `GET /health` — liveness check

---

### `api/db/models.py`

**`TestRunModel`**

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID | Primary key |
| `repo` | str | `org/repo` |
| `pr_number` | int | PR number |
| `branch` | str | Branch name |
| `commit_sha` | str | Commit SHA |
| `status` | str | running / passed / failed / error |
| `passed` | int | Passed test count |
| `failed` | int | Failed test count |
| `skipped` | int | Skipped test count |
| `duration_seconds` | float | Pipeline duration |
| `generated_tests` | JSON | List of test file paths |
| `debug_results` | JSON | Failure analyses |
| `report` | text | Markdown PR comment |
| `jira_task_id` | str | Extracted Jira ticket ID |

**`CoverageModel`** — `service`, `lines`, `branches`, `functions`, `statements`, `timestamp`

**`FlakinessModel`** — `test_name`, `file_path`, `score`, `failure_count`, `run_count`, `last_seen`

---

### `api/routers/`

| Route | Method | Description |
|-------|--------|-------------|
| `/api/runs` | GET | List recent test runs (limit 50) |
| `/api/runs/{run_id}` | GET | Get single run |
| `/api/trigger` | POST | Trigger pipeline for a PR |
| `/api/runs/sync` | POST | Queue runs for all open PRs |
| `/api/agents/status` | GET | In-memory agent state registry |
| `/api/coverage` | GET | List coverage reports |
| `/api/flakiness` | GET | List flaky tests |
| `/api/branches/local` | GET | Local git branches |
| `/api/branches/remote` | GET | Remote branches from GitHub |
| `/api/pulls` | GET | GitHub PRs with metadata |

---

### `dashboard/`

React 18 + Vite SPA served on port 5173.

**Routes:**
- `/` — Overview dashboard
- `/runs` — Test run history
- `/coverage` — Coverage trend charts
- `/flakiness` — Flakiness heatmap
- `/branches` — Branch browser

**Key Dependencies:** React Query (caching), Recharts (charts), Axios (HTTP), Tailwind CSS (styling), Lucide React (icons).

---

## Data Flow

### Pipeline Execution

```
1. GitHub PR opened/updated
2. GitHub Actions: POST /api/trigger {pr_number, branch, commit_sha, repo}
3. API creates TestRunModel (status=running), returns job_id
4. Background task runs LangGraph orchestrator:
   a. fetch_jira      — extract Jira ticket from commit message
   b. clone_repo      — git clone PR branch to temp dir
   c. setup_workspace — pip install / npm install
   d. analyze         — produce FileChange list from diffs
   e. generate_unit   — Claude generates unit tests
   f. generate_integration — Claude generates integration tests
   g. generate_e2e    — Claude generates Playwright tests
   h. run_tests       — Jest + Pytest execution
   i. debug (if failures) — Claude analyzes failures
   j. reporter        — format markdown report
   k. cleanup         — delete temp workspace
5. Update TestRunModel with all results
6. GitHub Actions polls GET /api/runs/{job_id} every 15s (max 10 min)
7. On completion, Actions posts PR comment with report
```

### Vector Search Flow

```
Analyzer extracts file content
        ↓
Generator calls vector_store.search("unit tests for <function>")
        ↓
Top-3 semantic matches included in Claude prompt
        ↓
Claude generates better-contextualized tests
```

---

## Quality Gates

### Test Pyramid (`quality/strategy.md`)

| Layer | Ratio | Framework |
|-------|-------|-----------|
| Unit | 70% | Vitest/Jest (TS), pytest (Python) |
| Integration | 20% | Supertest (NestJS), HTTPX (FastAPI) |
| E2E | 10% | Playwright |

### Coverage Thresholds (`quality/thresholds.json`)

| Scope | Lines | Branches | Functions | Statements |
|-------|-------|----------|-----------|------------|
| Global | 80% | 70% | 80% | 80% |
| Frontend | 75% | 65% | — | — |
| API | 85% | 75% | — | — |
| Agents | 70% | 60% | — | — |

### Flakiness Scoring

| Score | Action |
|-------|--------|
| ≥ 70 | Quarantine: auto-skip + alert |
| 40–69 | Warning: flag for review |
| < 40 | Stable: no action |

Max allowed flaky tests: **5**

---

## Environment Variables

```bash
# LLM Backend (anthropic | ollama)
LLM_BACKEND=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6

# Ollama (alternative to Anthropic)
# OLLAMA_MODEL=gemma3:1b
# OLLAMA_BASE_URL=http://localhost:11434

# GitHub
GITHUB_TOKEN=ghp_...
GITHUB_REPO=org/repo

# Jira (optional)
JIRA_URL=https://domain.atlassian.net
JIRA_EMAIL=user@example.com
JIRA_API_TOKEN=...

# Database
DATABASE_URL=sqlite+aiosqlite:///./aita.db

# Vector Store
CHROMA_PERSIST_DIR=.chroma

# Deployment
AITA_API_URL=http://localhost:8000
```

---

## Dependencies

### Python (`requirements.txt`)

| Package | Version | Purpose |
|---------|---------|---------|
| `anthropic` | 0.41.0 | Claude API client |
| `langgraph` | 0.2.60 | Stateful agent orchestration |
| `langchain` | 0.3.13 | LLM utility chains |
| `langchain-anthropic` | 0.3.3 | LangChain ↔ Anthropic bridge |
| `chromadb` | 0.6.3 | Vector DB |
| `gitpython` | 3.1.44 | Git operations |
| `PyGithub` | 2.5.0 | GitHub API |
| `jira` | 3.8.0 | Jira API |
| `tree-sitter` | 0.23.2 | AST parsing |
| `fastapi` | latest | Web framework |
| `sqlalchemy` | latest | Async ORM |
| `pydantic` | 2.10.3 | Schema validation |
| `pytest` | 8.3.4 | Test runner |
| `pytest-asyncio` | 0.24.0 | Async test support |
| `pytest-json-report` | 1.5.0 | JSON test reports |
| `httpx` | 0.27.2 | Async HTTP client |
| `ollama` | 0.4.4 | Ollama client (optional) |
| `python-dotenv` | 1.0.1 | Env var loading |
| `aiosqlite` | latest | Async SQLite driver |

### Node.js (`dashboard/package.json`)

| Package | Version | Purpose |
|---------|---------|---------|
| `react` | 18.3.1 | UI framework |
| `react-router-dom` | 6.28 | Client-side routing |
| `@tanstack/react-query` | 5.62 | Data fetching & caching |
| `axios` | 1.7.9 | HTTP client |
| `recharts` | 2.13.3 | Charts (line, bar, heatmap) |
| `lucide-react` | latest | Icons |
| `tailwindcss` | 3.4.17 | Utility CSS |
| `typescript` | 5.7.2 | Type safety |
| `vite` | 6.0.5 | Build tool & dev server |

---

## Setup & Usage

### 1. Install Dependencies

```bash
cp .env.example .env
# Fill in ANTHROPIC_API_KEY, GITHUB_TOKEN, etc.

pip install -r requirements.txt

cd dashboard && npm install && cd ..
```

### 2. Start the API

```bash
# If using Ollama (optional)
ollama serve

# Start FastAPI
uvicorn api.main:app --reload --port 8000
```

### 3. Start the Dashboard

```bash
cd dashboard
npm run dev
# Visit http://localhost:5173
```

### 4. Trigger a Test Run

```bash
# Manual API trigger
curl -X POST http://localhost:8000/api/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "pr_number": 42,
    "branch": "feature/auth",
    "commit_sha": "abc123",
    "repo": "org/repo",
    "changed_files": []
  }'

# Sync all open PRs
curl -X POST http://localhost:8000/api/runs/sync
```

### 5. GitHub Actions (Automatic)

The workflow at `infra/.github/workflows/ai-test-agent.yml` triggers on `pull_request` events and:
1. POSTs to `/api/trigger` with PR metadata
2. Polls `/api/runs/{job_id}` every 15s (max 10 min)
3. Posts the generated report as a PR comment

**Required secrets:** `AITA_API_URL`, `GITHUB_TOKEN`

### 6. Docker (Optional)

```bash
docker-compose -f infra/docker-compose.test.yml up
```

Spins up PostgreSQL 16, Redis 7, and the API service.

---

## Implementation Status

| Phase | Feature | Status |
|-------|---------|--------|
| 1 | LangGraph orchestration | Done |
| 1 | File analysis (git + GitHub API) | Done |
| 1 | Unit test generation | Done |
| 1 | Jest/Pytest execution | Done |
| 1 | FastAPI backend + database | Done |
| 2 | Integration test generation | Done |
| 2 | E2E Playwright generation | Done |
| 2 | GitHub PR trigger + commenting | Done |
| 3 | AI failure debugging | Done |
| 3 | React dashboard | Done |
| 3 | Docker Compose setup | Done |
| 4 | Allure Report integration | In Progress |
| 4 | Contract testing (Pact) | Planned |
| 4 | Slack notifications | Planned |
| 4 | Advanced flakiness scoring | Planned |
| 4 | PR comment feedback loop | Planned |
