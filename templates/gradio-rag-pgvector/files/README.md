# RAG-over-pgvector chatbot (PoC)

A minimal proof-of-concept: a Gradio chatbot that answers from a small corpus using **real vector
retrieval** in **pgvector** (Postgres + the `vector` extension), with citation markers.

- `core.py` — the logic: a deterministic stdlib embedding (`_embed`), a corpus, and `search()` over
  pgvector. `generate_reply(message, history)` retrieves and answers with a `[id]` citation.
- `app.py` — a thin `gr.ChatInterface` over `core.generate_reply`.
- `tests/` — a stdlib smoke test (the embedding) + the harness's criterion tests (retrieval).

## Run it

See `RUN.md`. The quick path is `docker compose up --build` (starts pgvector + the app), then open
http://localhost:7860. The PoC reaches pgvector by the address in `PF_SERVICE_PG_HOST`.

Embeddings are a deterministic hashing trick (no model, no network) so the demo is reproducible; the
similarity search itself runs in pgvector — swap in real embeddings to scale the idea up.
