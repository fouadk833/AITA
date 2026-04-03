from __future__ import annotations
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import AsyncGenerator, Literal

logger = logging.getLogger(__name__)

# Backend is selected via LLM_BACKEND env var: "anthropic" (default) or "ollama"
Backend = Literal["anthropic", "ollama"]

# ------------------------------------------------------------------
# Per-agent model configuration
# Each agent gets a model tuned for its task:
#   - unit_generator:         needs deep code reasoning  → large model
#   - integration_generator:  needs API/HTTP awareness   → large model
#   - e2e_generator:          needs UI/browser patterns  → large model
#   - debugger:               needs root cause analysis  → large model
#   - default:                fallback                   → large model
#
# Override any key via env var:  AITA_MODEL_<AGENT_KEY>
# e.g. AITA_MODEL_DEBUGGER=claude-haiku-4-5
# ------------------------------------------------------------------
_AGENT_MODEL_DEFAULTS: dict[str, str] = {
    "unit_generator":         "claude-sonnet-4-5",
    "integration_generator":  "claude-sonnet-4-5",
    "e2e_generator":          "claude-sonnet-4-5",
    "debugger":               "claude-sonnet-4-5",
    "default":                "claude-sonnet-4-5",
}

_AGENT_MAX_TOKENS: dict[str, int] = {
    "unit_generator":         8096,
    "integration_generator":  8096,
    "e2e_generator":          4096,
    "debugger":               2048,
    "default":                8096,
}


def _resolve_model(agent_key: str) -> str:
    """Return the model for a given agent key, with env-var override support."""
    env_key = f"AITA_MODEL_{agent_key.upper()}"
    return os.environ.get(env_key) or _AGENT_MODEL_DEFAULTS.get(agent_key) or _AGENT_MODEL_DEFAULTS["default"]


def _resolve_max_tokens(agent_key: str) -> int:
    return _AGENT_MAX_TOKENS.get(agent_key, _AGENT_MAX_TOKENS["default"])


@dataclass
class AgentClients:
    """Holds one LLMClient per agent role."""
    unit_generator: "LLMClient"
    integration_generator: "LLMClient"
    e2e_generator: "LLMClient"
    debugger: "LLMClient"

    @classmethod
    def build(cls, backend: Backend | None = None) -> "AgentClients":
        """Instantiate all per-agent clients. Logs the model assigned to each."""
        agents = ["unit_generator", "integration_generator", "e2e_generator", "debugger"]
        clients = {}
        for agent in agents:
            model = _resolve_model(agent)
            max_tokens = _resolve_max_tokens(agent)
            logger.info("AgentClients | %-25s → model=%-35s max_tokens=%d", agent, model, max_tokens)
            clients[agent] = LLMClient(model=model, max_tokens=max_tokens, backend=backend)
        return cls(**clients)


class LLMClient:
    def __init__(
        self,
        model: str | None = None,
        backend: Backend | None = None,
        max_tokens: int = 8096,
    ):
        self.backend: Backend = (backend or os.environ.get("LLM_BACKEND", "anthropic")).lower()  # type: ignore[assignment]
        self.default_max_tokens = max_tokens

        if self.backend == "ollama":
            self.model = model or os.environ.get("OLLAMA_MODEL", "gemma4:e2b")
            self._base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
            self._client = None
            self._async_client = None
        else:
            self.backend = "anthropic"
            self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
            import anthropic as _anthropic
            self._client = _anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
            self._async_client = _anthropic.AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
        logger.info("LLMClient ready — backend=%s model=%s max_tokens=%d", self.backend, self.model, self.default_max_tokens)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def generate(self, system_prompt: str, user_message: str, max_tokens: int | None = None) -> str:
        max_tokens = max_tokens or self.default_max_tokens
        logger.info("LLM generate — backend=%s model=%s max_tokens=%d prompt_chars=%d",
                    self.backend, self.model, max_tokens, len(user_message))
        t0 = time.monotonic()
        if self.backend == "ollama":
            result = self._ollama_generate(system_prompt, user_message)
        else:
            result = self._anthropic_generate(system_prompt, user_message, max_tokens)
        logger.info("LLM done — %.2fs response_chars=%d", time.monotonic() - t0, len(result))
        return result

    async def generate_async(self, system_prompt: str, user_message: str, max_tokens: int | None = None) -> str:
        max_tokens = max_tokens or self.default_max_tokens
        logger.info("LLM generate_async — backend=%s model=%s max_tokens=%d prompt_chars=%d",
                    self.backend, self.model, max_tokens, len(user_message))
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
        max_tokens: int | None = None,
    ) -> AsyncGenerator[str, None]:
        """Yield response tokens as they arrive."""
        max_tokens = max_tokens or self.default_max_tokens
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
