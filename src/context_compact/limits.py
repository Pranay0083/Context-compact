"""Free-tier-proof model calls: pacing, waiting out limits, and model fallback.

Groq's free tier enforces per-model limits on tokens per minute (TPM, which also caps a single
request), requests per day (RPD) and tokens per day (TPD, a rolling window). This middleware
makes an agent survive them instead of crashing:

* **Pacing.** Before each call it estimates the request and waits until the model's last-60s
  token usage leaves room, so we rarely trigger a TPM 429 in the first place.
* **Waiting.** On a 429 it reads Groq's "Please try again in 4m50.7s" (or the retry-after
  header) and sleeps that long. Daily quotas are rolling, so a short wait near the limit often
  frees enough tokens. Waits longer than `max_daily_wait` are not worth it...
* **Fallback.** ...then the call moves to the next model in `fallbacks` (for example another
  free model with its own quota). With no fallback left it raises `DailyQuotaExceeded`, which
  says when to come back.
* **Resampling.** Groq sometimes rejects a malformed tool call from the model
  (`tool_use_failed`); that is retried a few times.
* "Request too large" is never retried: the request itself exceeds the per-request ceiling.

Every wait and fallback is reported through `on_event`, so a UI can show a countdown.
"""

from __future__ import annotations

import re
import threading
import time
from collections import deque
from typing import Any, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from .tokens import TokenCounter, content_text

Event = dict[str, Any]


class DailyQuotaExceeded(RuntimeError):
    def __init__(self, model: str, kind: str, wait_s: float | None):
        when = f"; it frees up in about {format_wait(wait_s)}" if wait_s else ""
        super().__init__(f"{kind.upper()} quota exhausted for {model}{when}. Re-run with --resume later, or add --fallback-models.")
        self.model, self.kind, self.wait_s = model, kind, wait_s


def format_wait(s: float | None) -> str:
    if s is None:
        return "?"
    s = int(round(s))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{sec:02d}s" if m else f"{sec}s")


_WAIT = re.compile(r"try again in\s+((?:[\d.]+h)?(?:[\d.]+m(?!s))?(?:[\d.]+s)?(?:[\d.]+ms)?)", re.I)


def parse_wait(exc: BaseException) -> float | None:
    """Seconds to wait, from Groq's message ("try again in 4m50.736s") or a retry-after header."""
    m = _WAIT.search(str(exc))
    if m and m.group(1):
        total = 0.0
        for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", m.group(1)):
            total += float(num) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
        if total:
            return total
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError):
        return None


def limit_kind(exc: BaseException) -> str | None:
    """Classify a provider error: tpd, rpd, tpm, rpm, too_large, tool_choice, tool_use_failed, transient."""
    msg = str(exc).lower()
    status = getattr(exc, "status_code", None)
    if status == 413 or "request too large" in msg:
        return "too_large"
    if "tool_choice" in msg and ("does not match" in msg or "tool choice" in msg):
        return "tool_choice"
    if "tokens per day" in msg or "(tpd)" in msg:
        return "tpd"
    if "requests per day" in msg or "(rpd)" in msg:
        return "rpd"
    if "tokens per minute" in msg or "(tpm)" in msg:
        return "tpm"
    if "requests per minute" in msg or "(rpm)" in msg:
        return "rpm"
    if status == 429:
        return "tpm"
    if any(s in msg for s in ("tool_use_failed", "failed_generation", "output_parse_failed")):
        return "tool_use_failed"
    name = type(exc).__name__
    if name in ("APIConnectionError", "APITimeoutError", "InternalServerError") or (status and status >= 500):
        return "transient"
    return None


def model_id(llm: Any) -> str:
    return getattr(llm, "model_name", None) or getattr(llm, "model", None) or type(llm).__name__


class TokenPacer:
    """Per-model sliding 60-second window of tokens used, shared across agents in a process."""

    _windows: dict[str, deque] = {}
    _lock = threading.Lock()

    def __init__(self, tpm_limit: int | dict[str, int] | None):
        self.tpm_limit = tpm_limit

    def limit_for(self, model: str) -> int | None:
        if isinstance(self.tpm_limit, dict):
            return self.tpm_limit.get(model) or self.tpm_limit.get("*")
        return self.tpm_limit

    def wait_time(self, model: str, tokens: int) -> float:
        limit = self.limit_for(model)
        if not limit or tokens >= limit:
            return 0.0  # unlimited, or the request can never fit: let the provider decide
        now = time.time()
        with self._lock:
            q = self._windows.setdefault(model, deque())
            while q and now - q[0][0] > 60:
                q.popleft()
            used = sum(t for _, t in q)
            budget = int(limit * 0.95)
            if used + tokens <= budget:
                return 0.0
            freed, wait = 0, 0.0
            for ts, t in q:  # how long until enough of the window expires
                freed += t
                wait = 60 - (now - ts) + 0.5
                if used - freed + tokens <= budget:
                    break
            return max(0.0, wait)

    def record(self, model: str, tokens: int) -> None:
        if not self.limit_for(model):
            return
        with self._lock:
            self._windows.setdefault(model, deque()).append((time.time(), tokens))


class RateLimitMiddleware(AgentMiddleware):
    def __init__(
        self,
        *,
        tpm_limit: int | dict[str, int] | None = 8_000,  # per model: {"model-id": 7000, "*": 8000}
        max_daily_wait: float = 900.0,
        fallbacks: list[Any] | None = None,
        max_attempts: int = 10,
        on_event: Callable[[Event], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        super().__init__()
        self.pacer = TokenPacer(tpm_limit)
        self.max_daily_wait = max_daily_wait
        self.fallbacks = list(fallbacks or [])
        self.max_attempts = max_attempts
        self.on_event = on_event
        self.sleep = sleep
        self.counter = TokenCounter()
        self.waited_s = 0.0
        self.fallbacks_used: list[str] = []

    def _emit(self, event: Event) -> None:
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:
                pass

    def _wait(self, seconds: float, kind: str, model: str) -> None:
        self._emit({"type": "rate_limit", "kind": kind, "wait_s": round(seconds, 1), "model": model})
        self.waited_s += seconds
        self.sleep(seconds)

    def _estimate(self, request) -> int:
        system = content_text(request.system_message.content) if request.system_message else ""
        return (
            self.counter.count_text(system)
            + self.counter.count_messages(request.messages)
            + self.counter.count_tools(request.tools)
            + 600  # typical completion incl. reasoning
        )

    def wrap_model_call(self, request, handler):
        tool_use_retries = 0
        for attempt in range(self.max_attempts):
            model = model_id(request.model)
            pace = self.pacer.wait_time(model, self._estimate(request))
            if pace > 0:
                self._wait(pace, "tpm", model)
            try:
                response = handler(request)
            except Exception as exc:
                kind = limit_kind(exc)
                if kind in (None, "too_large", "tool_choice") or attempt == self.max_attempts - 1:
                    raise
                if kind == "tool_use_failed":
                    tool_use_retries += 1
                    if tool_use_retries > 3:
                        raise
                    continue
                wait = parse_wait(exc)
                if kind in ("tpd", "rpd"):
                    if wait is not None and wait <= self.max_daily_wait:
                        self._wait(wait + 1, kind, model)
                        continue
                    if self.fallbacks:
                        nxt = self.fallbacks.pop(0)
                        self.fallbacks_used.append(model_id(nxt))
                        self._emit({"type": "fallback", "from": model, "to": model_id(nxt),
                                    "reason": f"{kind} quota exhausted (frees in {format_wait(wait)})"})
                        request = request.override(model=nxt)
                        continue
                    raise DailyQuotaExceeded(model, kind, wait) from exc
                self._wait(min(wait or 2.0 * 2**attempt, 120.0), kind, model)
                continue
            ai = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
            usage = (ai.usage_metadata if ai else None) or {}
            self.pacer.record(model, (usage.get("input_tokens") or 0) + (usage.get("output_tokens") or 0))
            return response
        raise RuntimeError("unreachable")
