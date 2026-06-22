# AGENTS.md — continuing this PoC

This is an emitted **poc-foundry** bundle. It is a standalone git repo; you can continue it by hand
or in OpenCode.

## Layout
- `core.py` — pure logic. **Put new behaviour here**; keep it stdlib-friendly and importable.
- `app.py` — Gradio UI only. No logic.
- `tests/` — the suite. **Red-first**: add a failing test for new behaviour before implementing it.
- `requirements.txt` — pinned deps. Add deps deliberately.
- `RUN.md` — install / test / demo / run blocks.

## Working rules
1. Keep the **core/UI split**: logic in `core.py`, presentation in `app.py`.
2. Every feature gets a test in `tests/` first; make `python -m pytest -q` green before moving on.
3. Don't add network calls or services to `core.py` without a test that fakes them.
4. Keep `python app.py` runnable end-to-end.
