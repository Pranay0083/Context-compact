"use client";

import { useEffect, useRef } from "react";
import type { LogLine, Wait } from "@/hooks/useSessionRunner";
import { useCountdown } from "@/hooks/useSessionRunner";
import { Banner, cx, Spinner } from "./ui";

const TONE: Record<LogLine["tone"], string> = {
  info: "text-foreground",
  fold: "text-fold",
  warn: "text-amber-700 dark:text-amber-400",
  bad: "text-red-700 dark:text-red-400",
  ok: "text-emerald-700 dark:text-emerald-400",
  muted: "text-muted",
};

export function WaitBanner({ wait }: { wait: Wait | null }) {
  const left = useCountdown(wait?.until ?? null);
  if (!wait) return null;
  const what = wait.kind === "tpd" || wait.kind === "rpd" ? "daily quota" : "per-minute limit";
  return (
    <Banner tone="warn">
      <span className="inline-flex items-center gap-2">
        <Spinner /> Waiting for the Groq {what} to reset on <span className="font-mono">{wait.model}</span>: {left}s left
      </span>
    </Banner>
  );
}

export default function EventLog({ lines, running, height = 160 }: { lines: LogLine[]; running: boolean; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight });
  }, [lines.length]);
  return (
    <div className="rounded-lg border border-border">
      <div className="flex items-center gap-2 border-b border-border px-3 py-1.5 text-xs font-medium text-muted">
        Live events {running && <Spinner />}
      </div>
      <div ref={ref} style={{ maxHeight: height }} className="overflow-auto px-3 py-1.5 font-mono text-[11.5px] leading-5 scroll-thin">
        {lines.length === 0 && <div className="text-muted">Events from the agent stream show up here.</div>}
        {lines.map((l) => (
          <div key={l.id} className={cx("whitespace-pre-wrap", TONE[l.tone])}>
            <span className="mr-2 text-muted">{new Date(l.at).toLocaleTimeString([], { hour12: false })}</span>
            {l.text}
          </div>
        ))}
      </div>
    </div>
  );
}
