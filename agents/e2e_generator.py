from __future__ import annotations
from pathlib import Path
from typing import Awaitable, Callable, Optional
from core.llm_client import LLMClient
from core.prompts.e2e_test_prompt import SYSTEM_PROMPT, build_e2e_test_prompt
from agents.analyzer import FileChange


class E2EGeneratorAgent:
    def __init__(self, llm: LLMClient, base_url: str = "http://localhost:3000"):
        self.llm = llm
        self.base_url = base_url

    def _build_prompt(self, change: FileChange) -> str:
        route = self._infer_route(change.path)
        return build_e2e_test_prompt(
            component_code=change.full_content,
            file_path=change.path,
            route=route,
            base_url=self.base_url,
        )

    def generate_for_component(self, change: FileChange) -> str:
        prompt = self._build_prompt(change)
        response = self.llm.generate(SYSTEM_PROMPT, prompt)
        return self.llm.extract_code_block(response, "typescript")

    async def generate_streaming(
        self,
        change: FileChange,
        on_token: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        prompt = self._build_prompt(change)
        full_response = ""
        async for token in self.llm.generate_stream_async(SYSTEM_PROMPT, prompt):
            full_response += token
            if on_token:
                await on_token(token)
        return self.llm.extract_code_block(full_response, "typescript")

    def generate_for_route(self, route: str, component_code: str) -> str:
        prompt = build_e2e_test_prompt(
            component_code=component_code,
            file_path=route,
            route=route,
            base_url=self.base_url,
        )
        response = self.llm.generate(SYSTEM_PROMPT, prompt)
        return self.llm.extract_code_block(response, "typescript")

    def _infer_route(self, file_path: str) -> str:
        """Guess the app route from a file path like src/pages/Login.tsx → /login"""
        p = Path(file_path)
        name = p.stem.lower()
        if name in ("index", "app"):
            return "/"
        return f"/{name}"

    def save_test(self, test_code: str, source_path: str, output_dir: str = "tests") -> str:
        p = Path(source_path)
        out_path = Path(output_dir) / "frontend" / "e2e" / f"{p.stem}.spec.ts"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(test_code, encoding="utf-8")
        return str(out_path)
