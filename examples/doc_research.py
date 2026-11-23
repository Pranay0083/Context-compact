"""Research assistant over a folder of documents (notes, specs, papers exported to text/markdown).

    python examples/doc_research.py ~/notes "What did we decide about pricing, and why?"

Synthesising across many documents is where plain agents run out of context. Here each document
is read, the relevant facts are folded into notes with their source, and the answer is built
from the notes; exact quotes can still be pulled back with search_context/recall_context.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import build_agent, file_tools, repl  # noqa: E402

root = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent
questions = sys.argv[2:] or ["Read the documentation in this folder and summarise what the project does, "
                             "how it was evaluated, and its known limitations. Cite the file for each point."]
agent, compaction = build_agent(
    file_tools(root, (".md", ".txt", ".rst")),
    "You are a careful research assistant. Read the documents, attribute every claim to its file, "
    "and distinguish facts from opinions.",
)
repl(agent, compaction, questions)
