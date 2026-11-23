export const fmt = (n: number | null | undefined): string =>
  n === null || n === undefined || Number.isNaN(n) ? "–" : Math.round(n).toLocaleString("en-US");

export const kfmt = (n: number): string => (Math.abs(n) >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${Math.round(n)}`);

export const pct = (part: number, whole: number): number => (whole > 0 ? Math.min(100, (100 * part) / whole) : 0);


export function shortArgs(args: Record<string, unknown>, max = 80): string {
  const s = Object.entries(args)
    .filter(([k]) => k !== "summary")
    .map(([k, v]) => `${k}=${typeof v === "string" ? JSON.stringify(v) : JSON.stringify(v)}`)
    .join(", ");
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

export function statusTone(status: string): "ok" | "bad" | "warn" {
  if (status === "ok") return "ok";
  if (status === "rate_limited" || status === "step_limit") return "warn";
  return "bad";
}

/** "38% fewer" / "12% more": a relative to b. */
export function delta(a: number, b: number): string {
  if (!b) return "–";
  const d = Math.round(100 * (1 - a / b));
  return d >= 0 ? `${d}% fewer` : `${-d}% more`;
}

/** One sentence comparing a compact run with a baseline run (handles a baseline that died early). */
export function compareSentence(
  b: { status: string; calls: number; total: number; peak: number },
  c: { status: string; calls: number; total: number; peak: number },
): string {
  if (b.status !== "ok" && c.status === "ok") {
    return `The plain agent stopped after ${b.calls} calls (${b.status}) at a ${fmt(b.peak)}-token request; the compact agent finished in ${c.calls} calls and never sent more than ${fmt(c.peak)} tokens (${delta(c.peak, b.peak).replace("fewer", "smaller").replace("more", "larger")} than the plain agent's peak). Totals aren't comparable because the plain agent did not finish.`;
  }
  return `Compact used ${delta(c.total, b.total)} input tokens in total (${fmt(c.total)} vs ${fmt(b.total)}), and its peak request was ${delta(c.peak, b.peak).replace("fewer", "smaller").replace("more", "larger")} (${fmt(c.peak)} vs ${fmt(b.peak)}).`;
}

/** Drop a repeated "status: " prefix the server may already include in the message. */
export function cleanError(status: string, message: string): string {
  let m = message;
  while (m.startsWith(`${status}: `)) m = m.slice(status.length + 2);
  return m;
}
