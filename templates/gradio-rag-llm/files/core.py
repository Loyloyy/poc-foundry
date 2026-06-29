"""Core logic for a RAG chatbot that generates answers with a REAL LLM — PURE-ish + importable;
``app.py`` is just the UI.

This PoC retrieves from a REAL **pgvector** sibling (Postgres + the `vector` extension, reached BY IP
via ``PF_SERVICE_PG_HOST``) and then calls a REAL model (the OpenAI-compatible endpoint at
``PF_SANDBOX_MODEL_BASE_URL`` with ``PF_SANDBOX_VLLM_KEY``) to write a grounded answer from the
retrieved document. Retrieval uses REAL semantic embeddings — a small, efficient, CPU-only sentence
model (``BAAI/bge-small-en-v1.5`` via **fastembed**/ONNX, no PyTorch), baked into the sandbox image so a
build runs fully offline — plus **pgvector** for the similarity search. Embeddings are deterministic
(same text → same vector), so retrieval stays reproducible. The citation marker is appended IN CODE, so
the deterministic, verifiable part of a reply is the ``[id]`` marker — NOT the model's free-form prose.

The scaffold ships the plumbing WORKING — `_connect`/`_ensure_corpus`, `search` (pgvector cosine
ranking), `retrieve` (semantic search + a distance threshold), `snippet` (a verbatim quote), `cite`
(a `[id]` marker), and `_answer` (the REAL LLM call) — plus a STUB `generate_reply`. **Build iterations
implement `generate_reply` by composing these** (a few lines — see its docstring). Importing this module
is light (`fastembed`/`psycopg`/`openai` are imported lazily); the embedding model loads on first use.
"""
from __future__ import annotations

import os
import time

EMBED_DIM = 384   # BAAI/bge-small-en-v1.5 (fastembed) output dimension

# Cosine-distance cutoff (pgvector `<=>`: 0 = identical … 2 = opposite). A query whose nearest corpus
# doc is FARTHER than this is treated as out-of-corpus → no citation. Calibrated on this corpus:
# in-topic queries land ~0.08-0.10, unrelated ~0.55, so 0.4 separates them cleanly. Tunable.
RELEVANCE_THRESHOLD = 0.4

# The PoC's fixed knowledge base — topical to the artifact (RAG / retrieval / pgvector / gradio) so
# domain queries find a match; (id, title, content). Single-spaced content so a snippet is a verbatim
# substring.
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


_EMBEDDER = None


def _embedder():
    """The embedding model — a small, efficient, CPU-only sentence embedder (BAAI/bge-small-en-v1.5 via
    fastembed/ONNX, NO PyTorch). Loaded once, lazily; the sandbox image bakes the model so a build runs
    fully offline. Deterministic (same text → same vector) → reproducible retrieval."""
    global _EMBEDDER
    if _EMBEDDER is None:
        from fastembed import TextEmbedding  # lazy: importing this module stays light
        _EMBEDDER = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
    return _EMBEDDER


def _embed(text: str) -> list[float]:
    """Real semantic embedding of ``text`` → a 384-d vector. Deterministic, CPU-only, no PyTorch."""
    vec = next(iter(_embedder().embed([text])))
    return [float(x) for x in vec]


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


def _connect(retries: int = 30):
    """Connect to the pgvector sibling (by IP from PF_SERVICE_PG_HOST), retrying while it warms up."""
    import psycopg  # lazy: the stdlib smoke test imports this module without needing a DB

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
    """Idempotently create the vector table + extension and seed the corpus (safe every call → a fresh
    clean-room DB self-seeds on first query)."""
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute(f"CREATE TABLE IF NOT EXISTS docs "
                    f"(id int PRIMARY KEY, title text, content text, embedding vector({EMBED_DIM}))")
        for d in CORPUS:
            cur.execute("INSERT INTO docs (id, title, content, embedding) VALUES (%s, %s, %s, %s::vector) "
                        "ON CONFLICT (id) DO NOTHING",
                        (d["id"], d["title"], d["content"], _vec_literal(_embed(d["title"] + " " + d["content"]))))
    conn.commit()


def search(query: str, k: int = 1) -> list[dict]:
    """The k corpus documents ranked nearest to ``query`` by pgvector COSINE distance (`<=>`) —
    ``[{id, title, content, distance}]``. Always returns up to k rows (no relevance gate; use
    ``retrieve`` for that)."""
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


def retrieve(query: str, k: int = 1) -> list[dict]:
    """Relevant corpus docs for ``query`` (semantic similarity search in pgvector), best-first — or
    ``[]`` if the nearest doc is FARTHER than RELEVANCE_THRESHOLD (out-of-corpus → no citation). Real
    embeddings match by MEANING, so a paraphrased question still finds the right document."""
    return [h for h in search(query, k) if h["distance"] <= RELEVANCE_THRESHOLD]


def snippet(doc: dict, n: int = 8) -> str:
    """A verbatim grounding quote — the first ``n`` words of the document content (≥3 consecutive
    words, an exact substring of the source so a reader can verify the answer)."""
    return " ".join(doc["content"].split()[:n])


def cite(doc: dict) -> str:
    """A citation marker referencing the document id, e.g. ``[1]``."""
    return f"[{doc['id']}]"


_MODEL_ID: str | None = None


def _answer(question: str, context: str, max_tokens: int = 2048) -> str:
    """The REAL model call: ask the OpenAI-compatible endpoint (``PF_SANDBOX_MODEL_BASE_URL`` +
    ``PF_SANDBOX_VLLM_KEY``, injected by the build harness) to answer ``question`` GROUNDED ONLY in
    ``context`` (the retrieved document). ``temperature=0`` for stability. Lazy-imports ``openai`` so
    importing this module stays offline. The wording will vary run-to-run — tests must assert STRUCTURE
    (the code-appended citation), never the exact prose.

    NOTE: ``max_tokens`` is generous (2048) on purpose — the endpoint may serve a REASONING model that
    spends completion tokens on chain-of-thought before the answer; too small a budget returns an EMPTY
    ``content``. If a model exposes a separate reasoning channel and ``content`` is still empty, fall
    back to it so the caller never gets an empty answer."""
    global _MODEL_ID
    from openai import OpenAI  # lazy: the stdlib smoke test imports this module without the SDK

    base = os.environ.get("PF_SANDBOX_MODEL_BASE_URL")
    if not base:
        raise RuntimeError("PF_SANDBOX_MODEL_BASE_URL is not set — no model endpoint available")
    client = OpenAI(base_url=base, api_key=os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed"))
    if _MODEL_ID is None:                       # discover the served model id (no hard-coded name)
        _MODEL_ID = client.models.list().data[0].id
    resp = client.chat.completions.create(
        model=_MODEL_ID, temperature=0, max_tokens=max_tokens,
        messages=[{"role": "system",
                   "content": "Answer the question using ONLY the provided context. Be concise. "
                              "If the context does not contain the answer, say you don't know."},
                  {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}])
    msg = resp.choices[0].message
    return (msg.content or getattr(msg, "reasoning_content", None) or "").strip()


def generate_reply(message: str, history: list | None = None) -> str:
    """SCAFFOLD STUB — build iterations implement RAG HERE by composing the working helpers.

    This template generates the answer with a REAL LLM (`_answer`) but appends the citation in CODE
    (`cite`), so the DETERMINISTIC, verifiable part of a reply is the `[<int id>]` marker — the model's
    prose varies and must NOT be asserted on. Retrieval (`retrieve`, semantic search with a distance
    threshold → `[]` for an unrelated query) and `cite` (→ `[1]`, an INTEGER id) are ALREADY WORKING.
    The target shape::

        docs = retrieve(message)
        if not docs:
            return "no relevant documents found"
        d = docs[0]
        answer = _answer(message, f"{d['title']}: {d['content']}")   # REAL grounded LLM call
        return f"{answer} {cite(d)}"                                  # code-appends [id] → e.g. "...text... [2]"

    CITATION FORMAT: use `cite(d)` UNCHANGED — it yields `[<int id>]` (e.g. `[1]`); the staged test
    parses the integer between the brackets. Do NOT switch to `[doc-1]`/`[source:title]`. For an
    unrelated query, return the no-match fallback with NO `[N]` marker. The stub below does no
    retrieval, so a real criterion test is RED first.
    """
    return "I couldn't find any relevant documents."
