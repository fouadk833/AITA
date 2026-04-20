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
        code = self.llm.extract_code_block(response, change.language)
        return code

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

    def save_test(self, test_code: str, source_path: str, output_dir: str = "tests", source_content: str = "") -> str:
        p = Path(source_path)
        stem = p.stem
        ext = ".py" if p.suffix == ".py" else ".test" + p.suffix
        sub = "backend" if p.suffix == ".py" else "frontend"
        out_path = Path(output_dir) / sub / "unit" / f"{stem}{ext}"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        if p.suffix == ".py":
            test_code = self._validate_python(test_code, source_path)
        else:
            test_code = self._validate_typescript(test_code, source_path, source_content)

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

    def _validate_typescript(self, code: str, source_path: str, source_content: str = "") -> str:
        """Fix common LLM mistakes that produce runtime/compile errors."""
        import re

        # 1. Strip bad framework imports (jest/vitest globals are never imported)
        bad_frameworks = ["jest", "vitest"]
        for fw in bad_frameworks:
            if f"from '{fw}'" in code or f'from "{fw}"' in code:
                logger.warning(
                    "Generated test for %s imports %s globals — stripping", source_path, fw
                )
                code = re.sub(
                    rf"import\s*\{{[^}}]*\}}\s*from\s*['\"](?:{fw})['\"];?\n?",
                    "",
                    code,
                )

        # 2. Fix `clearAllMocks()` called without `jest.` prefix
        fixed_clear = re.sub(
            r'(?<!jest\.)(?<!\w)clearAllMocks\s*\(\s*\)',
            'jest.clearAllMocks()',
            code,
        )
        if fixed_clear != code:
            logger.warning(
                "Generated test for %s called clearAllMocks() without jest. prefix — fixed",
                source_path,
            )
            code = fixed_clear

        # 3. Remove jest.mock() calls that mock the module under test itself.
        import_paths: list[str] = re.findall(
            r"""import\s+(?:\{[^}]*\}|\*\s+as\s+\w+|\w+)\s+from\s+['"]([^'"]+)['"]""",
            code,
        )
        def _norm(p: str) -> str:
            p = p.lstrip("./")
            for ext in (".ts", ".tsx", ".js", ".jsx"):
                if p.endswith(ext):
                    p = p[: -len(ext)]
            return p.lower()

        normed_imports = {_norm(p) for p in import_paths}

        def _remove_if_self_mock(m: re.Match) -> str:
            mock_path = m.group(1)
            if _norm(mock_path) in normed_imports:
                logger.warning(
                    "Generated test for %s mocked the module under test (%s) — removing",
                    source_path, mock_path,
                )
                return ""
            return m.group(0)

        code = re.sub(
            r"""jest\.mock\s*\(\s*['"]([^'"]+)['"]\s*(?:,\s*[^)]+)?\s*\)\s*;?\n?""",
            _remove_if_self_mock,
            code,
        )

        # 4. Remove .toThrow() test blocks when the source has no throw statement.
        #    The LLM invents throw behaviour for sources that have no error handling.
        if source_content and "throw" not in source_content:
            before = code
            # Remove entire it/test blocks that use .toThrow() or .rejects.toThrow()
            code = re.sub(
                r"""\s*(?:it|test)\s*\([^,]+,\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{[^}]*\.toThrow[^}]*\}\s*\)\s*;?""",
                "",
                code,
                flags=re.DOTALL,
            )
            if code != before:
                logger.warning(
                    "Generated test for %s has .toThrow() assertions but source has no throw — removed",
                    source_path,
                )

        # 5. Check brace balance (warn only)
        opens = code.count("{")
        closes = code.count("}")
        if opens != closes:
            logger.warning(
                "Generated test for %s has unbalanced braces (%d open vs %d close)",
                source_path, opens, closes,
            )

        return code
