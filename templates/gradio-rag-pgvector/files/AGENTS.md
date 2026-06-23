# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle: a Gradio chatbot doing RAG over a real **pgvector**
database. It is a standalone git repo; you can continue it by hand or in OpenCode.

## Layout
- `core.py` — the logic. The scaffold ships working helpers: `retrieve(query)` (pgvector ranking + a
  lexical relevance gate → `[]` for an unrelated query), `snippet(doc)` (a verbatim grounding quote),
  `cite(doc)` (a `[id]` marker). **`generate_reply` is a stub — implement it by composing those
  helpers** (a few lines; see its docstring), e.g. `docs = retrieve(message); return f"{cite(docs[0])}
  {snippet(docs[0])}"` with a no-match fallback. No SQL/psycopg needed. **Put new behaviour here.**
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
