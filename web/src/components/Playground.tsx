"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, type Arm, type DemoQuery, type Kind, type Options, type PresetTurn, type SessionState, type Settings, type Size } from "@/lib/api";
import { cleanError, fmt } from "@/lib/format";
import { useSessionRunner } from "@/hooks/useSessionRunner";
import EventLog, { WaitBanner } from "./EventLog";
import Inspector from "./Inspector";
import Transcript from "./Transcript";
import { Banner, Button, Card, Details, Empty, Field, inputClass, Pill, Pre, ScorePill, Spinner } from "./ui";

export const FALLBACK_OPTIONS: Options = {
  models: ["openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b"],
  arms: ["compact", "baseline"],
  tasks: ["service_audit", "incident_trace"],
  sizes: ["small", "medium", "large"],
  defaults: { model: "openai/gpt-oss-120b", arm: "compact", window: 6000, task: "service_audit", size: "medium", seed: 7, fallback: true },
  demo_queries: {},
};

export function settingsFromParams(params: URLSearchParams, defaults: Settings): Settings {
  const s = { ...defaults };
  const arm = params.get("arm");
  if (arm === "compact" || arm === "baseline") s.arm = arm;
  const task = params.get("task");
  if (task) s.task = task;
  const size = params.get("size");
  if (size === "small" || size === "medium" || size === "large") s.size = size;
  const model = params.get("model");
  if (model) s.model = model;
  const win = Number(params.get("window"));
  if (win >= 1000) s.window = win;
  const seed = params.get("seed");
  if (seed !== null && seed !== "" && !Number.isNaN(Number(seed))) s.seed = Number(seed);
  const fb = params.get("fallback");
  if (fb === "0" || fb === "1") s.fallback = fb === "1";
  return s;
}

/** Graded preset questions for a session (server `sandbox.turns`, else task + probe). */
export function presetTurns(s: SessionState): PresetTurn[] {
  if (s.sandbox.turns && s.sandbox.turns.length) return s.sandbox.turns;
  return [
    { kind: "task", question: s.sandbox.prompt, label: "Ask task question" },
    { kind: "probe", question: s.sandbox.probe, label: "Ask follow-up probe" },
  ];
}

const withValue = (list: string[], v: string) => (list.includes(v) ? list : [v, ...list]);

export function SettingsForm({ value, onChange, options, disabled }: {
  value: Settings;
  onChange: (s: Settings) => void;
  options: Options;
  disabled?: boolean;
}) {
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => onChange({ ...value, [k]: v });
  return (
    <div className="space-y-3">
      <Field label="Model (Groq free tier)">
        <select className={inputClass} value={value.model} disabled={disabled} onChange={(e) => set("model", e.target.value)}>
          {withValue(options.models, value.model).map((m) => (
            <option key={m}>{m}</option>
          ))}
        </select>
      </Field>
      <Field label="Agent">
        <div className="grid grid-cols-2 gap-1 rounded-lg border border-border p-1">
          {(["compact", "baseline"] as Arm[]).map((a) => (
            <button
              key={a}
              type="button"
              disabled={disabled}
              onClick={() => set("arm", a)}
              className={`rounded-md px-2 py-1 text-sm ${value.arm === a ? "bg-accent text-white dark:text-zinc-950" : "hover:bg-zinc-100 dark:hover:bg-zinc-800"}`}
            >
              {a}
            </button>
          ))}
        </div>
      </Field>
      <Field label={`Compact window: ${fmt(value.window)} tokens`} hint="Token budget per request for the compact agent.">
        <input
          type="range"
          min={2000}
          max={16000}
          step={500}
          value={value.window}
          disabled={disabled || value.arm !== "compact"}
          onChange={(e) => set("window", Number(e.target.value))}
          className="w-full accent-[var(--accent)]"
        />
      </Field>
      <Field label="Sandbox">
        <select className={inputClass} value={value.task} disabled={disabled} onChange={(e) => set("task", e.target.value)}>
          {options.tasks.map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      </Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label="Size">
          <select className={inputClass} value={value.size} disabled={disabled} onChange={(e) => set("size", e.target.value as Size)}>
            {options.sizes.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </Field>
        <Field label="Seed">
          <input type="number" className={inputClass} value={value.seed} disabled={disabled} onChange={(e) => set("seed", Number(e.target.value))} />
        </Field>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={value.fallback} disabled={disabled} onChange={(e) => set("fallback", e.target.checked)} />
        Fall back to other free models on daily quota
      </label>
    </div>
  );
}

const same = (a: Settings, b: Settings) =>
  a.model === b.model && a.arm === b.arm && a.window === b.window && a.task === b.task && a.size === b.size && a.seed === b.seed && a.fallback === b.fallback;

export default function Playground() {
  const params = useSearchParams();
  const router = useRouter();
  const runner = useSessionRunner();
  const { state, create, ask, running, creating } = runner;
  const [options, setOptions] = useState<Options>(FALLBACK_OPTIONS);
  const [optionsError, setOptionsError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Settings>(() => settingsFromParams(params, FALLBACK_OPTIONS.defaults));
  const [text, setText] = useState("");
  const booted = useRef(false);
  const scroller = useRef<HTMLDivElement>(null);

  // Boot: load options, create a session from URL params, optionally auto-run `q`.
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    (async () => {
      let opts = FALLBACK_OPTIONS;
      try {
        opts = await api.options();
        setOptions(opts);
      } catch (e) {
        setOptionsError((e as Error).message);
      }
      const s = settingsFromParams(params, opts.defaults);
      setDraft(s);
      const session = await create(s);
      const q = params.get("q");
      const kindParam = params.get("kind");
      const kind: Kind = kindParam === "task" || kindParam === "probe" || kindParam === "followup" ? kindParam : null;
      let question = q;
      if (!question && session && kind) question = presetTurns(session).find((t) => t.kind === kind)?.question ?? null;
      if (question) setText(question);
      if (session && question && params.get("run") === "1") {
        setText("");
        const qs = new URLSearchParams(params.toString());
        qs.delete("run"); // a refresh should not re-run the question
        router.replace(`/?${qs.toString()}`, { scroll: false });
        await ask(question, kind, session);
      }
    })();
  }, [params, create, ask, router]);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [state?.transcript.length]);

  const demo: DemoQuery[] = useMemo(() => options.demo_queries[state?.settings.task ?? draft.task] ?? [], [options, state, draft.task]);
  const dirty = state ? !same(state.settings, draft) : false;

  const newSession = async () => {
    const qs = new URLSearchParams({
      arm: draft.arm,
      task: draft.task,
      size: draft.size,
      window: String(draft.window),
      model: draft.model,
      seed: String(draft.seed),
      fallback: draft.fallback ? "1" : "0",
    });
    router.replace(`/?${qs.toString()}`, { scroll: false });
    await create(draft);
  };

  const send = async (question: string, kind: Kind) => {
    if (!question.trim() || running) return;
    setText("");
    await ask(question, kind);
  };

  const askedTask = state?.transcript.some((m) => m.role === "user");

  return (
    <div className="grid gap-4 lg:grid-cols-[270px_minmax(0,1fr)] xl:grid-cols-[270px_minmax(0,1fr)_430px]">
      <aside className="space-y-4">
        <Card title="Session settings">
          <SettingsForm value={draft} onChange={setDraft} options={options} disabled={running} />
          <Button className="mt-4 w-full" onClick={newSession} disabled={running || creating}>
            {creating ? <Spinner /> : null} New session
          </Button>
          {dirty && <p className="mt-2 text-xs text-amber-700 dark:text-amber-400">Settings changed: start a new session to apply them.</p>}
        </Card>
        {state && (
          <Card title="Sandbox files">
            <div className="max-h-72 space-y-0.5 overflow-auto text-xs scroll-thin">
              {state.sandbox.file_list.map((f) => (
                <div key={f.path} className="flex justify-between gap-2 font-mono">
                  <span className="truncate">{f.path}</span>
                  <span className="shrink-0 tabular-nums text-muted">{fmt(f.tokens)}</span>
                </div>
              ))}
            </div>
          </Card>
        )}
        <p className="px-1 text-xs text-muted">
          Free tier: about 8K tokens per minute, which also caps a single request. The server waits out limits and shows a countdown.
        </p>
      </aside>

      <section className="flex min-h-[70vh] min-w-0 flex-col gap-3">
        {optionsError && <Banner tone="bad">API unreachable: {optionsError}</Banner>}
        {state && (
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <Pill tone={state.settings.arm === "compact" ? "info" : "neutral"}>{state.settings.arm}</Pill>
            <span className="font-mono text-xs">{state.settings.model}</span>
            <span className="text-muted">·</span>
            <span>
              <span className="font-medium">{state.sandbox.task}</span> · {state.sandbox.files} files · ~{fmt(state.sandbox.corpus_tokens)} tokens of content
            </span>
            {state.fake_llm && <Pill tone="warn">offline demo model</Pill>}
            {state.grades.map((g, i) => (
              <ScorePill key={i} score={g.score} label={g.kind} />
            ))}
          </div>
        )}
        {runner.error && (
          <Banner tone="bad">
            <div className="font-semibold">{runner.error.status === "request_too_large" ? "Request too large: the agent hit the provider's context ceiling" : runner.error.status}</div>
            <div className="mt-0.5 break-words text-xs">{cleanError(runner.error.status, runner.error.message)}</div>
          </Banner>
        )}
        <WaitBanner wait={runner.wait} />

        <div ref={scroller} className="min-h-[300px] flex-1 overflow-auto rounded-xl border border-border bg-card p-4 scroll-thin lg:max-h-[calc(100vh-330px)]">
          {!state && (creating ? <Empty><Spinner /> Creating session…</Empty> : <Empty>No session. Check that the API server is running, then press New session.</Empty>)}
          {state && state.transcript.length === 0 && (
            <Empty>
              Ask the task question to watch the agent work. With the <b>compact</b> agent you will see folds appear as the window fills.
            </Empty>
          )}
          {state && <Transcript items={state.transcript} />}
        </div>

        <EventLog lines={runner.log} running={running} />

        <div className="rounded-xl border border-border bg-card p-3">
          <div className="mb-2 flex flex-wrap gap-2">
            {state &&
              presetTurns(state).map((t, i) => (
                <Button
                  key={i}
                  name={`preset-${t.kind}`}
                  variant={t.kind === "task" ? "primary" : "secondary"}
                  title={t.question}
                  disabled={running || (t.kind !== "task" && !askedTask)}
                  onClick={() => send(t.question, t.kind)}
                >
                  <span className="text-[10px] uppercase tracking-wide opacity-75">{t.kind}</span> {t.label}
                </Button>
              ))}
            {demo.length > 0 && (
              <select
                className={`${inputClass} w-auto max-w-xs`}
                disabled={!state || running}
                value=""
                onChange={(e) => {
                  const q = demo[Number(e.target.value)];
                  if (q) send(q.question, q.kind);
                }}
              >
                <option value="">Demo queries…</option>
                {demo.map((q, i) => (
                  <option key={i} value={i}>
                    {q.label}
                  </option>
                ))}
              </select>
            )}
            {running && (
              <Button variant="danger" onClick={runner.stop} className="ml-auto">
                Stop
              </Button>
            )}
          </div>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              send(text, null);
            }}
          >
            <textarea
              className={`${inputClass} min-h-[44px] flex-1 resize-y`}
              rows={2}
              placeholder="Ask anything about the sandbox…"
              value={text}
              disabled={!state || running}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                  e.preventDefault();
                  send(text, null);
                }
              }}
            />
            <Button type="submit" disabled={!state || running || !text.trim()}>
              {running ? <Spinner /> : "Send"}
            </Button>
          </form>
          {state && (
            <Details className="mt-2" summary={<span className="text-xs text-muted">Preset questions and expected answers (spoilers)</span>}>
              <div className="space-y-2 text-xs">
                <ol className="list-decimal space-y-1.5 pl-4">
                  {presetTurns(state).map((t, i) => (
                    <li key={i}>
                      <span className="font-medium">{t.label}</span> <Pill>{t.kind}</Pill>
                      <div className="mt-0.5 whitespace-pre-wrap text-muted">{t.question}</div>
                    </li>
                  ))}
                </ol>
                <Pre>{JSON.stringify(state.expected, null, 2)}</Pre>
              </div>
            </Details>
          )}
        </div>
      </section>

      <aside className="min-w-0 lg:col-span-2 xl:col-span-1">
        <Inspector state={state} />
      </aside>
    </div>
  );
}
