"""Offline tests: no API key needed. A scripted chat model drives a real create_agent loop."""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from context_compact import (
    CompactionConfig,
    ContextCompactionMiddleware,
    ContextCompactor,
    ContextOverflowError,
    TokenBudgetGuard,
)

FILLER = "lorem ipsum dolor sit amet consectetur " * 60  # ~400 tokens


def make_history(n_reads: int) -> list:
    h: list = [HumanMessage("Audit the services.", id="h0")]
    for k in range(n_reads):
        h.append(AIMessage("", tool_calls=[{"name": "read_file", "args": {"path": f"s{k}.py"}, "id": f"c{k}"}], id=f"a{k}"))
        h.append(ToolMessage(f"PORT_{k}={8000 + k}\n{FILLER}", tool_call_id=f"c{k}", name="read_file", id=f"t{k}"))
    return h


def roles(msgs) -> list[str]:
    return [m.type for m in msgs]


def assert_pairs_valid(msgs) -> None:
    """Every tool message must directly follow the assistant call that requested it."""
    open_ids: set[str] = set()
    for m in msgs:
        if isinstance(m, AIMessage):
            open_ids = {tc["id"] for tc in m.tool_calls}
        elif isinstance(m, ToolMessage):
            assert m.tool_call_id in open_ids, f"orphan tool message {m.tool_call_id}"
        else:
            open_ids = set()


def test_fold_replaces_range_with_summary_and_keeps_pairs():
    c = ContextCompactor(CompactionConfig(window=50_000))
    h = make_history(4)
    c.sync(h)
    # m0003 is the first tool result; ask to fold it through the third read (m0007).
    receipt = c.compress("m0003", "m0007", "first reads", "s0 port 8000; s1 port 8001; s2 port 8002")
    assert receipt.startswith("Folded m0002-m0007 into b001"), receipt  # widened to whole steps
    view = c.prepare(h, "sys")
    assert roles(view.messages) == ["human", "ai", "ai", "tool"]  # task, fold notes, last read
    note = view.messages[1]
    assert note.content.startswith("[Notes on m0002-m0007 · fold b001") and "s1 port 8001" in note.content
    assert not note.tool_calls
    assert_pairs_valid(view.messages)
    assert view.messages[0].content.startswith("[m0001] ")


def test_task_message_is_protected():
    c = ContextCompactor(CompactionConfig(window=50_000))
    h = make_history(2)
    c.sync(h)
    receipt = c.compress("m0001", "m0003", "x", "short")
    assert "b001" in receipt and "m0002-m0003" in receipt
    assert "protected" in c.compress("m0001", "m0001", "x", "y")


def test_summary_must_be_smaller():
    c = ContextCompactor(CompactionConfig(window=50_000))
    h = make_history(1)
    c.sync(h)
    assert "not smaller" in c.compress("m0002", "m0003", "x", FILLER * 3)


def test_hierarchical_fold_and_recall_lineage():
    c = ContextCompactor(CompactionConfig(window=50_000))
    h = make_history(6)
    c.sync(h)
    c.compress("m0002", "m0005", "a", "s0=8000 s1=8001")
    c.compress("m0006", "m0009", "b", "s2=8002 s3=8003")
    receipt = c.compress("m0002", "m0009", "a+b", "ports 8000-8003")
    assert "b003 (tier 2)" in receipt
    b3 = c.blocks["b003"]
    assert b3.children == ["b001", "b002"] and not c.blocks["b001"].active
    recalled = c.recall("b003")
    assert "PORT_0=8000" in recalled and "PORT_3=8003" in recalled
    assert "PORT_2=8002" in c.search("PORT_2")


def test_nudge_is_growth_gated():
    c = ContextCompactor(CompactionConfig(window=3_000, nudge_ratio=0.3, growth_ratio=0.2))
    h = make_history(3)
    v = c.prepare(h, "sys")
    assert v.nudged and "## Context alert" in v.system  # nudges go in the system prompt
    assert "m0002-m0007" in v.system  # the newest step is offered: the model sees it in this request
    assert "[m0007]" in v.messages[-1].content and "Context alert" not in v.messages[-1].content
    c.compress("m0002", "m0005", "x", "ports 8000 8001")
    v2 = c.prepare(h, "sys")
    assert not v2.nudged  # just folded: no new growth yet


def test_message_placement_and_forced_fold_once_per_growth_period():
    c = ContextCompactor(CompactionConfig(window=3_000, nudge_ratio=0.3, growth_ratio=0.2,
                                          force_ratio=0.4, nudge_placement="message"))
    h = make_history(3)
    v = c.prepare(h, "sys")
    assert v.nudged and v.force_fold and "must call compress_context" in v.messages[-1].content
    assert not c.prepare(h, "sys").force_fold  # only once until a fold happens
    c.compress("m0002", "m0005", "x", "ports 8000 8001")
    assert not c.prepare(h, "sys").nudged  # first call after a fold sets the new baseline
    h += make_history(6)[7:]  # then more reads arrive
    assert c.prepare(h, "sys").force_fold  # a new growth period can force again


def test_emergency_autofold_keeps_request_under_window():
    c = ContextCompactor(CompactionConfig(window=2_000))
    h = make_history(8)  # ~3.4k tokens of tool output
    v = c.prepare(h, "sys")
    assert v.auto_folds and v.estimate <= 2_000
    assert c.blocks[v.auto_folds[0]].auto
    assert_pairs_valid(v.messages)
    assert "PORT_0=8000" in c.search("PORT_0")  # still recoverable


def test_strict_overflow_raises_when_nothing_can_be_folded():
    c = ContextCompactor(CompactionConfig(window=300))
    with pytest.raises(ContextOverflowError):
        c.prepare(make_history(1), "sys")


# ----------------------------------------------------------------- end-to-end
class ScriptedModel(FakeMessagesListChatModel):
    """Replays fixed responses; records what it was sent."""

    seen: list = []

    def bind_tools(self, tools: Any, **kwargs: Any):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop, run_manager, **kwargs)


@tool
def read_file(path: str) -> str:
    """Read a file."""
    return f"CONTENT of {path}: secret={path.upper()}\n{FILLER}"


def call(name, args, cid):
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": cid}])


def test_agent_loop_with_middleware_folds_and_finishes():
    model = ScriptedModel(
        responses=[
            call("read_file", {"path": "a.py"}, "1"),
            call("read_file", {"path": "b.py"}, "2"),
            AIMessage(
                "",
                tool_calls=[
                    {"name": "compress_context", "args": {"start_ref": "m0002", "end_ref": "m0005", "topic": "a,b", "summary": "a secret=A.PY; b secret=B.PY"}, "id": "3"},
                    {"name": "read_file", "args": {"path": "c.py"}, "id": "4"},
                ],
            ),
            call("search_context", {"query": "A.PY"}, "5"),
            AIMessage('Done. ```json\n{"secrets": ["A.PY", "B.PY", "C.PY"]}\n```'),
        ]
    )
    model.seen = []
    mw = ContextCompactionMiddleware(window=4_000)
    agent = create_agent(model, [read_file], system_prompt="You audit files.", middleware=[mw])
    out = agent.invoke({"messages": [HumanMessage("find the secrets")]})

    assert "C.PY" in out["messages"][-1].content
    assert len(out["messages"]) == 11  # full, uncompacted history stays in agent state
    after_fold = model.seen[3]  # request after the fold
    notes = [m for m in after_fold if isinstance(m, AIMessage) and str(m.content).startswith("[Notes on")]
    assert len(notes) == 1 and "a secret=A.PY" in notes[0].content and "fold b001" in notes[0].content
    assert not any("CONTENT of a.py" in str(m.content) for m in after_fold[1:])
    assert_pairs_valid(after_fold[1:])
    search_result = out["messages"][-2]
    assert isinstance(search_result, ToolMessage) and "A.PY" in search_result.content
    assert mw.compactor.stats()["folds_model"] == 1


def test_budget_guard_raises_for_baseline():
    model = ScriptedModel(responses=[call("read_file", {"path": "a.py"}, "1"), AIMessage("done")])
    agent = create_agent(model, [read_file], middleware=[TokenBudgetGuard(window=200)])
    with pytest.raises(ContextOverflowError):
        agent.invoke({"messages": [HumanMessage("go")]})
