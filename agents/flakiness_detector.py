"""
Flakiness Detector — proactive, not reactive.

Scans generated test code for timing/non-determinism patterns BEFORE saving.
High-risk tests are flagged for regeneration with anti-flakiness constraints.
"""
from __future__ import annotations
import re
import logging

logger = logging.getLogger(__name__)

# ── Pattern library (pattern, weight, description) ──────────────────────────

_PYTHON_PATTERNS: list[tuple[str, float, str]] = [
    (r"time\.sleep\(", 30, "time.sleep() — timing dependency"),
    (r"asyncio\.sleep\(", 20, "asyncio.sleep() — timing dependency"),
    (r"datetime\.now\(\)", 20, "datetime.now() — non-deterministic timestamp"),
    (r"datetime\.utcnow\(\)", 20, "datetime.utcnow() — non-deterministic timestamp"),
    (r"random\.", 25, "random.* — non-deterministic values"),
    (r"threading\.Timer", 25, "threading.Timer — timing dependency"),
    (r"os\.environ\[", 10, "os.environ[] — environment variable dependency"),
    (r"open\(.+['\"]r['\"]", 15, "file I/O — external state dependency"),
    (r"requests\.(get|post|put|delete)\(", 35, "HTTP call — network dependency"),
    (r"subprocess\.(run|call|check_output)", 20, "subprocess — external process dependency"),
]

_TYPESCRIPT_PATTERNS: list[tuple[str, float, str]] = [
    (r"setTimeout\(", 30, "setTimeout — timing dependency"),
    (r"setInterval\(", 25, "setInterval — timing dependency"),
    (r"Date\.now\(\)", 20, "Date.now() — non-deterministic timestamp"),
    (r"new Date\(\)", 15, "new Date() — non-deterministic timestamp"),
    (r"Math\.random\(\)", 25, "Math.random() — non-deterministic values"),
    (r"process\.env\[", 10, "process.env[] — environment variable dependency"),
    (r"fetch\(", 35, "fetch() — network dependency"),
    (r"axios\.", 35, "axios.* — network dependency"),
    (r"new Promise.*setTimeout", 35, "Promise with setTimeout — timing + async"),
    (r"\.then\(.*\.then\(", 15, "Chained .then() — async timing risk"),
]

ANTI_FLAKINESS_ADDENDUM_PYTHON = """
CRITICAL ANTI-FLAKINESS RULES (MANDATORY):
- NEVER use time.sleep() or asyncio.sleep() — mock timing with freezegun or pytest-mock
- NEVER use datetime.now() or datetime.utcnow() — use freezegun @freeze_time decorator
- NEVER use random values — use fixed seeds or hardcoded constants
- NEVER make real HTTP calls — use pytest-httpx or unittest.mock.patch
- NEVER read from the filesystem — inject test fixtures or use tmp_path
- ALL async tests must use pytest-asyncio with @pytest.mark.asyncio
- ALWAYS mock os.environ with monkeypatch.setenv()
"""

ANTI_FLAKINESS_ADDENDUM_TYPESCRIPT = """
CRITICAL ANTI-FLAKINESS RULES (MANDATORY):
- NEVER use setTimeout/setInterval — use jest.useFakeTimers() and jest.runAllTimers()
- NEVER use Date.now() or new Date() — use jest.setSystemTime(new Date('2024-01-01'))
- NEVER use Math.random() — mock with jest.spyOn(Math, 'random').mockReturnValue(0.5)
- NEVER make real HTTP calls — use jest.mock() or msw (Mock Service Worker)
- NEVER use process.env directly — inject via constructor or mock the module
- ALL async tests must use async/await with proper try/catch
"""


class FlakinessDetector:
    def __init__(self, extra_patterns: list[str] | None = None) -> None:
        self._extra = extra_patterns or []

    def scan(self, test_code: str, language: str) -> dict:
        """
        Returns {risk_level, score, patterns_found, addendum}.
        risk_level: 'high' | 'medium' | 'low'
        score: 0-100
        patterns_found: list of human-readable descriptions
        addendum: str to inject into prompt on regeneration (empty if low risk)
        """
        patterns = _PYTHON_PATTERNS if language == "python" else _TYPESCRIPT_PATTERNS

        # Add user-defined extra patterns with weight 20
        all_patterns = list(patterns) + [(p, 20.0, f"custom: {p}") for p in self._extra]

        total_score = 0.0
        found: list[str] = []

        for pattern, weight, description in all_patterns:
            if re.search(pattern, test_code, re.IGNORECASE):
                total_score += weight
                found.append(description)

        score = min(100.0, total_score)

        if score >= 60:
            risk_level = "high"
            addendum = (
                ANTI_FLAKINESS_ADDENDUM_PYTHON
                if language == "python"
                else ANTI_FLAKINESS_ADDENDUM_TYPESCRIPT
            )
        elif score >= 30:
            risk_level = "medium"
            addendum = ""
        else:
            risk_level = "low"
            addendum = ""

        if found:
            logger.info("[flakiness] score=%.0f risk=%s patterns=%s", score, risk_level, found)

        return {
            "risk_level": risk_level,
            "score": round(score, 1),
            "patterns_found": found,
            "addendum": addendum,
        }
