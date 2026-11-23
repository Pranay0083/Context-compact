"""LangChain `create_agent` middleware wrappers."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, SystemMessage

from .archive import Archive
from .compactor import COMPRESS_TOOL, CompactionConfig, ContextCompactor, ContextOverflowError, View
from .tokens import PER_MESSAGE_OVERHEAD, TokenCounter, content_text
from .tools import build_tools


def _last_ai(response: Any) -> AIMessage | None:
    msgs = getattr(response, "result", None)
    if msgs is None:
        msgs = [response]
    for m in reversed(msgs):
        if isinstance(m, AIMessage):
            return m
    return None


class ContextCompactionMiddleware(AgentMiddleware):
    """Drop-in compaction for `langchain.agents.create_agent`.

    Example:
        agent = create_agent(model, tools, middleware=[ContextCompactionMiddleware(window=12_000)])

    The agent's state keeps the full history; only what is sent to the model is compacted.
    """

    def __init__(
        self,
        compactor: ContextCompactor | None = None,
        *,
        archive_path: str | None = None,
        **config: Any,
    ):
        super().__init__()
        if compactor is None:
            archive = Archive(archive_path) if archive_path else None
            compactor = ContextCompactor(CompactionConfig(**config), archive=archive)
        self.compactor = compactor
        self.tools = build_tools(compactor)
        self.last_view: View | None = None

    @staticmethod
    def _forced_fold_rejected(exc: BaseException) -> bool:
        """Some providers reject a response that ignores a forced tool_choice; retry unforced."""
        msg = str(exc).lower()
        return "tool_choice" in msg or "tool choice" in msg

    def _prepare(self, request: ModelRequest) -> tuple[View, ModelRequest]:
        base = content_text(request.system_message.content) if request.system_message else ""
        tools_tokens = self.compactor.counter.count_tools(request.tools)
        view = self.compactor.prepare(request.messages, base, tools_tokens)
        self.last_view = view
        overrides: dict[str, Any] = {"system_message": SystemMessage(content=view.system), "messages": view.messages}
        if view.force_fold:
            overrides["tool_choice"] = COMPRESS_TOOL
        return view, request.override(**overrides)

    def wrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], ModelResponse]
    ) -> ModelResponse:
        original_choice = request.tool_choice
        view, request = self._prepare(request)
        try:
            response = handler(request)
        except Exception as exc:
            if not (view.force_fold and self._forced_fold_rejected(exc)):
                raise
            self.compactor.counts["forced_rejected"] = self.compactor.counts.get("forced_rejected", 0) + 1
            view.force_fold = False
            response = handler(request.override(tool_choice=original_choice))
        self.compactor.observe(_last_ai(response), view)
        return response

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        original_choice = request.tool_choice
        view, request = self._prepare(request)
        try:
            response = await handler(request)
        except Exception as exc:
            if not (view.force_fold and self._forced_fold_rejected(exc)):
                raise
            self.compactor.counts["forced_rejected"] = self.compactor.counts.get("forced_rejected", 0) + 1
            view.force_fold = False
            response = await handler(request.override(tool_choice=original_choice))
        self.compactor.observe(_last_ai(response), view)
        return response


class TokenBudgetGuard(AgentMiddleware):
    """Fail fast when a request exceeds a token budget (used for the plain baseline)."""

    def __init__(self, window: int):
        super().__init__()
        self.window = window
        self.counter = TokenCounter()

    def _check(self, request: ModelRequest) -> int:
        system = content_text(request.system_message.content) if request.system_message else ""
        raw = (
            self.counter.count_text(system)
            + PER_MESSAGE_OVERHEAD
            + self.counter.count_messages(request.messages)
            + self.counter.count_tools(request.tools)
        )
        if self.counter.scale(raw) > self.window:
            raise ContextOverflowError(self.counter.scale(raw), self.window)
        return raw

    def wrap_model_call(self, request, handler):
        raw = self._check(request)
        response = handler(request)
        ai = _last_ai(response)
        self.counter.calibrate(raw, (getattr(ai, "usage_metadata", None) or {}).get("input_tokens"))
        return response

    async def awrap_model_call(self, request, handler):
        raw = self._check(request)
        response = await handler(request)
        ai = _last_ai(response)
        self.counter.calibrate(raw, (getattr(ai, "usage_metadata", None) or {}).get("input_tokens"))
        return response
