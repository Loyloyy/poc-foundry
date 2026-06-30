"""Gradio UI for the tool-calling chatbot PoC — a thin wrapper over ``core.generate_reply``.

All logic lives in ``core.py`` (it composes the toolkit primitives into a tool-calling reply).
Importing this module does NOT launch a server or touch the network/tool/model (the smoke test imports
it). Run ``python app.py`` to launch. Analytics + CDN fonts are disabled via the env in the image.
"""
from __future__ import annotations

import gradio as gr

from core import generate_reply


def _respond(message, history):
    return generate_reply(message, history)


demo = gr.ChatInterface(fn=_respond, title="Tool-calling chatbot (private catalogue)")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
