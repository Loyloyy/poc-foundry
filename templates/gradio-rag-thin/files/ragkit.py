"""ragkit — the RAG PRIMITIVES for this PoC, provided as a LIBRARY (this file is NOT editable by the
build; ``core.py`` is). Treat these like any imported library: call them, don't reimplement them.

  • ``search(query, k)`` — semantic nearest-neighbour over the corpus in a real **pgvector** sibling;
    returns ``[{id, title, content, distance}]`` (cosine distance, SMALLER = closer). RAW: it ranks,
    it does NOT decide relevance — that's the glue's job.
  • ``llm(prompt, system=...)`` — a real model completion (OpenAI-compatible endpoint injected by the
    harness). The wording VARIES run to run.
  • ``CORPUS`` — the fixed private documents (``{id, title, content}``); the PoC may NOT add facts.
  • ``_embed(text)`` — a 384-d semantic embedding (offline, baked into the sandbox image).

Importing this module is light — ``fastembed``/``psycopg``/``openai`` are imported lazily inside the
functions; the embedding model loads offline from the image cache on first use.
"""
from __future__ import annotations

import os
import time

EMBED_DIM = 384   # BAAI/bge-small-en-v1.5 (fastembed) output dimension

# The PoC's fixed private corpus — the documents the chatbot may ground answers in; (id, title,
# content). Single-spaced content so a verbatim quote is an exact substring. You may NOT add facts:
# a question whose subject is not covered here has no grounded answer (the no-match case).
CORPUS = [
    {"id": 1, "title": "Retrieval-augmented generation",
     "content": "Retrieval augmented generation grounds a language model answer in documents fetched "
                "from a corpus so the reply can cite its sources instead of relying only on the model."},
    {"id": 2, "title": "Vector search with pgvector",
     "content": "pgvector adds a vector column type and similarity operators to Postgres letting you "
                "store document embeddings and run nearest neighbour search with a simple SQL query."},
    {"id": 3, "title": "Grounded citations",
     "content": "A grounded answer includes a citation marker pointing at the retrieved document and "
                "quotes a verbatim snippet of its content so a reader can verify the claim against the source."},
    {"id": 4, "title": "Gradio chat interface",
     "content": "Gradio provides a chat interface where each user message is passed to a pure reply "
                "function keeping the retrieval and generation logic testable without launching a browser."},
]


# ── embedding primitive (a library: text → vector) ───────────────────────────
_EMBEDDER = None


def _embedder():
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding  # lazy: importing this module stays light
        _EMBEDDER = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _EMBEDDER


def _embed(text: str) -> list[float]:
    """Real semantic embedding of ``text`` → a 384-d vector. Deterministic (same text → same vector),
    CPU-only, no PyTorch; loaded offline from the baked image cache."""
    vec = next(iter(_embedder().embed([text])))
    return [float(x) for x in vec]


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


# ── vector-store primitive (a library: query → ranked corpus docs) ───────────
def _connect(retries: int = 30):
    import psycopg  # lazy: the stdlib smoke test imports this module without a DB

    host = os.environ.get("PF_SERVICE_PG_HOST", "localhost")
    password = (os.environ.get("PF_SERVICE_PG_POSTGRES_PASSWORD")
                or os.environ.get("POSTGRES_PASSWORD") or "pf")
    last = None
    for _ in range(retries):
        try:
            return psycopg.connect(host=host, port=5432, user="postgres", password=password,
                                   dbname="postgres", connect_timeout=3)
        except Exception as e:  # noqa: BLE001 — service may still be starting
            last = e
            time.sleep(1)
    raise RuntimeError(f"cannot reach pgvector at {host}:5432: {last}")


def _ensure_corpus(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"CREATE TABLE IF NOT EXISTS docs "
                    f"(id int PRIMARY KEY, title text, content text, embedding vector({EMBED_DIM}))")
        for d in CORPUS:
            cur.execute("INSERT INTO docs (id, title, content, embedding) VALUES (%s, %s, %s, %s::vector) "
                        "ON CONFLICT (id) DO NOTHING",
                        (d["id"], d["title"], d["content"], _vec_literal(_embed(d["title"] + " " + d["content"]))))
    conn.commit()


def search(query: str, k: int = 3) -> list[dict]:
    """The ``k`` corpus documents most similar to ``query``, nearest first, as
    ``[{id, title, content, distance}]`` (pgvector COSINE distance: 0 = identical … 2 = opposite, so
    SMALLER is closer). RAW vector store: it ALWAYS returns up to ``k`` rows and does NOT decide
    relevance — deciding whether the best hit is close enough, and what to do with it, is the glue's
    job. Real embeddings match by MEANING, so a paraphrased question still finds its document."""
    conn = _connect()
    try:
        _ensure_corpus(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, content, embedding <=> %s::vector AS dist "
                        "FROM docs ORDER BY dist LIMIT %s", (_vec_literal(_embed(query)), k))
            return [{"id": r[0], "title": r[1], "content": r[2], "distance": float(r[3])}
                    for r in cur.fetchall()]
    finally:
        conn.close()


# ── model primitive (a library: prompt → completion text) ────────────────────
_MODEL_ID: str | None = None


def llm(prompt: str, system: str | None = None, max_tokens: int = 2048) -> str:
    """A real model completion from the OpenAI-compatible endpoint (``PF_SANDBOX_MODEL_BASE_URL`` +
    ``PF_SANDBOX_VLLM_KEY``, injected by the build harness). ``temperature=0`` for stability. The
    wording VARIES run to run — never assert exact prose on the result.

    ``max_tokens`` is generous on purpose: the endpoint may serve a REASONING model that spends
    completion tokens on chain-of-thought before the answer, so too small a budget returns an empty
    ``content`` (this primitive falls back to a separate reasoning channel if ``content`` is empty)."""
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
