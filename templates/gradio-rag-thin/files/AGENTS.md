# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle: a Gradio chatbot doing RAG over a real **pgvector** database
with a real **LLM** writing the answer. It is a standalone git repo; continue it by hand or in OpenCode.

This is the **thin** RAG scaffold, organised as **kit + glue**: the primitives (a vector store, an
embedder, a model call) and the fixed corpus live in `ragkit.py` (a library); the
retrieval/grounding/citation logic lives in the editable `core.py`, written by the build.

## Layout
- `ragkit.py` — the PRIMITIVES library (treat like any import; do NOT rewrite): `search(query, k)`
  (the k nearest corpus docs by semantic similarity, with `distance` — it does NOT decide relevance),
  `llm(prompt, system=...)` (a real model completion; the wording varies), and `CORPUS` (the fixed docs).
- `core.py` — the editable glue. **`generate_reply` is the RAG logic — implement it by composing the
  ragkit primitives.** Import and USE them; don't reimplement them or invent your own corpus. **Put
  new behaviour here.**
- `app.py` — Gradio UI only. No logic.
- `tests/` — the suite (a stdlib offline smoke + retrieval/generation criterion tests). **Red-first.**
- `requirements.txt` — pinned deps (incl. `openai`). `compose.yaml` — pgvector + the app.
- `RUN.md` — install / test / demo / run blocks.

## Working rules
1. Keep the **core/UI split**: logic in `core.py`, presentation in `app.py`.
2. The PoC needs **pgvector** at `PF_SERVICE_PG_HOST` AND a model at `PF_SANDBOX_MODEL_BASE_URL`
   (+ `PF_SANDBOX_VLLM_KEY`). All are read lazily; importing `core` touches neither DB nor model.
3. Every feature gets a test in `tests/` first. The model's prose VARIES — tests assert what the code
   CONTROLS (the relevance gate, a citation you produce, grounding against the cited doc), never the
   model's exact wording.
4. An out-of-corpus question must return a no-match reply, not an invented answer.
5. Keep `docker compose up` runnable end-to-end (set the model env vars first).
