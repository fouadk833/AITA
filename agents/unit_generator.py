from __future__ import annotations
import ast
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional
from core.llm_client import LLMClient
from core.vector_store import CodeVectorStore
from core.prompts.unit_test_prompt import SYSTEM_PROMPT, build_unit_test_prompt
from agents.analyzer import FileChange

logger = logging.getLogger(__name__)


class UnitGeneratorAgent:
    def __init__(self, llm: LLMClient, store: CodeVectorStore):
        self.llm = llm
        self.store = store

    def _build_prompt(self, change: FileChange, jira_ticket: dict | None) -> str:
        context_results = self.store.search(
            f"unit tests for {change.path} {' '.join(change.functions_changed)}",
            n_results=3,
        )
        context_str = "\n\n".join(r["content"] for r in context_results)
        framework = self._detect_framework(change.language)
        return build_unit_test_prompt(
            code=change.full_content,
            file_path=change.path,
            language=change.language,
            framework=framework,
            context=context_str,
            jira_ticket=jira_ticket,
        )

    def generate(self, change: FileChange, jira_ticket: dict | None = None) -> str:
        prompt = self._build_prompt(change, jira_ticket)
        response = self.llm.generate(SYSTEM_PROMPT, prompt)
        return self.llm.extract_code_block(response, change.language)

    async def generate_streaming(
        self,
        change: FileChange,
        jira_ticket: dict | None = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        prompt = self._build_prompt(change, jira_ticket)
        full_response = ""
        async for token in self.llm.generate_stream_async(SYSTEM_PROMPT, prompt):
            full_response += token
            if on_token:
                await on_token(token)
        return self.llm.extract_code_block(full_response, change.language)

    def _detect_framework(self, language: str) -> str:
        return "pytest" if language == "python" else "vitest"

    def save_test(self, test_code: str, source_path: str, output_dir: str = "tests") -> str:
        p = Path(source_path)
        stem = p.stem
        ext = ".py" if p.suffix == ".py" else ".test" + p.suffix
        sub = "backend" if p.suffix == ".py" else "frontend"
        out_path = Path(output_dir) / sub / "unit" / f"{stem}{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Validate Python syntax before writing — invalid files cause pytest collection
        # errors that mask real failures. Replace with a skip-placeholder instead.
        if p.suffix == ".py":
            try:
                ast.parse(test_code)
            except SyntaxError as e:
                logger.warning("Syntax error in generated test for %s: %s — saving skip placeholder", source_path, e)
                error_short = str(e).replace("'", "\\'")
                test_code = (
                    f"# AITA: generated test contained a syntax error and was skipped.\n"
                    f"# Source: {source_path}\n"
                    f"# Error:  {e}\n"
                    f"import pytest\n\n"
                    f"@pytest.mark.skip(reason='Generated test had a syntax error: {error_short}')\n"
                    f"def test_skipped_due_to_generation_error():\n"
                    f"    pass\n"
                )

        out_path.write_text(test_code, encoding="utf-8")
        return str(out_path)
