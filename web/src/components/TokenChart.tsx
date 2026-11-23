"use client";

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { fmt, kfmt } from "@/lib/format";

export const SERIES_COLORS: Record<string, string> = {
  baseline: "#71717a",
  compact: "#2563eb",
  "input tokens": "#2563eb",
};

export type Series = { name: string; data: (number | null)[]; color?: string };
export type RefLine = { y: number; label: string; color: string };

export default function TokenChart({ series, refs = [], height = 220 }: {
  series: Series[];
  refs?: RefLine[];
  height?: number;
}) {
  const n = Math.max(0, ...series.map((s) => s.data.length));
  const rows = Array.from({ length: n }, (_, i) => {
    const row: Record<string, number | null> = { call: i + 1 };
    for (const s of series) row[s.name] = s.data[i] ?? null;
    return row;
  });
  const top = Math.max(1000, ...refs.map((r) => r.y), ...series.flatMap((s) => s.data.map((v) => v ?? 0))) * 1.1;

  if (!n) {
    return (
      <div className="grid place-items-center rounded-lg border border-dashed border-border text-xs text-muted" style={{ height }}>
        No model calls yet
      </div>
    );
  }
  return (
    <div style={{ height }} className="w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" />
          <XAxis dataKey="call" tick={{ fontSize: 11, fill: "var(--muted)" }} stroke="var(--border)" allowDecimals={false} />
          <YAxis
            tickFormatter={kfmt}
            domain={[0, Math.ceil(top / 1000) * 1000]}
            tick={{ fontSize: 11, fill: "var(--muted)" }}
            stroke="var(--border)"
            width={44}
          />
          <Tooltip
            formatter={(v) => [fmt(Number(v)), undefined]}
            labelFormatter={(l) => `model call ${l}`}
            contentStyle={{
              background: "var(--card)",
              border: "1px solid var(--border)",
              borderRadius: 8,
              fontSize: 12,
              color: "var(--foreground)",
            }}
          />
          {series.length > 1 && <Legend wrapperStyle={{ fontSize: 12 }} />}
          {refs.map((r) => (
            <ReferenceLine
              key={r.label}
              y={r.y}
              stroke={r.color}
              strokeDasharray="6 4"
              label={{ value: r.label, position: "insideTopRight", fontSize: 11, fill: r.color }}
            />
          ))}
          {series.map((s) => (
            <Line
              key={s.name}
              type="monotone"
              dataKey={s.name}
              stroke={s.color || SERIES_COLORS[s.name] || "#2563eb"}
              strokeWidth={2}
              dot={{ r: 2.5 }}
              isAnimationActive={false}
              connectNulls
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
