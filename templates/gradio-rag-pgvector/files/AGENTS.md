# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle: a Gradio chatbot doing RAG over a real **pgvector**
database. It is a standalone git repo; you can continue it by hand or in OpenCode.

## Layout
- `core.py` — the logic. `_embed` (deterministic stdlib embedding), `search()` (pgvector
  nearest-neighbour), and `generate_reply` (retrieve → answer with a `[id]` citation). **Put new
  behaviour here.**
- `app.py` — Gradio UI only. No logic.
- `tests/` — the suite (a stdlib embedding smoke + retrieval criterion tests). **Red-first.**
- `requirements.txt` — pinned deps. `compose.yaml` — pgvector + the app for `docker compose up`.
- `RUN.md` — install / test / demo / run blocks.

## Working rules
1. Keep the **core/UI split**: logic in `core.py`, presentation in `app.py`.
2. The PoC needs **pgvector** reachable at `PF_SERVICE_PG_HOST` (compose sets it to the `pg` service).
   `search()`/`generate_reply` connect lazily; importing `core` touches no DB.
3. Every feature gets a test in `tests/` first; make `python -m pytest -q` green (with pgvector up).
4. Keep retrieval **deterministic** (the hashing embedding) so tests stay reproducible.
5. Keep `docker compose up` runnable end-to-end.
