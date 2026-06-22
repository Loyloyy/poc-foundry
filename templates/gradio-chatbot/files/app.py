"""Gradio chat UI over ``core.generate_reply`` — the thin presentation layer (no logic here).

Run: ``python app.py`` (serves on 0.0.0.0:7860; override with ``PORT``). Importing this module does
NOT launch the server (guarded by ``__main__``), so the clean-room can prove the UI wires up without
hanging on a blocking server.
"""
from __future__ import annotations

import os

# Kill the phone-home + CDN fonts before importing gradio (design §5.3 — also set in the image env).
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

import gradio as gr  # noqa: E402 — must follow the env guard above

from core import generate_reply


def _respond(message: str, history: list) -> str:
    return generate_reply(message, history)


demo = gr.ChatInterface(fn=_respond, title="PoC Chatbot", analytics_enabled=False)


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=int(os.environ.get("PORT", "7860")))
