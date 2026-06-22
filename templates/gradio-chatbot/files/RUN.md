# Run this PoC

A Gradio chatbot. The logic lives in `core.py` (pure, importable); `app.py` is just the UI.

The clean-room harness extracts and runs the blocks marked `<!-- pf:install -->`, `<!-- pf:test -->`,
and `<!-- pf:demo -->` below — keep those markers immediately above their fenced `bash` block.

## 1. Install dependencies
Installs into a local `.deps/` dir (no root needed; the sandbox runs as a non-root user).
<!-- pf:install -->
```bash
uv pip install --target .deps -r requirements.txt
```

## 2. Run the test suite
<!-- pf:test -->
```bash
PYTHONPATH=.deps python -m pytest -q
```

## 3. Smoke the UI wiring (does not launch a server)
<!-- pf:demo -->
```bash
PYTHONPATH=.deps python -c "import app; print('UI module imports OK')"
```

## 4. Launch the app (interactive)
<!-- pf:run -->
```bash
PYTHONPATH=.deps python app.py
```

Then open http://localhost:7860.
