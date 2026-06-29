"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any glue and WITHOUT a
database. It exercises the DB-free helpers: `_embed` (the baked embedding model, loaded offline from
the image cache), `snippet`, and `cite`. The DB-backed SEMANTIC retrieval + the REAL LLM generation in
`generate_reply` are covered by the harness's red-first criterion tests, which run with the pgvector
sibling + the model endpoint up.
"""
from core import EMBED_DIM, CORPUS, _embed, cite, snippet


def test_embed_is_deterministic_and_has_the_model_dim():
    # real semantic embedding via the baked model — deterministic (same text → same vector)
    assert _embed("vector retrieval") == _embed("vector retrieval")
    assert len(_embed("pgvector")) == EMBED_DIM   # 384 (bge-small-en-v1.5)


def test_snippet_is_a_verbatim_substring_of_at_least_three_words():
    doc = CORPUS[0]
    s = snippet(doc)
    assert s in doc["content"] and len(s.split()) >= 3


def test_cite_marks_the_document_id_as_an_integer():
    assert cite({"id": 7}) == "[7]"


def test_model_client_constructs_when_configured():
    """Guard the model-calling contract CHEAPLY + offline: when an endpoint is configured the OpenAI
    client must CONSTRUCT — this catches a broken client (e.g. the openai/httpx `proxies` version
    mismatch that silently disabled the model call). NO round-trip, so the scaffold stays fast and does
    NOT depend on the model being reachable or fast at scaffold time; the actual answer round-trip is
    verified by the criterion tests in the iteration VMs. Skipped when no endpoint is set (offline
    dev / dockerless CI), so it never blocks those."""
    import os

    import pytest

    if not os.environ.get("PF_SANDBOX_MODEL_BASE_URL"):
        pytest.skip("no PF_SANDBOX_MODEL_BASE_URL configured — offline")
    from openai import OpenAI
    OpenAI(base_url=os.environ["PF_SANDBOX_MODEL_BASE_URL"],
           api_key=os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed"))   # raises on the proxies break
