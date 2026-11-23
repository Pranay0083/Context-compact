"use client";

import Link from "next/link";
import { Suspense, useEffect, useMemo, useState } from "react";
import { api, sessionKey, type GoldenResponse, type ResultDetail, type Run, type RunSummary } from "@/lib/api";
import { fmt, statusTone } from "@/lib/format";
import { Banner, Card, Details, Empty, Pill, Pre, ScorePill, Spinner } from "@/components/ui";

function SummaryValue({ v }: { v: unknown }) {
  if (typeof v === "number") return <span className="tabular-nums">{Number.isInteger(v) ? fmt(v) : v.toFixed(3)}</span>;
  if (typeof v === "string" || typeof v === "boolean") return <span>{String(v)}</span>;
  return <Pre className="max-h-40">{JSON.stringify(v, null, 2)}</Pre>;
}

/** Per-agent aggregate of an eval's summary rows (the server sends RunSummary[]). */
function EvalAggregate({ rows }: { rows: RunSummary[] }) {
  const arms = [...new Set(rows.map((r) => r.arm))];
  const agg = arms.map((arm) => {
    const rs = rows.filter((r) => r.arm === arm);
    const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);
    const turns = rs.flatMap((r) => r.turn_scores ?? []);
    return {
      arm,
      sessions: rs.length,
      ok: rs.filter((r) => r.status === "ok").length,
      failed: rs.filter((r) => r.status !== "ok").length,
      mean: mean(rs.map((r) => r.mean_score ?? r.score ?? 0)),
      perfect: turns.length ? turns.filter((t) => t >= 0.999).length / turns.length : 0,
      input: rs.reduce((a, r) => a + (r.input_tokens_total ?? 0), 0),
      peak: Math.max(0, ...rs.map((r) => r.input_tokens_peak ?? 0)),
      folds: `${rs.reduce((a, r) => a + (r.folds_model ?? 0), 0)}/${rs.reduce((a, r) => a + (r.folds_auto ?? 0), 0)}`,
    };
  });
  return (
    <div className="mb-3 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            {["Agent", "Sessions", "Finished", "Failed", "Mean score", "Turns fully correct", "Input tokens", "Max peak", "Folds m/a"].map((h) => (
              <th key={h} className="py-1.5 pr-4 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {agg.map((a) => (
            <tr key={a.arm} className="border-t border-border">
              <td className="py-1.5 pr-4 font-medium">{a.arm}</td>
              <td className="pr-4">{a.sessions}</td>
              <td className="pr-4">{a.ok}</td>
              <td className="pr-4">{a.failed ? <Pill tone="bad">{a.failed}</Pill> : 0}</td>
              <td className="pr-4">
                <ScorePill score={a.mean} />
              </td>
              <td className="pr-4">{Math.round(100 * a.perfect)}%</td>
              <td className="pr-4">{fmt(a.input)}</td>
              <td className="pr-4">{fmt(a.peak)}</td>
              <td className="pr-4">{a.arm === "compact" ? a.folds : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EvalTurns({ id }: { id: string }) {
  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.result(id).then(setDetail).catch((e) => setError((e as Error).message));
  }, [id]);

  const rows = useMemo(() => {
    const runs = detail?.runs ?? [];
    const arms = [...new Set(runs.map((r) => r.arm))];
    const cases = [...new Set(runs.map((r) => sessionKey(r)))];
    const out: { caseId: string; turn: number; kind: string; question: string; scores: Record<string, number | null>; status: Record<string, string> }[] = [];
    // a turn counts as "not reached" (null) when it was skipped, e.g. after the baseline hit the wall
    for (const cid of cases) {
      const byArm: Record<string, Run | undefined> = Object.fromEntries(arms.map((a) => [a, runs.find((r) => sessionKey(r) === cid && r.arm === a)]));
      const n = Math.max(0, ...arms.map((a) => byArm[a]?.turns?.length ?? 0));
      for (let t = 0; t < n; t++) {
        const ref = arms.map((a) => byArm[a]?.turns?.[t]).find(Boolean);
        out.push({
          caseId: cid,
          turn: t + 1,
          kind: ref?.kind ?? "",
          question: ref?.question ?? "",
          scores: Object.fromEntries(arms.map((a) => { const tt = byArm[a]?.turns?.[t]; return [a, tt && !tt.skipped ? tt.score : null]; })),
          status: Object.fromEntries(arms.map((a) => [a, byArm[a]?.status ?? "–"])),
        });
      }
    }
    return { arms, out };
  }, [detail]);

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!detail)
    return (
      <div className="text-sm text-muted">
        <Spinner /> Loading eval…
      </div>
    );
  if (!rows.out.length) return <Empty>This eval has no per-turn results.</Empty>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            <th className="py-1.5 pr-4">Case</th>
            <th className="pr-4">Turn</th>
            <th className="pr-4">Question</th>
            {rows.arms.map((a) => (
              <th key={a} className="pr-4">
                {a}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.out.map((r, i) => (
            <tr key={i} className="border-t border-border align-top">
              <td className="py-1.5 pr-4 font-mono text-xs">{r.caseId}</td>
              <td className="pr-4 text-xs">
                {r.turn} <span className="text-muted">{r.kind}</span>
              </td>
              <td className="max-w-md pr-4 text-xs text-muted">
                <span className="line-clamp-2">{r.question}</span>
              </td>
              {rows.arms.map((a) => (
                <td key={a} className="pr-4">
                  {r.scores[a] === null ? <Pill tone={statusTone(r.status[a])}>{r.status[a]}</Pill> : <ScorePill score={r.scores[a] as number} />}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GoldenInner() {
  const [data, setData] = useState<GoldenResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api.golden().then(setData).catch((e) => setError((e as Error).message));
  }, []);

  if (error) return <Banner tone="bad">{error}</Banner>;
  if (!data)
    return (
      <div className="text-sm text-muted">
        <Spinner /> Loading golden dataset…
      </div>
    );
  const ds = data.dataset;
  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">
          Golden dataset <span className="font-mono text-base text-muted">{ds.version}</span>
        </h1>
        <p className="max-w-3xl text-sm text-muted">{ds.description}</p>
      </div>
      <Card title={`Cases (${ds.cases.length})`}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted">
              <tr>
                {["Case", "Family", "Seed", "Size", "Turns", "Questions", "Files sha256"].map((h) => (
                  <th key={h} className="py-1.5 pr-4 font-medium">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ds.cases.map((c) => (
                <tr key={c.id} className="border-t border-border align-top">
                  <td className="py-1.5 pr-4 font-mono text-xs">{c.id}</td>
                  <td className="pr-4">{c.family}</td>
                  <td className="pr-4 tabular-nums">{c.seed}</td>
                  <td className="pr-4">{c.size}</td>
                  <td className="pr-4 tabular-nums">{c.turns.length}</td>
                  <td className="max-w-xl pr-4">
                    <Details summary={<span className="text-xs text-muted">{c.turns.map((t) => t.kind).join(" → ")}</span>}>
                      <ol className="list-decimal space-y-2 pl-4 text-xs">
                        {c.turns.map((t, i) => (
                          <li key={i}>
                            <div className="whitespace-pre-wrap">{t.question}</div>
                            <Pre className="mt-1 max-h-32">{JSON.stringify(t.expected)}</Pre>
                          </li>
                        ))}
                      </ol>
                    </Details>
                  </td>
                  <td className="pr-4 font-mono text-[11px] text-muted" title={c.files_sha256}>
                    {c.files_sha256.slice(0, 12)}…
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      <Card title={`Eval runs (${data.evals.length})`}>
        {data.evals.length === 0 ? (
          <Empty>
            No golden evals yet. Run <span className="font-mono">python -m experiment.run --golden golden/v1.json</span>.
          </Empty>
        ) : (
          <div className="space-y-4">
            {data.evals.map((e) => (
              <div key={e.id} className="rounded-lg border border-border p-3">
                <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono font-medium">{e.id}</span>
                  {e.meta.model && <span className="font-mono text-xs text-muted">{e.meta.model}</span>}
                  {e.meta.date && <span className="text-xs text-muted">{e.meta.date}</span>}
                  <Link href={`/results?id=${encodeURIComponent(e.id)}`} className="ml-auto text-xs text-accent hover:underline">
                    open in Results →
                  </Link>
                </div>
                {Array.isArray(e.summary) ? (
                  <EvalAggregate rows={e.summary} />
                ) : e.summary && Object.keys(e.summary).length > 0 && (
                  <div className="mb-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
                    {Object.entries(e.summary).map(([k, v]) => (
                      <div key={k} className="rounded-lg border border-border px-3 py-2 text-sm">
                        <div className="text-[11px] uppercase tracking-wide text-muted">{k.replaceAll("_", " ")}</div>
                        <SummaryValue v={v} />
                      </div>
                    ))}
                  </div>
                )}
                <EvalTurns id={e.id} />
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

export default function GoldenPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <GoldenInner />
    </Suspense>
  );
}
