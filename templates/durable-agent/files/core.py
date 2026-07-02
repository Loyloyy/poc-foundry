"""core — the EDITABLE glue for the durable agent. The durability primitives live in ``agentkit`` (a
library you import, NOT edit). Your job: implement ``generate_reply`` as a DURABLE, RESUMABLE run of the
workflow. Do not reimplement the primitives or remove these imports.

THE CRASH IS INJECTED FOR YOU. A criterion test kills the process mid-run by setting ``PF_CRASH_AFTER`` —
``agentkit.checkpoint`` reads that env var and aborts the process itself. You do NOT implement the crash.
NEVER call ``os._exit`` / ``sys.exit`` / ``raise SystemExit`` in this file (the test asserts the KILLED
subprocess exited non-zero, but that exit comes from ``checkpoint`` — not from you): a hard exit here is
flagged as an integrity incident and your whole iteration is rolled back. Your ONLY job is the resume
loop — read ``load_progress``, and for each REMAINING step do the work (``append_ledger``) THEN
``checkpoint`` the new progress. That is what makes a fresh process resume exactly-once.

Available from ``agentkit`` (see that module's docstrings):
  • ``TASK_STEPS`` — the ordered workflow (run each step exactly once).
  • ``load_progress(task_id) -> int`` — steps already done (the RESUME pointer; start from here, not 0).
  • ``append_ledger(task_id, i)`` — durably log that step ``i`` executed.
  • ``checkpoint(task_id, done)`` — durably record progress AFTER a step (the durability barrier; may be
    killed mid-run in tests — uncatchably).
"""
from __future__ import annotations

from agentkit import TASK_STEPS, append_ledger, checkpoint, load_progress   # provided — use these


def generate_reply(message: str, history: list | None = None) -> str:
    """SCAFFOLD STUB — implement a DURABLE, RESUMABLE workflow run HERE.

    Treat ``message`` as a task id. The agent runs the ``TASK_STEPS`` for that task, but it can be KILLED
    mid-run (uncatchably) and re-invoked — it MUST then RESUME from where it left off, executing each step
    EXACTLY ONCE (never re-running a completed step, never skipping one). The durable, correct pattern:
    read ``load_progress(task_id)`` as the resume point, and for each REMAINING step do the work
    (``append_ledger``) and THEN ``checkpoint`` the new progress — so a crash always leaves an
    exactly-once-resumable state. A stub that restarts from 0 re-logs completed steps (duplicates) and
    fails; one that skips fails to complete. This stub does nothing, so a real criterion test is RED first.
    """
    return "not implemented"
