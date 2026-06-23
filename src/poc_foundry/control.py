"""Cooperative stop control (M2b S4; design §5.9).

A ``poc-foundry stop <id>`` writes a sentinel file under the build folder; the running LangGraph
checks it at every node boundary (``graph.py`` ``wrap``) and raises ``BuildStopped``. Like
``BudgetExceeded``, that is a ``BaseException`` ON PURPOSE — it must escape the phases' broad
``except Exception`` guards to unwind the run. The graph checkpoints AFTER each completed node, so a
stop raised at the START of the next node leaves a consistent last-checkpoint state → the build is
``resume``-able (``resume`` clears the sentinel first). Pure stdlib → ``py_compile``-able on 3.10.
"""
from __future__ import annotations

from pathlib import Path

_SENTINEL = ".stop"


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


def raise_if_stopped(build_dir: str | Path) -> None:
    """Called at each graph node boundary; raises ``BuildStopped`` iff a stop was requested."""
    if stop_requested(build_dir):
        raise BuildStopped()
