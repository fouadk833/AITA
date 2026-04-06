from __future__ import annotations
import ast
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional
from core.llm_client import LLMClient
from core.vector_store import CodeVectorStore
from core.prompts.unit_test_prompt import get_system_prompt, build_unit_test_prompt
from agents.analyzer import FileChange

logger = logging.getLogger(__name__)


class UnitGeneratorAgent:
    def __init__(self, llm: LLMClient, store: CodeVectorStore):
        self.llm = llm
        self.store = store

    def _build_prompt(
        self,
        change: FileChange,
        jira_ticket: dict | None,
        depth_instruction: str | None = None,
        heal_context: str | None = None,
    ) -> str:
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
            depth_instruction=depth_instruction,
            heal_context=heal_context,
            # AST metadata — tells the LLM exactly what to test and what to mock
            functions=change.functions_changed or [],
            classes=change.classes_changed or [],
            imports=getattr(change, "imports", []) or [],
            call_graph=getattr(change, "call_graph", {}) or {},
        )

    def _system_prompt(self, language: str) -> str:
        framework = self._detect_framework(language)
        lightweight = getattr(self.llm, "is_lightweight", False)
        return get_system_prompt(framework, lightweight=lightweight)

    def generate(self, change: FileChange, jira_ticket: dict | None = None) -> str:
        prompt = self._build_prompt(change, jira_ticket)
        response = self.llm.generate(self._system_prompt(change.language), prompt)
        return self.llm.extract_code_block(response, change.language)

    async def generate_streaming(
        self,
        change: FileChange,
        jira_ticket: dict | None = None,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
        risk_tier: str | None = None,
        heal_context: str | None = None,
    ) -> str:
        from agents.risk_scorer import DEPTH_INSTRUCTIONS
        depth_instruction = DEPTH_INSTRUCTIONS.get(risk_tier) if risk_tier else None
        prompt = self._build_prompt(
            change, jira_ticket,
            depth_instruction=depth_instruction,
            heal_context=heal_context,
        )
        system = self._system_prompt(change.language)
        full_response = ""
        async for token in self.llm.generate_stream_async(system, prompt):
            full_response += token
            if on_token:
                await on_token(token)
        return self.llm.extract_code_block(full_response, change.language)

    def _detect_framework(self, language: str) -> str:
        return "pytest" if language == "python" else "jest"

    def save_test(self, test_code: str, source_path: str, output_dir: str = "tests") -> str:
        p = Path(source_path)
        stem = p.stem
        ext = ".py" if p.suffix == ".py" else ".test" + p.suffix
        sub = "backend" if p.suffix == ".py" else "frontend"
        out_path = Path(output_dir) / sub / "unit" / f"{stem}{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if p.suffix == ".py":
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
            logger.warning("Syntax error in generated Python test for %s: %s", source_path, e)
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
        """Basic structural checks for TypeScript test code."""
        issues: list[str] = []

        # Check brace balance
        opens = code.count("{")
        closes = code.count("}")
        if opens != closes:
            issues.append(f"unbalanced braces: {opens} open vs {closes} close")

        # Detect wrong framework imports
        bad_imports = [
            "from 'jest'",
            'from "jest"',
            "from 'vitest'",
            'from "vitest"',
        ]
        for bad in bad_imports:
            if bad in code:
                logger.warning(
                    "Generated test for %s imports Jest/Vitest globals — stripping bad import",
                    source_path,
                )
                # Remove the entire import line
                import re
                code = re.sub(
                    r"import\s*\{[^}]*\}\s*from\s*['\"](?:jest|vitest)['\"];?\n?",
                    "",
                    code,
                )

        if issues:
            logger.warning(
                "TypeScript test for %s has structural issues: %s",
                source_path, "; ".join(issues),
            )

        return code
