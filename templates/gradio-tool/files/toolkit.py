"""toolkit — the tool-calling PRIMITIVES, provided as a LIBRARY (this file is NOT editable by the
build; ``core.py`` is). Treat these like any imported library: call them, don't reimplement them.

  • ``call_tool(product)`` — look up a product in the PRIVATE catalogue via the real tool sibling (HTTP,
    by IP from ``PF_SERVICE_TOOLSERVER_HOST``). Returns ``{"product","sku","price_usd","found"}`` — the
    SKU/price are OPAQUE values the model has NO way to produce WITHOUT calling the tool.
  • ``llm(prompt, system=...)`` — a real model completion (the wording VARIES run to run).
  • ``CATALOG_PRODUCTS`` — the product NAMES the tool knows. You may NOT invent prices/SKUs; only
    ``call_tool`` yields those.
  • ``tool_calls()`` — the tool server's recorded call log, for verifying genuine invocation in a test.

Use these helpers for ALL tool access — they bypass the VM's egress proxy (the sibling is internal; a
raw HTTP request to it gets a 403).

Importing this module is light — ``openai`` is imported lazily inside ``llm``; ``call_tool`` uses only
the stdlib (``urllib``).
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

# The product NAMES in the private catalogue (public so the chatbot/tests can match a user query to a
# product). The SKUs and prices are NOT here — only the tool returns them.
CATALOG_PRODUCTS = [
    "lattice router x1",
    "vortex sensor pad",
    "halcyon power cell",
    "meridian edge node",
]


def _tool_base() -> str:
    host = os.environ.get("PF_SERVICE_TOOLSERVER_HOST", "localhost")
    return f"http://{host}:8000"


# Reach the internal sibling DIRECTLY, bypassing any egress proxy set in the VM env (``http_proxy``).
# The tool server is on the per-build INTERNAL network by IP — NOT an external egress host — so routing
# its HTTP request through the egress proxy returns 403 (the proxy correctly denies a non-allowlisted
# host). An empty ``ProxyHandler`` disables proxy use for these calls (mirrors psycopg's direct TCP to
# pgvector). Any HTTP-based sibling needs this; a TCP driver like psycopg does not.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call_tool(product: str) -> dict:
    """Look up ``product`` in the PRIVATE catalogue via the real tool sibling. Returns the structured
    record ``{"product","sku","price_usd","found"}`` (``found=False`` for an unknown product). The
    ``sku``/``price_usd`` are OPAQUE — only this call yields them, so a model-only or echo stub cannot
    produce them. Raises on a transport failure (the sibling is injected by the build harness)."""
    url = _tool_base() + "/price?" + urllib.parse.urlencode({"product": product})
    with _DIRECT.open(url, timeout=10) as r:   # noqa: S310 — fixed internal sibling URL (proxy bypassed)
        return json.loads(r.read().decode())


def tool_calls() -> list[dict]:
    """The tool server's recorded call log — ``[{"product": ...}, ...]`` — for VERIFYING genuine
    invocation (e.g. a test calls ``generate_reply`` then asserts the queried product was recorded here).
    Proxy-bypassed like ``call_tool`` — use THIS helper, never a raw HTTP request to the sibling, or the
    egress proxy denies it (403)."""
    with _DIRECT.open(_tool_base() + "/calls", timeout=10) as r:   # noqa: S310 — internal sibling (proxy bypassed)
        return json.loads(r.read().decode()).get("calls", [])


_MODEL_ID: str | None = None


def llm(prompt: str, system: str | None = None, max_tokens: int = 2048) -> str:
    """A real model completion from the OpenAI-compatible endpoint (``PF_SANDBOX_MODEL_BASE_URL`` +
    ``PF_SANDBOX_VLLM_KEY``, injected by the build harness). ``temperature=0``. The wording VARIES run
    to run — never assert exact prose on the result. ``max_tokens`` is generous because the endpoint may
    serve a REASONING model (chain-of-thought consumes tokens before the answer)."""
    global _MODEL_ID
    from openai import OpenAI  # lazy: the stdlib smoke test imports this module without the SDK

    base = os.environ.get("PF_SANDBOX_MODEL_BASE_URL")
    if not base:
        raise RuntimeError("PF_SANDBOX_MODEL_BASE_URL is not set — no model endpoint available")
    client = OpenAI(base_url=base, api_key=os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed"))
    if _MODEL_ID is None:                       # discover the served model id (no hard-coded name)
        _MODEL_ID = client.models.list().data[0].id
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(model=_MODEL_ID, temperature=0, max_tokens=max_tokens,
                                          messages=messages)
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()
