"""Scaffold smoke test — GREEN the moment the template is stamped, BEFORE any agent logic and WITHOUT a
crash. It exercises the offline-safe durability primitives (the on-disk store round-trips) and confirms
the editable ``core.generate_reply`` interface exists. The kill-and-resume behaviour is covered by the
harness's red-first criterion tests (which spawn subprocesses + inject an uncatchable crash).

``agentkit`` is the non-editable primitives library, so these imports stay valid no matter what the build
writes in ``core.py``.
"""
import uuid

from agentkit import TASK_STEPS, append_ledger, checkpoint, load_progress, read_ledger
from core import generate_reply


def test_task_steps_is_a_nonempty_ordered_list():
    assert TASK_STEPS and all(isinstance(s, str) and s for s in TASK_STEPS)


def test_durable_store_round_trips_progress_and_ledger():
    t = "smoke-" + uuid.uuid4().hex[:8]
    assert load_progress(t) == 0 and read_ledger(t) == []      # a fresh task starts empty
    append_ledger(t, 0)
    checkpoint(t, 1)                                            # no crash (PF_CRASH_AFTER unset)
    assert read_ledger(t) == [0] and load_progress(t) == 1     # both persisted durably


def test_generate_reply_interface_exists():
    assert callable(generate_reply)                            # the editable glue keeps the interface
