"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any glue and WITHOUT a
database OR a model. It exercises only the pure, offline helpers (`_embed`, `snippet`, `cite`, and the
lexical gate of `retrieve` for an unrelated query, which short-circuits before any pgvector/model
call). The DB-backed retrieval + the REAL LLM generation in `generate_reply` are covered by the
harness's red-first criterion tests, which run with the pgvector sibling + the model endpoint up.
"""
import math

from core import EMBED_DIM, CORPUS, _embed, cite, retrieve, snippet


def test_embed_is_deterministic_and_normalized():
    assert _embed("vector retrieval") == _embed("vector retrieval")
    assert len(_embed("pgvector")) == EMBED_DIM
    assert abs(math.sqrt(sum(x * x for x in _embed("grounded citation"))) - 1.0) < 1e-6


def test_snippet_is_a_verbatim_substring_of_at_least_three_words():
    doc = CORPUS[0]
    s = snippet(doc)
    assert s in doc["content"] and len(s.split()) >= 3


def test_cite_marks_the_document_id_as_an_integer():
    assert cite({"id": 7}) == "[7]"


def test_retrieve_gates_out_an_unrelated_query_without_a_db_or_model():
    # no shared vocabulary → [] via the lexical gate, BEFORE any pgvector/model call (needs neither).
    assert retrieve("weather forecast tomorrow") == []
