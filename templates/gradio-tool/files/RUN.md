# Run this PoC

A tool-calling chatbot: it answers product questions by CALLING a real **tool server** (a private
catalogue) and using its structured result, then phrases the answer with a **real model**. The logic
lives in `core.py`; `app.py` is just the Gradio UI; `toolserver/` is the sibling tool service.

This PoC needs two things reachable: the **tool server** (`PF_SERVICE_TOOLSERVER_HOST`) and an
**OpenAI-compatible model endpoint** (`PF_SANDBOX_MODEL_BASE_URL` + `PF_SANDBOX_VLLM_KEY`). The build
harness provides both and injects them; to run it yourself, `docker compose up` builds + starts the
tool server and you supply the model endpoint via the environment (see below).

The clean-room harness extracts and runs the blocks marked `<!-- pf:install -->`, `<!-- pf:test -->`,
and `<!-- pf:demo -->` (the harness has already exported `PF_SERVICE_TOOLSERVER_HOST` and the
`PF_SANDBOX_MODEL_*` vars into the environment).

## 1. Install dependencies
Installs into a local `.deps/` dir (no root needed; the sandbox runs as a non-root user).
<!-- pf:install -->
```bash
uv pip install --target .deps -r requirements.txt
```

## 2. Run the test suite
Needs `PF_SERVICE_TOOLSERVER_HOST` (the tool) and `PF_SANDBOX_MODEL_BASE_URL` (the model) — the harness
sets both; locally use compose + your own model endpoint.
<!-- pf:test -->
```bash
PYTHONPATH=.deps python -m pytest -q
```

## 3. Launch the UI and verify it actually serves a page
Starts the Gradio server in the background, waits for it to answer on :7860, then stops it. This
catches UI-launch regressions; the page render does NOT call the tool/model (that happens per chat
message), so this needs neither the tool nor the model endpoint.
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

Then open http://localhost:7860 and ask e.g. *"how much is the lattice router x1?"*. (`docker compose`
builds + starts the tool server and the app; the app reads `PF_SERVICE_TOOLSERVER_HOST=toolserver` and
the two `PF_SANDBOX_MODEL_*` vars you exported.)
