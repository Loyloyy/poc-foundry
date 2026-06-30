# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle: a Gradio chatbot that answers product questions by CALLING a
real **tool server** (a private catalogue) and using its structured result, with a real **LLM** phrasing
the answer. It is a standalone git repo; continue it by hand or in OpenCode.

Organised as **kit + glue**: the tool client + model call live in `toolkit.py` (a library); the
tool-calling logic lives in the editable `core.py`.

## Layout
- `toolkit.py` — the PRIMITIVES library (treat like an import; do NOT rewrite): `call_tool(product)`
  (the private catalogue lookup via the tool sibling; returns `{product, sku, price_usd, found}` — the
  price/sku are obtainable ONLY by calling the tool), `llm(prompt, system=...)` (a real model
  completion; the wording varies), and `CATALOG_PRODUCTS` (the product names the tool knows).
- `core.py` — the editable glue. **`generate_reply` is the tool-calling logic — implement it by
  composing the toolkit primitives.** Import and USE them; don't reimplement them or invent prices.
- `toolserver/` — the private tool service (stdlib HTTP). The compose file builds + runs it.
- `app.py` — Gradio UI only. No logic.
- `tests/` — a stdlib offline smoke + the harness's tool-call criterion tests. **Red-first.**
- `requirements.txt` — pinned deps. `compose.yaml` — the tool server + the app.
- `RUN.md` — install / test / demo / run blocks.

## Working rules
1. Keep the **core/UI split**: logic in `core.py`, presentation in `app.py`.
2. The PoC needs the **tool server** at `PF_SERVICE_TOOLSERVER_HOST` AND a model at
   `PF_SANDBOX_MODEL_BASE_URL` (+ `PF_SANDBOX_VLLM_KEY`). All are read lazily; importing `core`/`toolkit`
   touches neither the tool nor the model.
3. Every feature gets a test in `tests/` first. The model's prose VARIES and prices are TOOL-only —
   tests assert what only a genuine tool call can produce (the exact returned price/sku), never the
   model's exact wording.
4. A product not in the catalogue must return a no-match reply, not an invented price.
5. Keep `docker compose up` runnable end-to-end (set the model env vars first).
