from __future__ import annotations
import json
from dataclasses import dataclass
from typing import Optional
from core.llm_client import LLMClient
from core.prompts.debugger_prompt import SYSTEM_PROMPT, build_debugger_prompt


@dataclass
class DebugResult:
    test_name: str
    root_cause: str
    fix_suggestion: str
    fix_code: Optional[str]
    confidence: int


class DebuggerAgent:
    def __init__(self, llm: LLMClient):
        self.llm = llm

    def analyze_failure(
        self,
        test_name: str,
        error: str,
        stack_trace: str,
        source: str,
        test_code: str = "",
    ) -> DebugResult:
        prompt = build_debugger_prompt(
            test_name=test_name,
            error_message=error,
            stack_trace=stack_trace,
            source_code=source,
            test_code=test_code,
        )
        response = self.llm.generate(SYSTEM_PROMPT, prompt, max_tokens=2048)

        try:
            # Strip markdown fences if present
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            data = json.loads(cleaned)
            return DebugResult(
                test_name=test_name,
                root_cause=data.get("root_cause", "Unknown"),
                fix_suggestion=data.get("fix_suggestion", ""),
                fix_code=data.get("fix_code"),
                confidence=int(data.get("confidence", 50)),
            )
        except (json.JSONDecodeError, ValueError):
            return DebugResult(
                test_name=test_name,
                root_cause=response[:500],
                fix_suggestion="Review the error manually.",
                fix_code=None,
                confidence=0,
            )

    async def analyze_failure_async(
        self,
        test_name: str,
        error: str,
        stack_trace: str,
        source: str,
        test_code: str = "",
    ) -> DebugResult:
        """Async version of analyze_failure (non-streaming — debugger output is JSON)."""
        prompt = build_debugger_prompt(
            test_name=test_name,
            error_message=error,
            stack_trace=stack_trace,
            source_code=source,
            test_code=test_code,
        )
        response = await self.llm.generate_async(SYSTEM_PROMPT, prompt, max_tokens=2048)
        try:
            cleaned = response.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            data = json.loads(cleaned)
            return DebugResult(
                test_name=test_name,
                root_cause=data.get("root_cause", "Unknown"),
                fix_suggestion=data.get("fix_suggestion", ""),
                fix_code=data.get("fix_code"),
                confidence=int(data.get("confidence", 50)),
            )
        except (json.JSONDecodeError, ValueError):
            return DebugResult(
                test_name=test_name,
                root_cause=response[:500],
                fix_suggestion="Review the error manually.",
                fix_code=None,
                confidence=0,
            )

    def analyze_run_failures(self, failures: list[dict]) -> list[DebugResult]:
        results: list[DebugResult] = []
        for failure in failures:
            result = self.analyze_failure(
                test_name=failure.get("test_name", "unknown"),
                error=failure.get("error", ""),
                stack_trace=failure.get("stack_trace", ""),
                source=failure.get("source", ""),
                test_code=failure.get("test_code", ""),
            )
            results.append(result)
        return results
