# RAG chatbot over pgvector (PoC, thin scaffold)

A minimal proof-of-concept: a Gradio chatbot that answers from a small private corpus using **real
vector retrieval** in **pgvector** and a **real model** to generate the grounded answer.

This is the **thin** scaffold — it ships only primitives; the retrieval-augmented-generation logic
itself is written in `generate_reply`.

- `core.py` — a fixed `CORPUS` plus three primitives: `search(query, k)` (nearest corpus docs by
  semantic similarity in pgvector, with a `distance`), `llm(prompt, system=...)` (a real model
  completion against an OpenAI-compatible endpoint), and an offline `_embed`. `generate_reply` composes
  them into RAG: find the relevant document, ground the model's answer in it, make the answer
  verifiable against that document, and return a no-match reply for an out-of-corpus question.
- `app.py` — a thin `gr.ChatInterface` over `core.generate_reply`.
- `tests/` — a stdlib smoke test (offline) + the harness's criterion tests (retrieval + generation).

## Run it

See `RUN.md`. The quick path is `docker compose up --build` (starts pgvector + the app) after you set
`PF_SANDBOX_MODEL_BASE_URL`/`PF_SANDBOX_VLLM_KEY` to point at a model, then open http://localhost:7860.

Retrieval uses real semantic embeddings (`fastembed` + `BAAI/bge-small-en-v1.5`, similarity in
pgvector); the answer text comes from the model and will vary, but the grounding contract — the answer
is verifiable against the corpus document it cites — is what the tests check.
