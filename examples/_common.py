"""Shared bits for the examples: a free-tier-proof Groq agent with compaction, file tools, a REPL."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from context_compact import ContextCompactionMiddleware, RateLimitMiddleware
from context_compact.tokens import content_text

load_dotenv(find_dotenv(usecwd=True))


def groq(model: str):
    from langchain_groq import ChatGroq

    extra = {"reasoning_effort": "medium"} if model.startswith("openai/gpt-oss") else {}
    return ChatGroq(model=model, temperature=0, max_retries=0, **extra)


def build_agent(tools, system_prompt: str, window: int = 6_000):
    """An agent that stays inside `window` tokens per request and survives free-tier limits."""
    if not os.getenv("GROQ_API_KEY"):
        sys.exit("Set GROQ_API_KEY in .env first.")
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    compaction = ContextCompactionMiddleware(window=window)
    limits = RateLimitMiddleware(
        tpm_limit=8_000,  # Groq free tier
        fallbacks=[groq(m) for m in ("openai/gpt-oss-20b", "qwen/qwen3.8-27b") if m != model],
        on_event=lambda e: print(f"   … {e}", file=sys.stderr),
    )
    agent = create_agent(groq(model), tools, system_prompt=system_prompt,
                         middleware=[compaction, limits], checkpointer=InMemorySaver())
    return agent, compaction


def file_tools(root: Path, suffixes: tuple[str, ...] | None = None):
    """Read-only tools confined to `root`."""
    root = root.resolve()

    def safe(rel: str) -> Path:
        p = (root / rel).resolve()
        if p != root and root not in p.parents:
            raise ValueError("path escapes the workspace")
        return p

    @tool
    def list_dir(path: str = ".") -> str:
        """List files under a directory of the workspace (recursive, with sizes in KB)."""
        base = safe(path)
        rows = []
        for p in sorted(base.rglob("*")):
            if p.is_file() and not any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in p.parts):
                if suffixes is None or p.suffix in suffixes:
                    rows.append(f"{p.relative_to(root)}  ({p.stat().st_size / 1024:.1f} KB)")
        return "\n".join(rows[:300]) or "(empty)"

    @tool
    def read_file(path: str, start_line: int = 1, max_lines: int = 300) -> str:
        """Read part of a text file: lines start_line .. start_line+max_lines-1."""
        lines = safe(path).read_text(errors="replace").splitlines()
        chunk = lines[start_line - 1 : start_line - 1 + max_lines]
        more = f"\n… {len(lines) - (start_line - 1 + len(chunk))} more lines" if start_line - 1 + len(chunk) < len(lines) else ""
        return "\n".join(f"{start_line + i:>5} {line}" for i, line in enumerate(chunk)) + more

    @tool
    def grep(pattern: str, path: str = ".") -> str:
        """Case-insensitive substring search; returns up to 40 matching lines as path:line: text."""
        out = []
        for p in sorted(safe(path).rglob("*")):
            if p.is_file() and (suffixes is None or p.suffix in suffixes) and p.stat().st_size < 2_000_000:
                for n, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if pattern.lower() in line.lower():
                        out.append(f"{p.relative_to(root)}:{n}: {line.strip()[:200]}")
                        if len(out) >= 40:
                            return "\n".join(out)
        return "\n".join(out) or "no matches"

    return [list_dir, read_file, grep]


def repl(agent, compaction, questions: list[str] | None = None) -> None:
    """Ask the scripted questions, then keep chatting; prints context stats after each answer."""
    thread = {"configurable": {"thread_id": "example"}, "recursion_limit": 150}
    queue = list(questions or [])
    print("Ask questions; an empty line quits.")
    while True:
        q = queue.pop(0) if queue else input("\nyou> ").strip()
        if not q:
            return
        if queue or questions:
            print(f"\nyou> {q}")
        out = agent.invoke({"messages": [HumanMessage(q)]}, thread)
        print("\nagent>", content_text(out["messages"][-1].content))
        st, view = compaction.compactor.stats(), compaction.last_view
        print(f"[context: last request ~{view.estimate:,} / {compaction.compactor.config.window:,} tokens · "
              f"history {len(out['messages'])} messages · folds {st['folds_model']} by model, "
              f"{st['folds_auto']} auto · {st['tokens_replaced']:,} tokens folded]")
        questions = None
