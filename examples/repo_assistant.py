"""Coding assistant over a real repository, inside a small context window.

    python examples/repo_assistant.py                       # this repo
    python examples/repo_assistant.py ~/code/my-project "Where is auth handled?"

Reading source files fills context fast; the agent folds what it has understood into notes
and keeps going, so a free 8K-per-request model can still explore a whole codebase.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import build_agent, file_tools, repl  # noqa: E402

root = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
questions = sys.argv[2:] or [
    "Give me a map of this repository: the main modules and what each one does.",
    "How does a request flow from the agent loop into the compactor and back? Cite file:line.",
    "Which configuration knobs control when folding happens, and what are their defaults?",
]
agent, compaction = build_agent(
    file_tools(root, (".py", ".md", ".ts", ".tsx", ".js", ".json", ".toml", ".go", ".rs", ".java")),
    "You are a senior engineer exploring a codebase with read-only tools. Read before you answer, "
    "cite file:line for claims, and say when you are unsure.",
)
repl(agent, compaction, questions)
