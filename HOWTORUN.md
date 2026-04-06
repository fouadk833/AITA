# How to Run AITA

## Prerequisites

| Tool | Version | Check |
|------|---------|-------|
| Python | 3.11+ | `python --version` |
| Node.js | 18+ | `node --version` |
| npm | 9+ | `npm --version` |
| Git | any | `git --version` |
| Ollama (if using local LLM) | latest | `ollama --version` |

---

## 1. Clone & enter the project

```bash
cd project_intern
```

---

## 2. Python environment

```bash
# Create virtualenv
python -m venv .venv311

# Activate it
# Windows:
.venv311\Scripts\activate
# Mac/Linux:
source .venv311/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## 3. Configure environment variables

Copy the `.env` file is already present. Open it and fill in your values:

```env
# Choose your LLM backend
LLM_BACKEND=ollama          # or: openai | anthropic | gemini

# Ollama (local — no API cost)
OLLAMA_MODEL=phi3:medium

# OpenAI
OPENAI_API_KEY=sk-proj-...
OPENAI_MODEL=gpt-4o-mini

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6

# Gemini
GEMINI_API_KEY=AIzaSy...
GEMINI_MODEL=gemini-2.0-flash

# GitHub (required — needs repo read + PR read permissions)
GITHUB_TOKEN=ghp_...
GITHUB_REPO=owner/repo

# Jira (optional — leave blank to skip)
JIRA_URL=https://yourworkspace.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=ATATT3x...
```

---

## 4. Set up your LLM

### Option A — Ollama (local, free)

```bash
# Install Ollama from https://ollama.com
# Then pull the model:
ollama pull phi3:medium

# Start the Ollama server (keep this running in a terminal):
ollama serve
```

Verify it works:
```bash
ollama list   # should show phi3:medium
```

### Option B — OpenAI / Anthropic / Gemini

Just set the matching API key in `.env` and set `LLM_BACKEND=openai` (or `anthropic` / `gemini`). No extra setup needed.

---

## 5. Start the backend API

```bash
# From project root, with virtualenv activated:
uvicorn api.main:app --reload --port 8000
```

You should see:
```
INFO | api.main | Starting AITA API — creating DB tables
INFO | api.main | DB ready
INFO | uvicorn | Application startup complete.
```

API is now available at: **http://localhost:8000**  
Health check: **http://localhost:8000/health**  
API docs: **http://localhost:8000/docs**

---

## 6. Start the dashboard (React frontend)

Open a **second terminal**:

```bash
cd dashboard
npm install        # only needed the first time
npm run dev
```

Dashboard is now available at: **http://localhost:5173**

---

## 7. Trigger a test run

### Via the dashboard

1. Open http://localhost:5173
2. Click **New Run**
3. Enter:
   - **Repo**: `owner/repo` (e.g. `AhmedSofrecom/HR`)
   - **PR number**: e.g. `4`
   - **Branch**: e.g. `KAN-2`
   - **Commit SHA**: the full or short SHA of the PR head commit
4. Click **Run** — watch the live log stream

### Via the API directly

```bash
curl -X POST http://localhost:8000/api/runs/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "repo": "AhmedSofrecom/HR",
    "pr_number": 4,
    "branch": "KAN-2",
    "commit_sha": "16b18cc231f2"
  }'
```

### Via GitHub webhook (automatic on every PR)

1. Go to your GitHub repo → **Settings → Webhooks → Add webhook**
2. **Payload URL**: `http://your-server:8000/api/webhooks/github`
3. **Content type**: `application/json`
4. **Events**: Pull requests
5. AITA will trigger automatically on every PR open/push

---

## 8. What happens during a run

```
1. fetch_jira     — looks for a Jira ticket ID in the commit message
2. analyze        — fetches PR diff from GitHub, runs AST analysis
3. risk_score     — scores each file (low / medium / high / critical)
4. clone_repo     — shallow-clones the PR branch into a temp folder
5. setup_workspace— installs Python deps (pip) + jest/ts-jest (npm)
6. generate_unit  — LLM generates unit tests per file
7. run_tests      — runs tests with jest (TS) or pytest (Python)
8. node_heal      — if tests fail, regenerates with error context (×3)
9. mutation_test  — mutates Python source to measure test strength
10. score_quality  — grades each test file A–F
11. reporter       — builds a Markdown summary
12. cleanup        — removes the temp clone
```

Live logs stream to the dashboard in real time.

---

## Switching LLM backends

Edit `.env` — no restart needed if you trigger a new run:

```env
# Use local Ollama (free, private)
LLM_BACKEND=ollama
OLLAMA_MODEL=phi3:medium

# Use OpenAI (best code quality)
LLM_BACKEND=openai
OPENAI_MODEL=gpt-4o-mini

# Use Anthropic Claude (best reasoning)
LLM_BACKEND=anthropic
ANTHROPIC_MODEL=claude-sonnet-4-6

# Use Gemini (fast, free tier)
LLM_BACKEND=gemini
GEMINI_MODEL=gemini-2.0-flash
```

Per-agent override (use a different model for generation vs. debugging):
```env
AGENT_GENERATOR_BACKEND=openai
AGENT_GENERATOR_MODEL=gpt-4o
AGENT_DEBUGGER_BACKEND=anthropic
AGENT_DEBUGGER_MODEL=claude-sonnet-4-6
```

---

## Tuning Ollama for lightweight models

```env
OLLAMA_TEMPERATURE=0.1       # lower = more deterministic code
OLLAMA_NUM_CTX=4096          # context window (phi3:medium = 4096)
OLLAMA_NUM_PREDICT=2048      # max tokens to generate
OLLAMA_REPEAT_PENALTY=1.1    # prevents repetition loops
```

---

## Common problems

| Problem | Fix |
|---------|-----|
| `Connection error` on Ollama | Run `ollama serve` in a terminal first |
| `model requires more memory` | Use a smaller model: `ollama pull qwen2.5-coder:1.5b` and set `OLLAMA_MODEL=qwen2.5-coder:1.5b` |
| `No tests found` in Jest | Check that `npm` is on your PATH and jest was installed |
| `GITHUB_TOKEN` permission error | Token needs `repo` scope (Settings → Developer settings → Tokens) |
| Dashboard can't reach API | Make sure backend is running on port 8000 and CORS is not blocked |
| ChromaDB errors on first start | Delete `.chroma/` folder and restart — it will rebuild |

---

## Folder layout

```
project_intern/
├── .env                  ← all config here
├── requirements.txt      ← Python deps
├── api/                  ← FastAPI backend (uvicorn api.main:app)
├── agents/               ← LangGraph pipeline nodes
├── core/                 ← LLM client, prompts, GitHub/Jira clients
├── runners/              ← Jest + pytest wrappers
├── dashboard/            ← React + Vite frontend (npm run dev)
├── aita.db               ← SQLite database (auto-created)
└── .chroma/              ← ChromaDB vector store (auto-created)
```
