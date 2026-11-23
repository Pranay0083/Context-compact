"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, askStream, type Kind, type SessionState, type Settings, type StreamEvent } from "@/lib/api";
import { cleanError, fmt, shortArgs } from "@/lib/format";

export type LogLine = { id: number; at: number; tone: "info" | "fold" | "warn" | "bad" | "ok" | "muted"; text: string };
export type Wait = { until: number; kind: string; model: string };
export type RunOutcome = { state: SessionState | null; error: { status: string; message: string } | null; final: string | null };

let lineId = 0;

export function useSessionRunner() {
  const [state, setState] = useState<SessionState | null>(null);
  const [log, setLog] = useState<LogLine[]>([]);
  const [running, setRunning] = useState(false);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<{ status: string; message: string } | null>(null);
  const [wait, setWait] = useState<Wait | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const stateRef = useRef<SessionState | null>(null);
  const refetchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (refetchTimer.current) clearTimeout(refetchTimer.current);
    },
    [],
  );

  const push = useCallback((tone: LogLine["tone"], text: string) => {
    setLog((l) => [...l.slice(-300), { id: ++lineId, at: Date.now(), tone, text }]);
  }, []);

  const create = useCallback(
    async (settings: Partial<Settings>): Promise<SessionState | null> => {
      abortRef.current?.abort();
      setCreating(true);
      setError(null);
      setWait(null);
      setLog([]);
      try {
        const s = await api.createSession(settings);
        setState(s);
        stateRef.current = s;
        return s;
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : String(e);
        setError({ status: "session_error", message: msg });
        return null;
      } finally {
        setCreating(false);
      }
    },
    [],
  );

  // Live transcript: refetch the session (throttled) while a turn is streaming.
  const scheduleRefetch = useCallback((id: string) => {
    if (refetchTimer.current) return;
    refetchTimer.current = setTimeout(async () => {
      refetchTimer.current = null;
      try {
        const s = await api.getSession(id);
        setState((prev) => (prev && prev.id === id ? { ...s, calls: s.calls.length >= prev.calls.length ? s.calls : prev.calls } : prev));
      } catch {
        /* non-fatal */
      }
    }, 600);
  }, []);

  const ask = useCallback(
    async (question: string, kind: Kind, sessionOverride?: SessionState): Promise<RunOutcome> => {
      const session = sessionOverride || stateRef.current;
      const outcome: RunOutcome = { state: session, error: null, final: null };
      if (!session || !question.trim()) return outcome;
      const ctrl = new AbortController();
      abortRef.current = ctrl;
      setRunning(true);
      setError(null);
      push("info", `› ${kind ? `[${kind}] ` : ""}${question.length > 140 ? question.slice(0, 139) + "…" : question}`);

      const onEvent = (ev: StreamEvent) => {
        switch (ev.type) {
          case "status":
            push("muted", ev.message);
            break;
          case "model_call": {
            const c = ev.call;
            const names = c.tool_calls.map((t) => (t.name === "read_file" ? `read_file(${shortArgs(t.args, 50)})` : t.name)).join(", ") || "final answer";
            const flags = [c.nudged && "nudged", c.forced && "forced fold", c.auto_folds.length && `auto-fold ${c.auto_folds.join(",")}`]
              .filter(Boolean)
              .join(" · ");
            push(c.auto_folds.length ? "warn" : "info", `call ${c.index}: in ${fmt(c.input_tokens)} · out ${fmt(c.output_tokens)} → ${names}${flags ? ` · ${flags}` : ""}`);
            setWait(null);
            setState((prev) => (prev ? { ...prev, calls: [...prev.calls.filter((x) => x.index !== c.index), c].sort((a, b) => a.index - b.index) } : prev));
            scheduleRefetch(session.id);
            break;
          }
          case "tool_result":
            push("muted", `${ev.ref} ${ev.name} returned ~${fmt(ev.tokens)} tokens`);
            break;
          case "fold":
            push("fold", `⇲ ${ev.fold.auto ? "auto-fold" : "fold"} ${ev.fold.id} (tier ${ev.fold.tier}) ${ev.fold.span}: ${fmt(ev.fold.replaced_tokens)} → ${fmt(ev.fold.summary_tokens)} tokens`);
            setState((prev) => (prev ? { ...prev, folds: [...prev.folds.filter((f) => f.id !== ev.fold.id), ev.fold] } : prev));
            scheduleRefetch(session.id);
            break;
          case "rate_limit":
            push("warn", `rate limit (${ev.kind.toUpperCase()}) on ${ev.model}: waiting ${Math.round(ev.wait_s)}s`);
            setWait({ until: Date.now() + ev.wait_s * 1000, kind: ev.kind, model: ev.model });
            break;
          case "fallback":
            push("warn", `fallback ${ev.from} → ${ev.to} (${ev.reason})`);
            break;
          case "final":
            outcome.final = ev.text;
            push(ev.grade ? (ev.grade.score >= 0.999 ? "ok" : "warn") : "ok", ev.grade ? `final answer · score ${ev.grade.score.toFixed(2)}` : "final answer");
            break;
          case "error":
            outcome.error = { status: ev.status, message: ev.message };
            setError({ status: ev.status, message: ev.message });
            push("bad", `${ev.status}: ${cleanError(ev.status, ev.message)}`);
            break;
          case "state":
            outcome.state = ev.state;
            setState(ev.state);
            stateRef.current = ev.state;
            break;
          case "done":
            break;
        }
      };

      try {
        await askStream(session.id, question, kind, onEvent, ctrl.signal);
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : String(e);
        const busy = e instanceof ApiError && e.status === 409;
        outcome.error = { status: busy ? "busy" : "stream_error", message: busy ? `This session is still working on a previous question. ${msg}` : msg };
        setError(outcome.error);
        push("bad", outcome.error.message);
      } finally {
        setRunning(false);
        setWait(null);
        if (refetchTimer.current) {
          clearTimeout(refetchTimer.current);
          refetchTimer.current = null;
        }
        if (!ctrl.signal.aborted) {
          try {
            const s = await api.getSession(session.id);
            setState(s);
            stateRef.current = s;
            outcome.state = s;
          } catch {
            /* keep last state */
          }
        }
      }
      return outcome;
    },
    [push, scheduleRefetch],
  );

  const stop = useCallback(() => {
    abortRef.current?.abort();
    push("warn", "stopped listening (the server may finish the current step)");
  }, [push]);

  return { state, log, running, creating, error, wait, create, ask, stop, setError };
}

export function useCountdown(until: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!until) return;
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, [until]);
  return until ? Math.max(0, Math.ceil((until - now) / 1000)) : 0;
}
