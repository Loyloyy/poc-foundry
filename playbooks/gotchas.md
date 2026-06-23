# Gotchas playbook (coder)

Cross-cutting traps observed in real builds. Short, concrete, reusable.

- **Never edit the test.** The staged tests are mounted read-only and the harness scans your diff — a
  tampering edit fails the attempt and burns a fix. Change the implementation, not the assertion.
- **Empty / odd input.** Handle `""`, `None`, and whitespace-only messages without raising — several
  criteria probe these explicitly.
- **Citations must be grounded.** If a criterion wants a citation marker, derive it from the actually
  retrieved document, not a hard-coded constant — a test may check the marker maps to a real source.
- **Imports.** Tests run with `PYTHONPATH=/work`; import the core as `from core import generate_reply`.
  Don't import gradio or open sockets in the gated path.
- **Same failure twice → change approach.** If the same error signature repeats, the harness forces a
  strategy change — diagnose the root cause instead of re-submitting a near-identical edit.
