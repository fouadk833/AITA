from __future__ import annotations
import ast
import logging
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional
from core.llm_client import LLMClient
from core.vector_store import CodeVectorStore
from core.prompts.integration_test_prompt import (
    SYSTEM_PROMPT,
    build_integration_test_prompt,
    build_openapi_test_prompt,
)
from agents.analyzer import FileChange

logger = logging.getLogger(__name__)

_NESTJS_PATTERNS = ("controller", "resolver", "gateway", "module")
_FASTAPI_PATTERNS = ("router", "route", "endpoint", "api")


class IntegrationGeneratorAgent:
    def __init__(self, llm: LLMClient, store: CodeVectorStore):
        self.llm = llm
        self.store = store

    def _build_prompt(self, change: FileChange, jira_ticket: dict | None) -> tuple[str, str]:
        framework = self._detect_framework(change)
        prompt = build_integration_test_prompt(
            code=change.full_content,
            file_path=change.path,
            framework=framework,
            jira_ticket=jira_ticket,
        )
        lang = "typescript" if "jest" in framework else "python"
        return prompt, lang

    def generate_from_file(self, change: FileChange, jira_ticket: dict | None = None) -> str:
        prompt, lang = self._build_prompt(change, jira_ticket)
        response = self.llm.generate(SYSTEM_PROMPT, prompt)
        return self.llm.extract_code_block(response, lang)

    async def generate_streaming(
        self,
        change: FileChange,
        jira_ticket: dict | None = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        prompt, lang = self._build_prompt(change, jira_ticket)
        full_response = ""
        async for token in self.llm.generate_stream_async(SYSTEM_PROMPT, prompt):
            full_response += token
            if on_token:
                await on_token(token)
        return self.llm.extract_code_block(full_response, lang)

    def generate_from_openapi(self, spec_path: str, framework: str = "pytest+httpx") -> list[str]:
        spec = Path(spec_path).read_text(encoding="utf-8")
        prompt = build_openapi_test_prompt(spec, framework)
        response = self.llm.generate(SYSTEM_PROMPT, prompt)
        lang = "typescript" if "jest" in framework else "python"
        return [self.llm.extract_code_block(response, lang)]

    def _detect_framework(self, change: FileChange) -> str:
        path_lower = change.path.lower()
        if change.language == "python":
            return "pytest+httpx"
        if any(p in path_lower for p in _NESTJS_PATTERNS):
            return "jest+supertest"
        return "vitest"

    def save_test(self, test_code: str, source_path: str, output_dir: str = "tests") -> str:
        p = Path(source_path)
        sub = "nestjs" if p.suffix in (".ts", ".js") else "fastapi"
        stem = p.stem
        ext = ".test.ts" if sub == "nestjs" else "_test.py"
        out_path = Path(output_dir) / "backend" / sub / f"{stem}{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if ext == "_test.py":
            test_code = self._validate_python(test_code, source_path)
        else:
            test_code = self._validate_typescript(test_code, source_path)

        out_path.write_text(test_code, encoding="utf-8")
        return str(out_path)

    # ------------------------------------------------------------------
    # Syntax validators
    # ------------------------------------------------------------------

    def _validate_python(self, code: str, source_path: str) -> str:
        try:
            ast.parse(code)
            return code
        except SyntaxError as e:
            logger.warning("Syntax error in generated integration test for %s: %s", source_path, e)
            error_short = str(e).replace("'", "\\'")
            return (
                f"# AITA: generated test contained a syntax error and was skipped.\n"
                f"# Source: {source_path}\n"
                f"# Error:  {e}\n"
                f"import pytest\n\n"
                f"@pytest.mark.skip(reason='Generated test had a syntax error: {error_short}')\n"
                f"def test_skipped_due_to_generation_error():\n"
                f"    pass\n"
            )

    def _validate_typescript(self, code: str, source_path: str) -> str:
        # Strip bad jest/vitest imports
        code = re.sub(
            r"import\s*\{[^}]*\}\s*from\s*['\"](?:jest|vitest)['\"];?\n?",
            "",
            code,
        )
        return code
