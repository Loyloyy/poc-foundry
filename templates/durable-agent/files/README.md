# Durable agent (kill-and-resume workflow) — PoC

A minimal proof-of-concept: a Gradio chatbot that runs a multi-step workflow per task and **resumes from a
disk checkpoint if killed mid-run**, executing each step exactly once — durable execution with **no
database, no model, no sibling service** (the durable store is a local file).

Organised as **kit + glue** — `agentkit.py` ships the durability primitives as a library; the
resume/checkpoint logic is written in `core.generate_reply`.

- `agentkit.py` — `TASK_STEPS` (the workflow), `load_progress` (resume pointer), `append_ledger` (durable
  execution log), `checkpoint` (persists progress; the durability barrier).
- `core.py` — the editable glue: `generate_reply(task_id)` resumes from `load_progress` and runs the
  remaining steps, checkpointing each, so an interruption never re-runs or skips a step.
- `app.py` — a thin `gr.ChatInterface` over `core.generate_reply`.
- `tests/` — a stdlib smoke test + the harness's kill-and-resume criterion tests.

## Run it

See `RUN.md`. The quick path is `docker compose up --build`, then open http://localhost:7860 and type a
task id (e.g. `order-42`). Kill the app mid-run and bring it back — re-sending the task resumes it, running
no step twice. State persists on the `agent_state` volume, so a reply that completes exactly-once across a
crash *proves* the checkpoint is durable.
