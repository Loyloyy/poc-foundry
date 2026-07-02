"""core — the EDITABLE glue for the durable agent. The durability primitives live in ``agentkit`` (a
library you import, NOT edit). Your job: implement ``generate_reply`` as a DURABLE, RESUMABLE run of the
workflow. Do not reimplement the primitives or remove these imports.

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
