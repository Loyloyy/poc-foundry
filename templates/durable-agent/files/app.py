"""Gradio UI for the durable-agent PoC — a thin wrapper over ``core.generate_reply``.

All logic lives in ``core.py`` (a durable, resumable run of the workflow over the on-disk store).
Importing this module does NOT launch a server (the smoke test imports it). Run ``python app.py`` to
launch; type a task id (e.g. ``order-42``) to run/resume its workflow.
"""
from __future__ import annotations

import gradio as gr

from core import generate_reply


def _respond(message, history):
    return generate_reply(message, history)


demo = gr.ChatInterface(fn=_respond, title="Durable agent (kill-and-resume workflow)")


if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
