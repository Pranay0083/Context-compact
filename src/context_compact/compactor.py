"""Core compaction engine.

Design (inspired by the ideas in ranxianglei/billion-context, implemented from scratch):

* The agent's real history is never mutated. Each model call gets a *view* rendered
  from it: folded ranges are swapped for summaries, everything else is passed through.
* The **model decides** what to fold. It calls ``compress_context`` with a message range
  and a summary it writes itself. The engine only nudges it once the window is filling
  *and* the context has grown since the last fold (a growth gate, so it is not nagged
  every turn).
* Folds are **hierarchical**: folding a range that contains earlier folds creates a
  higher-tier block whose children are those folds. Lineage is kept.
* Folds are **recoverable**: every original message is archived; the model can search
  or recall them without unfolding.
* Folds respect **tool-call atomicity**: an assistant tool-call message and its tool
  results are one unit, so the rendered view never contains orphaned tool messages.
* The original task and the latest user message are never folded.
* If the model ignores the nudges and the request nears the window, an **emergency
  auto-fold** replaces the oldest steps with mechanical previews (still recoverable).
"""

from __future__ import annotations

import re
import threading
from dataclasses import asdict, dataclass, field
from typing import Sequence

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from .archive import Archive
from .prompts import DOCTRINE, render_note, render_nudge
from .tokens import PER_MESSAGE_OVERHEAD, TokenCounter, content_text, message_text

COMPRESS_TOOL = "compress_context"
TAG_TOKENS = 4  # cost of the "[m0007] " prefix
AUTO_LINE_TOKENS = 45  # rough cost of one line in a mechanical auto-fold summary
NOTE_OVERHEAD = 30  # header line + message overhead of a rendered fold note


def ref(i: int) -> str:
    return f"m{i + 1:04d}"


def _nums(text: str) -> list[int]:
    return [int(x) - 1 for x in re.findall(r"\d+", str(text))]


def _block_key(text: str) -> str | None:
    m = re.fullmatch(r"\s*b0*(\d+)\s*", str(text), flags=re.I)
    return f"b{int(m.group(1)):03d}" if m else None


def _clip(text: str, n: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "…"


def _with_prefix(m: BaseMessage, prefix: str) -> BaseMessage:
    c = m.content
    if isinstance(c, str):
        return m.model_copy(update={"content": prefix + c})
    return m.model_copy(update={"content": [{"type": "text", "text": prefix}, *c]})


def _with_suffix(m: BaseMessage, suffix: str) -> BaseMessage:
    c = m.content
    if isinstance(c, str):
        return m.model_copy(update={"content": c + suffix})
    return m.model_copy(update={"content": [*c, {"type": "text", "text": suffix}]})


class ContextOverflowError(RuntimeError):
    """A request would not fit in the configured window."""

    def __init__(self, tokens: int, window: int):
        super().__init__(f"request needs ~{tokens:,} tokens but the window is {window:,}")
        self.tokens = tokens
        self.window = window


@dataclass
class CompactionConfig:
    window: int = 16_000  # token budget for one model request (system + tools + messages)
    nudge_ratio: float = 0.45  # start nudging once the request is this full...
    growth_ratio: float = 0.25  # ...and has grown this much (x window) since the last fold
    emergency_ratio: float = 0.85  # above this, auto-fold without asking the model
    emergency_target_ratio: float = 0.55  # auto-fold down to this
    auto_keep_recent_units: int = 1  # auto-fold never touches the newest (not yet seen) step
    recall_max_tokens: int | None = None  # cap on one recall_context result (default: window / 4)
    max_candidates: int = 4  # ranges listed in a nudge
    min_candidate_tokens: int = 150  # don't suggest folding tiny ranges
    nudge_placement: str = "system"  # "system" (authoritative) or "message" (appended to the last message)
    force_ratio: float | None = 0.70  # above this, one call is made with tool_choice=compress_context
    strict: bool = True  # raise ContextOverflowError if a request still exceeds the window


@dataclass
class Unit:
    """Smallest foldable piece: one message, or an assistant tool call plus its results."""

    start: int
    end: int
    kind: str  # human | assistant | tools | compress | other
    tool_names: list[str] = field(default_factory=list)


@dataclass
class Block:
    id: str
    tier: int
    topic: str
    summary: str
    start: int
    end: int
    children: list[str]
    original_tokens: int  # raw tokens of every message this block ultimately covers
    replaced_tokens: int  # tokens it removed from the view when created
    summary_tokens: int
    auto: bool = False
    active: bool = True
    created_at_call: int = 0

    @property
    def span(self) -> str:
        return f"{ref(self.start)}-{ref(self.end)}"


@dataclass
class View:
    system: str
    messages: list[BaseMessage]
    estimate: int  # calibrated token estimate of the whole request
    raw_estimate: int  # uncalibrated (tiktoken) estimate, used for calibration
    nudged: bool = False
    force_fold: bool = False  # the caller should set tool_choice=compress_context
    auto_folds: list[str] = field(default_factory=list)


class ContextCompactor:
    def __init__(
        self,
        config: CompactionConfig | None = None,
        *,
        archive: Archive | None = None,
        counter: TokenCounter | None = None,
    ):
        self.config = config or CompactionConfig()
        self.archive = archive or Archive()
        self.counter = counter or TokenCounter()
        self.calls = 0
        # Tools can run in parallel threads (LangGraph's ToolNode), so serialise state changes.
        self._lock = threading.RLock()
        self._reset()

    # ------------------------------------------------------------------ state
    def _reset(self) -> None:
        self.history: list[BaseMessage] = []
        self.tokens: list[int] = []
        self.units: list[Unit] = []
        self._unit_of: list[int] = []
        self.blocks: dict[str, Block] = {}
        self._next_block = 1
        self._baseline: int | None = 0
        self._forced = False  # a forced fold was already requested in this growth period
        self.counts = {"nudges": 0, "forced": 0, "recalls": 0, "searches": 0, "rejected_folds": 0}
        self.archive.clear()

    def sync(self, history: Sequence[BaseMessage]) -> None:
        """Register messages appended since the last call (history is append-only)."""
        known = len(self.history)
        rewritten = len(history) < known or any(
            a.id and b.id and a.id != b.id for a, b in zip(self.history, history)
        )
        if rewritten:  # someone rewrote history under us; start over rather than guess
            self._reset()
            known = 0
        for i in range(known, len(history)):
            msg = history[i]
            t = self.counter.count_message(msg)
            self.history.append(msg)
            self.tokens.append(t)
            self.archive.put(i, ref(i), msg.type, message_text(msg), t)
        if len(self.history) != known or rewritten:
            self._build_units()

    def _build_units(self) -> None:
        units: list[Unit] = []
        H, i = self.history, 0
        while i < len(H):
            m = H[i]
            if isinstance(m, AIMessage) and m.tool_calls:
                j = i + 1
                while j < len(H) and isinstance(H[j], ToolMessage):
                    j += 1
                names = [tc["name"] for tc in m.tool_calls]
                kind = "compress" if set(names) == {COMPRESS_TOOL} else "tools"
                units.append(Unit(i, j - 1, kind, names))
                i = j
            else:
                kind = {"human": "human", "ai": "assistant"}.get(m.type, "other")
                units.append(Unit(i, i, kind))
                i += 1
        self.units = units
        self._unit_of = [0] * len(H)
        for k, u in enumerate(units):
            for x in range(u.start, u.end + 1):
                self._unit_of[x] = k

    def _active(self) -> list[Block]:
        return sorted((b for b in self.blocks.values() if b.active), key=lambda b: b.start)

    def _covered(self) -> dict[int, Block]:
        cov: dict[int, Block] = {}
        for b in self._active():
            for i in range(b.start, b.end + 1):
                cov[i] = b
        return cov

    def _protected(self) -> set[int]:
        humans = [u.start for u in self.units if u.kind == "human"]
        return {humans[0], humans[-1]} if humans else set()

    def _hidden(self, cov: dict[int, Block]) -> set[int]:
        """Old compress_context receipts: their content already lives in the fold notes."""
        comp = [k for k, u in enumerate(self.units) if u.kind == "compress" and u.start not in cov]
        return set(comp[:-1])

    # ---------------------------------------------------------------- render
    def _system_text(self, base_system: str) -> str:
        parts = [base_system.strip()] if base_system.strip() else []
        parts.append(DOCTRINE.format(window=self.config.window, step_budget=int(self.config.window * 0.3)))
        return "\n\n".join(parts)

    def _present(self, i: int, unit: Unit) -> BaseMessage:
        m = self.history[i]
        if isinstance(m, (HumanMessage, ToolMessage)):
            return _with_prefix(m, f"[{ref(i)}] ")
        if unit.kind == "compress" and isinstance(m, AIMessage):
            calls = [
                {**tc, "args": {**tc.get("args", {}), "summary": "(kept as your notes for this fold)"}}
                for tc in m.tool_calls
            ]
            return m.model_copy(update={"tool_calls": calls})
        return m

    def _render(self, base_system: str, tools_tokens: int) -> View:
        cov = self._covered()
        hidden = self._hidden(cov)
        msgs: list[BaseMessage] = []
        raw = tools_tokens
        for k, u in enumerate(self.units):
            if u.start in cov:
                b = cov[u.start]
                if u.start == b.start:  # the fold's notes take the place of its range
                    note = AIMessage(content=render_note(b), id=f"note-{b.id}")
                    msgs.append(note)
                    raw += b.summary_tokens + self.counter.count_text(render_note(b)) - self.counter.count_text(b.summary.strip()) + PER_MESSAGE_OVERHEAD
                continue
            if k in hidden:
                continue
            for i in range(u.start, u.end + 1):
                shown = self._present(i, u)
                msgs.append(shown)
                if u.kind == "compress" and i == u.start:
                    raw += self.counter.count_message(shown)
                else:
                    raw += self.tokens[i] + (TAG_TOKENS if shown is not self.history[i] else 0)
        system = self._system_text(base_system)
        raw += self.counter.count_text(system) + PER_MESSAGE_OVERHEAD
        return View(system, msgs, self.counter.scale(raw), raw)

    def prepare(self, history: Sequence[BaseMessage], base_system: str = "", tools_tokens: int = 0) -> View:
        """Build the request view for the next model call."""
        with self._lock:
            return self._prepare_unlocked(history, base_system, tools_tokens)

    def _prepare_unlocked(self, history: Sequence[BaseMessage], base_system: str = "", tools_tokens: int = 0) -> View:
        self.sync(history)
        self.calls += 1
        cfg = self.config
        view = self._render(base_system, tools_tokens)

        if view.estimate > cfg.emergency_ratio * cfg.window:
            made = self._auto_fold(base_system, tools_tokens)
            if made:
                view = self._render(base_system, tools_tokens)
                view.auto_folds = made

        if cfg.strict and view.estimate > cfg.window:
            raise ContextOverflowError(view.estimate, cfg.window)

        if self._baseline is None:  # first call after a fold: measure from here
            self._baseline = view.estimate
        growth = view.estimate - self._baseline
        in_system = cfg.nudge_placement == "system"
        if (
            view.estimate >= cfg.nudge_ratio * cfg.window
            and growth >= cfg.growth_ratio * cfg.window
            and view.messages
            and (in_system or isinstance(view.messages[-1], (HumanMessage, ToolMessage)))
        ):
            force = bool(cfg.force_ratio) and view.estimate >= cfg.force_ratio * cfg.window and not self._forced
            notice = self._nudge_text(view.estimate, forced=force)
            if notice:
                if in_system:
                    view.system = view.system + "\n\n" + notice
                else:
                    view.messages[-1] = _with_suffix(view.messages[-1], notice)
                view.raw_estimate += self.counter.count_text(notice)
                view.estimate = self.counter.scale(view.raw_estimate)
                view.nudged = True
                self.counts["nudges"] += 1
                if force:
                    view.force_fold = True
                    self._forced = True
                    self.counts["forced"] += 1
        return view

    def observe(self, ai: BaseMessage | None, view: View) -> None:
        """Feed the provider's real input-token count back into the estimator."""
        usage = getattr(ai, "usage_metadata", None) or {}
        self.counter.calibrate(view.raw_estimate, usage.get("input_tokens"))

    # ----------------------------------------------------------------- nudge
    def _unit_view_tokens(self, k: int) -> int:
        u = self.units[k]
        tags = sum(isinstance(self.history[i], (HumanMessage, ToolMessage)) for i in range(u.start, u.end + 1))
        return sum(self.tokens[u.start : u.end + 1]) + TAG_TOKENS * tags

    def _foldable_runs(self, keep_recent: int) -> list[list[int]]:
        """Contiguous runs of unit indices that may be folded, oldest first."""
        cov = self._covered()
        hidden = self._hidden(cov)
        protected = self._protected()
        visible = [k for k, u in enumerate(self.units) if u.start not in cov and k not in hidden]
        recent = set(visible[-keep_recent:]) if keep_recent else set()
        runs: list[list[int]] = []
        cur: list[int] = []
        for k, u in enumerate(self.units):
            if k in hidden or (u.kind == "compress" and u.start not in cov):
                continue  # receipts are transparent: they get swallowed by a surrounding fold
            ok = u.start not in cov and k not in recent and not (
                protected & set(range(u.start, u.end + 1))
            )
            if ok:
                cur.append(k)
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        return runs

    def _describe(self, run: list[int]) -> tuple[str, int]:
        tokens = sum(self._unit_view_tokens(k) for k in run)
        names: dict[str, int] = {}
        texts = 0
        for k in run:
            u = self.units[k]
            for n in u.tool_names:
                names[n] = names.get(n, 0) + 1
            texts += u.kind in ("assistant", "human")
        bits = [f"{n} x{c}" if c > 1 else n for n, c in names.items()]
        if texts:
            bits.append(f"{texts} message{'s' if texts > 1 else ''}")
        u0, u1 = self.units[run[0]], self.units[run[-1]]
        return f"{ref(u0.start)}-{ref(u1.end)} ({', '.join(bits)}; ~{tokens:,} tok)", tokens

    def _nudge_text(self, used: int, forced: bool = False) -> str | None:
        candidates = []
        # The model sees the newest results in this very request, so it may fold them now.
        for run in self._foldable_runs(keep_recent=0):
            label, tokens = self._describe(run)
            if tokens >= self.config.min_candidate_tokens:
                candidates.append(label)
        candidates = candidates[: self.config.max_candidates]
        merge_hint = None
        active = self._active()
        if not candidates and len(active) >= 3:
            a, z = active[0], active[-1]
            merge_hint = (
                f"No raw ranges are left, but folds {a.id}..{z.id} can be merged into one "
                f"higher-level summary: compress_context('{ref(a.start)}', '{ref(z.end)}', ...)."
            )
        if not candidates and not merge_hint:
            return None
        return render_nudge(
            used=used, window=self.config.window, candidates=candidates, merge_hint=merge_hint,
            placement=self.config.nudge_placement, forced=forced,
        )

    # ------------------------------------------------------------------ fold
    def _fold(self, s: int, e: int, topic: str, summary: str, auto: bool) -> Block | str:
        cov = self._covered()
        hidden = self._hidden(cov)
        children = sorted({cov[i].id for i in range(s, e + 1) if i in cov})
        child_blocks = [self.blocks[c] for c in children]
        raw_visible = raw_original = 0
        for k in range(self._unit_of[s], self._unit_of[e] + 1):
            u = self.units[k]
            if u.start in cov:
                continue
            raw_original += sum(self.tokens[u.start : u.end + 1])
            if k not in hidden:
                raw_visible += self._unit_view_tokens(k)
        replaced = raw_visible + sum(b.summary_tokens + NOTE_OVERHEAD for b in child_blocks)
        original = raw_original + sum(b.original_tokens for b in child_blocks)
        summary = (summary or "").strip()
        summary_tokens = self.counter.count_text(summary)
        if not summary:
            return "Error: summary is empty. Write the facts you still need from that range."
        if replaced == 0:
            return "Error: nothing visible in that range to fold."
        if summary_tokens + NOTE_OVERHEAD >= replaced:
            self.counts["rejected_folds"] += 1
            return (
                f"Error: the summary (~{summary_tokens:,} tokens) is not smaller than what it "
                f"replaces (~{replaced:,} tokens). Write a tighter summary or fold a larger range."
            )
        block = Block(
            id=f"b{self._next_block:03d}",
            tier=max((b.tier for b in child_blocks), default=0) + 1,
            topic=_clip((topic or "").replace('"', "'"), 80) or "untitled",
            summary=summary,
            start=s,
            end=e,
            children=children,
            original_tokens=original,
            replaced_tokens=replaced,
            summary_tokens=summary_tokens,
            auto=auto,
            created_at_call=self.calls,
        )
        self._next_block += 1
        for b in child_blocks:
            b.active = False
            self.archive.put_block(b.id, asdict(b))
        self.blocks[block.id] = block
        self.archive.put_block(block.id, asdict(block))
        self._baseline = None
        self._forced = False
        return block

    def _resolve(self, text: str, first: bool) -> int | None:
        key = _block_key(text)
        if key and key in self.blocks:
            b = self.blocks[key]
            return b.start if first else b.end
        nums = _nums(text)
        if not nums:
            return None
        return nums[0] if first else nums[-1]

    def _snap(self, s: int, e: int) -> tuple[int, int]:
        """Widen to whole units, and to whole blocks where the range overlaps one."""
        while True:
            s2 = self.units[self._unit_of[s]].start
            e2 = self.units[self._unit_of[e]].end
            for b in self._active():
                if b.start <= e2 and b.end >= s2:
                    s2, e2 = min(s2, b.start), max(e2, b.end)
            if (s2, e2) == (s, e):
                return s, e
            s, e = s2, e2

    def _trim_protected(self, s: int, e: int) -> tuple[int, int, str] | None:
        prot = sorted(p for p in self._protected() if s <= p <= e)
        while prot and prot[0] == s:  # protected messages are single-message units
            prot.pop(0)
            s += 1
            if s > e:
                return None
        note = ""
        if prot:
            note = f"(Stopped before protected message {ref(prot[0])}.) "
            e = prot[0] - 1
            if e < s:
                return None
        return s, e, note

    def compress(self, start_ref: str, end_ref: str, topic: str, summary: str) -> str:
        """Model-initiated fold (backs the compress_context tool)."""
        with self._lock:
            return self._compress_unlocked(start_ref, end_ref, topic, summary)

    def _compress_unlocked(self, start_ref: str, end_ref: str, topic: str, summary: str) -> str:
        n = len(self.history)
        if n == 0:
            return "Error: there is no history to fold yet."
        s, e = self._resolve(start_ref, True), self._resolve(end_ref, False)
        if s is None or e is None:
            return 'Error: use message ids such as "m0004" for start_ref and end_ref.'
        if s > e:
            s, e = e, s
        if s >= n:
            return f"Error: {ref(s)} does not exist yet; the latest message is {ref(n - 1)}."
        s, e = self._snap(max(0, s), min(e, n - 1))
        trimmed = self._trim_protected(s, e)
        if trimmed is None:
            return (
                "Error: that range only covers protected messages (the original task and the "
                "latest user message always stay verbatim)."
            )
        s, e, note = trimmed
        result = self._fold(s, e, topic, summary, auto=False)
        if isinstance(result, str):
            return result
        b = result
        return (
            f"Folded {b.span} into {b.id} (tier {b.tier}): ~{b.replaced_tokens:,} tokens replaced "
            f"by a ~{b.summary_tokens:,}-token summary. {note}From now on your summary appears in "
            f"place of those messages as your notes; recall_context('{b.id}') shows the originals."
        )

    # ------------------------------------------------------- emergency fold
    def _mechanical_summary(self, s: int, e: int) -> str:
        lines = [
            "(Auto-folded by the context manager at the context limit. This is a lossy preview; "
            "exact text is archived: use search_context or recall_context.)"
        ]
        for k in range(self._unit_of[s], self._unit_of[e] + 1):
            u = self.units[k]
            m = self.history[u.start]
            if u.kind == "compress":
                continue
            if u.kind == "human":
                lines.append(f"- {ref(u.start)} user: {_clip(content_text(m.content), 160)}")
            elif u.kind == "tools":
                said = _clip(content_text(m.content), 120)
                if said:
                    lines.append(f"- {ref(u.start)} you: {said}")
                calls = {tc.get("id"): tc for tc in m.tool_calls}
                for j in range(u.start + 1, u.end + 1):
                    tm = self.history[j]
                    tc = calls.get(getattr(tm, "tool_call_id", None)) or {}
                    args = ", ".join(f"{a}={v!r}" for a, v in (tc.get("args") or {}).items())
                    call = f"{tc.get('name', getattr(tm, 'name', 'tool'))}({_clip(args, 80)})"
                    lines.append(
                        f"- {ref(j)} {call} -> {_clip(content_text(tm.content), 140)} "
                        f"[{self.tokens[j]:,} tok]"
                    )
            else:
                lines.append(f"- {ref(u.start)} you: {_clip(content_text(m.content), 200)}")
        return "\n".join(lines)

    def _auto_fold(self, base_system: str, tools_tokens: int) -> list[str]:
        made: list[str] = []
        target = self.config.emergency_target_ratio * self.config.window
        for _ in range(16):
            view = self._render(base_system, tools_tokens)
            if view.estimate <= target:
                break
            need = (view.estimate - target) / max(self.counter.ratio, 1e-6)
            runs = self._foldable_runs(keep_recent=self.config.auto_keep_recent_units)
            if not runs:
                break
            run = runs[0]
            saved, end_k = 0, run[0]
            for k in run:  # shortest prefix of the oldest run that frees enough
                saved += self._unit_view_tokens(k) - AUTO_LINE_TOKENS * (
                    self.units[k].end - self.units[k].start + 1
                )
                end_k = k
                if saved >= need:
                    break
            s, e = self.units[run[0]].start, self.units[end_k].end
            block = self._fold(s, e, "auto-folded at context limit", self._mechanical_summary(s, e), True)
            if isinstance(block, str):
                break
            made.append(block.id)
        return made

    # ------------------------------------------------------------- retrieval
    def recall(self, target: str) -> str:
        with self._lock:
            return self._recall_unlocked(target)

    def _recall_unlocked(self, target: str) -> str:
        self.counts["recalls"] += 1
        n = len(self.history)
        key = _block_key(target)
        if key:
            b = self.blocks.get(key)
            if not b:
                known = ", ".join(self.blocks) or "none yet"
                return f"Error: no fold named {target}. Known folds: {known}."
            idxs = list(range(b.start, b.end + 1))
        else:
            nums = _nums(target)
            if not nums:
                return 'Error: pass a fold id like "b002" or message ids like "m0007" / "m0005-m0009".'
            lo, hi = max(0, nums[0]), min(nums[-1], n - 1)
            idxs = list(range(lo, hi + 1))
        out, used = [], 0
        cap = self.config.recall_max_tokens or max(500, self.config.window // 4)
        for i in idxs:
            if self.units[self._unit_of[i]].kind == "compress":
                continue
            r, role, text, tok = self.archive.get(i)
            if out and used + tok > cap:
                out.append(
                    f"... stopped at the {cap:,}-token recall limit; continue with "
                    f"recall_context('{r}-{ref(idxs[-1])}')"
                )
                break
            if tok > cap:
                text = text[: cap * 4] + " …(truncated)"
            out.append(f"[{r}] {role}: {text}")
            used += tok
        if not out:
            return "Nothing to recall in that range."
        return "Archived originals (read-only; the range stays folded):\n\n" + "\n\n".join(out)

    def search(self, query: str) -> str:
        with self._lock:
            return self._search_unlocked(query)

    def _search_unlocked(self, query: str) -> str:
        self.counts["searches"] += 1
        cov = self._covered()
        if not cov:
            return "Nothing has been folded yet, so there is nothing to search; everything is still in view."
        hits = self.archive.search(query, set(cov))
        if not hits:
            return "No matches in folded history."
        lines = [f"[{h.ref}] in {cov[h.idx].id} ({h.role}): {h.snippet}" for h in hits]
        return (
            "Matches in folded history:\n" + "\n".join(lines) + "\nUse recall_context('mNNNN') for a full message."
        )

    # ----------------------------------------------------------------- stats
    def stats(self) -> dict:
        blocks = list(self.blocks.values())
        return {
            "folds_model": sum(not b.auto for b in blocks),
            "folds_auto": sum(b.auto for b in blocks),
            "max_tier": max((b.tier for b in blocks), default=0),
            "tokens_replaced": sum(b.replaced_tokens for b in blocks),
            "summary_tokens": sum(b.summary_tokens for b in blocks),
            "active_blocks": sum(b.active for b in blocks),
            "token_ratio": round(self.counter.ratio, 3),
            **self.counts,
        }
