"""Gradio UI for the RAG-over-pgvector chatbot PoC — thin wrapper over ``core.generate_reply``.

All logic lives in ``core.py`` (retrieval over the pgvector sibling). Importing this module does NOT
launch a server or touch the network (the smoke test imports it). Run ``python app.py`` to launch.
Analytics + CDN fonts are disabled via the env (GRADIO_ANALYTICS_ENABLED), set in the image.
"""
from __future__ import annotations

import gradio as gr

from core import generate_reply


def _respond(message, history):
    return generate_reply(message, history)


demo = gr.ChatInterface(fn=_respond, title="RAG over pgvector (PoC)")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
