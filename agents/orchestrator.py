from __future__ import annotations
import asyncio
import logging
import os
import queue as _stdlib_queue
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Annotated, Awaitable, Callable, Optional, TypedDict
from langgraph.graph import StateGraph, END

from agents.analyzer import AnalyzerAgent, FileChange
from agents.unit_generator import UnitGeneratorAgent
from agents.integration_generator import IntegrationGeneratorAgent
from agents.e2e_generator import E2EGeneratorAgent
from agents.debugger import DebuggerAgent, DebugResult
from agents.reporter import ReporterAgent
from core.llm_client import LLMClient, AgentClients
from core.vector_store import CodeVectorStore
from runners.jest_runner import JestRunner
from runners.pytest_runner import PytestRunner

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]


# ------------------------------------------------------------------
# State reducers
# ------------------------------------------------------------------

def _error_reducer(current: Optional[str], new: Optional[str]) -> Optional[str]:
    """Keep the first error that occurs — never overwrite with None from later nodes."""
    if current:
        return current
    return new


def _logs_reducer(current: Optional[list], new: Optional[list]) -> Optional[list]:
    """Accumulate console logs across nodes instead of overwriting."""
    if not current:
        return new
    if not new:
        return current
    return current + new


def _generated_tests_reducer(current: Optional[dict], new: Optional[dict]) -> Optional[dict]:
    """Merge generated test dicts so parallel/sequential nodes don't overwrite each other."""
    if not current:
        return new
    if not new:
        return current
    merged = dict(current)
    for key, val in new.items():
        if key in merged and isinstance(merged[key], list) and isinstance(val, list):
            merged[key] = merged[key] + val
        else:
            merged[key] = val
    return merged


class AgentState(TypedDict):
    # --- Immutable trigger fields ---
    repo: str
    pr_number: int
    branch: str
    commit_sha: str
    changed_files: list[str]

    # --- Pipeline fields (with reducers) ---
    # error: first error wins — later nodes cannot overwrite an existing error
    error: Annotated[Optional[str], _error_reducer]
    # console_logs: accumulated across all run_tests calls
    console_logs: Annotated[Optional[list], _logs_reducer]
    # generated_tests: merged so unit/integration/e2e nodes compose rather than overwrite
    generated_tests: Annotated[Optional[dict], _generated_tests_reducer]

    # --- Pipeline fields (last-write-wins, safe because written by a single node) ---
    jira_ticket: Optional[dict]
    file_changes: Optional[list[FileChange]]
    workspace_dir: Optional[str]
    run_results: Optional[dict]
    debug_results: Optional[list]
    report: Optional[str]
    error: Optional[str]
    console_logs: Optional[list]
    # Production upgrade fields
    risk_scores: Optional[dict]       # file_path → FileRisk
    mutation_reports: Optional[dict]  # file_path → MutationReport
    quality_scores: Optional[dict]    # test_file → TestQualityScore


def _notify_agent(name: str, status: str, task: str | None = None) -> None:
    try:
        from api.routers.agents import update_agent
        update_agent(name, status, task)
    except ImportError:
        pass


def _make_agent_llms() -> dict[str, LLMClient]:
    """
    Build per-agent LLMClient instances from env-var overrides.
    Falls back to the default LLM_BACKEND when an override is not set.
    """
    def _agent_llm(backend_var: str, model_var: str) -> LLMClient:
        backend = os.environ.get(backend_var, "").strip() or None
        model = os.environ.get(model_var, "").strip() or None
        return LLMClient(backend=backend, model=model)

    return {
        "generator": _agent_llm("AGENT_GENERATOR_BACKEND", "AGENT_GENERATOR_MODEL"),
        "debugger":  _agent_llm("AGENT_DEBUGGER_BACKEND",  "AGENT_DEBUGGER_MODEL"),
    }


def _build_graph(
    clients: AgentClients,
    store: CodeVectorStore,
    on_event: EventCallback,
    agent_llms: dict[str, LLMClient] | None = None,
) -> StateGraph:
    agent_llms = agent_llms or {}
    gen_llm = agent_llms.get("generator", clients.unit_generator)
    dbg_llm = agent_llms.get("debugger", clients.debugger)

    analyzer = AnalyzerAgent()
    unit_gen = UnitGeneratorAgent(gen_llm, store)
    int_gen = IntegrationGeneratorAgent(gen_llm, store)
    e2e_gen = E2EGeneratorAgent(gen_llm)
    debugger = DebuggerAgent(dbg_llm)
    reporter = ReporterAgent()
    jest_runner = JestRunner()
    pytest_runner = PytestRunner()

    async def _emit(event: dict) -> None:
        try:
            await on_event(event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Node definitions
    # ------------------------------------------------------------------

    async def node_fetch_jira(state: AgentState) -> AgentState:
        logger.info("[fetch_jira] Starting — repo=%s pr=%s commit=%s", state["repo"], state["pr_number"], state["commit_sha"][:8])
        await _emit({"type": "progress", "node": "fetch_jira", "status": "started", "message": "Fetching Jira context"})
        try:
            from core.github_client import GitHubClient
            from core.jira_client import JiraClient
            gh = GitHubClient(repo=state["repo"])
            logger.info("[fetch_jira] Fetching commit message for %s", state["commit_sha"][:8])
            commit_msg = await asyncio.to_thread(gh.get_commit_message, state["commit_sha"])
            logger.info("[fetch_jira] Commit message: %s", commit_msg[:120])
            jira = JiraClient()
            task_id = jira.extract_task_id(commit_msg)
            if not task_id:
                logger.info("[fetch_jira] No Jira task ID found in commit message")
                await _emit({"type": "progress", "node": "fetch_jira", "status": "done", "message": "No Jira ticket found"})
                return {**state, "jira_ticket": None}
            logger.info("[fetch_jira] Extracted task ID: %s — fetching ticket details", task_id)
            ticket = await asyncio.to_thread(jira.get_ticket, task_id)
            logger.info("[fetch_jira] Linked %s: %s", task_id, ticket["summary"])
            await _emit({"type": "progress", "node": "fetch_jira", "status": "done", "message": f"Linked {task_id}"})
            return {**state, "jira_ticket": ticket}
        except Exception as exc:
            logger.warning("[fetch_jira] Skipped — %s", exc)
            await _emit({"type": "progress", "node": "fetch_jira", "status": "done", "message": "Jira skipped"})
            return {**state, "jira_ticket": None}

    async def node_analyze(state: AgentState) -> AgentState:
        logger.info("[analyze] Starting analysis — %d changed file(s) reported", len(state.get("changed_files") or []))
        await _emit({"type": "progress", "node": "analyze", "status": "started", "message": "Analyzing changed files"})
        _notify_agent("Analyzer", "running", "Parsing changed files")
        try:
            if state.get("repo") and state.get("pr_number"):
                logger.info("[analyze] Fetching diff from GitHub PR #%s", state["pr_number"])
                from core.github_client import GitHubClient
                gh = GitHubClient(repo=state["repo"])
                changes = await asyncio.to_thread(
                    analyzer.analyze_from_github, state["pr_number"], state["commit_sha"], gh
                )
            else:
                logger.info("[analyze] Analyzing local files: %s", state.get("changed_files"))
                changes = await asyncio.to_thread(
                    analyzer.analyze_files, state["changed_files"], "."
                )
            for c in changes:
                logger.info("[analyze]   %s  lang=%s  type=%s  fns=%s",
                            c.path, c.language, c.change_type,
                            c.functions_changed or "[]")
            _notify_agent("Analyzer", "idle")
            await _emit({"type": "progress", "node": "analyze", "status": "done", "message": f"{len(changes)} file(s) to test"})
            return {**state, "file_changes": changes}
        except Exception as exc:
            logger.error("[analyze] Failed: %s", exc)
            _notify_agent("Analyzer", "error")
            await _emit({"type": "progress", "node": "analyze", "status": "error", "message": str(exc)})
            return {**state, "error": str(exc), "file_changes": []}

    async def node_risk_score(state: AgentState) -> AgentState:
        from agents.risk_scorer import RiskScorer
        from core.config import AITAConfig
        changes = state.get("file_changes") or []
        eligible = [c for c in changes if c.change_type != "deleted"]
        if not eligible:
            logger.info("[risk_score] No eligible files — skipping")
            await _emit({"type": "progress", "node": "risk_score", "status": "done", "message": "No files to score"})
            return {**state, "risk_scores": {}}

        workspace = state.get("workspace_dir") or "."
        try:
            config = AITAConfig.load(workspace)
        except Exception:
            config = AITAConfig()

        logger.info("[risk_score] Scoring %d file(s)", len(eligible))
        await _emit({"type": "progress", "node": "risk_score", "status": "started", "message": f"Scoring {len(eligible)} file(s)"})
        scorer = RiskScorer()
        risk_scores = scorer.score_changes(eligible, config)
        for path, risk in risk_scores.items():
            logger.info("[risk_score] %s → tier=%s composite=%.1f", path, risk.tier, risk.composite_risk)
        await _emit({"type": "progress", "node": "risk_score", "status": "done",
                     "message": f"{len(risk_scores)} file(s) scored"})
        return {**state, "risk_scores": risk_scores}

    async def node_clone_repo(state: AgentState) -> AgentState:
        logger.info("[clone_repo] Starting — repo=%s branch=%s", state["repo"], state["branch"])
        await _emit({"type": "progress", "node": "clone_repo", "status": "started", "message": "Cloning repository"})
        try:
            token = os.environ.get("GITHUB_TOKEN", "")
            repo = state["repo"]
            branch = state["branch"]
            clone_url = f"https://{token}@github.com/{repo}.git"
            tmp_dir = tempfile.mkdtemp(prefix="aita_")
            logger.info("[clone_repo] Cloning %s (branch=%s) → %s", repo, branch, tmp_dir)
            result = await asyncio.to_thread(
                subprocess.run,
                ["git", "clone", "--branch", branch, "--depth", "1", clone_url, tmp_dir],
                capture_output=True, text=True, timeout=180,
            )
            if result.returncode != 0:
                logger.error("[clone_repo] git clone failed (exit %d):\n%s", result.returncode, result.stderr[:500])
                await _emit({"type": "progress", "node": "clone_repo", "status": "error", "message": "Clone failed"})
                return {**state, "workspace_dir": None, "error": f"Git clone failed: {result.stderr[:300]}"}
            logger.info("[clone_repo] Clone complete → %s", tmp_dir)
            await _emit({"type": "progress", "node": "clone_repo", "status": "done", "message": "Repository cloned"})
            return {**state, "workspace_dir": tmp_dir}
        except Exception as exc:
            logger.error("[clone_repo] Error: %s", exc)
            await _emit({"type": "progress", "node": "clone_repo", "status": "error", "message": str(exc)})
            return {**state, "workspace_dir": None, "error": str(exc)}

    async def node_setup_workspace(state: AgentState) -> AgentState:
        workspace = state.get("workspace_dir")
        if not workspace:
            logger.warning("[setup_workspace] No workspace dir — skipping dependency install")
            return state
        logger.info("[setup_workspace] Setting up workspace: %s", workspace)
        await _emit({"type": "progress", "node": "setup_workspace", "status": "started", "message": "Installing dependencies"})
        try:
            ws = Path(workspace)
            deps_dir = str(ws / ".ws_deps")
            req_file = ws / "requirements.txt"
            pyproject = ws / "pyproject.toml"
            if req_file.exists():
                logger.info("[setup_workspace] Found requirements.txt — installing Python deps → %s", deps_dir)
                await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-m", "pip", "install", "-r", str(req_file),
                     "--target", deps_dir, "-q", "--no-warn-script-location"],
                    capture_output=True, timeout=300,
                )
                logger.info("[setup_workspace] Python deps installed")
            elif pyproject.exists():
                logger.info("[setup_workspace] Found pyproject.toml — installing Python deps → %s", deps_dir)
                await asyncio.to_thread(
                    subprocess.run,
                    [sys.executable, "-m", "pip", "install", ".", "--target", deps_dir,
                     "-q", "--no-warn-script-location"],
                    cwd=workspace, capture_output=True, timeout=300,
                )
                logger.info("[setup_workspace] Python deps installed")
            else:
                logger.info("[setup_workspace] No Python dependency file found — skipping pip install")

            # Ensure pytest-json-report is available (needed by PytestRunner for
            # structured results — without it every pytest run returns failed=1).
            logger.info("[setup_workspace] Installing pytest-json-report for test results")
            await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "pip", "install",
                 "pytest-json-report", "pytest", "--target", deps_dir,
                 "-q", "--no-warn-script-location"],
                capture_output=True, timeout=120,
            )
            _JEST_CONFIG_NAMES = (
                "jest.config.js", "jest.config.ts", "jest.config.mjs",
                "jest.config.cjs", "jest.config.cts", "jest.config.json",
            )
            if (ws / "package.json").exists():
                logger.info("[setup_workspace] Found package.json — running npm install")
                await asyncio.to_thread(
                    subprocess.run, ["npm", "install", "--prefer-offline", "--silent"],
                    cwd=workspace, capture_output=True, text=True, timeout=300,
                    shell=(sys.platform == "win32"),
                )
                logger.info("[setup_workspace] Node deps installed")
            else:
                logger.info("[setup_workspace] No package.json found — skipping npm install")

            # Ensure Jest + ts-jest are available for AITA-generated TypeScript tests.
            # This is needed when the workspace is a Python/backend project with no frontend setup.
            has_jest = (ws / "node_modules" / ".bin" / "jest").exists() or \
                       (ws / "node_modules" / ".bin" / "jest.cmd").exists()
            has_ts_jest = (ws / "node_modules" / "ts-jest").exists()

            if not has_jest or not has_ts_jest:
                logger.info("[setup_workspace] Installing jest + ts-jest for AITA test execution")
                await _emit({"type": "progress", "node": "setup_workspace", "status": "started",
                             "message": "Installing jest + ts-jest"})
                result = await asyncio.to_thread(
                    subprocess.run,
                    ["npm", "install", "--save-dev",
                     "jest", "ts-jest", "@types/jest", "typescript",
                     "--prefer-offline", "--silent", "--no-audit"],
                    cwd=workspace, capture_output=True, text=True, timeout=300,
                    shell=(sys.platform == "win32"),
                )
                if result.returncode == 0:
                    logger.info("[setup_workspace] jest + ts-jest installed")
                else:
                    logger.warning("[setup_workspace] jest install returned %d: %s",
                                   result.returncode, result.stderr[:200])

            # Write AITA jest config if no native jest config exists in workspace
            has_jest_config = any((ws / name).exists() for name in _JEST_CONFIG_NAMES)
            if not has_jest_config:
                aita_cfg = ws / "jest.aita.config.js"
                aita_cfg.write_text(
                    "/** Auto-generated by AITA — safe to delete */\n"
                    "module.exports = {\n"
                    "  testEnvironment: 'node',\n"
                    "  transform: { '^.+\\\\.(ts|tsx)$': ['ts-jest', { diagnostics: false, tsconfig: { strict: false, esModuleInterop: true, allowJs: true } }] },\n"
                    "  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json'],\n"
                    "  transformIgnorePatterns: [],\n"
                    "  moduleDirectories: ['node_modules', '<rootDir>'],\n"
                    "};\n",
                    encoding="utf-8",
                )
                logger.info("[setup_workspace] Written AITA jest config: %s", aita_cfg)
        except Exception as exc:
            logger.warning("[setup_workspace] Dependency install error (non-fatal): %s", exc)
        await _emit({"type": "progress", "node": "setup_workspace", "status": "done", "message": "Dependencies ready"})
        return state

    async def node_generate_unit(state: AgentState) -> AgentState:
        changes = state.get("file_changes") or []
        eligible = [c for c in changes if c.change_type != "deleted"]
        risk_scores = state.get("risk_scores") or {}
        logger.info("[generate_unit] Starting — %d/%d file(s) eligible (non-deleted)", len(eligible), len(changes))
        _notify_agent("UnitGenerator", "running", "Generating unit tests")
        await _emit({"type": "progress", "node": "generate_unit", "status": "started", "message": "Generating unit tests"})
        jira_ticket = state.get("jira_ticket")
        workspace = state.get("workspace_dir")
        tests_dir = str(Path(workspace) / "__aita_tests__") if workspace else "tests"
        unit_tests: list[str] = []
        try:
            for change in eligible:
                risk = risk_scores.get(change.path)
                risk_tier = risk.tier if risk else "medium"
                logger.info("[generate_unit] Generating unit tests for %s (lang=%s tier=%s fns=%s)",
                            change.path, change.language, risk_tier, change.functions_changed or "[]")
                await _emit({"type": "progress", "node": "generate_unit", "status": "started",
                             "message": f"[{risk_tier.upper()}] Generating: {Path(change.path).name}"})
                code = await unit_gen.generate_streaming(change, jira_ticket=jira_ticket, risk_tier=risk_tier)
                logger.info("[generate_unit] Generated %d chars of test code for %s", len(code), change.path)
                if not code:
                    logger.warning("[generate_unit] LLM returned empty response for %s — skipping", change.path)
                    await _emit({"type": "progress", "node": "generate_unit", "status": "started",
                                 "message": f"Skipped (empty LLM response): {Path(change.path).name}"})
                    continue
                path = unit_gen.save_test(code, change.path, output_dir=tests_dir, source_content=change.full_content or "")
                unit_tests.append(path)
                logger.info("[generate_unit] Saved → %s", path)
                # Index in vector store for future runs
                try:
                    store.index_test_relationship(
                        source_file=change.path,
                        test_file=path,
                        test_code=code,
                        metadata={"risk_tier": risk_tier},
                    )
                except Exception:
                    pass
                await _emit({"type": "test_saved", "path": path, "layer": "unit"})
        except Exception as exc:
            logger.error("[generate_unit] Failed: %s", exc)
            _notify_agent("UnitGenerator", "error")
            await _emit({"type": "progress", "node": "generate_unit", "status": "error", "message": str(exc)})
            return {**state, "error": str(exc)}
        _notify_agent("UnitGenerator", "idle")
        logger.info("[generate_unit] Done — %d test file(s) written", len(unit_tests))
        await _emit({"type": "progress", "node": "generate_unit", "status": "done", "message": f"{len(unit_tests)} test(s) generated"})
        generated = {**(state.get("generated_tests") or {}), "unit": unit_tests}
        return {**state, "generated_tests": generated}

    async def node_generate_integration(state: AgentState) -> AgentState:
        changes = state.get("file_changes") or []
        logger.info("[generate_integration] Scanning %d file(s) for API/service patterns", len(changes))
        _notify_agent("IntegrationGenerator", "running", "Generating integration tests")
        await _emit({"type": "progress", "node": "generate_integration", "status": "started", "message": "Generating integration tests"})
        jira_ticket = state.get("jira_ticket")
        workspace = state.get("workspace_dir")
        tests_dir = str(Path(workspace) / "__aita_tests__") if workspace else "tests"
        int_tests: list[str] = []
        try:
            for change in changes:
                path_lower = change.path.lower()
                is_api = any(kw in path_lower for kw in ("controller", "router", "route", "endpoint", "service"))
                if not is_api or change.change_type == "deleted":
                    logger.info("[generate_integration] Skipping %s (is_api=%s change=%s)", change.path, is_api, change.change_type)
                    continue
                logger.info("[generate_integration] Generating integration tests for %s", change.path)
                await _emit({"type": "progress", "node": "generate_integration", "status": "started",
                             "message": f"Generating: {Path(change.path).name}"})
                code = await int_gen.generate_streaming(change, jira_ticket=jira_ticket)
                logger.info("[generate_integration] Generated %d chars of test code for %s", len(code), change.path)
                path = int_gen.save_test(code, change.path, output_dir=tests_dir)
                int_tests.append(path)
                logger.info("[generate_integration] Saved → %s", path)
                await _emit({"type": "test_saved", "path": path, "layer": "integration"})
        except Exception as exc:
            logger.error("[generate_integration] Failed: %s", exc)
            _notify_agent("IntegrationGenerator", "error")
            await _emit({"type": "progress", "node": "generate_integration", "status": "error", "message": str(exc)})
            return {**state, "error": str(exc)}
        _notify_agent("IntegrationGenerator", "idle")
        logger.info("[generate_integration] Done — %d test file(s) written", len(int_tests))
        await _emit({"type": "progress", "node": "generate_integration", "status": "done", "message": f"{len(int_tests)} test(s) generated"})
        generated = {**(state.get("generated_tests") or {}), "integration": int_tests}
        return {**state, "generated_tests": generated}

    async def node_generate_e2e(state: AgentState) -> AgentState:
        changes = state.get("file_changes") or []
        logger.info("[generate_e2e] Scanning %d file(s) for TypeScript UI components", len(changes))
        await _emit({"type": "progress", "node": "generate_e2e", "status": "started", "message": "Generating E2E tests"})
        workspace = state.get("workspace_dir")
        tests_dir = str(Path(workspace) / "__aita_tests__") if workspace else "tests"
        e2e_tests: list[str] = []
        try:
            for change in changes:
                is_ts = change.language == "typescript"
                path_lower = change.path.lower()
                is_component = any(kw in path_lower for kw in ("page", "component", "view", "screen"))
                if not is_ts or change.change_type == "deleted" or not is_component:
                    logger.info("[generate_e2e] Skipping %s (ts=%s component=%s change=%s)",
                                change.path, is_ts, is_component, change.change_type)
                    continue
                logger.info("[generate_e2e] Generating E2E tests for %s", change.path)
                await _emit({"type": "progress", "node": "generate_e2e", "status": "started",
                             "message": f"Generating: {Path(change.path).name}"})
                code = await e2e_gen.generate_streaming(change)
                logger.info("[generate_e2e] Generated %d chars of test code for %s", len(code), change.path)
                path = e2e_gen.save_test(code, change.path, output_dir=tests_dir)
                e2e_tests.append(path)
                logger.info("[generate_e2e] Saved → %s", path)
                await _emit({"type": "test_saved", "path": path, "layer": "e2e"})
        except Exception as exc:
            logger.error("[generate_e2e] Failed: %s", exc)
            await _emit({"type": "progress", "node": "generate_e2e", "status": "error", "message": str(exc)})
            return {**state, "error": str(exc)}
        logger.info("[generate_e2e] Done — %d test file(s) written", len(e2e_tests))
        await _emit({"type": "progress", "node": "generate_e2e", "status": "done", "message": f"{len(e2e_tests)} test(s) generated"})
        generated = {**(state.get("generated_tests") or {}), "e2e": e2e_tests}
        return {**state, "generated_tests": generated}

    async def node_run_tests(state: AgentState) -> AgentState:
        generated = state.get("generated_tests") or {}
        workspace = state.get("workspace_dir") or "."
        heartbeat_seconds = max(5, int(os.environ.get("AITA_TEST_HEARTBEAT_SECONDS", "10")))
        total_passed = total_failed = total_skipped = 0
        total_duration = 0.0
        failures: list[dict] = []

        all_tests = generated.get("unit", []) + generated.get("integration", [])
        logger.info("[run_tests] Starting — %d test file(s) in workspace: %s", len(all_tests), workspace)
        await _emit({"type": "progress", "node": "run_tests", "status": "started", "message": f"Running {len(all_tests)} test file(s)"})

        async def _run_with_heartbeat(test_path: str, runner_name: str, index: int, total: int):
            """Run one test file, streaming output to the WebSocket every 300 ms."""
            started_at = time.monotonic()
            file_label = Path(test_path).name

            # Thread-safe queue: the runner thread pushes (stream, line) tuples;
            # the drain coroutine reads them and emits live test_log events.
            line_q: _stdlib_queue.SimpleQueue = _stdlib_queue.SimpleQueue()

            def _line_cb(stream: str, line: str) -> None:
                line_q.put((stream, line))

            if test_path.endswith(".py"):
                task = asyncio.create_task(
                    asyncio.to_thread(pytest_runner.run, test_path, cwd=workspace, line_callback=_line_cb)
                )
            else:
                task = asyncio.create_task(
                    asyncio.to_thread(jest_runner.run, test_path, cwd=workspace, line_callback=_line_cb)
                )

            async def _drain() -> None:
                """Batch-emit queued lines every 300 ms while the test process runs."""
                while not task.done():
                    await asyncio.sleep(0.3)
                    lines_out: list[str] = []
                    lines_err: list[str] = []
                    while True:
                        try:
                            stream, line = line_q.get_nowait()
                            (lines_out if stream == "stdout" else lines_err).append(line)
                        except _stdlib_queue.Empty:
                            break
                    if lines_out or lines_err:
                        await _emit({
                            "type": "test_log",
                            "source": test_path,
                            "stdout": "".join(lines_out),
                            "stderr": "".join(lines_err),
                            "passed": 0,
                            "failed": 0,
                            "skipped": 0,
                            "exit_code": -1,   # sentinel: still running
                        })
                # Final drain after the task finishes
                lines_out, lines_err = [], []
                while True:
                    try:
                        stream, line = line_q.get_nowait()
                        (lines_out if stream == "stdout" else lines_err).append(line)
                    except _stdlib_queue.Empty:
                        break
                if lines_out or lines_err:
                    await _emit({
                        "type": "test_log",
                        "source": test_path,
                        "stdout": "".join(lines_out),
                        "stderr": "".join(lines_err),
                        "passed": 0,
                        "failed": 0,
                        "skipped": 0,
                        "exit_code": -1,
                    })

            drain_task = asyncio.create_task(_drain())

            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(task), timeout=heartbeat_seconds)
                    await drain_task   # ensure every last line is flushed
                    return result
                except asyncio.TimeoutError:
                    elapsed = int(time.monotonic() - started_at)
                    logger.info("[run_tests] [%d/%d] still running %s with %s (%ds)",
                                index, total, file_label, runner_name, elapsed)
                    await _emit({
                        "type": "progress",
                        "node": "run_tests",
                        "status": "started",
                        "message": f"[{index}/{total}] {file_label} still running ({elapsed}s)",
                    })

        console_logs: list[dict] = []
        for i, test_path in enumerate(all_tests, 1):
            try:
                runner_name = "pytest" if test_path.endswith(".py") else "jest"
                file_label = Path(test_path).name
                logger.info("[run_tests] [%d/%d] Running %s with %s", i, len(all_tests), test_path, runner_name)
                await _emit({"type": "progress", "node": "run_tests", "status": "started",
                             "message": f"[{i}/{len(all_tests)}] {file_label} ({runner_name})"})
                # Emit a "started" placeholder so the DevConsole row appears immediately
                start_log = {
                    "source": test_path,
                    "stdout": "",
                    "stderr": f"▶ Running {runner_name} on {file_label}…",
                    "passed": 0,
                    "failed": 0,
                    "skipped": 0,
                    "exit_code": -1,
                }
                console_logs.append(start_log)
                await _emit({"type": "test_log", **start_log})
                result = await _run_with_heartbeat(test_path, runner_name, i, len(all_tests))
                logger.info("[run_tests] [%d/%d] %s → passed=%d failed=%d skipped=%d duration=%.2fs",
                            i, len(all_tests), test_path, result.passed, result.failed, result.skipped, result.duration_seconds)
                total_passed += result.passed
                total_failed += result.failed
                total_skipped += result.skipped
                total_duration += result.duration_seconds
                log_entry = {
                    "source": test_path,
                    "stdout": result.output or "",
                    "stderr": result.error or "",
                    "passed": result.passed,
                    "failed": result.failed,
                    "skipped": result.skipped,
                    "exit_code": result.exit_code,
                }
                console_logs.append(log_entry)
                await _emit({"type": "test_log", **log_entry})
                if result.failed > 0:
                    failures.append({
                        "test_name": test_path,
                        "error": result.error or "",
                        "stack_trace": result.output,
                        "source": "",
                    })
            except Exception as exc:
                logger.warning("[run_tests] Runner exception for %s: %s", test_path, exc)
                total_failed += 1
                log_entry = {
                    "source": test_path,
                    "stdout": "",
                    "stderr": str(exc),
                    "passed": 0,
                    "failed": 1,
                    "skipped": 0,
                    "exit_code": 1,
                }
                console_logs.append(log_entry)
                await _emit({"type": "test_log", **log_entry})
                failures.append({"test_name": test_path, "error": str(exc), "stack_trace": "", "source": ""})

        run_results = {
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "duration_seconds": round(total_duration, 2),
            "failures": failures,
        }
        await _emit({
            "type": "run_result",
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "duration": round(total_duration, 2),
        })
        logger.info("[run_tests] Done — total: passed=%d failed=%d skipped=%d duration=%.2fs",
                    total_passed, total_failed, total_skipped, total_duration)
        await _emit({"type": "progress", "node": "run_tests", "status": "done", "message": f"{total_passed} passed, {total_failed} failed"})
        return {**state, "run_results": run_results, "console_logs": console_logs}

    async def node_mutation_test(state: AgentState) -> AgentState:
        from agents.mutation_agent import MutationAgent
        from core.config import AITAConfig

        # Skip mutation testing when no tests passed — mutating is pointless
        run_results = state.get("run_results") or {}
        if run_results.get("passed", 0) == 0:
            logger.info("[mutation_test] No passing tests — skipping mutation testing")
            await _emit({"type": "progress", "node": "mutation_test", "status": "done",
                         "message": "Skipped (no passing tests)"})
            return {**state, "mutation_reports": {}}

        file_changes = state.get("file_changes") or []
        generated_tests = state.get("generated_tests") or {}
        workspace = state.get("workspace_dir") or "."

        py_changes = [c for c in file_changes if c.language == "python" and c.change_type != "deleted"]
        if not py_changes:
            logger.info("[mutation_test] No Python files to mutate — skipping")
            await _emit({"type": "progress", "node": "mutation_test", "status": "done",
                         "message": "Skipped (no Python files)"})
            return {**state, "mutation_reports": {}}

        try:
            config = AITAConfig.load(workspace)
        except Exception:
            config = AITAConfig()

        if not config.mutation.enabled:
            logger.info("[mutation_test] Mutation testing disabled in config — skipping")
            await _emit({"type": "progress", "node": "mutation_test", "status": "done",
                         "message": "Disabled in config"})
            return {**state, "mutation_reports": {}}

        logger.info("[mutation_test] Starting on %d Python file(s)", len(py_changes))
        await _emit({"type": "progress", "node": "mutation_test", "status": "started",
                     "message": f"Mutating {len(py_changes)} Python file(s)"})

        agent = MutationAgent()
        try:
            reports_list = await asyncio.to_thread(
                agent.run,
                py_changes[:3],       # cap at 3 files per run
                generated_tests,
                workspace,
                config.mutation.threshold,
                config.mutation.max_mutants_per_file,
            )
        except Exception as exc:
            logger.warning("[mutation_test] Failed: %s", exc)
            await _emit({"type": "progress", "node": "mutation_test", "status": "done",
                         "message": f"Error: {exc}"})
            return {**state, "mutation_reports": {}}

        mutation_reports = {r.source_file: r for r in reports_list}
        for src, r in mutation_reports.items():
            logger.info("[mutation_test] %s → score=%.1f%% killed=%d survived=%d %s",
                        src, r.mutation_score, r.killed, r.survived,
                        "PASS" if r.passed_threshold else "FAIL")

        await _emit({"type": "progress", "node": "mutation_test", "status": "done",
                     "message": f"{len(mutation_reports)} file(s) mutation-tested"})
        return {**state, "mutation_reports": mutation_reports}

    async def node_score_quality(state: AgentState) -> AgentState:
        from agents.quality_scorer import QualityScorer
        from agents.flakiness_detector import FlakinessDetector

        generated_tests = state.get("generated_tests") or {}
        all_test_files = generated_tests.get("unit", []) + generated_tests.get("integration", [])
        mutation_reports = state.get("mutation_reports") or {}
        file_changes_map = {c.path: c for c in (state.get("file_changes") or [])}
        workspace = state.get("workspace_dir") or "."

        if not all_test_files:
            logger.info("[score_quality] No test files to score — skipping")
            await _emit({"type": "progress", "node": "score_quality", "status": "done",
                         "message": "No tests to score"})
            return {**state, "quality_scores": {}}

        scorer = QualityScorer()
        detector = FlakinessDetector()
        quality_scores = {}

        logger.info("[score_quality] Scoring %d test file(s)", len(all_test_files))
        await _emit({"type": "progress", "node": "score_quality", "status": "started",
                     "message": f"Scoring {len(all_test_files)} test file(s)"})

        for test_file in all_test_files:
            test_stem = Path(test_file).stem.replace(".test", "").replace("test_", "")
            source_file = next(
                (p for p in file_changes_map if Path(p).stem == test_stem or test_stem in p),
                test_file,
            )

            lang = "python" if test_file.endswith(".py") else "typescript"
            flakiness_score = 0.0
            try:
                code = Path(test_file).read_text(encoding="utf-8", errors="ignore")
                flaky = detector.scan(code, lang)
                flakiness_score = flaky["score"]
                if flaky["risk_level"] != "low":
                    logger.info("[score_quality] Flakiness %s=%s score=%.0f patterns=%s",
                                Path(test_file).name, flaky["risk_level"],
                                flakiness_score, flaky["patterns_found"])
            except Exception:
                pass

            mutation_report = mutation_reports.get(source_file)
            try:
                score = scorer.score_file(
                    test_file=test_file,
                    source_file=source_file,
                    workspace_dir=workspace,
                    mutation_report=mutation_report,
                    flakiness_score=flakiness_score,
                )
                quality_scores[test_file] = score
                logger.info("[score_quality] %s → grade=%s composite=%.1f",
                            Path(test_file).name, score.grade, score.composite_score)
            except Exception as exc:
                logger.warning("[score_quality] Scoring failed for %s: %s", test_file, exc)

        await _emit({"type": "progress", "node": "score_quality", "status": "done",
                     "message": f"{len(quality_scores)} test(s) scored"})
        return {**state, "quality_scores": quality_scores}

    async def node_debug(state: AgentState) -> AgentState:
        run_results = state.get("run_results") or {}
        failures = run_results.get("failures", [])
        logger.info("[debug] Starting — %d failure(s) to analyze (cap=10)", len(failures))
        _notify_agent("Debugger", "running", "Analyzing failures")
        await _emit({"type": "progress", "node": "debug", "status": "started", "message": "Analyzing failures with AI"})
        results: list[dict] = []
        try:
            for i, failure in enumerate(failures[:10], 1):
                logger.info("[debug] [%d/%d] Analyzing failure: %s", i, min(len(failures), 10), failure.get("test_name", "unknown"))
                dr = await debugger.analyze_failure_async(
                    test_name=failure.get("test_name", "unknown"),
                    error=failure.get("error", ""),
                    stack_trace=failure.get("stack_trace", ""),
                    source=failure.get("source", ""),
                )
                logger.info("[debug] [%d/%d] Root cause: %s (confidence=%d%%)", i, min(len(failures), 10), dr.root_cause[:120], dr.confidence)
                results.append({
                    "test_name": dr.test_name,
                    "root_cause": dr.root_cause,
                    "fix_suggestion": dr.fix_suggestion,
                    "fix_code": dr.fix_code,
                    "confidence": dr.confidence,
                })
                await _emit({
                    "type": "debug_result",
                    "test_name": dr.test_name,
                    "root_cause": dr.root_cause,
                    "fix_suggestion": dr.fix_suggestion,
                    "confidence": dr.confidence,
                })
        except Exception as exc:
            logger.error("[debug] Failed: %s", exc)
            _notify_agent("Debugger", "error")
            await _emit({"type": "progress", "node": "debug", "status": "error", "message": str(exc)})
            return {**state, "debug_results": results}
        _notify_agent("Debugger", "idle")
        logger.info("[debug] Done — %d failure(s) analyzed", len(results))
        await _emit({"type": "progress", "node": "debug", "status": "done", "message": f"{len(results)} failure(s) analyzed"})
        return {**state, "debug_results": results}

    async def node_report(state: AgentState) -> AgentState:
        run_results = state.get("run_results") or {}
        debug_results_raw = state.get("debug_results") or []
        quality_scores = state.get("quality_scores")
        mutation_reports = state.get("mutation_reports")
        logger.info("[reporter] Building report — passed=%d failed=%d debug=%d quality=%d mutation=%d",
                    run_results.get("passed", 0), run_results.get("failed", 0),
                    len(debug_results_raw),
                    len(quality_scores) if quality_scores else 0,
                    len(mutation_reports) if mutation_reports else 0)
        await _emit({"type": "progress", "node": "reporter", "status": "started", "message": "Building report"})
        debug_results = [
            DebugResult(
                test_name=d["test_name"],
                root_cause=d["root_cause"],
                fix_suggestion=d["fix_suggestion"],
                fix_code=d.get("fix_code"),
                confidence=d.get("confidence", 50),
            )
            for d in debug_results_raw
        ]
        report = reporter.build_pr_comment(
            run_results, debug_results,
            quality_scores=quality_scores,
            mutation_reports=mutation_reports,
        )
        logger.info("[reporter] Report built (%d chars)", len(report))
        await _emit({"type": "progress", "node": "reporter", "status": "done"})
        return {**state, "report": report}

    async def node_cleanup(state: AgentState) -> AgentState:
        workspace = state.get("workspace_dir")
        logger.info("[cleanup] Starting — workspace=%s", workspace or "none")
        await _emit({"type": "progress", "node": "cleanup", "status": "started", "message": "Cleaning up workspace"})
        if workspace:
            await asyncio.to_thread(shutil.rmtree, workspace, True)
            logger.info("[cleanup] Removed workspace: %s", workspace)
        else:
            logger.info("[cleanup] No workspace to remove")
        await _emit({"type": "progress", "node": "cleanup", "status": "done"})
        return {**state, "workspace_dir": None}

    # ------------------------------------------------------------------
    # Routing functions
    # ------------------------------------------------------------------

    def _debug_or_report(s: AgentState) -> str:
        return "debug" if (s.get("run_results") or {}).get("failed", 0) > 0 else "reporter"

    def _continue_or_abort(s: AgentState) -> str:
        """Route to cleanup immediately when a fatal error has been recorded."""
        return "abort" if s.get("error") else "continue"

    # ------------------------------------------------------------------
    # Build the graph
    # ------------------------------------------------------------------
    graph = StateGraph(AgentState)

    for name, fn in [
        ("fetch_jira",            node_fetch_jira),
        ("analyze",               node_analyze),
        ("risk_score",            node_risk_score),
        ("clone_repo",            node_clone_repo),
        ("setup_workspace",       node_setup_workspace),
        ("generate_unit",         node_generate_unit),
        ("generate_integration",  node_generate_integration),
        ("generate_e2e",          node_generate_e2e),
        ("run_tests",             node_run_tests),
        ("mutation_test",         node_mutation_test),
        ("score_quality",         node_score_quality),
        ("debug",                 node_debug),
        ("reporter",              node_report),
        ("cleanup",               node_cleanup),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("fetch_jira")
    graph.add_edge("fetch_jira",           "analyze")
    graph.add_edge("analyze",              "risk_score")
    graph.add_edge("risk_score",           "clone_repo")
    graph.add_edge("clone_repo",           "setup_workspace")
    graph.add_edge("setup_workspace",      "generate_unit")
    # If unit generation fails (e.g. LLM connection error), skip straight to reporter
    graph.add_conditional_edges(
        "generate_unit",
        _continue_or_abort,
        {"continue": "generate_integration", "abort": "reporter"},
    )
    graph.add_edge("generate_integration", "generate_e2e")
    graph.add_edge("generate_e2e",         "run_tests")
    graph.add_edge("run_tests",            "mutation_test")

    graph.add_edge("mutation_test",  "score_quality")
    graph.add_conditional_edges(
        "score_quality",
        _debug_or_report,
        {"debug": "debug", "reporter": "reporter"},
    )
    graph.add_edge("debug",    "reporter")
    graph.add_edge("reporter", "cleanup")
    graph.add_edge("cleanup",  END)

    return graph


_NOISY_LOGGERS = frozenset({
    "uvicorn", "uvicorn.error", "uvicorn.access",
    "fastapi", "httpx", "httpcore",
    "api.ws_manager", "api.routers.ws",
})


class _LiveLogHandler(logging.Handler):
    """Forwards log records to the SSE event stream in real-time."""

    def __init__(self, callback: EventCallback, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._callback = callback
        self._loop = loop
        self._emitting = False  # re-entrancy guard

    def emit(self, record: logging.LogRecord) -> None:
        if self._emitting:
            return
        if record.name in _NOISY_LOGGERS or record.name.startswith("uvicorn"):
            return
        try:
            self._emitting = True
            msg = self.format(record)
            event = {
                "type": "log",
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": msg,
            }
            if self._loop.is_running():
                self._loop.create_task(self._callback(event))
        except Exception:
            pass
        finally:
            self._emitting = False


async def run_pipeline(trigger: dict, on_event: Optional[EventCallback] = None) -> AgentState:
    if on_event is None:
        async def on_event(_: dict) -> None:
            pass

    loop = asyncio.get_event_loop()
    handler = _LiveLogHandler(on_event, loop)
    handler.setFormatter(logging.Formatter("%(name)s: %(message)s"))
    handler.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        clients = AgentClients.build()
        store = CodeVectorStore()
        compiled = _build_graph(clients, store, on_event, _make_agent_llms()).compile()

        initial_state = AgentState(
            repo=trigger.get("repo") or os.environ.get("GITHUB_REPO", ""),
            pr_number=trigger["pr_number"],
            branch=trigger["branch"],
            commit_sha=trigger["commit_sha"],
            changed_files=trigger.get("changed_files", []),
            jira_ticket=None,
            file_changes=None,
            workspace_dir=None,
            generated_tests=None,
            run_results=None,
            debug_results=None,
            report=None,
            error=None,
            console_logs=None,
            risk_scores=None,
            mutation_reports=None,
            quality_scores=None,
        )
        return await compiled.ainvoke(initial_state)
    finally:
        root_logger.removeHandler(handler)
