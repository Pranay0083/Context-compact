"""HTTP API for the web UI: live agent sessions (streamed), experiment results, golden dataset.

    .venv/bin/uvicorn server.app:app --port 8000                 # real Groq models
    CC_FAKE_LLM=1 .venv/bin/uvicorn server.app:app --port 8000   # offline scripted demo model

See server/API.md for the contract.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import BaseModel

from context_compact.compactor import ref
from context_compact.tokens import content_text, count_text, message_text
from experiment.agents import DEFAULT_GROQ_MODEL, FREE_GROQ_MODELS, FREE_TIER_TPM, AgentBundle, build_agent, make_llm
from experiment.golden import load as load_golden
from experiment.run import classify, final_text, scrub
from experiment.workloads import BUILDERS, SIZES, build_task, extract_json, sandbox_tools

load_dotenv(find_dotenv(usecwd=True))
ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "results"
FAKE = bool(os.getenv("CC_FAKE_LLM"))
PROVIDER_LIMIT = 8_000  # Groq free tier: tokens/minute, which also caps one request
MAX_SESSIONS = 20

app = FastAPI(title="Context-compact API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DEMO_QUERIES = {
    "service_audit": [
        {"label": "Who depends on auth?", "question": "Which services depend on auth, and who owns each of them?", "kind": None},
        {"label": "One line per service", "question": "Give me one line per service: owner, region and timeout_ms.", "kind": None},
        {"label": "Busiest region", "question": "Which region has the most services, and what is their total max_connections?", "kind": None},
        {"label": "Zero retries", "question": "Are any services configured with 0 retries? List them with their owners.", "kind": None},
    ],
    "incident_trace": [
        {"label": "Incident timeline", "question": "Summarise the incident as a timeline with exact timestamps, one line per hop.", "kind": None},
        {"label": "Services involved", "question": "Which services were involved in the failed checkout, in the order the request reached them?", "kind": None},
        {"label": "Other errors", "question": "Was anything else failing in the same time window? List the other errors you saw.", "kind": None},
        {"label": "Prevention", "question": "Based on the logs, what would you change to stop this incident from recurring?", "kind": None},
    ],
}
TURN_LABELS = {
    "service_audit": ["Main audit", "Recall: a port and its deps", "Follow-up: busiest team", "Follow-up: region filter"],
    "incident_trace": ["Root-cause trace", "Recall: gateway timestamp", "Follow-up: the other failure", "Follow-up: lock holder"],
}


class SettingsIn(BaseModel):
    model: str = "demo/offline-scripted" if FAKE else DEFAULT_GROQ_MODEL
    arm: Literal["compact", "baseline"] = "compact"
    window: int = 6000
    task: Literal["service_audit", "incident_trace"] = "service_audit"
    size: Literal["small", "medium", "large"] = "medium"
    seed: int = 7
    fallback: bool = True


class AskIn(BaseModel):
    question: str
    kind: Literal["task", "probe", "followup"] | None = None


@dataclass
class Session:
    id: str
    settings: dict
    task: Any
    bundle: AgentBundle | None = None
    config: dict = field(default_factory=dict)
    grades: list = field(default_factory=list)
    busy: bool = False
    error: str | None = None
    seen_folds: set = field(default_factory=set)
    q: queue.Queue | None = None

    def emit(self, event: dict) -> None:
        if self.q is not None:
            self.q.put(event)


SESSIONS: "OrderedDict[str, Session]" = OrderedDict()


# ------------------------------------------------------------------ helpers
def _history(s: Session) -> list:
    snap = s.bundle.agent.get_state(s.config)
    return list(snap.values.get("messages", [])) if snap and snap.values else []


def _fold_dict(b) -> dict:
    return {"id": b.id, "tier": b.tier, "span": b.span, "topic": b.topic, "summary": b.summary, "auto": b.auto,
            "active": b.active, "children": b.children, "replaced_tokens": b.replaced_tokens,
            "summary_tokens": b.summary_tokens, "original_tokens": b.original_tokens,
            "created_at_call": b.created_at_call}


def _role(m) -> str:
    return {"human": "user", "ai": "assistant", "tool": "tool"}.get(m.type, m.type)


def _calls(m) -> list[dict]:
    return [{"name": tc["name"], "args": tc.get("args", {})} for tc in (getattr(m, "tool_calls", None) or [])]


def state(s: Session) -> dict:
    msgs = _history(s)
    comp = s.bundle.compaction
    cov = {i: b.id for i, b in comp.compactor._covered().items()} if comp else {}
    names = {tc["id"]: tc["name"] for m in msgs if isinstance(m, AIMessage) for tc in m.tool_calls}
    transcript = []
    for i, m in enumerate(msgs):
        text = content_text(m.content)
        transcript.append({
            "ref": ref(i), "role": _role(m),
            "text": text[:4000] + ("\n…" if len(text) > 4000 else "") if isinstance(m, ToolMessage) else text,
            "tool_calls": _calls(m) if isinstance(m, AIMessage) else [],
            "tool_name": names.get(getattr(m, "tool_call_id", None), getattr(m, "name", None)) if isinstance(m, ToolMessage) else None,
            "tokens": count_text(message_text(m)), "folded_into": cov.get(i),
        })
    view = comp.last_view if comp else None
    last_view = None
    if view:
        last_view = {"system": view.system, "estimate": view.estimate, "nudged": view.nudged,
                     "force_fold": view.force_fold,
                     "messages": [{"role": _role(m), "text": content_text(m.content)[:2000], "tool_calls": _calls(m)}
                                  for m in view.messages]}
    task = s.task
    return {
        "id": s.id, "settings": s.settings, "fake_llm": FAKE,
        "sandbox": {
            "task": task.name, "files": len(task.files), "corpus_tokens": task.corpus_tokens,
            "prompt": task.prompt, "probe": task.probe,
            "file_list": [{"path": p, "tokens": count_text(c)} for p, c in sorted(task.files.items())],
            "turns": [{"kind": t.kind, "question": t.question, "label": lbl}
                      for t, lbl in zip(task.turns, TURN_LABELS[task.name])],
        },
        "transcript": transcript,
        "calls": s.bundle.tracker.calls,
        "folds": [_fold_dict(b) for b in sorted(comp.compactor.blocks.values(), key=lambda b: (b.start, -b.tier))] if comp else [],
        "stats": comp.compactor.stats() if comp else None,
        "window": s.settings["window"] if comp else None,
        "limit": FREE_TIER_TPM.get(s.settings["model"], PROVIDER_LIMIT),
        "last_view": last_view,
        "grades": s.grades,
        "expected": {"task": task.expected, "probe": task.probe_expected},
        "busy": s.busy, "error": s.error,
    }


def _new_session(settings: SettingsIn) -> Session:
    cfg = settings.model_dump()
    task = build_task(cfg["task"], cfg["seed"], cfg["size"])
    s = Session(id=uuid.uuid4().hex[:12], settings=cfg, task=task)
    llm = make_llm("fake" if FAKE else "groq", cfg["model"], task=task)
    fallbacks = [] if FAKE or not cfg["fallback"] else [make_llm("groq", m) for m in FREE_GROQ_MODELS if m != cfg["model"]]
    s.bundle = build_agent(
        cfg["arm"], llm, sandbox_tools(task.files), window=cfg["window"], fallbacks=fallbacks,
        tpm_limit=None if FAKE else {**FREE_TIER_TPM, "*": PROVIDER_LIMIT}, on_event=s.emit,
        on_call=lambda call: s.emit({"type": "model_call", "call": call}),
    )
    s.config = {"configurable": {"thread_id": s.id}, "recursion_limit": 120}
    SESSIONS[s.id] = s
    while len(SESSIONS) > MAX_SESSIONS:
        SESSIONS.popitem(last=False)
    return s


def _get(sid: str) -> Session:
    if sid not in SESSIONS:
        raise HTTPException(404, "unknown session (the server may have restarted)")
    return SESSIONS[sid]


def _emit_new_folds(s: Session) -> None:
    comp = s.bundle.compaction
    if not comp:
        return
    for b in comp.compactor.blocks.values():
        if b.id not in s.seen_folds:
            s.seen_folds.add(b.id)
            s.emit({"type": "fold", "fold": _fold_dict(b)})


def _turn_for(task, question: str, kind: str | None):
    exact = next((t for t in task.turns if t.question.strip() == question.strip()), None)
    if exact or not kind:
        return exact
    return next((t for t in task.turns if t.kind == kind), None)


def _run(s: Session, body: AskIn) -> None:
    try:
        s.emit({"type": "status", "message": "Agent started"})
        # History is append-only and node updates arrive in order, so positions can be counted:
        # the question lands at len(history), each streamed message after it.
        position = len(_history(s)) + 1
        for update in s.bundle.agent.stream({"messages": [HumanMessage(body.question)]}, s.config, stream_mode="updates"):
            for payload in (update or {}).values():
                for m in payload.get("messages", []) if isinstance(payload, dict) else []:
                    if isinstance(m, ToolMessage):
                        text = content_text(m.content)
                        s.emit({"type": "tool_result", "ref": ref(position), "name": m.name or "tool",
                                "tokens": count_text(text), "preview": " ".join(text[:240].split())})
                    position += 1
            _emit_new_folds(s)
        final = final_text(_history(s)) or ""
        turn = _turn_for(s.task, body.question, body.kind)
        grade = None
        if turn:
            score, detail = turn.grade(extract_json(final), turn.expected)
            grade = {"score": score, "detail": detail}
            s.grades.append({"kind": turn.kind, "score": score, "detail": detail})
        s.emit({"type": "final", "text": final, "grade": grade})
    except Exception as e:  # surface the failure mode (request_too_large, daily_quota, ...)
        status = classify(e)
        s.error = scrub(f"{type(e).__name__}: {str(e)[:400]}")
        s.emit({"type": "error", "status": status, "message": s.error})
    finally:
        _emit_new_folds(s)
        s.busy = False
        s.emit({"type": "state", "state": state(s)})
        s.emit({"type": "done"})
        s.emit(None)


# --------------------------------------------------------------- endpoints
@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "has_key": bool(os.getenv("GROQ_API_KEY")), "fake_llm": FAKE}


@app.get("/api/options")
def options() -> dict:
    return {
        "models": ["demo/offline-scripted"] if FAKE else FREE_GROQ_MODELS,
        "arms": ["compact", "baseline"],
        "tasks": list(BUILDERS),
        "sizes": list(SIZES),
        "defaults": SettingsIn().model_dump(),
        "demo_queries": DEMO_QUERIES,
    }


@app.post("/api/sessions")
def create_session(settings: SettingsIn) -> dict:
    return state(_new_session(settings))


@app.get("/api/sessions/{sid}")
def get_session(sid: str) -> dict:
    return state(_get(sid))


@app.post("/api/sessions/{sid}/ask")
def ask(sid: str, body: AskIn) -> StreamingResponse:
    s = _get(sid)
    if s.busy:
        raise HTTPException(409, "this session is already answering a question")
    s.busy, s.error, s.q = True, None, queue.Queue()
    q = s.q
    threading.Thread(target=_run, args=(s, body), daemon=True).start()

    def events():
        while (event := q.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _run_dirs() -> list[Path]:
    return sorted((p.parent for p in RESULTS.glob("*/runs.json")), key=lambda p: p.name, reverse=True)


def _load_run(d: Path) -> dict:
    return json.loads(re.sub(r"\borg_[A-Za-z0-9]+", "org_<redacted>", (d / "runs.json").read_text()))


def _summary(r: dict) -> dict:
    c = r.get("compaction") or {}
    return {k: r.get(k) for k in ("session", "task", "arm", "status", "score", "probe_score", "mean_score", "model_calls",
                                  "input_tokens_total", "input_tokens_peak", "output_tokens_total")} | {
        "folds_model": c.get("folds_model", 0), "folds_auto": c.get("folds_auto", 0),
        "turn_scores": [t["score"] for t in r.get("turns") or []]}


@app.get("/api/results")
def results() -> list[dict]:
    out = []
    for d in _run_dirs():
        data = _load_run(d)
        out.append({"id": d.name, "meta": data["meta"], "summary": [_summary(r) for r in data["runs"]]})
    return out


@app.get("/api/results/{run_id}")
def result(run_id: str) -> dict:
    d = RESULTS / run_id
    if "/" in run_id or ".." in run_id or not (d / "runs.json").exists():
        raise HTTPException(404, "unknown run")
    return _load_run(d)


@app.get("/api/golden")
def golden() -> dict:
    evals = []
    for d in _run_dirs():
        data = _load_run(d)
        if data["meta"].get("golden"):
            evals.append({"id": d.name, "meta": data["meta"], "summary": [_summary(r) for r in data["runs"]]})
    return {"dataset": load_golden(), "evals": evals}
