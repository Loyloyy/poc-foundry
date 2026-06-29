"""Core logic for a RAG-over-pgvector chatbot PoC — PURE-ish and importable; ``app.py`` is just the UI.

This PoC retrieves from a REAL **pgvector** sibling service (Postgres + the `vector` extension),
reached BY IP via the harness-injected ``PF_SERVICE_PG_HOST`` (Kata VMs have no container-name DNS).
Embeddings are a deterministic stdlib hashing trick — NO model, NO network — so retrieval is
reproducible and unit-testable; the nearest-neighbour ranking runs in pgvector.

The scaffold ships the retrieval plumbing WORKING — `_connect`/`_ensure_corpus`, `search` (pgvector
ranking), `retrieve` (ranking + a lexical relevance gate so an unrelated query matches nothing),
`snippet` (a verbatim grounding quote) and `cite` (a citation marker) — plus a STUB `generate_reply`.
**Build iterations implement `generate_reply` on top of these helpers** (a few lines of glue — see its
docstring); they should not need to write SQL or psycopg. Importing this module touches no DB
(`psycopg` is imported lazily), so the stdlib smoke test runs without a database.
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
    """Connect to the pgvector sibling (by IP from PF_SERVICE_PG_HOST), retrying while it warms up.
    Defaults suit local `docker compose up` (host localhost, password from the env)."""
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
    NO vocabulary with the corpus. The lexical gate makes "unrelated query → no citation" RELIABLE (a
    pure vector-distance threshold is too noisy on a tiny hashed-embedding corpus)."""
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


def generate_reply(message: str, history: list | None = None) -> str:
    """SCAFFOLD STUB — build iterations implement retrieval HERE using ``retrieve`` / ``cite`` /
    ``snippet`` (no SQL needed).

    THIS IS A DETERMINISTIC SCAFFOLD. Retrieval (``retrieve`` — pgvector ranking + a lexical relevance
    gate that returns ``[]`` for unrelated queries), the verbatim grounding quote (``snippet``), and the
    citation marker (``cite`` → ``[<int id>]``, e.g. ``[1]``) are ALREADY IMPLEMENTED AND WORKING. Your
    job is ~3 lines of GLUE composing them. Do NOT add embeddings, similarity thresholds, an LLM call,
    or any new retrieval logic — and ignore generic "how to build RAG" advice (threshold tuning,
    prompting a model to quote verbatim): none of it applies here. ``snippet`` already returns a verbatim
    substring; the lexical gate already gives the no-match path. The target shape::

        docs = retrieve(message)
        if not docs:
            return "no relevant documents found"
        d = docs[0]
        return f"{cite(d)} {snippet(d)}"          # → e.g. "[1] Retrieval augmented generation grounds ..."

    CITATION FORMAT: use ``cite(d)`` UNCHANGED — it yields ``[<int id>]`` (e.g. ``[1]``), which is the
    format the staged test parses (it reads the integer between the brackets). Do NOT switch to
    ``[doc-1]`` / ``[source:title]`` — that mismatches the test's parser. Extend for the spec's other
    criteria (multiple matches → cite each in id order; absent topic → no marker). The stub below does
    NO retrieval, so a real criterion test is RED first.
    """
    return "I couldn't find any relevant documents."
