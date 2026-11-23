// Typed client for the Context-compact FastAPI server (see server/API.md).

export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export type Arm = "compact" | "baseline";
export type Size = "small" | "medium" | "large";
export type Kind = "task" | "probe" | "followup" | null;
export type TurnKind = "task" | "probe" | "followup";
export type PresetTurn = { kind: TurnKind; question: string; label: string };

export type Settings = {
  model: string;
  arm: Arm;
  window: number;
  task: string;
  size: Size;
  seed: number;
  fallback: boolean;
};

export type ToolCall = { name: string; args: Record<string, unknown> };

export type TranscriptItem = {
  ref: string;
  role: "user" | "assistant" | "tool";
  text: string;
  tool_calls: ToolCall[];
  tool_name: string | null;
  tokens: number;
  folded_into: string | null;
};

export type Fold = {
  id: string;
  tier: number;
  span: string;
  topic: string;
  summary: string;
  auto: boolean;
  active: boolean;
  children: string[];
  replaced_tokens: number;
  summary_tokens: number;
  original_tokens: number;
  created_at_call: number;
};

export type Call = {
  index: number;
  input_tokens: number | null;
  output_tokens: number | null;
  model: string;
  nudged: boolean;
  forced: boolean;
  auto_folds: string[];
  tool_calls: ToolCall[];
  latency_s: number;
};

export type ViewMessage = { role: "user" | "assistant" | "tool"; text: string; tool_calls: ToolCall[] };

export type Grade = { kind: TurnKind | "golden"; score: number; detail: Record<string, unknown> };

export type SessionState = {
  id: string;
  settings: Settings;
  fake_llm: boolean;
  sandbox: {
    task: string;
    files: number;
    corpus_tokens: number;
    prompt: string;
    probe: string;
    file_list: { path: string; tokens: number }[];
    turns?: PresetTurn[];
  };
  transcript: TranscriptItem[];
  calls: Call[];
  folds: Fold[];
  stats: Record<string, number> | null;
  window: number | null;
  limit: number;
  last_view: {
    system: string;
    messages: ViewMessage[];
    estimate: number;
    nudged: boolean;
    force_fold: boolean;
  } | null;
  grades: Grade[];
  expected: { task: unknown; probe: unknown };
  busy: boolean;
  error: string | null;
};

export type DemoQuery = { label: string; question: string; kind: Kind };

export type Options = {
  models: string[];
  arms: string[];
  tasks: string[];
  sizes: string[];
  defaults: Settings;
  demo_queries: Record<string, DemoQuery[]>;
};

export type Health = { ok: boolean; has_key: boolean; fake_llm: boolean };

export type RunSummary = {
  session?: string;
  mean_score?: number;
  turn_scores?: number[];
  task: string;
  arm: string;
  status: string;
  score: number;
  probe_score: number;
  model_calls: number;
  input_tokens_total: number;
  input_tokens_peak: number;
  output_tokens_total: number;
  folds_model: number;
  folds_auto: number;
};

export type Turn = {
  kind: string;
  question: string;
  answer: string | null;
  score: number;
  detail: Record<string, unknown>;
  calls?: number;
  skipped?: boolean;
};

export type Run = RunSummary & {
  per_call_input: number[];
  answer: string | null;
  probe_answer: string | null;
  score_detail: Record<string, unknown>;
  probe_detail: Record<string, unknown>;
  error: string | null;
  folds?: Fold[];
  calls?: Call[];
  compaction?: Record<string, number> | null;
  turns?: Turn[];
  case_id?: string;
  failed_in?: string | null;
  models_used?: string[];
  rate_limit_wait_s?: number;
  wall_s?: number;
  re_reads?: number;
};

export type RunMeta = {
  model?: string;
  provider?: string;
  size?: string;
  seed?: number;
  window?: number;
  baseline_window?: number | null;
  date?: string;
  golden?: string;
  [k: string]: unknown;
};

export type ResultListItem = { id: string; meta: RunMeta; summary: RunSummary[] };
export type ResultDetail = { meta: RunMeta; runs: Run[] };

export type GoldenCase = {
  id: string;
  family: string;
  seed: number;
  size: string;
  files_sha256: string;
  turns: { kind: string; question: string; expected: unknown }[];
};

export type GoldenDataset = { version: string; description: string; cases: GoldenCase[] };
export type GoldenResponse = {
  dataset: GoldenDataset;
  evals: { id: string; meta: RunMeta; summary: RunSummary[] | Record<string, unknown> }[]; // server sends RunSummary[]
};

export type StreamEvent =
  | { type: "status"; message: string }
  | { type: "model_call"; call: Call }
  | { type: "tool_result"; ref: string; name: string; tokens: number; preview: string }
  | { type: "fold"; fold: Fold }
  | { type: "rate_limit"; kind: "tpm" | "tpd" | "rpm" | "rpd"; wait_s: number; model: string }
  | { type: "fallback"; from: string; to: string; reason: string }
  | { type: "final"; text: string; grade: { score: number; detail: Record<string, unknown> } | null }
  | { type: "error"; status: string; message: string }
  | { type: "state"; state: SessionState }
  | { type: "done" };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the server running?`);
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = typeof body?.detail === "string" ? body.detail : JSON.stringify(body?.detail ?? body);
    } catch {
      /* keep status text */
    }
    throw new ApiError(res.status, msg);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),
  options: () => request<Options>("/api/options"),
  createSession: (settings: Partial<Settings>) =>
    request<SessionState>("/api/sessions", { method: "POST", body: JSON.stringify(settings) }),
  getSession: (id: string) => request<SessionState>(`/api/sessions/${encodeURIComponent(id)}`),
  results: () => request<ResultListItem[]>("/api/results"),
  result: (id: string) => request<ResultDetail>(`/api/results/${encodeURIComponent(id)}`),
  golden: () => request<GoldenResponse>("/api/golden"),
};

/**
 * POST a question and stream server-sent events. EventSource can't POST, so this reads the
 * response body manually and parses `data: {...}` frames separated by blank lines.
 */
export async function askStream(
  sessionId: string,
  question: string,
  kind: Kind,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/sessions/${encodeURIComponent(sessionId)}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify({ question, kind }),
      signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") return;
    throw new ApiError(0, `Cannot reach the API at ${API_URL}. Is the server running?`);
  }
  if (!res.ok || !res.body) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      msg = typeof body?.detail === "string" ? body.detail : msg;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const flush = (frame: string) => {
    const data = frame
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).replace(/^ /, ""))
      .join("\n");
    if (!data) return;
    try {
      onEvent(JSON.parse(data) as StreamEvent);
    } catch {
      /* ignore malformed frame */
    }
  };
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        flush(buffer.slice(0, idx));
        buffer = buffer.slice(idx + 2);
      }
    }
    buffer += decoder.decode();
    if (buffer.trim()) flush(buffer);
  } catch (e) {
    if ((e as Error).name !== "AbortError") throw e;
  }
}

/** Group key for runs: golden case id, else task name (server field `session`). */
export function sessionKey(r: { session?: string; case_id?: string; task: string }): string {
  return r.session || r.case_id || r.task;
}
