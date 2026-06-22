"""Core logic for a RAG-over-pgvector chatbot PoC — PURE-ish and importable; ``app.py`` is just the UI.

This PoC retrieves from a REAL **pgvector** sibling service (Postgres + the `vector` extension),
reached BY IP via the harness-injected ``PF_SERVICE_PG_HOST`` (Kata VMs have no container-name DNS).
Embeddings are a deterministic stdlib hashing trick — NO model, NO network — so retrieval is
reproducible and unit-testable; the nearest-neighbour search itself runs in pgvector.

The scaffold ships the DB plumbing (`_connect` / `_ensure_corpus` / `search`) working against a tiny
fixed corpus, plus a STUB ``generate_reply``. Build iterations implement ``generate_reply`` on top of
``search`` (retrieve → format a reply with a citation marker). Importing this module does nothing and
touches no DB; ``psycopg`` is imported lazily so the stdlib smoke test runs without a database.
"""
from __future__ import annotations

import hashlib
import os
import time

EMBED_DIM = 64

# The PoC's fixed knowledge base (id, title, content). Distinct keywords per doc so retrieval is clear.
CORPUS = [
    {"id": 1, "title": "Python",
     "content": "Python is a high level programming language known for readability and a large ecosystem of libraries."},
    {"id": 2, "title": "Rust",
     "content": "Rust is a systems programming language focused on memory safety and performance without a garbage collector."},
    {"id": 3, "title": "PostgreSQL",
     "content": "PostgreSQL is an advanced open source relational database with strong SQL support and powerful extensions."},
]


def _embed(text: str) -> list[float]:
    """Deterministic stdlib embedding: hash each alphanumeric token into one of EMBED_DIM buckets
    (bag-of-words), then L2-normalize. Pure + reproducible (no model, no network) — good enough for
    nearest-neighbour over a tiny corpus, and trivially unit-testable."""
    vec = [0.0] * EMBED_DIM
    cleaned = "".join(c.lower() if c.isalnum() else " " for c in text)
    for tok in cleaned.split():
        bucket = int(hashlib.sha1(tok.encode()).hexdigest(), 16) % EMBED_DIM
        vec[bucket] += 1.0
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec] if norm else vec


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


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
    """Idempotently create the vector table + extension and seed the corpus (safe to call every time;
    so a fresh clean-room DB self-seeds on first query)."""
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
    """The k nearest corpus documents to ``query`` (pgvector L2 distance). Returns
    ``[{id, title, content}]``. Opens a connection, ensures the corpus, queries, closes."""
    conn = _connect()
    try:
        _ensure_corpus(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT id, title, content FROM docs ORDER BY embedding <-> %s::vector LIMIT %s",
                        (_vec_literal(_embed(query)), k))
            return [{"id": r[0], "title": r[1], "content": r[2]} for r in cur.fetchall()]
    finally:
        conn.close()


def generate_reply(message: str, history: list | None = None) -> str:
    """SCAFFOLD STUB — build iterations implement retrieval + citation HERE using ``search``.

    Target behaviour (what the iterations build): retrieve the most relevant corpus document(s) for
    ``message`` via ``search`` and return a reply that cites them with a ``[id]`` marker referencing
    the matched document. The scaffold stub below does no retrieval (so a real criterion test is RED
    first)."""
    return "I couldn't find any relevant documents."
