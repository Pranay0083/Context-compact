# Context-compact web UI

Next.js 15 (App Router) + TypeScript + Tailwind CSS + Recharts. It talks to the FastAPI server in
`../server` (contract: `../server/API.md`).

## Run

```bash
# 1. API (from the repo root). CC_FAKE_LLM=1 = scripted offline model: no key, no quota.
CC_FAKE_LLM=1 .venv/bin/uvicorn server.app:app --port 8000
#    or, for real Groq calls (needs GROQ_API_KEY in .env):
.venv/bin/uvicorn server.app:app --port 8000

# 2. Web
cd web
npm install
npm run dev            # http://localhost:3000
```

The API URL defaults to `http://localhost:8000`; override it with `NEXT_PUBLIC_API_URL`
(read at build/dev start).

## Pages

| Route | What it shows |
|---|---|
| `/` | **Playground**: pick model, agent (compact or baseline), window and sandbox, then ask the graded preset questions (task, probe, follow-ups), a demo query or anything else. Live event log (model calls, tool results, folds, rate-limit countdowns, fallbacks, errors such as `request_too_large`), transcript with fold cards and "folded into bXXX" badges, and an inspector with the context gauge, metrics, a tokens-per-call chart, the fold list, and the exact view the model saw on its last call. |
| `/compare` | Runs the baseline, then the compact agent (sequentially, to respect free-tier limits) on the same sandbox and questions, and compares status, scores, tokens and the per-call chart. |
| `/results` | Browses experiment runs under `results/`: table, per-session charts against the window and the 8K limit, answers, grading and fold logs. |
| `/golden` | The golden dataset's cases and per-turn scores of each golden eval. |
| `/demo` | A six-step presentation flow with buttons that open the other pages preset. |

The Playground accepts query params, so a link can preset and auto-run a demo:
`/?arm=compact&task=incident_trace&size=medium&window=6000&kind=task&run=1`
(`arm`, `task`, `size`, `window`, `model`, `seed`, `fallback`, `q` = question text, `kind` =
`task|probe|followup`, `run=1` = ask it immediately).
