"""core — the EDITABLE glue for the RAG chatbot. The RAG primitives live in ``ragkit`` (a library you
import, NOT edit). Your job: implement ``generate_reply`` by COMPOSING the primitives. Do not
reimplement them, invent your own corpus, or remove these imports.

Available from ``ragkit`` (see that module's docstrings for details):
  • ``search(query, k=3) -> [{id, title, content, distance}]`` — ranked corpus docs (smaller distance
    = closer); it does NOT decide relevance.
  • ``llm(prompt, system=None) -> str`` — a real model completion (non-deterministic wording).
  • ``CORPUS`` — the fixed documents the answer may be grounded in.
"""
from __future__ import annotations

from ragkit import CORPUS, llm, search   # the provided primitives — use these, don't reinvent them


def generate_reply(message: str, history: list | None = None) -> str:
    """SCAFFOLD STUB — implement retrieval-augmented generation HERE by composing the ragkit primitives.

    A RAG reply should: find the relevant corpus document for the question (``search`` + your own
    relevance decision), ground the model's answer in that document (``llm``), make the answer
    VERIFIABLE against the document it used, and return a no-match reply when the question is outside
    the corpus. This stub does none of that, so a real criterion test is RED first.
    """
    return "not implemented"
