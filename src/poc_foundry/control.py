"""Cooperative stop control (M2b S4; design §5.9).

A ``poc-foundry stop <id>`` writes a sentinel file under the build folder; the running LangGraph
checks it at every node boundary (``graph.py`` ``wrap``) and raises ``BuildStopped``. Like
``BudgetExceeded``, that is a ``BaseException`` ON PURPOSE — it must escape the phases' broad
``except Exception`` guards to unwind the run. The graph checkpoints AFTER each completed node, so a
stop raised at the START of the next node leaves a consistent last-checkpoint state → the build is
``resume``-able (``resume`` clears the sentinel first). Pure stdlib → ``py_compile``-able on 3.10.
"""
from __future__ import annotations

import os
from pathlib import Path

_SENTINEL = ".stop"
_VISITS: dict[str, int] = {}   # per-process node-visit counts (for the deterministic test hook)


class BuildStopped(BaseException):
    """A cooperative stop was requested (the sentinel is present). ``BaseException`` so it escapes the
    phases' broad ``except Exception`` guards and unwinds cleanly to ``core`` (see ``BudgetExceeded``)."""


def stop_path(build_dir: str | Path) -> Path:
    return Path(build_dir) / _SENTINEL


def request_stop(build_dir: str | Path) -> None:
    p = stop_path(build_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("stop\n")


def stop_requested(build_dir: str | Path) -> bool:
    return stop_path(build_dir).exists()


def clear_stop(build_dir: str | Path) -> None:
    stop_path(build_dir).unlink(missing_ok=True)


def raise_if_stopped(build_dir: str | Path, node: str | None = None) -> None:
    """Called at each graph node boundary; raises ``BuildStopped`` iff a stop was requested — either
    via the sentinel (the real ``stop`` CLI path) OR the deterministic test hook ``PF_STOP_AT_NODE``
    (+ optional ``PF_STOP_AT_NODE_HITS``, default 1): stop on the Nth entry of the named node. The
    hook makes the kill/resume acceptance test reproducible WITHOUT racing a manual Ctrl-C — e.g.
    ``PF_STOP_AT_NODE=iterate PF_STOP_AT_NODE_HITS=2`` stops just before the 2nd iteration, so the
    checkpoint holds a completed iter0; ``resume`` (run WITHOUT the env) continues to completion."""
    if stop_requested(build_dir):
        raise BuildStopped()
    target = os.environ.get("PF_STOP_AT_NODE", "").strip()
    # accept "node" or a combined "node:hits" (one env var → one short `-e` on the server)
    hits_env = os.environ.get("PF_STOP_AT_NODE_HITS", "").strip()
    if ":" in target:
        target, _, hits_env = target.partition(":")
        target, hits_env = target.strip(), hits_env.strip()
    if node and target and node == target:
        _VISITS[node] = _VISITS.get(node, 0) + 1
        if _VISITS[node] >= (int(hits_env) if hits_env.isdigit() and int(hits_env) > 0 else 1):
            raise BuildStopped()


def reset_visits() -> None:
    _VISITS.clear()
