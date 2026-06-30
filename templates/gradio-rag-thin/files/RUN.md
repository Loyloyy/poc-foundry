# Run this PoC

A RAG chatbot that retrieves from a real **pgvector** database and then asks a **real model** to write
a grounded answer. The logic lives in `core.py`; `app.py` is just the Gradio UI.

This PoC needs two things reachable: a **pgvector** sibling (`PF_SERVICE_PG_HOST`) and an
**OpenAI-compatible model endpoint** (`PF_SANDBOX_MODEL_BASE_URL` + `PF_SANDBOX_VLLM_KEY`). The build
harness provides both and injects them; to run it yourself, `docker compose up` starts pgvector and you
supply the model endpoint via the environment (see below).

The clean-room harness extracts and runs the blocks marked `<!-- pf:install -->`, `<!-- pf:test -->`,
and `<!-- pf:demo -->` (the harness has already exported `PF_SERVICE_PG_HOST` and
`PF_SANDBOX_MODEL_BASE_URL`/`PF_SANDBOX_VLLM_KEY` into the environment).

## 1. Install dependencies
Installs into a local `.deps/` dir (no root needed; the sandbox runs as a non-root user).
<!-- pf:install -->
```bash
uv pip install --target .deps -r requirements.txt
```

## 2. Run the test suite
Needs `PF_SERVICE_PG_HOST` (pgvector) and `PF_SANDBOX_MODEL_BASE_URL` (the model) — the harness sets
both; locally use compose + your own model endpoint.
<!-- pf:test -->
```bash
PYTHONPATH=.deps python -m pytest -q
```

## 3. Launch the UI and verify it actually serves a page
Starts the Gradio server in the background, waits for it to answer on :7860, then stops it. This
catches UI-launch regressions (e.g. an unpinned web-stack dependency); the page render does NOT call
the model (that happens per chat message), so this needs no model endpoint.
<!-- pf:demo -->
```bash
PYTHONPATH=.deps python app.py & APP_PID=$!
PYTHONPATH=.deps python -c "
import time, sys, urllib.request
for _ in range(25):
    try:
        urllib.request.urlopen('http://localhost:7860/', timeout=2)
        print('UI launched + served a page OK'); break
    except Exception:
        time.sleep(1)
else:
    sys.exit(1)
"
RC=$?
kill $APP_PID 2>/dev/null || true
exit $RC
```

## 4. Launch the full stack (interactive)
<!-- pf:run -->
```bash
export PF_SANDBOX_MODEL_BASE_URL="http://<your-model-host:port>/v1"   # OpenAI-compatible
export PF_SANDBOX_VLLM_KEY="not-needed"                               # any non-empty string for a keyless vLLM
docker compose up --build
```

Then open http://localhost:7860. (`docker compose` starts pgvector + the app; the app reads
`PF_SERVICE_PG_HOST=pg` and the two `PF_SANDBOX_MODEL_*` vars you exported.)
