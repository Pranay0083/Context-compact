"""Markdown report + dependency-free SVG charts for an experiment run."""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

COLORS = {"baseline": "#6b7280", "compact": "#2563eb"}


def svg_chart(series: dict[str, list[int]], window: int | None, title: str) -> str:
    W, H, L, R, T, B = 680, 300, 64, 20, 40, 46
    n = max((len(v) for v in series.values()), default=1)
    top = max([max(v, default=0) for v in series.values()] + [window or 0]) * 1.12 or 1
    x = lambda i: L + (W - L - R) * (i / max(1, n - 1))  # noqa: E731
    y = lambda v: H - B - (H - T - B) * v / top  # noqa: E731
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
        'font-family="ui-sans-serif,system-ui,sans-serif" font-size="12">',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
        f'<text x="{L}" y="22" font-size="14" font-weight="600" fill="#111827">{escape(title)}</text>',
    ]
    for k in range(5):
        v = top / 1.12 * k / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#e5e7eb"/>')
        out.append(f'<text x="{L - 8}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#6b7280">{v / 1000:.1f}k</text>')
    if window:
        out.append(
            f'<line x1="{L}" x2="{W - R}" y1="{y(window):.1f}" y2="{y(window):.1f}" stroke="#dc2626" '
            'stroke-dasharray="6 4"/>'
        )
        out.append(f'<text x="{W - R}" y="{y(window) - 6:.1f}" text-anchor="end" fill="#dc2626">compact window {window:,}</text>')
    for name, vals in series.items():
        if not vals:
            continue
        color = COLORS.get(name, "#111827")
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
        out += [f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="2.5" fill="{color}"/>' for i, v in enumerate(vals)]
    lx = L + 8
    for name in series:
        out.append(f'<rect x="{lx}" y="{T - 4}" width="12" height="3" fill="{COLORS.get(name, "#111827")}"/>')
        out.append(f'<text x="{lx + 16}" y="{T}" fill="#374151">{escape(name)}</text>')
        lx += 90
    out.append(f'<text x="{(L + W - R) / 2}" y="{H - 10}" text-anchor="middle" fill="#6b7280">model call #</text>')
    out.append(
        f'<text x="16" y="{(T + H - B) / 2}" text-anchor="middle" fill="#6b7280" '
        f'transform="rotate(-90 16 {(T + H - B) / 2})">input tokens per request</text>'
    )
    out.append("</svg>")
    return "\n".join(out)


def _pct(a: float, b: float) -> str:
    return f"{100 * (1 - a / b):.0f}%" if b else "n/a"


def write_report(out_dir: Path, meta: dict, runs: list[dict]) -> Path:
    bw = meta.get("baseline_window") or "unbounded (provider limit only)"
    title = f"golden {meta['golden']}" if meta.get("golden") else f"size={meta['size']} · seed={meta['seed']}"
    lines = [
        f"# Results: `{meta['model']}` · {title}",
        "",
        f"- Date: {meta['date']}  ",
        f"- Compact window: {meta['window']:,} tokens · baseline window: {bw}  ",
        f"- Models that served calls: {', '.join(sorted({m for r in runs for m in r.get('models_used', [])})) or meta['model']}",
        "",
        "| Session | Arm | Status | Turn scores | Mean | Calls | Input tokens | Peak request | Output | Folds (model/auto) | Re-reads | Recall/Search | Time |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        c = r.get("compaction") or {}
        folds = f"{c.get('folds_model', 0)}/{c.get('folds_auto', 0)}" if c else "–"
        rs = f"{r['recall_calls']}/{r['search_calls']}" if c else "–"
        turns = r.get("turns") or [{"score": r["score"]}, {"score": r["probe_score"]}]
        scores = " / ".join(f"{t['score']:.2f}" for t in turns)
        mean = r.get("mean_score", sum(t["score"] for t in turns) / len(turns))
        lines.append(
            f"| {r.get('session', r['task'])} | {r['arm']} | {r['status']} | {scores} | {mean:.2f} | "
            f"{r['model_calls']} | {r['input_tokens_total']:,} | {r['input_tokens_peak']:,} | "
            f"{r['output_tokens_total']:,} | {folds} | {r['re_reads']} | {rs} | {r['wall_s']:.0f}s |"
        )
    by_task: dict[str, dict[str, dict]] = {}
    for r in runs:
        by_task.setdefault(r.get("session", r["task"]), {})[r["arm"]] = r

    for task, arms in by_task.items():
        lines += ["", f"## {task}", ""]
        info = meta["tasks"].get(task, {})
        if info:
            lines.append(f"{info['files']} files, ~{info['corpus_tokens']:,} tokens of content.\n")
        b, k = arms.get("baseline"), arms.get("compact")
        if b and k:
            lines.append(
                f"- Total input tokens: compact {k['input_tokens_total']:,} vs baseline "
                f"{b['input_tokens_total']:,} ({_pct(k['input_tokens_total'], b['input_tokens_total'])} fewer)"
            )
            lines.append(
                f"- Peak request: compact {k['input_tokens_peak']:,} vs baseline {b['input_tokens_peak']:,} "
                f"({_pct(k['input_tokens_peak'], b['input_tokens_peak'])} smaller)"
            )
            lines.append(f"- Mean turn score: compact {k.get('mean_score', k['score']):.2f} vs baseline {b.get('mean_score', b['score']):.2f}")
        chart = f"chart-{task}.svg"
        series = {arm: r["per_call_input"] for arm, r in arms.items()}
        (out_dir / chart).write_text(svg_chart(series, meta["window"], f"{task}: input tokens per model call"))
        lines += ["", f"![{task}]({chart})", ""]
        for arm, r in arms.items():
            if r.get("error"):
                lines.append(f"- **{arm} error** ({r.get('failed_in')}): `{r['error'][:300]}`")
            for i, t in enumerate(r.get("turns") or [], 1):
                state = " (skipped)" if t.get("skipped") else ""
                lines.append(f"- **{arm}** turn {i} {t['kind']}: {t['score']:.2f}{state} · {t['detail']}")
        if k and k.get("folds"):
            lines += ["", "<details><summary>Fold log (compact arm)</summary>", ""]
            for f in k["folds"]:
                kind = "auto" if f["auto"] else "model"
                state = "" if f["active"] else " (merged into a higher tier)"
                lines.append(
                    f"**{f['id']}** · tier {f['tier']} · {f['span']} · {kind} · {f['replaced_tokens']:,} → "
                    f"{f['summary_tokens']:,} tokens{state} · _{f['topic']}_"
                )
                lines += ["", "```", f["summary"][:1500], "```", ""]
            lines.append("</details>")
    path = out_dir / "report.md"
    path.write_text("\n".join(lines) + "\n")
    return path
