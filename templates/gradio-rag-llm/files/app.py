"""Gradio UI for the RAG-with-LLM chatbot PoC — a thin wrapper over ``core.generate_reply``.

All logic lives in ``core.py`` (retrieval over the pgvector sibling + a real model call for the answer).
Importing this module does NOT launch a server or touch the network/model (the smoke test imports it).
Run ``python app.py`` to launch. Analytics + CDN fonts are disabled via the env in the image.
"""
from __future__ import annotations

import gradio as gr

from core import generate_reply


def _respond(message, history):
    return generate_reply(message, history)


demo = gr.ChatInterface(fn=_respond, title="RAG + LLM over pgvector (PoC)")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
