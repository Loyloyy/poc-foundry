"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any retrieval code and
WITHOUT a database. It exercises only the pure stdlib embedding (`_embed`); the DB-backed `search` +
`generate_reply` are covered by the harness's red-first criterion tests (which run with the pgvector
sibling up). No network, no psycopg import here.
"""
import math

from core import EMBED_DIM, _embed


def test_embed_is_deterministic():
    assert _embed("python is great") == _embed("python is great")


def test_embed_has_fixed_dimension():
    assert len(_embed("rust and postgres")) == EMBED_DIM


def test_embed_is_l2_normalized():
    norm = math.sqrt(sum(x * x for x in _embed("postgresql database")))
    assert abs(norm - 1.0) < 1e-6


def test_distinct_tokens_give_distinct_vectors():
    assert _embed("python") != _embed("rust")
