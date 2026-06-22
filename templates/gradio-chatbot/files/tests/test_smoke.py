"""Scaffold smoke test — GREEN the moment the template is stamped (before any feature code).

Imports only the pure core (no gradio, no network), so it runs fast in a fresh VM and proves the
core/UI split holds. The harness's red-first iteration tests live OUTSIDE the workspace (staged) and
are mounted read-only.
"""
from core import generate_reply


def test_reply_is_nonempty_string():
    out = generate_reply("hello", [])
    assert isinstance(out, str) and out.strip()


def test_reply_handles_empty_message():
    out = generate_reply("", [])
    assert isinstance(out, str) and out.strip()


def test_reply_accepts_none_history():
    assert isinstance(generate_reply("hi"), str)
