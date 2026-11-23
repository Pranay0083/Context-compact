# Presenting Context-compact

A 10–15 minute demo. It works **offline** (scripted demo model: no key, no quota, same 8K wall)
or **live** on Groq's free tier. Rehearse offline; present live if you have quota left today.

## Setup (2 terminals)

```bash
# 1. API (pick one)
CC_FAKE_LLM=1 .venv/bin/uvicorn server.app:app --port 8000   # offline demo model
.venv/bin/uvicorn server.app:app --port 8000                 # live Groq (needs GROQ_API_KEY)

# 2. Web UI
cd web && npm install && npm run dev                          # http://localhost:3000
```

Live budget: each free model has roughly **200K tokens per day** and an **8K per-request ceiling**
(7K for Qwen). A medium task costs about 30–80K tokens per agent. Plan two or three live runs,
and keep the offline server ready as a backup.

## The flow

The `/demo` page walks through these steps with one-click presets.

### 1. The problem (2 min): the plain agent hits the wall

Playground → agent **baseline**, sandbox **service_audit**, size **medium** → **Ask task question**.

Say: *"The plain agent re-sends its whole history every step. Watch the gauge: each file it
reads adds about 900 tokens. Groq's free tier refuses any request over 8K."* It ends with a red
**request_too_large** banner after about 8 files of 12. Live, we measured 7,557 tokens at the
moment it failed.

### 2. The fix (3 min): the same agent with the compactor

Switch the agent to **compact** (window 6000) → **Ask task question**.

Point at, as they appear:
- the **gauge** staying under the red window line, with the chart going up and down instead
  of climbing;
- **fold cards**: the model calls `compress_context` and writes its own summary, keeping the
  exact numbers (owner, port, timeout_ms…);
- transcript messages turning grey, marked *"folded into b002"*;
- **"What the model saw last call"**: the folded range replaced in place by a short
  `[Notes on m0004-m0011 · fold b002 …]` message.

It finishes with a green grade pill (score 1.00 when the answer is right).

### 3. Nothing is lost (2 min): recall a folded detail

Same session → **Ask follow-up probe** (e.g. *"what port does analytics listen on…"*).

Say: *"That file was folded twenty steps ago. The agent searches its archive
(`search_context`) or recalls the original (`recall_context`), instead of re-reading files."*
Then ask the two follow-ups (busiest team, region filter). All are graded.

### 4. It is a different task, not a trick (2 min)

Sandbox **incident_trace**, compact → **Ask task question**. A multi-hop root cause across 8
noisy logs: gateway 502 → request id → payments trace → ledger txn → table lock → scheduler
job. Each hop's id has to survive folding. Then try the free-form demo queries:

- *Summarise the incident as a timeline with exact timestamps, one line per hop.*
- *Which services were involved in the failed checkout, in the order the request reached them?*
- *Was anything else failing in the same time window?*
- *Based on the logs, what would you change to stop this incident from recurring?*

### 5. The numbers (2 min): Results and Golden pages

**Results**: per-call token charts for both agents against the window and the 8K limit; total
and peak tokens; folds made by the model and by the auto-fold; the fold log with every summary.
**Golden**: 6 versioned cases × 4 graded turns (24 questions); score per turn for each agent.

### 6. Where to use it (1 min)

Any agent whose history grows with tool output (see *Where to use it* in the README, and
`examples/`): coding assistants over a repo, log and incident triage, research over many
documents, long support chats, and running capable agents on free or small-context models.

## More queries to try

**service_audit** (the preset buttons are graded; these are free-form)
- Which services depend on auth, and who owns each of them?
- Give me one line per service: owner, region and timeout_ms.
- Which region has the most services, and what is their total max_connections?
- Are any services configured with 0 retries? List them with their owners.

**incident_trace**
- What exactly failed for the customer's order, and what was the root cause?
- How long did the blocking job hold its lock, and what was its pid?
- A different checkout failed with HTTP 402 just before. Which order, and why?

## If something goes wrong live

- **Rate-limit countdown in the event log.** That's the free tier's per-minute budget; the agent
  waits and continues on its own. Keep talking: it is part of the story.
- **`daily_quota` error.** Restart the API with `CC_FAKE_LLM=1` and continue offline, or enable
  *fallback* in settings to move to another free model.
- **The answer scores below 1.00.** That's a real model making a real mistake. Open the fold log
  to show whether a summary dropped a value, which is exactly what the golden eval measures.
