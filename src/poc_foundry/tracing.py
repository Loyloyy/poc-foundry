"""Tolerated-absent observability (design §5.11) — manual Langfuse spans around the half the LangChain
callback handler can't see.

The LLM calls go through ``models.py`` (``chat_text`` is a raw urllib call — no LangChain handler at
all), and the load-bearing work happens in the broker (provision/exec/destroy), VERIFY, the integrity
gates, the critic, the clean-room, and proxy denials. None of that is visible to a LangChain handler,
so we instrument it MANUALLY here.

Mirrors the Stage-2 ``tracing.py`` discipline (env-gated, lazy-imported, tolerated-absent): if
``PF_TRACING`` is off, or ``langfuse`` is not installed, or the creds are missing/misconfigured, EVERY
function degrades to a safe no-op and NOTHING crashes a build (rule: tracing must never take down a
run). Self-hosted only — creds point at an in-network Langfuse via ``LANGFUSE_HOST``/``_PUBLIC_KEY``/
``_SECRET_KEY`` (project ``stage-3-poc``, separate keys from Stage-2). NO secrets / model / host
literals here — all env-driven. ``build_env.json`` (gitignored) preserves the real model bindings for
attribution.

Import-light: ``langfuse`` is imported lazily inside ``_init_tracer`` so this module ``py_compile``s
on the 3.10 dev box and importing it never pulls the obs dep.

Usage::

    from poc_foundry import tracing
    with tracing.build(build_id, tags=[driver, template]):
        with tracing.span("broker.exec", cmd=cmd) as sp:
            ...
            sp.update(output=result)
        tracing.event("proxy.denials", count=3)
    tracing.flush()                       # mandatory before an ephemeral process exits
"""
from __future__ import annotations

import contextlib
import logging
import os

logger = logging.getLogger(__name__)

PROJECT = "stage-3-poc"   # informational; the actual project is selected by the LANGFUSE_* keys


def tracing_enabled() -> bool:
    return os.environ.get("PF_TRACING", "").strip().lower() in ("1", "true", "yes")


# ── span handles ──────────────────────────────────────────────────────────────
class _NoopSpan:
    """The handle yielded by a no-op span — every method is a safe no-op."""

    def update(self, **kwargs) -> None:
        pass

    def event(self, name: str, **attrs) -> None:
        pass


class _LangfuseSpan:
    """Wraps a live Langfuse span; every call is guarded so a tracing hiccup never reaches the build."""

    def __init__(self, span):
        self._span = span

    def update(self, **kwargs) -> None:
        try:
            self._span.update(**kwargs)
        except Exception:  # noqa: BLE001 — tracing is additive; never crash on it
            pass

    def event(self, name: str, **attrs) -> None:
        try:
            self._span.create_event(name=name, input=(attrs or None))
        except Exception:  # noqa: BLE001
            pass


# ── tracers ───────────────────────────────────────────────────────────────────
class _Tracer:
    """Base / no-op tracer (the tolerated-absent default)."""

    enabled = False

    @contextlib.contextmanager
    def build(self, build_id: str, tags=None):
        yield _NoopSpan()

    @contextlib.contextmanager
    def span(self, name: str, **attrs):
        yield _NoopSpan()

    def event(self, name: str, **attrs) -> None:
        pass

    def flush(self) -> None:
        pass


class _LangfuseTracer(_Tracer):
    """Real tracer over a Langfuse v3 client. Each operation is guarded — on ANY failure it falls back
    to a no-op span so a tracing problem can never halt a build."""

    enabled = True

    def __init__(self, client):
        self._client = client

    @contextlib.contextmanager
    def build(self, build_id: str, tags=None):
        try:
            cm = self._client.start_as_current_span(name="build", input={"build_id": build_id})
        except Exception:  # noqa: BLE001
            yield _NoopSpan()
            return
        with cm as span:
            try:
                self._client.update_current_trace(
                    name=f"build/{build_id}", session_id=build_id, tags=list(tags or []))
            except Exception:  # noqa: BLE001
                pass
            yield _LangfuseSpan(span)

    @contextlib.contextmanager
    def span(self, name: str, **attrs):
        try:
            cm = self._client.start_as_current_span(name=name, input=(attrs or None))
        except Exception:  # noqa: BLE001
            yield _NoopSpan()
            return
        with cm as span:
            yield _LangfuseSpan(span)

    def event(self, name: str, **attrs) -> None:
        try:
            self._client.create_event(name=name, input=(attrs or None))
        except Exception:  # noqa: BLE001
            pass

    def flush(self) -> None:
        try:
            self._client.flush()
        except Exception:  # noqa: BLE001 — best-effort; never crash on shutdown
            pass


# ── module singleton (lazy; injectable for tests) ─────────────────────────────
_current: _Tracer | None = None


def _init_tracer() -> _Tracer:
    if not tracing_enabled():
        return _Tracer()
    try:
        from langfuse import get_client
    except Exception:  # noqa: BLE001 — dep absent (pip install '.[obs]')
        logger.info("PF_TRACING set but `langfuse` not installed; tracing off")
        return _Tracer()
    try:
        return _LangfuseTracer(get_client())   # creds from LANGFUSE_HOST/_PUBLIC_KEY/_SECRET_KEY
    except Exception as e:  # noqa: BLE001 — misconfigured creds etc.
        logger.warning("could not initialise Langfuse (%s); tracing off", e)
        return _Tracer()


def get_tracer() -> _Tracer:
    global _current
    if _current is None:
        _current = _init_tracer()
    return _current


def set_tracer(tracer) -> None:
    """Inject a tracer (tests use a fake recorder; resets the cached singleton)."""
    global _current
    _current = tracer


def reset_tracer() -> None:
    """Drop the cached tracer so the next call re-reads the env (tests / a config change)."""
    global _current
    _current = None


# ── module-level delegators (the seam every caller uses) ──────────────────────
def build(build_id: str, tags=None):
    return get_tracer().build(build_id, tags)


def span(name: str, **attrs):
    return get_tracer().span(name, **attrs)


def event(name: str, **attrs) -> None:
    get_tracer().event(name, **attrs)


def flush() -> None:
    get_tracer().flush()
