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


def test_model_endpoint_actually_answers_when_configured():
    """Guard the model-calling contract: when a model endpoint IS configured (the build/clean-room VMs
    inject PF_SANDBOX_MODEL_BASE_URL), `_answer` MUST construct its client and return non-empty model
    output. This fails the build on a broken client (e.g. an openai/httpx `proxies` mismatch) or an
    unreachable/misconfigured endpoint — instead of `generate_reply` silently degrading to the snippet
    fallback. Skipped when no endpoint is set (offline dev / dockerless CI), so it never blocks those."""
    import os

    import pytest

    if not os.environ.get("PF_SANDBOX_MODEL_BASE_URL"):
        pytest.skip("no PF_SANDBOX_MODEL_BASE_URL configured — offline; model call not exercised")
    from core import _answer
    out = _answer("Reply briefly.", "pgvector adds a vector column type to Postgres.")
    assert isinstance(out, str) and out.strip(), (
        "model endpoint is configured but _answer returned empty — the model call is broken "
        "(client construction, reachability, or token budget), not a content issue")
