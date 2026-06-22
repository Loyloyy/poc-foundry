"""Core logic for the Gradio chatbot PoC — PURE and importable.

No UI, no network, nothing at import time. ``app.py`` (the Gradio UI) and the test suite both call
``generate_reply``. Keep this stdlib-only and deterministic so it is unit-testable without launching
the UI or reaching any service — that is what makes the PoC verifiable (the whole point of Stage 3).

The scaffold ships a working echo stub so the smoke test is GREEN before any feature is built; build
iterations replace/extend ``generate_reply`` to meet the spec's success criteria.
"""
from __future__ import annotations


def generate_reply(message: str, history: list | None = None) -> str:
    """Return the assistant's reply to ``message``.

    ``history`` is the prior chat turns (Gradio passes a list; may be ``None`` when called directly).
    Scaffold behaviour: a deterministic echo so the PoC runs and the smoke test passes from the
    start.
    """
    message = (message or "").strip()
    if not message:
        return "Say something and I'll respond."
    return f"You said: {message}"
