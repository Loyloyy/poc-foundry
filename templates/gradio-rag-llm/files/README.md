# RAG + LLM chatbot over pgvector (PoC)

A minimal proof-of-concept: a Gradio chatbot that answers from a small corpus using **real vector
retrieval** in **pgvector** and a **real model** to generate the grounded answer, with a citation
marker the code appends.

- `core.py` — the logic: a deterministic stdlib embedding (`_embed`), a corpus, `search()` over
  pgvector, `retrieve()` (ranking + a lexical gate), and `_answer()` (the real model call against an
  OpenAI-compatible endpoint). `generate_reply(message, history)` retrieves, asks the model to answer
  from the retrieved document, and appends a `[id]` citation.
- `app.py` — a thin `gr.ChatInterface` over `core.generate_reply`.
- `tests/` — a stdlib smoke test (offline) + the harness's criterion tests (retrieval + generation).

## Run it

See `RUN.md`. The quick path is `docker compose up --build` (starts pgvector + the app) after you set
`PF_SANDBOX_MODEL_BASE_URL`/`PF_SANDBOX_VLLM_KEY` to point at a model, then open http://localhost:7860.

Retrieval is deterministic (a hashing embedding, similarity in pgvector) so retrieval is reproducible;
the answer text comes from the model and will vary, but the **citation marker is deterministic** — the
verifiable contract. Swap in real embeddings to scale the idea up.
