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
    """Real tracer over a Langfuse client. Version-resilient across the v3/v4 SDK split (the on-prem
    instance resolved langfuse 4.x): the span-creator is `start_as_current_observation` on v4 and
    `start_as_current_span` on v3 (feature-detected). v4 dropped `update_current_trace`/`update_trace`,
    so the TRACE name comes from the root observation's name (`build/<id>`) and tags/session ride in
    `metadata`. Every operation is guarded — on ANY failure it falls back to a no-op span so a tracing
    problem can never halt a build."""

    enabled = True

    def __init__(self, client):
        self._client = client
        # v4: start_as_current_observation (as_type defaults to 'span'); v3: start_as_current_span.
        self._span_cm = (getattr(client, "start_as_current_observation", None)
                         or getattr(client, "start_as_current_span", None))

    @contextlib.contextmanager
    def build(self, build_id: str, tags=None):
        if self._span_cm is None:
            yield _NoopSpan()
            return
        try:
            # The root observation's name becomes the TRACE name (v4 has no update_current_trace);
            # tags/session_id ride in metadata since v4 exposes no trace-tag setter on the obs.
            cm = self._span_cm(name=f"build/{build_id}", input={"build_id": build_id},
                               metadata={"session_id": build_id, "tags": list(tags or [])})
        except Exception:  # noqa: BLE001
            yield _NoopSpan()
            return
        with cm as span:
            yield _LangfuseSpan(span)

    @contextlib.contextmanager
    def span(self, name: str, **attrs):
        if self._span_cm is None:
            yield _NoopSpan()
            return
        try:
            cm = self._span_cm(name=name, input=(attrs or None))
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
        # PF_LANGFUSE_TIMEOUT_S raises the SDK's HTTP/OTEL-export timeout: the on-prem langfuse OTEL
        # ingest endpoint (clickhouse/minio-backed) can be slow and the default 5s export read-timeout
        # drops batches ("Failed to export span batch … Read timed out"). Unset → the SDK default.
        client = None
        to = os.environ.get("PF_LANGFUSE_TIMEOUT_S", "").strip()
        if to:
            try:
                from langfuse import Langfuse
                client = Langfuse(timeout=int(to))   # creds still from LANGFUSE_* env; just a longer timeout
            except Exception:  # noqa: BLE001 — fall back to the env-configured singleton
                client = None
        return _LangfuseTracer(client or get_client())   # creds from LANGFUSE_HOST/_PUBLIC_KEY/_SECRET_KEY
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
