"""The golden dataset: fixed, versioned evaluation sessions with known answers.

    python -m experiment.golden build    # (re)write golden/v1.json from the generators
    python -m experiment.golden check    # verify the file still matches the generators

Each case is a sandbox (family + seed + size, pinned by a SHA-256 of its files) and a list of
turns asked in one conversation: the main task, a recall probe about an early detail, and two
follow-ups. Later turns are where compaction matters: by then the early evidence has usually
been folded, so the agent must answer from its notes, the archive, or by re-reading.

The JSON file is the source of truth for questions and expected answers. Graders live in code
(experiment/workloads.py) and are re-attached by regenerating the sandbox and checking the hash.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from experiment.workloads import Task, build_task

VERSION = "v1"
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "golden" / f"{VERSION}.json"
FAMILY_PREFIX = {"service_audit": "audit", "incident_trace": "incident"}

# (family, seed, size): small cases fit a free-tier day; medium ones are the stress tests.
SPEC = [
    ("service_audit", 11, "small"),
    ("service_audit", 23, "small"),
    ("service_audit", 37, "medium"),
    ("incident_trace", 11, "small"),
    ("incident_trace", 23, "small"),
    ("incident_trace", 37, "medium"),
]


def case_id(family: str, seed: int, size: str) -> str:
    return f"{FAMILY_PREFIX[family]}-s{seed}-{size}"


def build() -> dict:
    cases = []
    for family, seed, size in SPEC:
        task = build_task(family, seed, size)
        cases.append({
            "id": case_id(family, seed, size),
            "family": family, "seed": seed, "size": size,
            "files": len(task.files), "corpus_tokens": task.corpus_tokens,
            "files_sha256": task.files_sha256,
            "turns": [{"kind": t.kind, "question": t.question, "expected": t.expected} for t in task.turns],
        })
    return {
        "version": VERSION,
        "description": (
            "Multi-turn agent sessions over synthetic sandboxes with exact, checkable answers. "
            "Turn 1 is the main task, turn 2 a recall probe about an early detail, turns 3-4 follow-ups."
        ),
        "cases": cases,
    }


def task_for(case: dict) -> Task:
    """Regenerate a case's sandbox, verify it is byte-identical, and attach graders."""
    task = build_task(case["family"], case["seed"], case["size"])
    if task.files_sha256 != case["files_sha256"]:
        raise ValueError(f"{case['id']}: sandbox no longer matches the golden hash (generator changed?)")
    for t, g in zip(task.turns, case["turns"], strict=True):
        if (t.kind, t.question, t.expected) != (g["kind"], g["question"], g["expected"]):
            raise ValueError(f"{case['id']}: turn {g['kind']!r} differs from the golden file")
    return task


def load(path: str | Path = DEFAULT_PATH) -> dict:
    p = Path(path)
    if not p.is_absolute() and not p.exists():
        p = ROOT / p
    return json.loads(p.read_text())


def load_cases(path: str | Path = DEFAULT_PATH, ids: str | None = None) -> tuple[dict, list[tuple[dict, Task]]]:
    dataset = load(path)
    wanted = {i.strip() for i in ids.split(",")} if ids else None
    cases = [c for c in dataset["cases"] if wanted is None or c["id"] in wanted]
    if wanted and len(cases) != len(wanted):
        missing = wanted - {c["id"] for c in cases}
        raise SystemExit(f"unknown golden case id(s): {', '.join(sorted(missing))}")
    return dataset, [(c, task_for(c)) for c in cases]


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "build":
        DEFAULT_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEFAULT_PATH.write_text(json.dumps(build(), indent=2) + "\n")
        print(f"wrote {DEFAULT_PATH}")
    elif cmd == "check":
        dataset = load()
        for c in dataset["cases"]:
            task_for(c)
        turns = sum(len(c["turns"]) for c in dataset["cases"])
        print(f"golden {dataset['version']}: {len(dataset['cases'])} cases, {turns} graded turns, all reproducible")
    else:
        raise SystemExit("usage: python -m experiment.golden [build|check]")


if __name__ == "__main__":
    main()
