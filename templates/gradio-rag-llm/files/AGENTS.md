# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle: a Gradio chatbot doing RAG over a real **pgvector** database
with a real **LLM** writing the answer. It is a standalone git repo; continue it by hand or in OpenCode.

## Layout
- `core.py` — the logic. The scaffold ships working helpers: `retrieve(query)` (pgvector ranking + a
  lexical relevance gate → `[]` for an unrelated query), `snippet(doc)` (a verbatim quote), `cite(doc)`
  (a `[id]` marker), and `_answer(question, context)` (the REAL model call). **`generate_reply` is a
  stub — implement it by composing those**: retrieve → `_answer` from the retrieved doc → append
  `cite`. No SQL/psycopg needed. **Put new behaviour here.**
- `app.py` — Gradio UI only. No logic.
- `tests/` — the suite (a stdlib offline smoke + retrieval/generation criterion tests). **Red-first.**
- `requirements.txt` — pinned deps (incl. `openai`). `compose.yaml` — pgvector + the app.
- `RUN.md` — install / test / demo / run blocks.

## Working rules
1. Keep the **core/UI split**: logic in `core.py`, presentation in `app.py`.
2. The PoC needs **pgvector** at `PF_SERVICE_PG_HOST` AND a model at `PF_SANDBOX_MODEL_BASE_URL`
   (+ `PF_SANDBOX_VLLM_KEY`). All are read lazily; importing `core` touches neither DB nor model.
3. Every feature gets a test in `tests/` first. The model's prose VARIES — tests assert STRUCTURE
   (the `[<int>]` citation, the cited id, presence/absence), never exact wording.
4. Keep retrieval **deterministic** (the hashing embedding) so the verifiable contract stays stable.
5. Keep `docker compose up` runnable end-to-end (set the model env vars first).
