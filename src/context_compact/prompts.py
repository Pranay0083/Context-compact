"""Model-facing text: the context-management rules, fold notes, the nudge."""

from __future__ import annotations

DOCTRINE = """\
## Context management
Your context window holds about {window:,} tokens, and you manage it yourself.
- User and tool messages carry ids such as [m0007].
- Once a range of messages is no longer needed word for word, fold it with \
compress_context(start_ref, end_ref, topic, summary). The summary replaces those \
messages; afterwards it is all you will see of them.
- Fold between steps, after you have taken what you need from the tool results. You may \
fold and call your next tool in the same turn.
- Keep steps small: you can only fold tool results after you have seen them, so avoid \
requesting more than about {step_budget:,} tokens of new tool output in one step.
- In a summary KEEP every exact value you may still need (numbers, names, ids, paths, \
timestamps, error strings), plus findings, decisions with reasons, and what is left to do. \
DROP boilerplate, noise and repetition.
- Folded ranges are replaced, in place, by your own notes: assistant messages that start \
with "[Notes on m…". They are your memory of finished work. Trust them and do not redo \
steps they cover.
- Nothing is lost: originals are archived. If you need an exact detail from folded \
history, use search_context(query) or recall_context("b003" or "m0007") rather than \
re-reading files.
- Never mention this mechanism in your final answer."""


def render_note(block) -> str:
    """A fold as it appears in the view: the model's own notes, in place of the range."""
    return (
        f"[Notes on {block.span} · fold {block.id}, tier {block.tier} · {block.topic}]\n"
        f"{block.summary.strip()}"
    )


def render_nudge(
    *, used: int, window: int, candidates: list[str], merge_hint: str | None,
    placement: str = "system", forced: bool = False,
) -> str:
    pct = round(100 * used / window)
    head = (
        ["## Context alert (automatic)"]
        if placement == "system"
        else ["", "[context manager: automatic notice, not from the user]"]
    )
    action = (
        "You must call compress_context in this turn."
        if forced
        else "Fold finished work now with compress_context, before or alongside your next tool call."
    )
    lines = [*head, f"Your context is at {pct}% of its {window:,}-token budget (~{used:,} tokens). {action}"]
    if candidates:
        lines.append("Foldable ranges: " + "; ".join(candidates))
    if merge_hint:
        lines.append(merge_hint)
    lines.append("Put every exact value you will still need into the summary, then continue the task.")
    if placement != "system":
        lines.append("Do not reply to this notice.")
    return "\n".join(lines)
