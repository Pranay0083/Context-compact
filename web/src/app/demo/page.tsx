"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, sessionKey, type ResultListItem, type RunSummary } from "@/lib/api";
import { delta, fmt } from "@/lib/format";
import { Card, Pill, Pre } from "@/components/ui";

type Step = {
  n: number;
  title: string;
  body: string;
  say: string;
  links: { label: string; href: string; primary?: boolean }[];
};

const STEPS: Step[] = [
  {
    n: 1,
    title: "The problem: a plain agent hits the wall",
    body:
      "A tool-using agent re-sends its whole history on every call. On Groq's free tier a single request can't exceed ~8,000 tokens, so after reading a handful of files the plain agent's request is refused.",
    say: "Watch the gauge on the right climb with every read_file, until the provider returns 413 request_too_large.",
    links: [{ label: "Run the plain agent", href: "/?arm=baseline&task=service_audit&size=medium&kind=task&run=1", primary: true }],
  },
  {
    n: 2,
    title: "Compaction: the model folds its own finished work",
    body:
      "Same model, same task, plus ContextCompactionMiddleware with a 6,000-token window. When the window fills, the model is nudged (and, if needed, forced) to call compress_context and write the summary itself. The fold replaces the range in place as the model's notes.",
    say: "Point at a fold card: its tier and span, how many tokens it replaced (e.g. ~2,300 → ~130), and the exact values the model kept in its summary.",
    links: [{ label: "Run the compact agent", href: "/?arm=compact&task=service_audit&size=medium&window=6000&kind=task&run=1", primary: true }],
  },
  {
    n: 3,
    title: "Nothing is lost: recall a folded detail",
    body:
      "Every original message stays in an SQLite archive. After the main answer, the follow-up probe asks for a detail that was folded away early (a port, an exact timestamp). The model gets it back with search_context or recall_context instead of re-reading files.",
    say: "After the task finishes, press the PROBE preset (Recall: …) and watch for search_context / recall_context in the live events.",
    links: [
      { label: "Incident trace (compact)", href: "/?arm=compact&task=incident_trace&size=medium&window=6000&kind=task&run=1", primary: true },
      { label: "Side-by-side with probe", href: "/compare" },
    ],
  },
  {
    n: 4,
    title: "The numbers",
    body:
      "Every experiment run records provider-reported tokens per call, peak request size, folds (model vs auto), re-reads and graded scores, for both agents on the same seeded sandboxes.",
    say: "Show the chart: the baseline line runs into the red 8K limit; the compact line saw-tooths under the purple window.",
    links: [{ label: "Open results", href: "/results", primary: true }],
  },
  {
    n: 5,
    title: "Golden dataset",
    body:
      "A fixed, versioned set of sandbox sessions with multi-turn questions and known answers (file hashes pinned), so changes to the compactor can be measured, not guessed.",
    say: "Per-turn scores per agent show where compaction keeps accuracy and where it costs some.",
    links: [{ label: "Open golden set", href: "/golden", primary: true }],
  },
  {
    n: 6,
    title: "Where to use it",
    body: "Anywhere an agent's history grows faster than its window, or where you pay per input token.",
    say: "It's one middleware line on any LangChain create_agent.",
    links: [],
  },
];

const USE_CASES = [
  ["Coding agents", "Reading many files of a repo; keep signatures, paths and line numbers, drop file bodies once used."],
  ["Support / CRM agents", "Long multi-day tickets and order histories; keep customer facts and decisions."],
  ["Research agents", "Dozens of web pages or PDFs; keep cited facts and URLs, drop page boilerplate."],
  ["SRE / incident triage", "Huge log dumps; keep ids, timestamps and error strings across services."],
  ["Data & SQL agents", "Wide query results; keep the aggregates and schema facts that matter."],
  ["Small or free models", "8K–32K context models (Groq free tier, local Llama/Qwen) that would otherwise overflow."],
];

function Headline() {
  const [pair, setPair] = useState<{ id: string; task: string; b: RunSummary; c: RunSummary; scripted: boolean; model: string } | null>(null);
  useEffect(() => {
    api
      .results()
      .then((list: ResultListItem[]) => {
        // Prefer runs from real models; scripted offline runs only when nothing else exists.
        const isScripted = (r: ResultListItem) => Boolean((r.meta as { fake?: boolean }).fake) || String((r.meta as { model?: string }).model ?? "").startsWith("demo/");
        const ordered = [...list.filter((r) => !isScripted(r)), ...list.filter(isScripted)];
        for (const r of ordered) {
          const keys = [...new Set(r.summary.map((s) => sessionKey(s)))];
          for (const t of keys) {
            const b = r.summary.find((s) => sessionKey(s) === t && s.arm === "baseline");
            const c = r.summary.find((s) => sessionKey(s) === t && s.arm === "compact");
            if (b && c) return setPair({ id: r.id, task: t, b, c, scripted: isScripted(r), model: String((r.meta as { model?: string }).model ?? "") });
          }
        }
      })
      .catch(() => {});
  }, []);
  if (!pair) return null;
  const { b, c } = pair;
  return (
    <Card title={`Latest result · ${pair.task} · ${pair.scripted ? "offline scripted model (not a real result)" : pair.model}`} actions={<Link href={`/results?id=${encodeURIComponent(pair.id)}`} className="text-xs text-accent hover:underline">details →</Link>}>
      <div className="grid gap-3 sm:grid-cols-2">
        {[
          ["Plain agent", b],
          ["With compactor", c],
        ].map(([label, s]) => {
          const r = s as RunSummary;
          return (
            <div key={label as string} className="rounded-lg border border-border p-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                {label as string}
                <Pill tone={r.status === "ok" ? "ok" : "bad"}>{r.status}</Pill>
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-xs">
                <div>
                  <div className="text-muted">score</div>
                  <div className="text-base font-semibold tabular-nums">{r.score.toFixed(2)}</div>
                </div>
                <div>
                  <div className="text-muted">peak request</div>
                  <div className="text-base font-semibold tabular-nums">{fmt(r.input_tokens_peak)}</div>
                </div>
                <div>
                  <div className="text-muted">calls</div>
                  <div className="text-base font-semibold tabular-nums">{r.model_calls}</div>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {b.input_tokens_peak > 0 && (
        <p className="mt-2 text-sm text-muted">
          Peak request {delta(c.input_tokens_peak, b.input_tokens_peak).replace("fewer", "smaller").replace("more", "larger")} with the compactor
          {b.status !== "ok" && c.status === "ok" ? `; the plain agent stopped with ${b.status}, the compact agent finished.` : "."}
        </p>
      )}
    </Card>
  );
}

export default function DemoPage() {
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div>
        <h1 className="text-2xl font-semibold">Demo: letting an agent manage its own context</h1>
        <p className="mt-1 text-sm text-muted">A six-step presentation flow. Each step opens the right page with the settings preset.</p>
      </div>
      <Headline />
      <ol className="space-y-4">
        {STEPS.map((s) => (
          <li key={s.n}>
            <Card
              title={
                <span className="flex items-center gap-2">
                  <span className="grid h-6 w-6 place-items-center rounded-full bg-accent text-xs text-white dark:text-zinc-950">{s.n}</span>
                  {s.title}
                </span>
              }
            >
              <p className="text-sm leading-relaxed">{s.body}</p>
              <p className="mt-2 text-sm text-muted">
                <span className="font-medium text-foreground">Say: </span>
                {s.say}
              </p>
              {s.n === 6 && (
                <>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {USE_CASES.map(([t, d]) => (
                      <div key={t} className="rounded-lg border border-border p-3">
                        <div className="text-sm font-medium">{t}</div>
                        <div className="mt-1 text-xs text-muted">{d}</div>
                      </div>
                    ))}
                  </div>
                  <Pre className="mt-3">{`from langchain.agents import create_agent
from context_compact import ContextCompactionMiddleware

agent = create_agent(
    model, tools,
    middleware=[ContextCompactionMiddleware(window=8_000)],
)`}</Pre>
                </>
              )}
              {s.links.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-2">
                  {s.links.map((l) => (
                    <Link
                      key={l.href}
                      href={l.href}
                      className={
                        l.primary
                          ? "rounded-lg bg-accent px-3 py-1.5 text-sm font-medium text-white hover:opacity-90 dark:text-zinc-950"
                          : "rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-zinc-50 dark:hover:bg-zinc-900"
                      }
                    >
                      {l.label} →
                    </Link>
                  ))}
                </div>
              )}
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}
