# Tool-calling chatbot (PoC)

A minimal proof-of-concept: a Gradio chatbot that answers product questions by calling a **real
external tool** (a private product-catalogue service) and using its structured result, with a **real
model** phrasing the answer.

Organised as **kit + glue** — `toolkit.py` ships the primitives as a library; the tool-calling logic
itself is written in `core.generate_reply`.

- `toolkit.py` — `call_tool(product)` (the private catalogue lookup via the tool sibling; returns
  `{product, sku, price_usd, found}` — the price/sku come ONLY from the tool), `llm(prompt, system=...)`
  (a real model completion), and `CATALOG_PRODUCTS` (the product names the tool knows).
- `core.py` — the editable glue: `generate_reply` matches the user's question to a catalogue product,
  CALLS the tool for its real price/sku, answers with that structured result, and returns a no-match
  reply for an unknown product.
- `toolserver/` — the private tool service (stdlib HTTP; opaque prices the model can't know).
- `app.py` — a thin `gr.ChatInterface` over `core.generate_reply`.
- `tests/` — a stdlib smoke test (offline) + the harness's tool-call criterion tests.

## Run it

See `RUN.md`. The quick path is `docker compose up --build` (builds + starts the tool server + the app)
after you set `PF_SANDBOX_MODEL_BASE_URL`/`PF_SANDBOX_VLLM_KEY`, then open http://localhost:7860 and ask
e.g. *"how much is the lattice router x1?"*.

Because the catalogue prices are private to the tool, a reply that states a real price *proves* the
chatbot actually called the tool — the verifiable contract.
