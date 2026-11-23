"use client";

import type { Fold, ToolCall, TranscriptItem } from "@/lib/api";
import { fmt, shortArgs } from "@/lib/format";
import { cx, Details, Pill, Pre } from "./ui";

export function FoldCard({ fold, compact = false }: { fold: Fold; compact?: boolean }) {
  return (
    <div
      className={cx(
        "rounded-lg border px-3 py-2",
        fold.active
          ? "border-violet-300 bg-violet-50/70 dark:border-violet-900 dark:bg-violet-950/40"
          : "border-border bg-zinc-50 opacity-70 dark:bg-zinc-900/60",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="font-mono font-semibold text-fold">{fold.id}</span>
        <Pill tone="fold">tier {fold.tier}</Pill>
        <span className="font-mono text-muted">{fold.span}</span>
        <Pill tone={fold.auto ? "warn" : "info"}>{fold.auto ? "auto" : "model"}</Pill>
        <span className="tabular-nums text-muted">
          {fmt(fold.replaced_tokens)} → {fmt(fold.summary_tokens)} tok
        </span>
        {!fold.active && <Pill>merged</Pill>}
        {fold.children.length > 0 && <span className="text-muted">children: {fold.children.join(", ")}</span>}
      </div>
      {fold.topic && <div className="mt-1 text-xs italic text-muted">{fold.topic}</div>}
      {!compact && <Pre className="mt-1.5 max-h-56 bg-white/70 dark:bg-zinc-950/40">{fold.summary}</Pre>}
    </div>
  );
}

function CompressCall({ call }: { call: ToolCall }) {
  const a = call.args as Record<string, string>;
  return (
    <div className="rounded-lg border border-violet-300 bg-violet-50/70 px-3 py-2 dark:border-violet-900 dark:bg-violet-950/40">
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span aria-hidden>⇲</span>
        <span className="font-semibold text-fold">compress_context</span>
        <span className="font-mono text-muted">
          {a.start_ref} – {a.end_ref}
        </span>
        {a.topic && <span className="italic text-muted">{a.topic}</span>}
      </div>
      {a.summary && <Pre className="mt-1.5 max-h-48 bg-white/70 dark:bg-zinc-950/40">{a.summary}</Pre>}
    </div>
  );
}

function ToolChip({ call }: { call: ToolCall }) {
  const icon = call.name === "search_context" || call.name === "recall_context" ? "⌕" : "⚙";
  return (
    <span className="inline-flex max-w-full items-center gap-1 rounded-md border border-border bg-zinc-50 px-2 py-0.5 font-mono text-xs dark:bg-zinc-900">
      <span aria-hidden>{icon}</span>
      <span className="font-semibold">{call.name}</span>
      <span className="truncate text-muted">({shortArgs(call.args)})</span>
    </span>
  );
}

function RefTag({ item }: { item: TranscriptItem }) {
  return (
    <span className="flex items-center gap-1.5 text-[11px] text-muted">
      <span className="font-mono">{item.ref}</span>
      {item.folded_into && <Pill tone="fold">folded into {item.folded_into}</Pill>}
    </span>
  );
}

export default function Transcript({ items }: { items: TranscriptItem[] }) {
  return (
    <div className="space-y-3">
      {items.map((m) => {
        const dim = m.folded_into ? "opacity-55" : "";
        if (m.role === "user") {
          return (
            <div key={m.ref} className={cx("flex flex-col items-end gap-1", dim)}>
              <RefTag item={m} />
              <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-tr-sm bg-accent px-3.5 py-2 text-sm text-white dark:text-zinc-950">
                {m.text}
              </div>
            </div>
          );
        }
        if (m.role === "tool") {
          return (
            <div key={m.ref} className={cx("pl-6", dim)}>
              <Details
                summary={
                  <span className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-muted">{m.ref}</span>
                    <span>
                      result of <span className="font-mono font-medium">{m.tool_name || "tool"}</span>
                    </span>
                    <span className="tabular-nums text-muted">~{fmt(m.tokens)} tokens</span>
                    {m.folded_into && <Pill tone="fold">folded into {m.folded_into}</Pill>}
                  </span>
                }
              >
                <Pre>{m.text}</Pre>
              </Details>
            </div>
          );
        }
        const compress = m.tool_calls.filter((c) => c.name === "compress_context");
        const others = m.tool_calls.filter((c) => c.name !== "compress_context");
        return (
          <div key={m.ref} className={cx("flex flex-col items-start gap-1", dim)}>
            <RefTag item={m} />
            <div className="w-full max-w-[92%] space-y-1.5 rounded-2xl rounded-tl-sm border border-border bg-card px-3.5 py-2">
              {m.text && <div className="whitespace-pre-wrap text-sm">{m.text}</div>}
              {others.length > 0 && (
                <div className="flex flex-wrap gap-1.5">
                  {others.map((c, i) => (
                    <ToolChip key={i} call={c} />
                  ))}
                </div>
              )}
              {compress.map((c, i) => (
                <CompressCall key={i} call={c} />
              ))}
              {!m.text && m.tool_calls.length === 0 && <span className="text-xs text-muted">(empty)</span>}
            </div>
          </div>
        );
      })}
    </div>
  );
}
