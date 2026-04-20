from __future__ import annotations
import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class RunResult:
    passed: int
    failed: int
    skipped: int
    duration_seconds: float
    output: str
    error: Optional[str] = None
    exit_code: int = 0


class BaseRunner:
    def run(self, test_path: str, **kwargs) -> RunResult:
        raise NotImplementedError

    def _exec(
        self,
        cmd: list[str],
        cwd: str = ".",
        shell: bool = False,
        env=None,
        line_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[str, str, int]:
        """Run *cmd* and return (stdout, stderr, returncode).

        When *line_callback* is provided the process is launched with Popen
        so each output line is forwarded to the callback as it is emitted
        (real-time streaming).  The callback receives two arguments:
          - ``stream``: ``"stdout"`` or ``"stderr"``
          - ``line``:   the raw text line (may include trailing newline)
        The full accumulated output is still returned as normal.
        """
        timeout_seconds = int(os.environ.get("AITA_TEST_TIMEOUT_SECONDS", "180"))

        if line_callback is None:
            # ── buffered path (original behaviour) ──────────────────────
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    cwd=cwd,
                    timeout=timeout_seconds,
                    shell=shell,
                    env=env,                    stdin=subprocess.DEVNULL,                )
                return result.stdout, result.stderr, result.returncode
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or ""
                stderr = (exc.stderr or "")
                stderr = f"{stderr}\nTest execution timed out after {timeout_seconds}s".strip()
                return stdout, stderr, 124

        # ── streaming path ───────────────────────────────────────────────
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                cwd=cwd,
                shell=shell,
                env=env,
                bufsize=1,   # line-buffered
            )
        except OSError as exc:
            return "", str(exc), 1

        stdout_lines: list[str] = []
        stderr_lines: list[str] = []

        def _read(stream, buf: list[str], tag: str) -> None:
            for raw in stream:
                buf.append(raw)
                try:
                    line_callback(tag, raw)
                except Exception:
                    pass
            stream.close()

        t_out = threading.Thread(target=_read, args=(proc.stdout, stdout_lines, "stdout"), daemon=True)
        t_err = threading.Thread(target=_read, args=(proc.stderr, stderr_lines, "stderr"), daemon=True)
        t_out.start()
        t_err.start()

        try:
            proc.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
            t_out.join(2)
            t_err.join(2)
            stderr_lines.append(f"\nTest execution timed out after {timeout_seconds}s")
            return "".join(stdout_lines), "".join(stderr_lines), 124

        t_out.join()
        t_err.join()
        return "".join(stdout_lines), "".join(stderr_lines), proc.returncode
