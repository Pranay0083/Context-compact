"""The three tools the model uses to manage its own context."""

from __future__ import annotations

from langchain_core.tools import BaseTool, tool

from .compactor import ContextCompactor


def build_tools(compactor: ContextCompactor) -> list[BaseTool]:
    @tool("compress_context")
    def compress_context(start_ref: str, end_ref: str, topic: str, summary: str) -> str:
        """Fold a finished range of the conversation into a summary to free context space.

        start_ref and end_ref are message ids such as "m0004" (inclusive; the range is
        widened to whole steps). topic is a few words naming the range. summary replaces
        the range: keep every exact value you may still need (numbers, names, ids, paths,
        timestamps, errors), plus findings, decisions and what is left to do.
        """
        return compactor.compress(start_ref, end_ref, topic, summary)

    @tool("recall_context")
    def recall_context(target: str) -> str:
        """Show archived originals of a fold ("b002") or of messages ("m0007" or "m0005-m0009").

        Read-only: the range stays folded. Use it when a summary lacks an exact detail.
        """
        return compactor.recall(target)

    @tool("search_context")
    def search_context(query: str) -> str:
        """Keyword search over folded (archived) history; returns snippets with message ids."""
        return compactor.search(query)

    return [compress_context, recall_context, search_context]
