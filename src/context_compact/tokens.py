"""Token estimation, calibrated online against the provider's reported usage.

The compactor has to decide *before* each call whether the request fits, but every
provider tokenizes differently. We estimate with tiktoken (or chars/4 if it is
unavailable) and keep a running correction ratio from `usage_metadata.input_tokens`.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterable

from langchain_core.messages import AIMessage, BaseMessage

PER_MESSAGE_OVERHEAD = 4  # role markers / separators added by chat templates


@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception:  # offline or not installed: fall back to a heuristic
        return None


def count_text(text: str) -> int:
    if not text:
        return 0
    enc = _encoder()
    if enc is None:
        return max(1, len(text) // 4)
    return len(enc.encode(text, disallowed_special=()))


def content_text(content: Any) -> str:
    """Flatten LangChain message content (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(parts)


def message_text(msg: BaseMessage) -> str:
    """Text of a message as the model sees it, including tool-call arguments."""
    text = content_text(msg.content)
    if isinstance(msg, AIMessage) and msg.tool_calls:
        calls = [f"{tc['name']}({json.dumps(tc.get('args', {}), ensure_ascii=False)})" for tc in msg.tool_calls]
        text = (text + "\n" if text else "") + "\n".join(calls)
    return text


class TokenCounter:
    """Estimates request size and self-corrects from real usage numbers."""

    def __init__(self, ratio: float = 1.0, alpha: float = 0.3):
        self.ratio = ratio
        self.alpha = alpha
        self._tool_cache: dict[tuple, int] = {}

    def count_text(self, text: str) -> int:
        return count_text(text)

    def count_message(self, msg: BaseMessage) -> int:
        return count_text(message_text(msg)) + PER_MESSAGE_OVERHEAD

    def count_messages(self, msgs: Iterable[BaseMessage]) -> int:
        return sum(self.count_message(m) for m in msgs)

    def count_tools(self, tools: Iterable[Any]) -> int:
        tools = list(tools or [])
        key = tuple(getattr(t, "name", None) or json.dumps(t, sort_keys=True, default=str) for t in tools)
        if key not in self._tool_cache:
            from langchain_core.utils.function_calling import convert_to_openai_tool

            total = 0
            for t in tools:
                try:
                    schema = t if isinstance(t, dict) else convert_to_openai_tool(t)
                except Exception:
                    schema = {"name": str(t)}
                total += count_text(json.dumps(schema, ensure_ascii=False))
            self._tool_cache[key] = total
        return self._tool_cache[key]

    def scale(self, raw: int) -> int:
        return int(raw * self.ratio)

    def calibrate(self, raw_estimate: int, actual: int | None) -> None:
        if not actual or raw_estimate <= 0:
            return
        observed = actual / raw_estimate
        if 0.3 < observed < 3.0:  # ignore nonsense (e.g. cached-token accounting quirks)
            self.ratio = (1 - self.alpha) * self.ratio + self.alpha * observed
