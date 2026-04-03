import json
import sys
from runners.base_runner import BaseRunner, RunResult


class JestRunner(BaseRunner):
    def run(self, test_path: str, cwd: str = ".", **kwargs) -> RunResult:
        # On Windows, npx is a .cmd file and is not resolved without shell=True
        cmd = ["npx", "jest", test_path, "--json", "--passWithNoTests"]
        stdout, stderr, exit_code = self._exec(cmd, cwd=cwd, shell=(sys.platform == "win32"))

        try:
            report = json.loads(stdout)
            passed = report.get("numPassedTests", 0)
            failed = report.get("numFailedTests", 0)
            skipped = report.get("numPendingTests", 0)

            duration = sum(
                r.get("testExecError", {}) or {}
                and 0
                or sum(t.get("duration", 0) or 0 for t in r.get("testResults", []))
                for r in report.get("testResults", [])
            ) / 1000.0

        except (json.JSONDecodeError, KeyError):
            return RunResult(
                passed=0, failed=1, skipped=0,
                duration_seconds=0.0,
                output=stdout,
                error=f"Failed to parse Jest output: {stderr}",
                exit_code=exit_code,
            )

        return RunResult(
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_seconds=round(duration, 2),
            output=stdout,
            error=stderr if stderr else None,
            exit_code=exit_code,
        )
