"""Structured build events — the M3 web-UI seam (design §5.12: ``event_callbacks → handler → queue
→ SSE → React SPA``).

The presentation layer (the web UI) watches a build's progress WITHOUT reaching into the pipeline
(rule #5: presentation holds NO pipeline logic). The seam is one optional callable carried on ``Ctx``
(``ctx.events``): ``Ctx.say`` and the graph node boundaries push small structured events through it;
the ``RunManager`` wires that callable to an in-process queue the SSE endpoint drains. When no sink is
set — the CLI path — emitting is a no-op beyond the existing stderr stream, so the headless contract
is unchanged.

Pure stdlib (no pydantic import, no agent stack) → ``py_compile``-able on the 3.10 dev box and import
of this module never pulls a heavy dep. ``snapshot`` reads a ``BuildState`` purely via ``getattr`` so
it neither imports the state model nor couples to it.
"""
from __future__ import annotations

import time
from typing import Any, Callable

# An event sink is just a callable taking one serialized event dict. Typed here for readers; we never
# isinstance-check it (duck-typed; a plain function or a bound ``queue.put`` both qualify).
EventSink = Callable[[dict], None]


def make_event(etype: str, build_id: str = "", **payload: Any) -> dict:
    """A JSON-serializable event. ``type`` drives the SPA's dispatch; ``ts`` is wall-clock seconds.

    Conventions (the only types the UI dispatches on):
      - ``start``     a run began            {source, kind: build|resume}
      - ``node``      a graph node boundary  {node, snapshot}     ← drives the live slice board
      - ``log``       a ``Ctx.say`` line     {line}
      - ``end``       the run finished        {status, artifact_id, demonstrates}
      - ``error``     the run raised          {error}
    """
    return {"type": etype, "build_id": build_id, "ts": time.time(), **payload}


def snapshot(state: Any) -> dict:
    """Slice-board projection of a ``BuildState`` — the fields the live board renders, nothing more.
    Pure presentation shaping (NOT pipeline logic); ``getattr`` throughout so a partial/early state or
    a plain stand-in never raises. The board flips a criterion green as ``success_criteria[].status``
    moves ``pending → met``; iteration records advance ``pending → green|descoped|abandoned``."""
    spec = getattr(state, "spec", None)
    criteria = []
    if spec is not None:
        for c in getattr(spec, "success_criteria", []) or []:
            criteria.append({
                "text": getattr(c, "text", ""),
                "status": getattr(c, "status", "pending"),
                "core": bool(getattr(c, "core", False)),
            })
    plan = getattr(state, "plan", None)
    iterations = []
    if plan is not None:
        for it in getattr(plan, "iterations", []) or []:
            iterations.append({"goal": getattr(it, "goal", ""),
                               "acceptance": list(getattr(it, "acceptance", []) or [])})
    records = []
    for r in getattr(state, "iteration_records", []) or []:
        records.append({"goal": getattr(r, "goal", ""), "status": getattr(r, "status", "pending"),
                        "attempts": getattr(r, "attempts", 0)})
    return {
        "phase": getattr(state, "phase", ""),
        "status": getattr(state, "status", ""),
        "iteration": getattr(state, "iteration", 0),
        "verdict": getattr(state, "verdict", ""),
        "goal": getattr(spec, "goal", "") if spec is not None else "",
        "criteria": criteria,
        "iterations": iterations,
        "iteration_records": records,
        "descope_report": [dict(d) for d in getattr(state, "descope_report", []) or []],
        "caps_hit": list(getattr(state, "caps_hit", []) or []),
    }


def emit(sink: EventSink | None, event: dict) -> None:
    """Push an event through the sink, swallowing any error. Tracing-grade tolerance: the event seam
    must NEVER take down a build (mirrors ``tracing``/hint-write discipline). A no-op when no sink."""
    if sink is None:
        return
    try:
        sink(event)
    except Exception:  # noqa: BLE001 — a flaky subscriber must not crash the pipeline
        pass


def sse_format(event: dict) -> str:
    """Serialize one event as an SSE frame (``event:`` + ``data:`` + blank line). The ``type`` doubles
    as the SSE event name so the SPA can ``addEventListener(type, …)`` or read ``data`` generically."""
    import json

    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event)}\n\n"
