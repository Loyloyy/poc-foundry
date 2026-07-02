"""agentkit — DURABLE-EXECUTION primitives (a library, NOT editable; the editable glue is ``core.py``).

The state store is a LOCAL FILE on disk, so it SURVIVES a process crash — durable execution WITHOUT any
sibling service. Provided (import and use; do NOT reimplement):

  • ``TASK_STEPS`` — the fixed workflow: an ordered list of step names (like a corpus). The agent runs
    these steps for a task and must be able to RESUME if interrupted.
  • ``load_progress(task_id) -> int`` — how many steps are durably DONE for this task (0 if new). This is
    the RESUME POINTER: a correct agent starts from here, NOT from 0.
  • ``append_ledger(task_id, i)`` — durably APPEND step ``i`` to the task's execution log. NON-idempotent
    on purpose: re-running a completed step leaves a DUPLICATE in the ledger — that is how a broken
    resume is detected.
  • ``read_ledger(task_id) -> list`` — the durable execution log (used to verify exactly-once execution).
  • ``checkpoint(task_id, done)`` — durably record that ``done`` steps are complete. This is the durability
    BARRIER: call it AFTER a step's work so a crash always leaves a resumable state. In TESTS it may abort
    the PROCESS (see below); a real run just persists progress.

Crash injection (TEST-ONLY, and UNCATCHABLE by design): if the env var ``PF_CRASH_AFTER`` is set,
``checkpoint`` aborts the process with ``os._exit`` the instant ``done`` reaches that value — bypassing
``try/except/finally`` entirely. This simulates a real kill: the ONLY way to survive is durable disk
state read back by a FRESH process. A real chat never sets ``PF_CRASH_AFTER``. Do NOT try to catch it —
you cannot, and durability (not exception handling) is the point.
"""
from __future__ import annotations

import json
import os

# The fixed workflow the agent executes per task (ordered). The agent must run each step exactly once,
# resuming after an interruption rather than restarting.
TASK_STEPS = ["validate", "reserve", "charge", "pack", "ship"]


def _state_dir() -> str:
    d = os.environ.get("PF_AGENT_STATE_DIR", "/tmp/pf_agent_state")
    os.makedirs(d, exist_ok=True)
    return d


def _path(task_id: str) -> str:
    safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(task_id))[:80] or "task"
    return os.path.join(_state_dir(), safe + ".json")


def _read(task_id: str) -> dict:
    try:
        with open(_path(task_id)) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {"progress": 0, "ledger": []}


def _write(task_id: str, state: dict) -> None:
    tmp = _path(task_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, _path(task_id))          # atomic — a crash never leaves a half-written file


def load_progress(task_id: str) -> int:
    """Durably-recorded count of completed steps for ``task_id`` (0 if new). The RESUME pointer."""
    return int(_read(task_id).get("progress", 0))


def append_ledger(task_id: str, i: int) -> None:
    """Durably append step ``i`` to the task's execution log (non-idempotent — a re-run shows a dup)."""
    s = _read(task_id)
    s.setdefault("ledger", []).append(int(i))
    _write(task_id, s)


def read_ledger(task_id: str) -> list:
    """The durable execution log for ``task_id`` — e.g. ``[0, 1, 2, 3, 4]`` when each step ran once."""
    return list(_read(task_id).get("ledger", []))


def checkpoint(task_id: str, done: int) -> None:
    """Durably record that ``done`` steps are complete (the durability barrier — call AFTER a step's
    work). TEST-ONLY: if ``PF_CRASH_AFTER == done`` this aborts the PROCESS with ``os._exit`` (UNCATCHABLE
    — no ``finally``/``except`` runs), simulating a real crash. Only durable disk state survives."""
    s = _read(task_id)
    s["progress"] = int(done)
    _write(task_id, s)
    crash_at = os.environ.get("PF_CRASH_AFTER")
    if crash_at and int(crash_at) == int(done):
        os._exit(137)                        # SIGKILL-ish: uncatchable, immediate — durability is the only escape
