"""SRE incident triage over a folder of logs.

    python examples/log_triage.py /var/log/myapp "Why did checkout fail at 14:03?"
    python examples/log_triage.py            # generates a sample incident from the benchmark

Logs are huge and mostly noise. The agent reads them, keeps exact ids and timestamps in its
fold notes, and follows a request across services without holding every log in context.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _common import build_agent, file_tools, repl  # noqa: E402

if len(sys.argv) > 1:
    root, questions = Path(sys.argv[1]).expanduser(), sys.argv[2:]
else:
    from experiment.workloads import build_task

    task = build_task("incident_trace", 7, "medium")
    root = Path(tempfile.mkdtemp(prefix="incident-"))
    for rel, text in task.files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    print(f"Sample logs written to {root}")
    questions = [task.prompt.split(" Finish with")[0], "Give me a timeline of the incident with exact timestamps.",
                 "What would you change to prevent this?"]
agent, compaction = build_agent(
    file_tools(root),
    "You are an on-call SRE. Investigate with the tools, follow request/trace/transaction ids across "
    "services, and quote exact log lines and timestamps as evidence.",
)
repl(agent, compaction, questions)
