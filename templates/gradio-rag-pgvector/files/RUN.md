# Run this PoC

A RAG chatbot that retrieves from a real **pgvector** database. The retrieval logic lives in
`core.py` (pure-ish, importable); `app.py` is just the Gradio UI.

This PoC needs a **pgvector** sibling running and reachable. The build harness provides it and injects
its address as `PF_SERVICE_PG_HOST` (reached by IP). To run it yourself, `docker compose up` (below)
starts pgvector alongside the app.

The clean-room harness extracts and runs the blocks marked `<!-- pf:install -->`, `<!-- pf:test -->`,
and `<!-- pf:demo -->` (the harness has already exported `PF_SERVICE_PG_HOST` into the environment).

## 1. Install dependencies
Installs into a local `.deps/` dir (no root needed; the sandbox runs as a non-root user).
<!-- pf:install -->
```bash
uv pip install --target .deps -r requirements.txt
```

## 2. Run the test suite
Needs `PF_SERVICE_PG_HOST` pointing at a running pgvector (the harness sets it; locally use compose).
<!-- pf:test -->
```bash
PYTHONPATH=.deps python -m pytest -q
```

## 3. Smoke the UI wiring (does not launch a server)
<!-- pf:demo -->
```bash
PYTHONPATH=.deps python -c "import app; print('UI module imports OK')"
```

## 4. Launch the full stack (interactive)
<!-- pf:run -->
```bash
docker compose up --build
```

Then open http://localhost:7860. (`docker compose` starts pgvector + the app; the app reads
`PF_SERVICE_PG_HOST=pg`, the compose service name.)
