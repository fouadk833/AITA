from __future__ import annotations
import logging
import os
import re
import time
from typing import AsyncGenerator, Literal

logger = logging.getLogger(__name__)

# Backend is selected via LLM_BACKEND env var: "openai", "anthropic", "gemini", or "ollama"
Backend = Literal["openai", "anthropic", "gemini", "ollama"]


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        backend: Backend | None = None,
    ):
        self.backend: Backend = (backend or os.environ.get("LLM_BACKEND", "anthropic")).lower()  # type: ignore[assignment]

        if self.backend == "openai":
            self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
            import openai as _openai
            self._client = _openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
            self._async_client = _openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        elif self.backend == "ollama":
            self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
            self._base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            self._client = None
            self._async_client = None
            # Inference options — tuned for lightweight models
            self._ollama_options = {
                "temperature":    float(os.environ.get("OLLAMA_TEMPERATURE",    "0.1")),
                "num_ctx":        int(os.environ.get("OLLAMA_NUM_CTX",          "4096")),
                "num_predict":    int(os.environ.get("OLLAMA_NUM_PREDICT",      "2048")),
                "top_p":          float(os.environ.get("OLLAMA_TOP_P",          "0.9")),
                "top_k":          int(os.environ.get("OLLAMA_TOP_K",            "40")),
                "repeat_penalty": float(os.environ.get("OLLAMA_REPEAT_PENALTY", "1.1")),
            }
        elif self.backend == "gemini":
            self.model = model or os.environ.get("GEMINI_MODEL", "gemini-2.0-flash")
            import google.generativeai as _genai
            _genai.configure(api_key=os.environ.get("GEMINI_API_KEY", ""))
            self._genai_model = _genai.GenerativeModel(self.model)
            self._client = None
            self._async_client = None
        else:
            self.backend = "anthropic"
            self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            self._async_client = _anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        # Lightweight flag: ollama with a small model — prompts are trimmed, examples omitted
        self.is_lightweight: bool = self.backend == "ollama"
        logger.info("LLMClient ready — backend=%s model=%s lightweight=%s",
                    self.backend, self.model, self.is_lightweight)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(self, system_prompt: str, user_message: str, max_tokens: int = 8096) -> str:
        logger.info("LLM generate — backend=%s model=%s prompt_chars=%d", self.backend, self.model, len(user_message))
        t0 = time.monotonic()
        if self.backend == "openai":
            result = self._openai_generate(system_prompt, user_message, max_tokens)
        elif self.backend == "ollama":
            result = self._ollama_generate(system_prompt, user_message)
        elif self.backend == "gemini":
            result = self._gemini_generate(system_prompt, user_message)
        else:
            result = self._anthropic_generate(system_prompt, user_message, max_tokens)
        logger.info("LLM done — %.2fs response_chars=%d", time.monotonic() - t0, len(result))
        return result

    async def generate_async(self, system_prompt: str, user_message: str, max_tokens: int = 8096) -> str:
        logger.info("LLM generate_async — backend=%s model=%s prompt_chars=%d", self.backend, self.model, len(user_message))
        t0 = time.monotonic()
        if self.backend == "openai":
            result = await self._openai_generate_async(system_prompt, user_message, max_tokens)
        elif self.backend == "ollama":
            result = await self._ollama_generate_async(system_prompt, user_message)
        elif self.backend == "gemini":
            result = await self._gemini_generate_async(system_prompt, user_message)
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
        if self.backend == "openai":
            async for token in self._openai_stream_async(system_prompt, user_message, max_tokens):
                yield token
            return

        if self.backend == "ollama":
            async for token in self._ollama_stream_async(system_prompt, user_message):
                yield token
            return

        if self.backend == "gemini":
            async for token in self._gemini_stream_async(system_prompt, user_message):
                yield token
            return

        import anthropic as _anthropic
        max_retries = 3
        for attempt in range(max_retries):
            tokens_yielded = 0
            try:
                async with self._async_client.messages.stream(  # type: ignore[union-attr]
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_message}],
                ) as stream:
                    async for text in stream.text_stream:
                        tokens_yielded += 1
                        yield text
                return  # success
            except (_anthropic.APIConnectionError, _anthropic.InternalServerError) as exc:
                # Only retry if we haven't yielded any tokens yet — we can't un-yield
                if tokens_yielded > 0 or attempt >= max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    "LLM connection error — retrying in %ds (attempt %d/%d): %s",
                    wait, attempt + 1, max_retries, exc,
                )
                await asyncio.sleep(wait)
            except _anthropic.AuthenticationError:
                logger.error("LLM authentication failed — check ANTHROPIC_API_KEY")
                raise

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
    # OpenAI backend
    # ------------------------------------------------------------------

    def _openai_generate(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        response = self._client.chat.completions.create(  # type: ignore[union-attr]
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    async def _openai_generate_async(self, system_prompt: str, user_message: str, max_tokens: int) -> str:
        response = await self._async_client.chat.completions.create(  # type: ignore[union-attr]
            model=self.model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        return response.choices[0].message.content or ""

    async def _openai_stream_async(self, system_prompt: str, user_message: str, max_tokens: int) -> AsyncGenerator[str, None]:
        import openai as _openai
        max_retries = 3
        for attempt in range(max_retries):
            tokens_yielded = 0
            try:
                async with await self._async_client.chat.completions.create(  # type: ignore[union-attr]
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    stream=True,
                ) as stream:
                    async for chunk in stream:
                        token = (chunk.choices[0].delta.content or "") if chunk.choices else ""
                        if token:
                            tokens_yielded += 1
                            yield token
                return
            except (_openai.APIConnectionError, _openai.InternalServerError) as exc:
                if tokens_yielded > 0 or attempt >= max_retries - 1:
                    raise
                wait = 2 ** attempt
                logger.warning("OpenAI connection error — retrying in %ds (attempt %d/%d): %s",
                               wait, attempt + 1, max_retries, exc)
                await asyncio.sleep(wait)
            except _openai.AuthenticationError:
                logger.error("OpenAI authentication failed — check OPENAI_API_KEY")
                raise

    # ------------------------------------------------------------------
    # Google Gemini backend
    # ------------------------------------------------------------------

    def _gemini_generate(self, system_prompt: str, user_message: str) -> str:
        response = self._genai_model.generate_content(  # type: ignore[union-attr]
            f"{system_prompt}\n\n{user_message}"
        )
        return response.text

    async def _gemini_generate_async(self, system_prompt: str, user_message: str) -> str:
        import asyncio
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._gemini_generate, system_prompt, user_message)

    async def _gemini_stream_async(self, system_prompt: str, user_message: str) -> AsyncGenerator[str, None]:
        import asyncio
        import google.generativeai as _genai
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        loop = asyncio.get_event_loop()
        errors: list[Exception] = []

        def _stream() -> None:
            try:
                response = self._genai_model.generate_content(  # type: ignore[union-attr]
                    f"{system_prompt}\n\n{user_message}",
                    stream=True,
                )
                for chunk in response:
                    token = chunk.text or ""
                    if token:
                        loop.call_soon_threadsafe(queue.put_nowait, token)
            except Exception as exc:
                errors.append(exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _stream)
        while True:
            token = await queue.get()
            if token is None:
                break
            yield token
        if errors:
            raise errors[0]

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
            options=self._ollama_options,
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
        errors: list[Exception] = []

        def _stream() -> None:
            try:
                for chunk in ollama.chat(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    options=self._ollama_options,
                    stream=True,
                ):
                    token = (chunk.get("message") or {}).get("content", "")
                    if token:
                        loop.call_soon_threadsafe(queue.put_nowait, token)
            except Exception as exc:
                errors.append(exc)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        loop.run_in_executor(None, _stream)
        while True:
            token = await queue.get()
            if token is None:
                break
            yield token
        if errors:
            raise errors[0]
