"""The two arms of the experiment, built on the same LangChain `create_agent` loop.

* baseline: the plain agent. Every model call gets the full history.
* compact:  the same agent plus `ContextCompactionMiddleware`.

Both share the model, the task tools, the system prompt, retries and usage tracking, so
the only difference is the compactor.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, Callable

from langchain.agents import create_agent
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from context_compact import ContextCompactionMiddleware, TokenBudgetGuard
from experiment.limits import DailyQuotaExceeded, RateLimitMiddleware, format_wait, model_id  # noqa: F401

# Default free-tier Groq model; override with --model or GROQ_MODEL.
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"

SYSTEM_PROMPT = """\
You are a careful engineer investigating a read-only sandbox through tools.
- Use list_files and read_file to gather evidence. Never guess a value you have not read.
- Work step by step. You may call several tools in one step.
- When you have the answer, reply with a short explanation followed by the final answer as \
a JSON object in a ```json code block, using exactly the keys requested."""


FREE_GROQ_MODELS = ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"]
# Free-tier tokens per minute (for Qwen it is *input* tokens per minute). These also cap one request.
FREE_TIER_TPM = {"openai/gpt-oss-120b": 8_000, "openai/gpt-oss-20b": 8_000, "qwen/qwen3.8-27b": 7_000}


def make_llm(provider: str = "groq", model: str | None = None, temperature: float = 0.0, task=None):
    """Chat model factory. provider "fake" returns the scripted offline demo model for `task`."""
    if provider == "fake" or os.getenv("CC_FAKE_LLM"):
        from experiment.fake import DemoChatModel

        if task is None:
            raise ValueError("the offline demo model needs the task it is answering")
        return DemoChatModel.for_task(task)
    if provider == "groq":
        from langchain_groq import ChatGroq

        name = model or os.getenv("GROQ_MODEL") or DEFAULT_GROQ_MODEL
        extra: dict[str, Any] = {}
        if name.startswith("openai/gpt-oss"):
            extra["reasoning_effort"] = "medium"
        elif name.startswith("qwen/"):
            extra["reasoning_format"] = "hidden"
        # max_retries=0: RateLimitMiddleware owns retries, so waits are visible and bounded.
        return ChatGroq(model=name, temperature=temperature, max_retries=0, timeout=120, **extra)
    if provider == "xai":
        from langchain_xai import ChatXAI  # pip install -e .[xai]

        return ChatXAI(model=model or os.getenv("XAI_MODEL", "grok-4-fast"), temperature=temperature, max_retries=0)
    raise ValueError(f"unknown provider {provider!r}")


class UsageTracker(AgentMiddleware):
    """Records what every model call actually cost, as reported by the provider."""

    def __init__(self, compaction: ContextCompactionMiddleware | None = None, verbose: bool = False,
                 on_call: Callable[[dict], None] | None = None):
        super().__init__()
        self.compaction = compaction
        self.verbose = verbose
        self.on_call = on_call
        self.calls: list[dict] = []
        self.errors: list[dict] = []

    def wrap_model_call(self, request, handler):
        t0 = time.perf_counter()
        try:
            response = handler(request)
        except Exception as e:
            self.errors.append({"type": type(e).__name__, "message": str(e)[:500]})
            raise
        ai = next((m for m in reversed(response.result) if isinstance(m, AIMessage)), None)
        usage = (ai.usage_metadata if ai else None) or {}
        view = self.compaction.last_view if self.compaction else None
        call = {
            "index": len(self.calls) + 1,
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "model": model_id(request.model),
            "messages_sent": len(request.messages),
            "latency_s": round(time.perf_counter() - t0, 2),
            "tool_calls": [{"name": tc["name"], "args": tc.get("args", {})} for tc in (ai.tool_calls if ai else [])],
            "nudged": bool(view and view.nudged),
            "forced": bool(view and view.force_fold),
            "auto_folds": list(view.auto_folds) if view else [],
        }
        self.calls.append(call)
        if self.on_call:
            self.on_call(call)
        if self.verbose:
            flags = (" nudged" if call["nudged"] else "") + (" forced" if call["forced"] else "")
            flags += f" auto={call['auto_folds']}" if call["auto_folds"] else ""
            names = ",".join(t["name"] for t in call["tool_calls"]) or "answer"
            print(f"      call {call['index']:>2}: in={call['input_tokens']} out={call['output_tokens']} "
                  f"[{call['model'].split('/')[-1]}] -> {names}{flags}", flush=True)
        return response


@dataclass
class AgentBundle:
    agent: Any
    tracker: UsageTracker
    compaction: ContextCompactionMiddleware | None
    limiter: RateLimitMiddleware


def build_agent(
    arm: str,
    llm,
    tools: list,
    *,
    window: int,
    baseline_window: int | None = None,
    archive_path: str | None = None,
    verbose: bool = False,
    fallbacks: list | None = None,
    tpm_limit: int | None = 8_000,
    max_daily_wait: float = 900.0,
    on_event: Callable[[dict], None] | None = None,
    on_call: Callable[[dict], None] | None = None,
) -> AgentBundle:
    compaction = None
    middleware: list[AgentMiddleware] = []
    if arm == "compact":
        compaction = ContextCompactionMiddleware(window=window, archive_path=archive_path)
        middleware.append(compaction)
    elif arm == "baseline":
        if baseline_window:
            middleware.append(TokenBudgetGuard(baseline_window))
    else:
        raise ValueError(f"unknown arm {arm!r}")
    if verbose and on_event is None:
        def on_event(e: dict) -> None:
            if e["type"] == "rate_limit":
                print(f"      … {e['kind'].upper()} limit on {e['model']}: waiting {format_wait(e['wait_s'])}", flush=True)
            elif e["type"] == "fallback":
                print(f"      … falling back {e['from']} -> {e['to']} ({e['reason']})", flush=True)
    limiter = RateLimitMiddleware(tpm_limit=tpm_limit, max_daily_wait=max_daily_wait,
                                  fallbacks=fallbacks, on_event=on_event)
    tracker = UsageTracker(compaction, verbose=verbose, on_call=on_call)
    middleware += [limiter, tracker]
    agent = create_agent(llm, tools, system_prompt=SYSTEM_PROMPT, middleware=middleware, checkpointer=InMemorySaver())
    return AgentBundle(agent, tracker, compaction, limiter)
