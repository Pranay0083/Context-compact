"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useMemo, useState } from "react";
import { api, sessionKey, type ResultDetail, type ResultListItem, type Run } from "@/lib/api";
import { compareSentence, fmt, statusTone } from "@/lib/format";
import TokenChart from "@/components/TokenChart";
import { FoldCard } from "@/components/Transcript";
import { Banner, Card, cx, Details, Empty, Pill, Pre, ScorePill, Spinner } from "@/components/ui";

const LIMIT = 8000;

function RunsTable({ runs }: { runs: Run[] }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-left text-xs text-muted">
          <tr>
            {["Session", "Agent", "Status", "Score", "Probe", "Mean", "Calls", "Input total", "Peak", "Output", "Folds m/a", "Re-reads", "Time"].map((h) => (
              <th key={h} className="whitespace-nowrap py-1.5 pr-4 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="tabular-nums">
          {runs.map((r, i) => (
            <tr key={i} className="border-t border-border">
              <td className="py-1.5 pr-4 font-medium">{sessionKey(r)}</td>
              <td className="pr-4">{r.arm}</td>
              <td className="pr-4">
                <Pill tone={statusTone(r.status)}>{r.status}</Pill>
              </td>
              <td className="pr-4">
                <ScorePill score={r.score ?? 0} />
              </td>
              <td className="pr-4">
                <ScorePill score={r.probe_score ?? 0} />
              </td>
              <td className="pr-4">{r.mean_score !== undefined && r.mean_score !== null ? <ScorePill score={r.mean_score} /> : "–"}</td>
              <td className="pr-4">{r.model_calls}</td>
              <td className="pr-4">{fmt(r.input_tokens_total)}</td>
              <td className="pr-4">{fmt(r.input_tokens_peak)}</td>
              <td className="pr-4">{fmt(r.output_tokens_total)}</td>
              <td className="pr-4">{r.compaction ? `${r.compaction.folds_model ?? 0}/${r.compaction.folds_auto ?? 0}` : "–"}</td>
              <td className="pr-4">{r.re_reads ?? "–"}</td>
              <td className="pr-4">{r.wall_s !== undefined ? `${Math.round(r.wall_s)}s` : "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ArmDetail({ r }: { r: Run }) {
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">{r.arm}</span>
        <Pill tone={statusTone(r.status)}>{r.status}</Pill>
        <ScorePill score={r.score ?? 0} label="score" />
        <ScorePill score={r.probe_score ?? 0} label="probe" />
      </div>
      {r.error && <Banner tone="bad">{r.failed_in ? <b>failed in {r.failed_in}: </b> : null}{r.error}</Banner>}
      {(r.models_used?.length || r.rate_limit_wait_s) ? (
        <div className="flex flex-wrap gap-1.5 text-xs">
          {r.models_used?.map((m) => <Pill key={m}>{m}</Pill>)}
          {r.rate_limit_wait_s ? <Pill tone="warn">waited {Math.round(r.rate_limit_wait_s)}s for rate limits</Pill> : null}
        </div>
      ) : null}
      {r.turns && r.turns.length > 0 && (
        <div className="space-y-1">
          {r.turns.map((t, i) => (
            <Details key={i} summary={<span className="flex items-center gap-2 text-xs">{t.skipped ? <Pill>skipped</Pill> : <ScorePill score={t.score} />} <span className="font-medium">{t.kind}</span>{t.calls !== undefined && <span className="text-muted">{t.calls} calls</span>} <span className="truncate text-muted">{t.question}</span></span>}>
              <div className="space-y-1 text-xs">
                <div className="whitespace-pre-wrap">{t.answer || "(no answer)"}</div>
                <Pre>{JSON.stringify(t.detail, null, 2)}</Pre>
              </div>
            </Details>
          ))}
        </div>
      )}
      {!r.turns?.length && (
        <>
          <Details summary="Answer">
            <div className="whitespace-pre-wrap text-sm">{r.answer || "(none)"}</div>
          </Details>
          <Details summary="Probe answer">
            <div className="whitespace-pre-wrap text-sm">{r.probe_answer || "(none)"}</div>
          </Details>
        </>
      )}
      <Details summary="Grading">
        <Pre>{JSON.stringify({ main: r.score_detail, probe: r.probe_detail }, null, 2)}</Pre>
      </Details>
      {r.folds && r.folds.length > 0 && (
        <Details summary={`Fold log (${r.folds.length})`}>
          <div className="space-y-2">
            {r.folds.map((f) => (
              <FoldCard key={f.id} fold={f} />
            ))}
          </div>
        </Details>
      )}
    </div>
  );
}

function ResultsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const [list, setList] = useState<ResultListItem[] | null>(null);
  const [detail, setDetail] = useState<ResultDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const selected = params.get("id") ?? list?.[0]?.id ?? null;

  useEffect(() => {
    api.results().then(setList).catch((e) => setError((e as Error).message));
  }, []);

  useEffect(() => {
    if (!selected) return;
    setLoading(true);
    api
      .result(selected)
      .then((d) => {
        setDetail(d);
        setError(null);
      })
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
  }, [selected]);

  const byTask = useMemo(() => {
    const m = new Map<string, Run[]>();
    for (const r of detail?.runs ?? []) {
      const k = sessionKey(r);
      m.set(k, [...(m.get(k) ?? []), r]);
    }
    return [...m.entries()];
  }, [detail]);

  const meta = detail?.meta;
  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside>
        <Card title="Experiment runs">
          {!list && !error && (
            <div className="text-sm text-muted">
              <Spinner /> Loading…
            </div>
          )}
          {list && list.length === 0 && <Empty>No runs yet. Run `python -m experiment.run`.</Empty>}
          <div className="space-y-1">
            {list?.map((r) => (
              <button
                key={r.id}
                onClick={() => router.replace(`/results?id=${encodeURIComponent(r.id)}`, { scroll: false })}
                className={cx(
                  "w-full rounded-lg px-2.5 py-2 text-left text-xs",
                  selected === r.id ? "bg-zinc-100 dark:bg-zinc-800" : "hover:bg-zinc-50 dark:hover:bg-zinc-900",
                )}
              >
                <div className="font-mono font-medium">{r.id}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">
                  {r.summary.map((s, i) => (
                    <Pill key={i} tone={statusTone(s.status)}>
                      {sessionKey(s).replaceAll("_", " ")} · {s.arm}
                    </Pill>
                  ))}
                </div>
              </button>
            ))}
          </div>
        </Card>
      </aside>
      <section className="min-w-0 space-y-4">
        {error && <Banner tone="bad">{error}</Banner>}
        {loading && (
          <div className="text-sm text-muted">
            <Spinner /> Loading run…
          </div>
        )}
        {detail && meta && (
          <>
            <div>
              <h1 className="text-xl font-semibold">{selected}</h1>
              <p className="text-sm text-muted">
                <span className="font-mono">{meta.model}</span> · size {meta.size ?? "–"} · seed {meta.seed ?? "–"} · compact window {fmt(meta.window)} · baseline window{" "}
                {meta.baseline_window ? fmt(meta.baseline_window) : "provider limit"} · {meta.date}
              </p>
            </div>
            <Card title="All runs">
              <RunsTable runs={detail.runs} />
            </Card>
            {byTask.map(([task, runs]) => {
              const b = runs.find((r) => r.arm === "baseline");
              const c = runs.find((r) => r.arm === "compact");
              return (
                <Card key={task} title={task}>
                  {b && c && b.input_tokens_total > 0 && c.input_tokens_total > 0 && (
                    <p className="mb-2 text-sm text-muted">
                      {compareSentence(
                        { status: b.status, calls: b.model_calls, total: b.input_tokens_total, peak: b.input_tokens_peak },
                        { status: c.status, calls: c.model_calls, total: c.input_tokens_total, peak: c.input_tokens_peak },
                      )}
                    </p>
                  )}
                  <TokenChart
                    series={runs.map((r) => ({ name: r.arm, data: r.per_call_input ?? [] }))}
                    refs={[
                      ...(meta.window ? [{ y: meta.window, label: `compact window ${fmt(meta.window)}`, color: "#7c3aed" }] : []),
                      { y: LIMIT, label: "Groq free-tier limit 8,000", color: "#dc2626" },
                    ]}
                    height={260}
                  />
                  <div className="mt-4 grid gap-4 md:grid-cols-2">
                    {runs.map((r, i) => (
                      <ArmDetail key={i} r={r} />
                    ))}
                  </div>
                </Card>
              );
            })}
          </>
        )}
      </section>
    </div>
  );
}

export default function ResultsPage() {
  return (
    <Suspense fallback={<Empty>Loading…</Empty>}>
      <ResultsInner />
    </Suspense>
  );
}
