# Context-compact API (FastAPI, `server/app.py`)

Base URL: `http://localhost:8000` (the web app reads `NEXT_PUBLIC_API_URL`). All bodies are JSON.
CORS allows `http://localhost:3000`.

Set `CC_FAKE_LLM=1` when starting the server to use a scripted offline demo model (no API key,
no quota). It reads files one by one, folds when nudged, and answers the preset questions from the
answer key, so the whole UI can be developed and demoed offline. It enforces a simulated 8K request
limit, so a baseline session on a medium sandbox ends with a `request_too_large` error, like the
real free tier. In this mode `/api/options.models` is `["demo/offline-scripted"]`.

## Types

```ts
type Settings = {
  model: string;            // e.g. "openai/gpt-oss-120b"
  arm: "compact" | "baseline";
  window: number;           // compact token budget, e.g. 6000
  task: "service_audit" | "incident_trace";
  size: "small" | "medium" | "large";
  seed: number;
  fallback: boolean;        // fall back to other free models when a daily quota is exhausted
};

type ToolCall = { name: string; args: Record<string, unknown> };

type TranscriptItem = {
  ref: string;                       // "m0007"
  role: "user" | "assistant" | "tool";
  text: string;                      // tool text is truncated to 4000 chars
  tool_calls: ToolCall[];            // assistant only
  tool_name: string | null;          // tool only
  tokens: number;
  folded_into: string | null;        // block id if this message is currently folded
};

type Fold = {
  id: string; tier: number; span: string; topic: string; summary: string;
  auto: boolean; active: boolean; children: string[];
  replaced_tokens: number; summary_tokens: number; original_tokens: number; created_at_call: number;
};

type Call = {
  index: number; input_tokens: number | null; output_tokens: number | null; model: string; messages_sent: number;
  nudged: boolean; forced: boolean; auto_folds: string[]; tool_calls: ToolCall[]; latency_s: number;
};

type ViewMessage = { role: "user" | "assistant" | "tool"; text: string; tool_calls: ToolCall[] };

type SessionState = {
  id: string;
  settings: Settings;
  fake_llm: boolean;
  sandbox: { task: string; files: number; corpus_tokens: number; prompt: string; probe: string;
             file_list: { path: string; tokens: number }[];
             turns: { kind: "task" | "probe" | "followup"; question: string; label: string }[] };  // graded presets
  transcript: TranscriptItem[];
  calls: Call[];
  folds: Fold[];                       // [] for baseline
  stats: Record<string, number> | null;  // compactor stats (folds_model, folds_auto, max_tier,
                                          // tokens_replaced, summary_tokens, nudges, forced,
                                          // recalls, searches, ...) or null for baseline
  window: number | null;               // compact window, null for baseline
  limit: number;                       // provider per-request ceiling for this model (8000; 7000 for Qwen)
  last_view: { system: string; messages: ViewMessage[]; estimate: number;
               nudged: boolean; force_fold: boolean } | null;
  grades: { kind: "task" | "probe" | "followup"; score: number; detail: Record<string, unknown> }[];
  expected: { task: unknown; probe: unknown };
  busy: boolean;
  error: string | null;
};
```

## Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| GET | `/api/health` | | `{ ok: true, has_key: boolean, fake_llm: boolean }` |
| GET | `/api/options` | | `{ models: string[], arms: string[], tasks: string[], sizes: string[], defaults: Settings, demo_queries: Record<task, {label: string, question: string, kind: "task" \| "probe" \| null}[]> }` |
| POST | `/api/sessions` | `Partial<Settings>` | `SessionState` |
| GET | `/api/sessions/{id}` | | `SessionState` |
| POST | `/api/sessions/{id}/ask` | `{ question: string, kind: "task" \| "probe" \| "followup" \| null }` | **Server-sent events** (see below); 409 if the session is busy |
| GET | `/api/results` | | `{ id: string, meta: object, summary: RunSummary[] }[]`, newest first |
| GET | `/api/results/{id}` | | the run's `runs.json`: `{ meta, runs: Run[] }` |
| GET | `/api/golden` | | `{ dataset: GoldenDataset, evals: { id: string, meta: object, summary: RunSummary[] }[] }` |

`RunSummary = { session, task, arm, status, score, probe_score, mean_score, turn_scores: number[], model_calls, input_tokens_total, input_tokens_peak, output_tokens_total, folds_model, folds_auto }`.
`session` is the golden case id or the task name; group runs by it.
A `Run` additionally has `per_call_input: number[]`, `answer`, `probe_answer`, `score_detail`,
`probe_detail`, `error`, `failed_in`, `folds: Fold[]`, `calls: Call[]`, `compaction` (stats),
`models_used`, `rate_limit_wait_s`, `mean_score` and `turns: { kind, question, answer, score, detail, calls, skipped? }[]`.

`GoldenDataset = { version: string, description: string, cases: { id, family, seed, size, files_sha256, turns: { kind, question, expected }[] }[] }`.

## Streaming `/ask`

`POST /api/sessions/{id}/ask` responds with `text/event-stream`. Every event is one line
`data: <json>\n\n`. Read it with `fetch` plus a `ReadableStream` reader, not `EventSource`, because
the request is a POST.

```ts
type Event =
  | { type: "status"; message: string }
  | { type: "model_call"; call: Call }                   // after each model response
  | { type: "tool_result"; ref: string; name: string; tokens: number; preview: string }
  | { type: "fold"; fold: Fold }                          // a new fold (model or auto)
  | { type: "rate_limit"; kind: "tpm" | "tpd" | "rpm" | "rpd"; wait_s: number; model: string }
  | { type: "fallback"; from: string; to: string; reason: string }
  | { type: "final"; text: string; grade: { score: number; detail: object } | null }
  | { type: "error"; status: string; message: string }    // status e.g. "request_too_large"; message is the error text
  | { type: "state"; state: SessionState }                // full state, sent last
  | { type: "done" };
```
