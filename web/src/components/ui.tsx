import type { ReactNode } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export function Card({ title, actions, children, className }: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-border bg-card shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-2 border-b border-border px-4 py-2.5">
          <h2 className="text-sm font-semibold">{title}</h2>
          {actions}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const TONES = {
  neutral: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  bad: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-300",
  warn: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  info: "bg-blue-100 text-blue-800 dark:bg-blue-950 dark:text-blue-300",
  fold: "bg-violet-100 text-violet-800 dark:bg-violet-950 dark:text-violet-300",
} as const;
export type Tone = keyof typeof TONES;

export function Pill({ tone = "neutral", children, className, title }: {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  title?: string;
}) {
  return (
    <span title={title} className={cx("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium", TONES[tone], className)}>
      {children}
    </span>
  );
}

export function Metric({ label, value, hint }: { label: string; value: ReactNode; hint?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2" title={hint}>
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className="mt-0.5 text-base font-semibold tabular-nums">{value}</div>
    </div>
  );
}

export function Button({ children, onClick, disabled, variant = "primary", className, type = "button", title, name }: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  variant?: "primary" | "secondary" | "ghost" | "danger";
  className?: string;
  type?: "button" | "submit";
  title?: string;
  name?: string;
}) {
  const styles = {
    primary: "bg-accent text-white hover:opacity-90 dark:text-zinc-950",
    secondary: "border border-border bg-card hover:bg-zinc-50 dark:hover:bg-zinc-900",
    ghost: "hover:bg-zinc-100 dark:hover:bg-zinc-900",
    danger: "bg-red-600 text-white hover:bg-red-700",
  }[variant];
  return (
    <button
      type={type}
      title={title}
      name={name}
      onClick={onClick}
      disabled={disabled}
      className={cx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        styles,
        className,
      )}
    >
      {children}
    </button>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs font-medium text-muted">{label}</span>
      {children}
      {hint && <span className="mt-1 block text-[11px] text-muted">{hint}</span>}
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-border bg-card px-2.5 py-1.5 text-sm outline-none focus:ring-2 focus:ring-accent/40";

export function Banner({ tone, children }: { tone: "bad" | "warn" | "info" | "ok"; children: ReactNode }) {
  const styles = {
    bad: "border-red-300 bg-red-50 text-red-900 dark:border-red-900 dark:bg-red-950/60 dark:text-red-200",
    warn: "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-900 dark:bg-amber-950/60 dark:text-amber-200",
    info: "border-blue-300 bg-blue-50 text-blue-900 dark:border-blue-900 dark:bg-blue-950/60 dark:text-blue-200",
    ok: "border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/60 dark:text-emerald-200",
  }[tone];
  return <div className={cx("rounded-lg border px-3 py-2 text-sm", styles)}>{children}</div>;
}

export function Details({ summary, children, defaultOpen, className }: {
  summary: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  return (
    <details open={defaultOpen} className={cx("group rounded-lg border border-border", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-sm select-none">
        <span className="text-muted transition group-open:rotate-90">▸</span>
        <span className="min-w-0 flex-1">{summary}</span>
      </summary>
      <div className="border-t border-border px-3 py-2">{children}</div>
    </details>
  );
}

export function Pre({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <pre className={cx("max-h-96 overflow-auto rounded-md bg-zinc-50 p-2 font-mono text-xs leading-relaxed scroll-thin dark:bg-zinc-900", className)}>
      {children}
    </pre>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="rounded-lg border border-dashed border-border p-6 text-center text-sm text-muted">{children}</div>;
}

export function Spinner() {
  return <span className="inline-block h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-r-transparent" aria-hidden />;
}

export function ScorePill({ score, label }: { score: number; label?: string }) {
  const tone: Tone = score >= 0.999 ? "ok" : score > 0 ? "warn" : "bad";
  return (
    <Pill tone={tone}>
      {label ? `${label} ` : ""}
      {score.toFixed(2)}
    </Pill>
  );
}
