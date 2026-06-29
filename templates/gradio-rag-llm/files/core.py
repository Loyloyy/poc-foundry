"""Core logic for a RAG chatbot that generates answers with a REAL LLM — PURE-ish + importable;
``app.py`` is just the UI.

This PoC retrieves from a REAL **pgvector** sibling (Postgres + the `vector` extension, reached BY IP
via ``PF_SERVICE_PG_HOST``) and then calls a REAL model (the OpenAI-compatible endpoint at
``PF_SANDBOX_MODEL_BASE_URL`` with ``PF_SANDBOX_VLLM_KEY``) to write a grounded answer from the
retrieved document. Retrieval + embeddings are a deterministic stdlib hashing trick (no model, no
network) so RETRIEVAL stays reproducible + unit-testable; the citation marker is appended IN CODE, so
the deterministic, verifiable part of a reply is the ``[id]`` marker — NOT the model's free-form prose.

The scaffold ships the plumbing WORKING — `_connect`/`_ensure_corpus`, `search` (pgvector ranking),
`retrieve` (ranking + a lexical relevance gate), `snippet` (a verbatim quote), `cite` (a `[id]` marker),
and `_answer` (the REAL LLM call) — plus a STUB `generate_reply`. **Build iterations implement
`generate_reply` by composing these** (a few lines — see its docstring). Importing this module touches
no DB and no model (`psycopg` + `openai` are imported lazily), so the stdlib smoke test runs offline.
"""
from __future__ import annotations

import hashlib
import os
import time

EMBED_DIM = 64

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


def _tokens(text: str) -> set[str]:
    return {t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t}


def _embed(text: str) -> list[float]:
    """Deterministic stdlib embedding: hash each alphanumeric token into one of EMBED_DIM buckets
    (bag-of-words), then L2-normalize. Pure + reproducible (no model, no network)."""
    vec = [0.0] * EMBED_DIM
    for tok in _tokens(text):
        vec[int(hashlib.sha1(tok.encode()).hexdigest(), 16) % EMBED_DIM] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else vec


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


_VOCAB: set[str] | None = None


def _vocab() -> set[str]:
    global _VOCAB
    if _VOCAB is None:
        _VOCAB = set().union(*(_tokens(d["title"] + " " + d["content"]) for d in CORPUS))
    return _VOCAB


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
    """The k corpus documents ranked nearest to ``query`` by pgvector L2 distance — ``[{id, title,
    content, distance}]``. Always returns up to k rows (no relevance gate; use ``retrieve`` for that)."""
    conn = _connect()
    try:
        _ensure_corpus(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, content, embedding <-> %s::vector AS dist "
                        "FROM docs ORDER BY dist LIMIT %s", (_vec_literal(_embed(query)), k))
            return [{"id": r[0], "title": r[1], "content": r[2], "distance": float(r[3])}
                    for r in cur.fetchall()]
    finally:
        conn.close()


def retrieve(query: str, k: int = 1) -> list[dict]:
    """Relevant corpus docs for ``query``, best-first (pgvector ranking) — or ``[]`` if the query shares
    NO vocabulary with the corpus. The lexical gate makes "unrelated query → no citation" RELIABLE."""
    if not (_tokens(query) & _vocab()):
        return []
    return search(query, k)


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
    prose varies and must NOT be asserted on. Retrieval (`retrieve`, with a lexical gate → `[]` for an
    unrelated query) and `cite` (→ `[1]`, an INTEGER id) are ALREADY WORKING. The target shape::

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
