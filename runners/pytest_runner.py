import json
import sys
import tempfile
import os
from runners.base_runner import BaseRunner, RunResult


class PytestRunner(BaseRunner):
    def run(self, test_path: str, cwd: str = ".", line_callback=None, **kwargs) -> RunResult:
        report_file = os.path.join(tempfile.gettempdir(), "pytest_report.json")
        cmd = [
            sys.executable, "-m", "pytest", test_path,
            "--json-report",
            f"--json-report-file={report_file}",
            "-v", "--tb=short",   # verbose: one PASSED/FAILED line per test
            f"--rootdir={cwd}",
            "--import-mode=importlib",
        ]
        # Inject workspace root and .ws_deps into PYTHONPATH so generated tests
        # can import source code as well as third-party deps installed by setup_workspace.
        env = os.environ.copy()
        ws_deps = os.path.join(cwd, ".ws_deps")
        extra_paths = [cwd]  # workspace root first — makes "from src.x import y" work
        if os.path.isdir(ws_deps):
            extra_paths.append(ws_deps)
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(extra_paths) + (os.pathsep + existing if existing else "")
        stdout, stderr, exit_code = self._exec(cmd, cwd=cwd, env=env, line_callback=line_callback)

        try:
            with open(report_file) as f:
                report = json.load(f)

            summary = report.get("summary", {})
            passed = summary.get("passed", 0)
            failed = summary.get("failed", 0)
            skipped = summary.get("skipped", 0)
            duration = report.get("duration", 0.0)

        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return RunResult(
                passed=0, failed=1, skipped=0,
                duration_seconds=0.0,
                output=stdout,
                error=f"Failed to parse pytest output: {stderr}",
                exit_code=exit_code,
            )
        finally:
            if os.path.exists(report_file):
                os.unlink(report_file)

        return RunResult(
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=round(duration, 2),
            output=stdout,
            error=stderr if stderr else None,
            exit_code=exit_code,
        )
