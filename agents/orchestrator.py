from __future__ import annotations
import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional, TypedDict
from langgraph.graph import StateGraph, END

from agents.analyzer import AnalyzerAgent, FileChange
from agents.unit_generator import UnitGeneratorAgent
from agents.integration_generator import IntegrationGeneratorAgent
from agents.e2e_generator import E2EGeneratorAgent
from agents.debugger import DebuggerAgent, DebugResult
from agents.reporter import ReporterAgent
from core.llm_client import LLMClient
from core.vector_store import CodeVectorStore
from runners.jest_runner import JestRunner
from runners.pytest_runner import PytestRunner

logger = logging.getLogger(__name__)

EventCallback = Callable[[dict], Awaitable[None]]


class AgentState(TypedDict):
    repo: str
    pr_number: int
    branch: str
    commit_sha: str
    changed_files: list[str]
    jira_ticket: Optional[dict]
    file_changes: Optional[list[FileChange]]
    workspace_dir: Optional[str]
    generated_tests: Optional[dict]
    run_results: Optional[dict]
    debug_results: Optional[list]
    report: Optional[str]
    error: Optional[str]
    console_logs: Optional[list]


def _notify_agent(name: str, status: str, task: str | None = None) -> None:
    try:
        from api.routers.agents import update_agent
        update_agent(name, status, task)
    except ImportError:
        pass


def _build_graph(
    llm: LLMClient,
    store: CodeVectorStore,
    on_event: EventCallback,
) -> StateGraph:
    analyzer = AnalyzerAgent()
    unit_gen = UnitGeneratorAgent(llm, store)
    int_gen = IntegrationGeneratorAgent(llm, store)
    e2e_gen = E2EGeneratorAgent(llm)
    debugger = DebuggerAgent(llm)
    reporter = ReporterAgent()
    jest_runner = JestRunner()
    pytest_runner = PytestRunner()

    async def _emit(event: dict) -> None:
        try:
            await on_event(event)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Node definitions (all async so they can emit events and stream LLM)
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
            if (ws / "package.json").exists():
                logger.info("[setup_workspace] Found package.json — running npm install")
                await asyncio.to_thread(
                    subprocess.run, ["npm", "install", "--prefer-offline", "--silent"],
                    cwd=workspace, capture_output=True, text=True, timeout=300,
                )
                logger.info("[setup_workspace] Node deps installed")
            else:
                logger.info("[setup_workspace] No package.json found — skipping npm install")
        except Exception as exc:
            logger.warning("[setup_workspace] Dependency install error (non-fatal): %s", exc)
        await _emit({"type": "progress", "node": "setup_workspace", "status": "done", "message": "Dependencies ready"})
        return state

    async def node_generate_unit(state: AgentState) -> AgentState:
        changes = state.get("file_changes") or []
        eligible = [c for c in changes if c.change_type != "deleted"]
        logger.info("[generate_unit] Starting — %d/%d file(s) eligible (non-deleted)", len(eligible), len(changes))
        _notify_agent("UnitGenerator", "running", "Generating unit tests")
        await _emit({"type": "progress", "node": "generate_unit", "status": "started", "message": "Generating unit tests"})
        jira_ticket = state.get("jira_ticket")
        workspace = state.get("workspace_dir")
        tests_dir = str(Path(workspace) / "__aita_tests__") if workspace else "tests"
        unit_tests: list[str] = []
        try:
            for change in eligible:
                logger.info("[generate_unit] Generating unit tests for %s (lang=%s fns=%s)",
                            change.path, change.language, change.functions_changed or "[]")
                file_path = change.path

                async def on_token(tok: str, fp: str = file_path) -> None:
                    await _emit({"type": "llm_token", "agent": "UnitGenerator", "file": fp, "token": tok})

                code = await unit_gen.generate_streaming(change, jira_ticket=jira_ticket, on_token=on_token)
                logger.info("[generate_unit] Generated %d chars of test code for %s", len(code), change.path)
                path = unit_gen.save_test(code, change.path, output_dir=tests_dir)
                unit_tests.append(path)
                logger.info("[generate_unit] Saved → %s", path)
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
                file_path = change.path

                async def on_token(tok: str, fp: str = file_path) -> None:
                    await _emit({"type": "llm_token", "agent": "IntegrationGenerator", "file": fp, "token": tok})

                code = await int_gen.generate_streaming(change, jira_ticket=jira_ticket, on_token=on_token)
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
                file_path = change.path

                async def on_token(tok: str, fp: str = file_path) -> None:
                    await _emit({"type": "llm_token", "agent": "E2EGenerator", "file": fp, "token": tok})

                code = await e2e_gen.generate_streaming(change, on_token=on_token)
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
        total_passed = total_failed = total_skipped = 0
        total_duration = 0.0
        failures: list[dict] = []
        all_tests = generated.get("unit", []) + generated.get("integration", [])
        logger.info("[run_tests] Starting — %d test file(s) in workspace: %s", len(all_tests), workspace)
        await _emit({"type": "progress", "node": "run_tests", "status": "started", "message": f"Running {len(all_tests)} test file(s)"})

        console_logs: list[dict] = []
        for i, test_path in enumerate(all_tests, 1):
            try:
                runner_name = "pytest" if test_path.endswith(".py") else "jest"
                logger.info("[run_tests] [%d/%d] Running %s with %s", i, len(all_tests), test_path, runner_name)
                if test_path.endswith(".py"):
                    result = await asyncio.to_thread(pytest_runner.run, test_path, cwd=workspace)
                else:
                    result = await asyncio.to_thread(jest_runner.run, test_path, cwd=workspace)
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
        logger.info("[reporter] Building report — passed=%d failed=%d debug_entries=%d",
                    run_results.get("passed", 0), run_results.get("failed", 0), len(debug_results_raw))
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
        report = reporter.build_pr_comment(run_results, debug_results)
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
    # Build the graph
    # ------------------------------------------------------------------
    graph = StateGraph(AgentState)

    for name, fn in [
        ("fetch_jira", node_fetch_jira),
        ("analyze", node_analyze),
        ("clone_repo", node_clone_repo),
        ("setup_workspace", node_setup_workspace),
        ("generate_unit", node_generate_unit),
        ("generate_integration", node_generate_integration),
        ("generate_e2e", node_generate_e2e),
        ("run_tests", node_run_tests),
        ("debug", node_debug),
        ("reporter", node_report),
        ("cleanup", node_cleanup),
    ]:
        graph.add_node(name, fn)

    graph.set_entry_point("fetch_jira")
    graph.add_edge("fetch_jira", "analyze")
    graph.add_edge("analyze", "clone_repo")
    graph.add_edge("clone_repo", "setup_workspace")
    graph.add_edge("setup_workspace", "generate_unit")
    graph.add_edge("generate_unit", "generate_integration")
    graph.add_edge("generate_integration", "generate_e2e")
    graph.add_edge("generate_e2e", "run_tests")
    graph.add_conditional_edges(
        "run_tests",
        lambda s: "debug" if (s.get("run_results") or {}).get("failed", 0) > 0 else "reporter",
        {"debug": "debug", "reporter": "reporter"},
    )
    graph.add_edge("debug", "reporter")
    graph.add_edge("reporter", "cleanup")
    graph.add_edge("cleanup", END)

    return graph


class _LiveLogHandler(logging.Handler):
    """Forwards log records to the SSE event stream in real-time."""

    def __init__(self, callback: EventCallback, loop: asyncio.AbstractEventLoop) -> None:
        super().__init__()
        self._callback = callback
        self._loop = loop

    def emit(self, record: logging.LogRecord) -> None:
        try:
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
        llm = LLMClient()
        store = CodeVectorStore()
        compiled = _build_graph(llm, store, on_event).compile()

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
        )
        return await compiled.ainvoke(initial_state)
    finally:
        root_logger.removeHandler(handler)
