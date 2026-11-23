"use client";

import { useEffect, useState } from "react";
import { api, type Options, type SessionState, type Settings, type Size } from "@/lib/api";
import { cleanError, compareSentence, fmt } from "@/lib/format";
import { useSessionRunner } from "@/hooks/useSessionRunner";
import EventLog, { WaitBanner } from "@/components/EventLog";
import { FALLBACK_OPTIONS, presetTurns } from "@/components/Playground";
import TokenChart from "@/components/TokenChart";
import Transcript, { FoldCard } from "@/components/Transcript";
import { Banner, Button, Card, Details, Empty, Field, inputClass, Pill, ScorePill, Spinner } from "@/components/ui";

type Mode = "task" | "task+probe" | "all" | "custom";

function summarize(s: SessionState | null) {
  if (!s) return null;
  const inputs = s.calls.map((c) => c.input_tokens ?? 0);
  return {
    calls: s.calls.length,
    total: inputs.reduce((a, b) => a + b, 0),
    peak: Math.max(0, ...inputs),
    folds: s.stats ? `${s.stats.folds_model ?? 0}/${s.stats.folds_auto ?? 0}` : "–",
    score: s.grades.find((g) => g.kind === "task")?.score,
    probe: s.grades.find((g) => g.kind === "probe")?.score,
    mean: s.grades.length ? s.grades.reduce((a, g) => a + g.score, 0) / s.grades.length : undefined,
    graded: s.grades.length,
  };
}

export default function ComparePage() {
  const [options, setOptions] = useState<Options>(FALLBACK_OPTIONS);
  const [cfg, setCfg] = useState<Settings>({ ...FALLBACK_OPTIONS.defaults });
  const [mode, setMode] = useState<Mode>("task");
  const [custom, setCustom] = useState("");
  const [phase, setPhase] = useState<"idle" | "baseline" | "compact" | "done">("idle");
  const base = useSessionRunner();
  const comp = useSessionRunner();

  useEffect(() => {
    api
      .options()
      .then((o) => {
        setOptions(o);
        setCfg({ ...o.defaults });
      })
      .catch(() => {});
  }, []);

  const running = phase === "baseline" || phase === "compact";
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setCfg((c) => ({ ...c, [k]: v }));

  const runArm = async (runner: ReturnType<typeof useSessionRunner>, arm: "baseline" | "compact") => {
    const s = await runner.create({ ...cfg, arm });
    if (!s) return;
    if (mode === "custom") {
      await runner.ask(custom, null, s);
      return;
    }
    const presets = presetTurns(s);
    const turns = mode === "task" ? presets.slice(0, 1) : mode === "task+probe" ? presets.filter((t) => t.kind !== "followup").slice(0, 2) : presets;
    let current = s;
    for (const t of turns) {
      const out = await runner.ask(t.question, t.kind, current);
      if (out.error || !out.state) break; // e.g. the baseline hit request_too_large
      current = out.state;
    }
  };

  const run = async () => {
    if (mode === "custom" && !custom.trim()) return;
    setPhase("baseline");
    await runArm(base, "baseline"); // strictly sequential: free-tier limits are per account
    setPhase("compact");
    await runArm(comp, "compact");
    setPhase("done");
  };

  const b = summarize(base.state);
  const c = summarize(comp.state);
  const limit = comp.state?.limit ?? base.state?.limit ?? 8000;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold">Compare: plain agent vs compacting agent</h1>
        <p className="text-sm text-muted">Same model, sandbox and question. Runs the baseline first, then the compact agent.</p>
      </div>
      <Card title="Setup">
        <div className="grid gap-3 md:grid-cols-3 lg:grid-cols-6">
          <Field label="Model">
            <select className={inputClass} value={cfg.model} disabled={running} onChange={(e) => set("model", e.target.value)}>
              {options.models.map((m) => (
                <option key={m}>{m}</option>
              ))}
            </select>
          </Field>
          <Field label="Sandbox">
            <select className={inputClass} value={cfg.task} disabled={running} onChange={(e) => set("task", e.target.value)}>
              {options.tasks.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label="Size">
            <select className={inputClass} value={cfg.size} disabled={running} onChange={(e) => set("size", e.target.value as Size)}>
              {options.sizes.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
          </Field>
          <Field label="Seed">
            <input type="number" className={inputClass} value={cfg.seed} disabled={running} onChange={(e) => set("seed", Number(e.target.value))} />
          </Field>
          <Field label={`Compact window: ${fmt(cfg.window)}`}>
            <input type="range" min={2000} max={16000} step={500} value={cfg.window} disabled={running} onChange={(e) => set("window", Number(e.target.value))} className="w-full accent-[var(--accent)]" />
          </Field>
          <Field label="Question">
            <select className={inputClass} value={mode} disabled={running} onChange={(e) => setMode(e.target.value as Mode)}>
              <option value="task">Task question</option>
              <option value="task+probe">Task + follow-up probe</option>
              <option value="all">All graded turns</option>
              <option value="custom">Custom question</option>
            </select>
          </Field>
        </div>
        {mode === "custom" && (
          <textarea className={`${inputClass} mt-3`} rows={2} placeholder="Your question for both agents…" value={custom} disabled={running} onChange={(e) => setCustom(e.target.value)} />
        )}
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button onClick={run} disabled={running || (mode === "custom" && !custom.trim())}>
            {running ? (
              <>
                <Spinner /> Running {phase}…
              </>
            ) : (
              "Run both"
            )}
          </Button>
          <span className="text-xs text-amber-700 dark:text-amber-400">Uses about twice the quota of one run. Free tier: ~200K tokens per model per day.</span>
        </div>
      </Card>

      {(b || c) && (
        <Card title="Summary">
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-muted">
                <tr>
                  <th className="py-1.5 pr-4">Agent</th>
                  <th className="pr-4">Status</th>
                  <th className="pr-4">Score</th>
                  <th className="pr-4">Probe</th>
                  <th className="pr-4">Mean (graded)</th>
                  <th className="pr-4">Calls</th>
                  <th className="pr-4">Input total</th>
                  <th className="pr-4">Peak request</th>
                  <th className="pr-4">Folds model/auto</th>
                </tr>
              </thead>
              <tbody className="tabular-nums">
                {([
                  ["baseline", base, b],
                  ["compact", comp, c],
                ] as const).map(([name, r, s]) => (
                  <tr key={name} className="border-t border-border">
                    <td className="py-1.5 pr-4 font-medium">{name}</td>
                    <td className="pr-4">{r.error ? <Pill tone="bad">{r.error.status}</Pill> : s ? <Pill tone={r.running ? "info" : "ok"}>{r.running ? "running" : "ok"}</Pill> : "–"}</td>
                    <td className="pr-4">{s?.score !== undefined ? <ScorePill score={s.score} /> : "–"}</td>
                    <td className="pr-4">{s?.probe !== undefined ? <ScorePill score={s.probe} /> : "–"}</td>
                    <td className="pr-4">{s?.mean !== undefined ? <span className="inline-flex items-center gap-1"><ScorePill score={s.mean} /> <span className="text-xs text-muted">×{s.graded}</span></span> : "–"}</td>
                    <td className="pr-4">{s?.calls ?? "–"}</td>
                    <td className="pr-4">{fmt(s?.total)}</td>
                    <td className="pr-4">{fmt(s?.peak)}</td>
                    <td className="pr-4">{s?.folds ?? "–"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {b && c && b.total > 0 && c.total > 0 && !base.running && !comp.running && (
            <p className="mt-2 text-sm text-muted">
              {compareSentence(
                { status: base.error?.status ?? "ok", calls: b.calls, total: b.total, peak: b.peak },
                { status: comp.error?.status ?? "ok", calls: c.calls, total: c.total, peak: c.peak },
              )}
            </p>
          )}
          <div className="mt-3">
            <TokenChart
              series={[
                { name: "baseline", data: base.state?.calls.map((x) => x.input_tokens ?? 0) ?? [] },
                { name: "compact", data: comp.state?.calls.map((x) => x.input_tokens ?? 0) ?? [] },
              ]}
              refs={[
                { y: cfg.window, label: `compact window ${fmt(cfg.window)}`, color: "#7c3aed" },
                { y: limit, label: `provider limit ${fmt(limit)}`, color: "#dc2626" },
              ]}
              height={260}
            />
          </div>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        {([
          ["Baseline (plain agent)", base],
          ["Compact (with compactor)", comp],
        ] as const).map(([title, r]) => (
          <Card key={title} title={title}>
            <div className="space-y-3">
              {r.error && (
                <Banner tone="bad">
                  <b>{r.error.status}</b>: <span className="break-words text-xs">{cleanError(r.error.status, r.error.message)}</span>
                </Banner>
              )}
              <WaitBanner wait={r.wait} />
              <EventLog lines={r.log} running={r.running} height={140} />
              {r.state ? (
                <>
                  <TokenChart
                    series={[{ name: "input tokens", data: r.state.calls.map((x) => x.input_tokens ?? 0) }]}
                    refs={[
                      ...(r.state.settings.arm === "compact" ? [{ y: r.state.settings.window, label: "window", color: "#7c3aed" }] : []),
                      { y: r.state.limit, label: "limit", color: "#dc2626" },
                    ]}
                    height={170}
                  />
                  {r.state.folds.length > 0 && (
                    <Details summary={`Folds (${r.state.folds.length})`}>
                      <div className="space-y-2">
                        {r.state.folds.map((f) => (
                          <FoldCard key={f.id} fold={f} />
                        ))}
                      </div>
                    </Details>
                  )}
                  <Details summary={`Transcript (${r.state.transcript.length} messages)`}>
                    <div className="max-h-[520px] overflow-auto scroll-thin">
                      <Transcript items={r.state.transcript} />
                    </div>
                  </Details>
                </>
              ) : (
                <Empty>Not run yet.</Empty>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}
