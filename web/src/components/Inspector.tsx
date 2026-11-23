"use client";

import type { SessionState } from "@/lib/api";
import { fmt, pct } from "@/lib/format";
import TokenChart from "./TokenChart";
import { FoldCard } from "./Transcript";
import { Card, cx, Details, Empty, Metric, Pill, Pre } from "./ui";

function Gauge({ value, max, label, tone }: { value: number; max: number; label: string; tone: "accent" | "limit" }) {
  const p = pct(value, max);
  const color = p >= 100 ? "bg-red-500" : p >= 85 ? "bg-amber-500" : tone === "accent" ? "bg-accent" : "bg-zinc-500";
  return (
    <div>
      <div className="mb-1 flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="tabular-nums">
          {fmt(value)} / {fmt(max)} <span className="text-muted">({Math.round(p)}%)</span>
        </span>
      </div>
      <div className="h-2.5 overflow-hidden rounded-full bg-zinc-200 dark:bg-zinc-800">
        <div className={cx("h-full rounded-full transition-all", color)} style={{ width: `${p}%` }} />
      </div>
    </div>
  );
}

export default function Inspector({ state }: { state: SessionState | null }) {
  if (!state) return <Empty>Start a session to inspect its context.</Empty>;
  const calls = state.calls;
  const inputs = calls.map((c) => c.input_tokens ?? 0);
  const last = inputs.at(-1) ?? 0;
  const total = inputs.reduce((a, b) => a + b, 0);
  const peak = Math.max(0, ...inputs);
  const st = state.stats;
  const isCompact = state.settings.arm === "compact" && state.window;
  const refs = [
    ...(isCompact ? [{ y: state.window as number, label: `compact window ${fmt(state.window)}`, color: "#7c3aed" }] : []),
    { y: state.limit, label: `provider limit ${fmt(state.limit)}`, color: "#dc2626" },
  ];
  const folds = [...state.folds].sort((a, b) => a.created_at_call - b.created_at_call || a.id.localeCompare(b.id));

  return (
    <div className="space-y-4">
      <Card title="Context window">
        <div className="space-y-3">
          {isCompact && <Gauge value={last} max={state.window as number} label="Last request vs compact window" tone="accent" />}
          <Gauge value={last} max={state.limit} label="Last request vs Groq free-tier limit" tone="limit" />
        </div>
        <div className="mt-4 grid grid-cols-3 gap-2">
          <Metric label="Model calls" value={calls.length} />
          <Metric label="Input total" value={fmt(total)} hint="Sum of provider-reported input tokens" />
          <Metric label="Peak request" value={fmt(peak)} />
        </div>
        {st && (
          <>
            <div className="mt-2 grid grid-cols-4 gap-2">
              <Metric label="Folds (model)" value={st.folds_model ?? 0} />
              <Metric label="Auto-folds" value={st.folds_auto ?? 0} />
              <Metric label="Max tier" value={st.max_tier ?? 0} />
              <Metric label="Tokens folded" value={fmt(st.tokens_replaced)} />
            </div>
            <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
              <Pill>nudges {st.nudges ?? 0}</Pill>
              <Pill>forced {st.forced ?? 0}</Pill>
              <Pill>searches {st.searches ?? 0}</Pill>
              <Pill>recalls {st.recalls ?? 0}</Pill>
              {st.rejected_folds ? <Pill tone="warn">rejected {st.rejected_folds}</Pill> : null}
            </div>
          </>
        )}
      </Card>

      <Card title="Input tokens per model call">
        <TokenChart series={[{ name: "input tokens", data: inputs }]} refs={refs} height={200} />
      </Card>

      {state.settings.arm === "compact" && (
        <Card title={`Folds (${folds.length})`}>
          {folds.length === 0 ? (
            <Empty>No folds yet. The model is nudged once the request passes ~45% of the window.</Empty>
          ) : (
            <div className="max-h-[420px] space-y-2 overflow-auto pr-1 scroll-thin">
              {folds.map((f) => (
                <FoldCard key={f.id} fold={f} />
              ))}
            </div>
          )}
        </Card>
      )}

      {state.last_view && (
        <Details
          summary={
            <span className="flex flex-wrap items-center gap-2">
              What the model saw on its last call
              <Pill>~{fmt(state.last_view.estimate)} tokens</Pill>
              <Pill>{state.last_view.messages.length} messages</Pill>
              {state.last_view.nudged && <Pill tone="warn">nudged</Pill>}
              {state.last_view.force_fold && <Pill tone="fold">forced fold</Pill>}
            </span>
          }
        >
          <div className="space-y-2">
            <div className="text-xs font-medium text-muted">system</div>
            <Pre>{state.last_view.system}</Pre>
            {state.last_view.messages.map((m, i) => (
              <div key={i}>
                <div className="text-xs font-medium text-muted">
                  {m.role}
                  {m.tool_calls.length > 0 && ` → ${m.tool_calls.map((t) => t.name).join(", ")}`}
                </div>
                <Pre className="max-h-40">{m.text || "(tool call)"}</Pre>
              </div>
            ))}
          </div>
        </Details>
      )}
    </div>
  );
}
