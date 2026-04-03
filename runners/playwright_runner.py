import json
from runners.base_runner import BaseRunner, RunResult


class PlaywrightRunner(BaseRunner):
    def run(self, test_path: str, cwd: str = ".", **kwargs) -> RunResult:
        cmd = ["npx", "playwright", "test", test_path, "--reporter=json"]
        stdout, stderr, exit_code = self._exec(cmd, cwd=cwd)

        try:
            report = json.loads(stdout)
            stats = report.get("stats", {})
            passed = stats.get("expected", 0)
            failed = stats.get("unexpected", 0)
            skipped = stats.get("skipped", 0)
            duration = stats.get("duration", 0) / 1000.0

        except (json.JSONDecodeError, KeyError):
            return RunResult(
                passed=0, failed=1, skipped=0,
                duration_seconds=0.0,
                output=stdout,
                error=f"Failed to parse Playwright output: {stderr}",
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
