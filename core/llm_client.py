from __future__ import annotations
import logging
import os
import re
import time
from typing import AsyncGenerator, Literal

logger = logging.getLogger(__name__)

# Backend is selected via LLM_BACKEND env var: "anthropic" (default) or "ollama"
Backend = Literal["anthropic", "ollama"]


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        backend: Backend | None = None,
    ):
        self.backend: Backend = (backend or os.environ.get("LLM_BACKEND", "anthropic")).lower()  # type: ignore[assignment]

        if self.backend == "ollama":
            self.model = model or os.environ.get("OLLAMA_MODEL", "gemma4:e2b")
            self._base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            self._client = None
            self._async_client = None
        else:
            self.backend = "anthropic"
            self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            self._async_client = _anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        logger.info("LLMClient ready — backend=%s model=%s", self.backend, self.model)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(self, system_prompt: str, user_message: str, max_tokens: int = 8096) -> str:
        logger.info("LLM generate — backend=%s model=%s prompt_chars=%d", self.backend, self.model, len(user_message))
        t0 = time.monotonic()
        if self.backend == "ollama":
            result = self._ollama_generate(system_prompt, user_message)
        else:
            result = self._anthropic_generate(system_prompt, user_message, max_tokens)
        logger.info("LLM done — %.2fs response_chars=%d", time.monotonic() - t0, len(result))
        return result

    async def generate_async(self, system_prompt: str, user_message: str, max_tokens: int = 8096) -> str:
        logger.info("LLM generate_async — backend=%s model=%s prompt_chars=%d", self.backend, self.model, len(user_message))
        t0 = time.monotonic()
        if self.backend == "ollama":
            result = await self._ollama_generate_async(system_prompt, user_message)
        else:
            result = await self._anthropic_generate_async(system_prompt, user_message, max_tokens)
        logger.info("LLM async done — %.2fs response_chars=%d", time.monotonic() - t0, len(result))
        return result

    async def generate_stream_async(
        self,
        system_prompt: str,
        user_message: str,
        max_tokens: int = 8096,
    ) -> AsyncGenerator[str, None]:
        """Yield response tokens as they arrive."""
        if self.backend == "ollama":
            async for token in self._ollama_stream_async(system_prompt, user_message):
                yield token
            return
        async with self._async_client.messages.stream(  # type: ignore[union-attr]
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        ) as stream:
            async for text in stream.text_stream:
                yield text

    def extract_code_block(self, response: str, language: str = "") -> str:
        """Extract code from a fenced block, with multiple fallback strategies."""
        # 1. Language-specific fence (wrap in non-capturing group so the whole word is optional)
        if language:
            m = re.search(rf"```(?:{re.escape(language)})\s*\n(.*?)```", response, re.DOTALL)
            if m:
                return m.group(1).strip()
        # 2. Any fenced block
        m = re.search(r"```(?:\w+)?\s*\n(.*?)```", response, re.DOTALL)
        if m:
            return m.group(1).strip()
        # 3. Incomplete closing fence (LLM cut off) — grab everything after opening fence
        m = re.search(r"```(?:\w+)?\s*\n(.*?)$", response, re.DOTALL)
        if m:
            return m.group(1).strip()
        # 4. Strip all fence lines manually from the raw response
        lines = response.strip().splitlines()
        cleaned = "\n".join(line for line in lines if not re.match(r"^\s*```", line))
        return cleaned.strip()

    # ------------------------------------------------------------------
    # Anthropic backend
    # ------------------------------------------------------------------

    def _anthropic_generate(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        message = self._client.messages.create(  # type: ignore[union-attr]
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return message.content[0].text

    async def _anthropic_generate_async(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        message = await self._async_client.messages.create(  # type: ignore[union-attr]
            model=self.model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        return message.content[0].text

    # ------------------------------------------------------------------
    # Ollama backend  (uses ollama-python SDK)
    # ------------------------------------------------------------------

    def _ollama_generate(self, system_prompt: str, user_message: str) -> str:
        import ollama
        response = ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response["message"]["content"]

    async def _ollama_generate_async(self, system_prompt: str, user_message: str) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._ollama_generate, system_prompt, user_message)

    async def _ollama_stream_async(self, system_prompt: str, user_message: str) -> AsyncGenerator[str, None]:
        import asyncio
        import ollama
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()

        def _stream() -> None:
            try:
                for chunk in ollama.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    stream=True,
                ):
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        loop.call_soon_threadsafe(queue.put_nowait, token)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _stream)
        while True:
            token = await queue.get()
            if token is None:
                break
            yield token
