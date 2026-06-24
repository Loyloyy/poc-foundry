"""Single-slot run controller for the web UI (M3 S1; design §5.12).

ONE build runs at a time — matching the runtime reality (one vLLM, build-id-scoped broker nets);
a second concurrent start raises ``RunBusy`` → the server answers **409**. Concurrent builds are out
of scope for M3. The manager:

  - launches ``build_poc`` / ``resume_build`` on a background thread, wiring an **event sink** that
    fans every structured event (design §5.12 seam) out to all SSE subscribers;
  - keeps a small **replay buffer** so a late- or re-connecting SPA immediately sees the run so far;
  - exposes ``stop`` → the cooperative-stop sentinel via ``request_stop_build`` (M2b S4 — already
    checkpoint-backed; NOT reimplemented here).

Pure stdlib (threading + queue) so it ``py_compile``s and runs under the no-pytest fakes on the 3.10
box. The heavy ``core`` functions are injected (``build_fn`` / ``resume_fn`` / ``stop_fn``); the
defaults lazy-import ``poc_foundry.core`` so importing THIS module never pulls the agent stack — the
fakes pass stand-ins instead. The web layer holds no pipeline logic (rule #5): it only calls core.
"""
from __future__ import annotations

import threading
from queue import Empty, Queue
from typing import Any, Callable

from poc_foundry.events import emit, make_event

_MAX_REPLAY = 2000   # cap the per-run replay buffer (a long build streams many log lines)


class RunBusy(Exception):
    """A run is already in progress (single-slot). The server maps this to HTTP 409."""


# default core bindings — lazy so importing this module stays import-light (rule #3)
def _default_build(source, *, event_sink, **kw):
    from poc_foundry.core import build_poc
    return build_poc(source, event_sink=event_sink, **kw)


def _default_resume(build_id, *, event_sink, **kw):
    from poc_foundry.core import resume_build
    return resume_build(build_id, event_sink=event_sink, **kw)


def _default_refine(build_id, *, event_sink, **kw):
    from poc_foundry.core import refine_build
    return refine_build(build_id, event_sink=event_sink, **kw)


def _default_stop(build_id):
    from poc_foundry.core import request_stop_build
    return request_stop_build(build_id)


class RunManager:
    def __init__(self, *, build_fn: Callable | None = None, resume_fn: Callable | None = None,
                 stop_fn: Callable | None = None, refine_fn: Callable | None = None):
        self._build_fn = build_fn or _default_build
        self._resume_fn = resume_fn or _default_resume
        self._refine_fn = refine_fn or _default_refine
        self._stop_fn = stop_fn or _default_stop
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._subscribers: list[Queue] = []
        self._replay: list[dict] = []
        self._current: dict[str, Any] | None = None   # the live/last run summary (None until first start)

    # ── lifecycle ────────────────────────────────────────────────────────────
    @property
    def busy(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, source: str, **kw) -> dict:
        """Start a fresh build. Raises ``RunBusy`` if one is already running."""
        return self._launch("build", lambda sink: self._build_fn(source, event_sink=sink, **kw),
                             {"kind": "build", "source": source})

    def resume(self, build_id: str, **kw) -> dict:
        """Resume a checkpointed build. Raises ``RunBusy`` if one is already running."""
        return self._launch("resume",
                             lambda sink: self._resume_fn(build_id, event_sink=sink, **kw),
                             {"kind": "resume", "build_id": build_id})

    def refine(self, build_id: str, **kw) -> dict:
        """Refine a finished build's descoped backlog (M4 S1). Raises ``RunBusy`` if one is running."""
        return self._launch("refine",
                             lambda sink: self._refine_fn(build_id, event_sink=sink, **kw),
                             {"kind": "refine", "build_id": build_id})

    def _launch(self, kind: str, call: Callable[[Callable], Any], summary: dict) -> dict:
        with self._lock:
            if self.busy:
                raise RunBusy(f"a {self._current.get('kind') if self._current else 'build'} is already "
                              f"running ({self._current.get('build_id') if self._current else '?'})")
            # a new run resets the replay buffer + subscriber feeds so the board starts clean
            self._replay = []
            self._current = {"state": "running", "build_id": summary.get("build_id", ""),
                             "phase": "", "status": "", "error": "", **summary}
            self._thread = threading.Thread(target=self._run, args=(call,), name=f"pf-{kind}",
                                            daemon=True)
            self._thread.start()
            return dict(self._current)

    def _run(self, call: Callable[[Callable], Any]) -> None:
        try:
            _report, artifact = call(self._publish)
            status = getattr(artifact, "status", "")
            demo = getattr(getattr(artifact, "final_verdict", None), "demonstrates_core_value", "")
            aid = getattr(artifact, "id", "")
            with self._lock:
                if self._current is not None:
                    self._current.update(state="finished", status=status, build_id=aid or
                                         self._current.get("build_id", ""))
            self._publish(make_event("end", aid, status=status, artifact_id=aid, demonstrates=demo))
        except Exception as e:  # noqa: BLE001 — surface the failure to the UI; the build folder has the forensic artifact
            with self._lock:
                bid = self._current.get("build_id", "") if self._current else ""
                if self._current is not None:
                    self._current.update(state="error", error=f"{type(e).__name__}: {e}")
            self._publish(make_event("error", bid, error=f"{type(e).__name__}: {e}"))

    # ── event fan-out ────────────────────────────────────────────────────────
    def _publish(self, event: dict) -> None:
        """The event sink handed to ``build_poc``: record + fan out to every subscriber. Tracks the
        live ``build_id``/``phase`` from the stream so ``status`` is current even before the thread
        ends. Never raises into the pipeline (the ``events.emit`` wrapper guards the call site)."""
        with self._lock:
            self._replay.append(event)
            if len(self._replay) > _MAX_REPLAY:
                del self._replay[: len(self._replay) - _MAX_REPLAY]
            if self._current is not None:
                if event.get("build_id"):
                    self._current["build_id"] = event["build_id"]
                snap = event.get("snapshot")
                if isinstance(snap, dict) and snap.get("phase"):
                    self._current["phase"] = snap["phase"]
            subs = list(self._subscribers)
        for q in subs:
            q.put(event)

    def subscribe(self) -> Queue:
        """Register an SSE feed, pre-loaded with the current run's events so far (replay)."""
        q: Queue = Queue()
        with self._lock:
            for ev in self._replay:
                q.put(ev)
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    # ── control / introspection ──────────────────────────────────────────────
    def stop(self, build_id: str | None = None) -> dict:
        """Request a cooperative stop of the running build (or an explicit id). Returns the target.
        Idempotent — a no-op-but-OK if nothing is running."""
        with self._lock:
            target = build_id or (self._current.get("build_id") if self._current else None)
        if not target:
            return {"stopped": False, "reason": "no build id / nothing running"}
        self._stop_fn(target)
        return {"stopped": True, "build_id": target}

    def status(self) -> dict:
        with self._lock:
            cur = dict(self._current) if self._current else {"state": "idle"}
            cur["busy"] = self.busy
            return cur

    @staticmethod
    def drain(q: Queue, timeout: float = 0.5):
        """Yield queued events, blocking up to ``timeout`` for the next. Yields a keep-alive ``None``
        on idle so the SSE generator can send a comment frame and notice client disconnects."""
        try:
            yield q.get(timeout=timeout)
        except Empty:
            yield None
