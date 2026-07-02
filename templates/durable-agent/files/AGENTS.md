# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle: a **durable agent** — a Gradio chatbot that runs a multi-step
workflow per task and RESUMES from a disk checkpoint if killed mid-run (each step exactly once). It is a
standalone git repo; continue it by hand or in OpenCode.

Organised as **kit + glue**: the durability primitives live in `agentkit.py` (a library); the
resume/checkpoint logic lives in the editable `core.py`. No database, no model, no sibling service —
durability is a local file (`PF_AGENT_STATE_DIR`, default `/tmp/pf_agent_state`).

## Layout
- `agentkit.py` — the PRIMITIVES library (treat like an import; do NOT rewrite): `TASK_STEPS`,
  `load_progress` (resume pointer), `append_ledger` (durable execution log), `checkpoint` (durability
  barrier — persists progress; in tests it can uncatchably abort the process to simulate a crash).
- `core.py` — the editable glue. **`generate_reply` is the durable logic — implement it by composing the
  primitives**: resume from `load_progress`, do each remaining step's work then `checkpoint`. Import and
  USE them; don't reimplement them.
- `app.py` — Gradio UI only. No logic.
- `tests/` — a stdlib smoke + the harness's kill-and-resume criterion tests. **Red-first.**
- `requirements.txt` — pinned deps (stdlib durable store; no db/model). `compose.yaml` — the app + a
  persistent volume.
- `RUN.md` — install / test / demo / run blocks.

## Working rules
1. Keep the **core/UI split**: logic in `core.py`, presentation in `app.py`.
2. Durability is the point: `generate_reply` must RESUME after an interruption, running each step exactly
   once. Never re-run a completed step; never skip one.
3. Every feature gets a test in `tests/` first. Prove durability by killing mid-run (uncatchably) and
   asserting the durable execution log shows each step exactly once across the resume.
4. Keep `docker compose up` runnable; state persists on the `agent_state` volume across restarts.
