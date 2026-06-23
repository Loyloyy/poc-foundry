"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any retrieval glue and
WITHOUT a database. It exercises only the pure, DB-free helpers (`_embed`, `snippet`, `cite`, and the
lexical relevance gate of `retrieve` for an unrelated query, which short-circuits before touching
pgvector). The DB-backed positive path (`search` / a matching `retrieve`) + `generate_reply` are
covered by the harness's red-first criterion tests, which run with the pgvector sibling up.
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


def test_cite_marks_the_document_id():
    assert cite({"id": 7}) == "[7]"


def test_retrieve_gates_out_an_unrelated_query_without_a_db():
    # no shared vocabulary → [] via the lexical gate, BEFORE any pgvector call (so this needs no DB).
    assert retrieve("weather forecast tomorrow") == []
