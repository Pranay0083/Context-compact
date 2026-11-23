"""Scripted offline "demo model": exercises the whole stack without an API key or quota.

It explores the sandbox one file per call, folds when the context manager nudges (writing a
real summary from the facts in the tool results), searches folded history before answering a
follow-up, and answers preset questions from the task's answer key. Free-form questions get a
canned reply. Use it for UI development, demos without a key, and dry runs (`--fake`).
"""

from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from context_compact.tokens import TokenCounter, content_text

class SimulatedRequestTooLarge(RuntimeError):
    """Mimics Groq's free-tier HTTP 413 so the offline demo hits the same wall."""

    status_code = 413


FACT = re.compile(r'"(name|owner|region|port|timeout_ms|retries|max_connections|depends_on)":|ERROR|WARN|lock|job=')
TAG = re.compile(r"^\[m(\d+)\] ")


class DemoChatModel(BaseChatModel):
    model_name: str = "demo/offline-scripted"
    request_limit: int | None = 8_000  # like Groq's free tier; None = unlimited
    files: list[str] = []
    answer_key: list[dict] = []  # [{"question": str, "expected": dict, "kind": str}]
    _read: set = PrivateAttr(default_factory=set)
    _searched: set = PrivateAttr(default_factory=set)
    _counter: TokenCounter = PrivateAttr(default_factory=TokenCounter)
    _n: int = PrivateAttr(default=0)

    @classmethod
    def for_task(cls, task) -> "DemoChatModel":
        return cls(
            files=sorted(p for p in task.files if not p.endswith(".md")),
            answer_key=[{"question": t.question, "expected": t.expected, "kind": t.kind} for t in task.turns],
        )

    @property
    def _llm_type(self) -> str:
        return "demo-scripted"

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any):
        names = [getattr(t, "name", None) or t.get("function", {}).get("name") for t in tools]
        return self.bind(tool_choice=tool_choice, tool_names=names)

    # ------------------------------------------------------------- helpers
    def _msg(self, messages, **kwargs) -> ChatResult:
        usage = {
            "input_tokens": sum(self._counter.count_message(m) for m in messages),
            "output_tokens": 40,
            "total_tokens": 0,
        }
        self._n += 1
        return ChatResult(generations=[ChatGeneration(message=AIMessage(usage_metadata=usage, **kwargs))])

    def _call(self, name: str, args: dict) -> dict:
        return {"name": name, "args": args, "id": f"demo-{self._n}-{name}"}

    @staticmethod
    def _untag(text: str) -> str:
        return TAG.sub("", text)

    def _fold_call(self, system: str, messages: list) -> dict | None:
        m = re.search(r"Foldable ranges: (m\d+)-(m\d+)", system)
        if not m:
            return None
        lo, hi = int(m.group(1)[1:]), int(m.group(2)[1:])
        facts, paths = [], []
        for msg in messages:
            if isinstance(msg, ToolMessage):
                text = content_text(msg.content)
                tag = TAG.match(text)
                if tag and lo <= int(tag.group(1)) <= hi:
                    body = self._untag(text)
                    path = re.search(r'"name": "([\w-]+)"', body)
                    paths.append(path.group(1) if path else msg.name or "result")
                    facts += [" ".join(line.split()) for line in body.splitlines() if FACT.search(line)][:12]
        summary = "Done so far: read " + (", ".join(paths) or "earlier files") + ".\n" + "\n".join(facts[:40])
        return self._call("compress_context", {"start_ref": m.group(1), "end_ref": m.group(2),
                                               "topic": "files read so far", "summary": summary})

    # ------------------------------------------------------------ generate
    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        size = sum(self._counter.count_message(m) for m in messages)
        if self.request_limit and size > self.request_limit:
            raise SimulatedRequestTooLarge(
                f"Request too large for model {self.model_name} on tokens per minute (TPM): "
                f"Limit {self.request_limit}, Requested {size} (simulated free-tier limit)"
            )
        system = next((content_text(m.content) for m in messages if isinstance(m, SystemMessage)), "")
        tool_names = kwargs.get("tool_names") or []
        forced = kwargs.get("tool_choice") == "compress_context"
        human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
        question = self._untag(content_text(human.content)) if human else ""
        entry = next((a for a in self.answer_key if question.startswith(a["question"][:80])), None)

        calls = []
        if "## Context alert" in system or forced:
            fold = self._fold_call(system, messages)
            if fold:
                calls.append(fold)
        if forced:
            return self._msg(messages, content="", tool_calls=calls[:1] or [self._call("compress_context", {
                "start_ref": "m0002", "end_ref": "m0003", "topic": "early steps", "summary": "early exploration"})])

        if entry is None:
            text = ("I'm the offline demo model, so I can only answer the preset questions for this sandbox. "
                    "Start the server without CC_FAKE_LLM (and with GROQ_API_KEY set) for real answers.")
            return self._msg(messages, content=text, tool_calls=calls)

        if entry["kind"] == "task":
            todo = [p for p in self.files if p not in self._read]
            if todo:
                self._read.add(todo[0])
                return self._msg(messages, content="", tool_calls=[*calls, self._call("read_file", {"path": todo[0]})])
        elif "search_context" in tool_names and question not in self._searched:
            self._searched.add(question)
            words = re.findall(r"[a-z_]{5,}", question.lower())
            return self._msg(messages, content="", tool_calls=[*calls, self._call("search_context", {"query": " ".join(words[:3])})])
        answer = "Here is what I found.\n```json\n" + json.dumps(entry["expected"]) + "\n```"
        return self._msg(messages, content=answer, tool_calls=calls)
