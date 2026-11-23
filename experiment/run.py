"""Run the experiment: plain agent vs the same agent with the context compactor.

    python -m experiment.run                          # both tasks, both arms, medium, task + probe
    python -m experiment.run --size small --window 4000 --all-turns
    python -m experiment.run --golden                 # the golden dataset (golden/v1.json)
    python -m experiment.run --golden --cases audit-s11-small --arms compact
    python -m experiment.run --resume results/<run>   # continue after a quota stop
    python -m experiment.run --fake                   # offline dry run with the scripted model
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from context_compact import ContextOverflowError
from context_compact.tokens import content_text
from experiment.agents import FREE_GROQ_MODELS, FREE_TIER_TPM, build_agent, make_llm, model_id
from experiment.limits import DailyQuotaExceeded
from experiment.report import write_report
from experiment.workloads import BUILDERS, SIZES, build_task, extract_json, sandbox_tools

RERUN_STATUSES = {"daily_quota", "rate_limited", "error"}


def final_text(messages) -> str | None:
    for m in reversed(messages):
        if isinstance(m, AIMessage) and not m.tool_calls:
            return content_text(m.content)
    return None


def scrub(text: str) -> str:
    """Remove account identifiers (e.g. Groq org ids) from provider error messages."""
    text = re.sub(r"\borg_[A-Za-z0-9]+", "org_<redacted>", text)
    return re.sub(r"\b(gsk|xai)-?_?[A-Za-z0-9]{16,}", "<redacted-key>", text)


def classify(exc: BaseException) -> str:
    if isinstance(exc, ContextOverflowError):
        return "context_overflow"
    if isinstance(exc, DailyQuotaExceeded):
        return "daily_quota"
    if isinstance(exc, GraphRecursionError):
        return "step_limit"
    status = getattr(exc, "status_code", None)
    msg = str(exc).lower()
    if status == 413 or "request too large" in msg:
        return "request_too_large"
    if "per day" in msg or "(tpd)" in msg:
        return "daily_quota"
    if status == 429:
        return "rate_limited"
    return "error"


def run_one(session_id: str, task, turns, arm: str, llm, args, out_dir: Path, fallbacks=None) -> dict:
    archive = str(out_dir / f"archive-{session_id}-{arm}.sqlite") if arm == "compact" else None
    b = build_agent(
        arm, llm, sandbox_tools(task.files), window=args.window,
        baseline_window=args.baseline_window or None, archive_path=archive, verbose=args.verbose,
        fallbacks=fallbacks, max_daily_wait=args.max_daily_wait,
        tpm_limit=None if args.fake else ({**FREE_TIER_TPM, "*": args.tpm} if args.tpm else None),
    )
    cfg = {"configurable": {"thread_id": f"{session_id}-{arm}"}, "recursion_limit": args.max_steps * 2 + 5}
    rec: dict = {"session": session_id, "task": task.name, "arm": arm, "status": "ok", "error": None, "failed_in": None}
    t0 = time.time()
    turn_recs: list[dict] = []
    for k, turn in enumerate(turns):
        start = len(b.tracker.calls)
        try:
            out = b.agent.invoke({"messages": [HumanMessage(turn.question)]}, cfg)
            answer = final_text(out["messages"])
            score, detail = turn.grade(extract_json(answer), turn.expected)
            turn_recs.append({"kind": turn.kind, "question": turn.question, "answer": answer,
                              "score": score, "detail": detail, "calls": len(b.tracker.calls) - start})
        except Exception as e:  # the failure mode is a result, not a crash
            rec.update(status=classify(e), error=scrub(f"{type(e).__name__}: {str(e)[:400]}"),
                       failed_in=f"turn {k + 1} ({turn.kind})")
            turn_recs.append({"kind": turn.kind, "question": turn.question, "answer": None, "score": 0.0,
                              "detail": {}, "calls": len(b.tracker.calls) - start, "error": rec["status"]})
            turn_recs += [{"kind": t.kind, "question": t.question, "answer": None, "score": 0.0, "detail": {},
                           "calls": 0, "skipped": True} for t in turns[k + 1:]]
            break
    rec["wall_s"] = round(time.time() - t0, 1)
    rec["turns"] = turn_recs
    first = turn_recs[0]
    probe = next((t for t in turn_recs if t["kind"] == "probe"), None)
    rec.update(answer=first["answer"], score=first["score"], score_detail=first["detail"],
               probe_answer=probe and probe["answer"], probe_score=probe["score"] if probe else 0.0,
               probe_detail=probe["detail"] if probe else {},
               mean_score=round(sum(t["score"] for t in turn_recs) / len(turn_recs), 3))

    calls = b.tracker.calls
    inputs = [c["input_tokens"] or 0 for c in calls]
    tcs = [tc for c in calls for tc in c["tool_calls"]]
    reads = [str(tc["args"].get("path", "")).strip().removeprefix("./") for tc in tcs if tc["name"] == "read_file"]
    n = lambda name: sum(tc["name"] == name for tc in tcs)  # noqa: E731
    rec.update(
        model_calls=len(calls), per_call_input=inputs,
        input_tokens_total=sum(inputs), input_tokens_peak=max(inputs, default=0),
        output_tokens_total=sum(c["output_tokens"] or 0 for c in calls),
        read_file_calls=len(reads), re_reads=len(reads) - len(set(reads)),
        compress_calls=n("compress_context"), recall_calls=n("recall_context"), search_calls=n("search_context"),
        nudged_calls=sum(c["nudged"] for c in calls), forced_calls=sum(c["forced"] for c in calls),
        models_used=sorted({c["model"] for c in calls}), rate_limit_wait_s=round(b.limiter.waited_s, 1),
        transient_errors=len(b.tracker.errors), calls=calls,
    )
    if b.compaction:
        cc = b.compaction.compactor
        rec["compaction"] = cc.stats()
        rec["folds"] = [
            {"id": x.id, "tier": x.tier, "span": x.span, "topic": x.topic, "auto": x.auto, "active": x.active,
             "children": x.children, "replaced_tokens": x.replaced_tokens, "summary_tokens": x.summary_tokens,
             "original_tokens": x.original_tokens, "created_at_call": x.created_at_call, "summary": x.summary}
            for x in cc.blocks.values()
        ]
    return rec


def sessions_from_args(args) -> tuple[list[tuple[str, object, list]], dict]:
    if args.golden:
        from experiment.golden import load_cases

        dataset, cases = load_cases(args.golden, args.cases)
        return [(c["id"], task, task.turns) for c, task in cases], {"golden": dataset["version"]}
    out = []
    for name in [t.strip() for t in args.tasks.split(",") if t.strip()]:
        task = build_task(name, args.seed, args.size)
        out.append((name, task, task.turns if args.all_turns else task.turns[:2]))
    return out, {}


def main(argv: list[str] | None = None) -> None:
    load_dotenv(find_dotenv(usecwd=True))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", default="groq", choices=["groq", "xai"])
    ap.add_argument("--model", help="model id (default: GROQ_MODEL env or the built-in default)")
    ap.add_argument("--fake", action="store_true", help="offline dry run with the scripted demo model")
    ap.add_argument("--tasks", default=",".join(BUILDERS))
    ap.add_argument("--arms", default="baseline,compact")
    ap.add_argument("--size", default="medium", choices=list(SIZES))
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--all-turns", action="store_true", help="also ask the follow-up questions")
    ap.add_argument("--golden", nargs="?", const="golden/v1.json", help="run the golden dataset (default golden/v1.json)")
    ap.add_argument("--cases", help="comma-separated golden case ids (default: all)")
    ap.add_argument("--window", type=int, default=6000, help="token budget per request for the compact arm")
    ap.add_argument("--baseline-window", type=int, default=0, help="also cap the baseline (0 = provider limit only)")
    ap.add_argument("--max-steps", type=int, default=40)
    ap.add_argument("--no-probe", action="store_true", help="only ask the main question")
    ap.add_argument("--tpm", type=int, default=8000, help="client-side tokens/minute pacing per model (0 = off)")
    ap.add_argument("--max-daily-wait", type=float, default=900, help="max seconds to wait out a daily-quota 429")
    ap.add_argument("--fallback-models", default="", help=f"comma-separated, e.g. {','.join(FREE_GROQ_MODELS[1:])}")
    ap.add_argument("--resume", help="results directory of an interrupted run to continue")
    ap.add_argument("--pause", type=float, default=10.0, help="seconds between runs")
    ap.add_argument("--out", default="results")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every model call")
    args = ap.parse_args(argv)

    prior: dict[tuple[str, str], dict] = {}
    if args.resume:
        out_dir = Path(args.resume)
        old = json.loads((out_dir / "runs.json").read_text())
        m = old["meta"]
        args.model, args.window, args.size, args.seed = m["model"], m["window"], m["size"], m["seed"]
        args.baseline_window = m.get("baseline_window") or 0
        args.golden, args.cases = (m.get("golden_path"), m.get("cases")) if m.get("golden") else (None, None)
        if not m.get("golden"):
            args.tasks = ",".join(m.get("sessions") or m["tasks"])
        args.all_turns = m.get("all_turns", False)
        args.fake = m.get("fake", False)
        prior = {(r.get("session", r["task"]), r["arm"]): r for r in old["runs"] if r["status"] not in RERUN_STATUSES}
        print(f"Resuming {out_dir}: {len(prior)} finished run(s) kept")

    key = {"groq": "GROQ_API_KEY", "xai": "XAI_API_KEY"}[args.provider]
    if not args.fake and not os.getenv(key):
        sys.exit(f"{key} is not set. Copy .env.example to .env and add your key (or use --fake).")

    sessions, extra_meta = sessions_from_args(args)
    if args.no_probe:
        sessions = [(sid, t, turns[:1]) for sid, t, turns in sessions]
    name = "demo/offline-scripted" if args.fake else model_id(make_llm(args.provider, args.model))
    if not args.resume:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        tag = f"golden-{extra_meta['golden']}" if args.golden else args.size
        out_dir = Path(args.out) / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}-{tag}"
        out_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "model": name, "provider": "fake" if args.fake else args.provider, "fake": args.fake,
        "size": args.size, "seed": args.seed, "window": args.window,
        "baseline_window": args.baseline_window or None, "no_probe": args.no_probe, "all_turns": args.all_turns,
        "golden_path": args.golden, "cases": args.cases, **extra_meta,
        "date": f"{datetime.now():%Y-%m-%d %H:%M}",
        "versions": {p: version(p) for p in ("langchain", "langchain-core", "langchain-groq", "langgraph")},
        "sessions": [sid for sid, _, _ in sessions],
        "tasks": {sid: {"family": t.name, "files": len(t.files), "corpus_tokens": t.corpus_tokens,
                        "turns": len(turns)} for sid, t, turns in sessions},
    }
    fallback_names = [m.strip() for m in args.fallback_models.split(",") if m.strip()]
    runs: list[dict] = []
    stop = False
    for sid, task, turns in sessions:
        print(f"\n== {sid}: {len(task.files)} files, ~{task.corpus_tokens:,} tokens, {len(turns)} turn(s)", flush=True)
        for arm in [a.strip() for a in args.arms.split(",") if a.strip()]:
            if (sid, arm) in prior:
                runs.append(prior[(sid, arm)])
                print(f"   {arm:<9}  (kept from previous run)")
                continue
            if stop:
                continue
            if runs and args.pause and not args.fake:
                time.sleep(args.pause)
            llm = make_llm("fake" if args.fake else args.provider, args.model, task=task)
            fallbacks = [make_llm(args.provider, f) for f in fallback_names] if not args.fake else []
            print(f"   {arm:<9}…", end=" " if not args.verbose else "\n", flush=True)
            rec = run_one(sid, task, turns, arm, llm, args, out_dir, fallbacks)
            runs.append(rec)
            turn_scores = "/".join(f"{t['score']:.2f}" for t in rec["turns"])
            print(
                f"{'   ' if args.verbose else ''}{rec['status']:<17} turns={turn_scores} calls={rec['model_calls']:>2} "
                f"input={rec['input_tokens_total']:>7,} peak={rec['input_tokens_peak']:>6,} "
                f"waited={rec['rate_limit_wait_s']:.0f}s ({rec['wall_s']:.0f}s)",
                flush=True,
            )
            (out_dir / "runs.json").write_text(json.dumps({"meta": meta, "runs": runs}, indent=2, default=str))
            if rec["status"] == "daily_quota":
                print(f"   Daily quota exhausted. Continue later with: python -m experiment.run --resume {out_dir}")
                stop = True
    (out_dir / "runs.json").write_text(json.dumps({"meta": meta, "runs": runs}, indent=2, default=str))
    report = write_report(out_dir, meta, runs)
    print(f"\nReport: {report}")


if __name__ == "__main__":
    main()
