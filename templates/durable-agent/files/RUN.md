# Run this PoC

A **durable agent**: a chatbot that runs a multi-step workflow per task, checkpointing progress to disk so
that if it is KILLED mid-run and re-invoked, it RESUMES from the last checkpoint (each step runs exactly
once) instead of restarting. The logic lives in `core.py`; `app.py` is the Gradio UI; `agentkit.py` is the
durable-store library. No database, no model, no sibling service — durability is a local file.

The clean-room harness extracts and runs the blocks marked `<!-- pf:install -->`, `<!-- pf:test -->`, and
`<!-- pf:demo -->`.

## 1. Install dependencies
Installs into a local `.deps/` dir (no root needed; the sandbox runs as a non-root user).
<!-- pf:install -->
```bash
uv pip install --target .deps -r requirements.txt
```

## 2. Run the test suite
The durability tests spawn subprocesses and inject an UNCATCHABLE crash (`PF_CRASH_AFTER`) to prove
kill-and-resume; they read the durable store back from a fresh process.
<!-- pf:test -->
```bash
PYTHONPATH=.deps python -m pytest -q
```

## 3. Launch the UI and verify it actually serves a page
Starts the Gradio server in the background, waits for it to answer on :7860, then stops it.
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
docker compose up --build
```

Then open http://localhost:7860 and type a task id (e.g. `order-42`) to run its workflow. Kill the app
(`docker compose kill app`) mid-run and bring it back (`docker compose up`) — re-sending the same task id
resumes it from where it stopped, running no step twice. State persists on the `agent_state` volume.
