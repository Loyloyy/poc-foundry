"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any RAG logic and WITHOUT a
database. It exercises ONLY the provided primitives that work offline: ``_embed`` (the baked embedding
model, loaded from the image cache) and the ``CORPUS`` shape, plus a cheap offline guard that the model
client can be constructed. The DB-backed ``search`` and the real ``llm`` round-trip are covered by the
harness's red-first criterion tests, which run with the pgvector sibling + the model endpoint up.
"""
from core import EMBED_DIM, CORPUS, _embed


def test_embed_is_deterministic_and_has_the_model_dim():
    # real semantic embedding via the baked model — deterministic (same text → same vector)
    assert _embed("vector retrieval") == _embed("vector retrieval")
    assert len(_embed("pgvector")) == EMBED_DIM   # 384 (bge-small-en-v1.5)


def test_corpus_is_nonempty_documents():
    assert CORPUS and all({"id", "title", "content"} <= set(d) for d in CORPUS)


def test_model_client_constructs_when_configured():
    """Guard the model-calling contract CHEAPLY + offline: when an endpoint is configured the OpenAI
    client must CONSTRUCT — this catches a broken client (e.g. the openai/httpx `proxies` version
    mismatch that silently disabled the model call). NO round-trip, so the scaffold stays fast and does
    NOT depend on the model being reachable; the actual answer round-trip is verified by the criterion
    tests in the iteration VMs. Skipped when no endpoint is set (offline dev), so it never blocks."""
    import os

    import pytest

    if not os.environ.get("PF_SANDBOX_MODEL_BASE_URL"):
        pytest.skip("no PF_SANDBOX_MODEL_BASE_URL configured — offline")
    from openai import OpenAI
    OpenAI(base_url=os.environ["PF_SANDBOX_MODEL_BASE_URL"],
           api_key=os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed"))   # raises on the proxies break
