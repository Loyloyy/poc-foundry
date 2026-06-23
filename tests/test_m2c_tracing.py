"""M2c S1 fakes — the tolerated-absent ``tracing.py`` + manual spans at the right seams (no Docker/LLM).

Proves:
  • with ``PF_TRACING`` off / langfuse absent, ``get_tracer`` returns the no-op tracer and every span/
    event/flush is a safe no-op (a tracing hiccup can NEVER crash a build);
  • a fake tracer injected via ``set_tracer`` records the manual spans at the seams the LangChain
    handler can't see — the broker (provision/create/exec/destroy), VERIFY, the integrity gates, the
    critic, the clean-room, the LLM call, and the proxy-denials event.

The fakes drive the REAL phase pipeline (fake broker + fake LLM, as in ``test_m1_spine``) so the spans
are exercised exactly where they live, plus the in-process ``Broker`` with a fake ``_run`` for the
broker-layer spans (which the fake broker doesn't go through).
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry import tracing
from poc_foundry.config import load_config
from poc_foundry.sandbox import ExecResult


# ── a recording fake tracer ───────────────────────────────────────────────────
class _RecSpan:
    def __init__(self, rec, name):
        self._rec, self.name = rec, name

    def update(self, **kwargs):
        self._rec.updates.append((self.name, kwargs))

    def event(self, name, **attrs):
        self._rec.events.append((name, attrs))


class FakeTracer:
    def __init__(self):
        self.spans: list[tuple[str, dict]] = []
        self.events: list[tuple[str, dict]] = []
        self.updates: list = []
        self.flushed = 0

    @contextlib.contextmanager
    def build(self, build_id, tags=None):
        self.spans.append(("build", {"build_id": build_id, "tags": list(tags or [])}))
        yield _RecSpan(self, "build")

    @contextlib.contextmanager
    def span(self, name, **attrs):
        self.spans.append((name, attrs))
        yield _RecSpan(self, name)

    def event(self, name, **attrs):
        self.events.append((name, attrs))

    def flush(self):
        self.flushed += 1

    # convenience
    def span_names(self):
        return [n for n, _ in self.spans]

    def event_names(self):
        return [n for n, _ in self.events]


# ── tolerated-absent: no-op when off ──────────────────────────────────────────
def test_tracing_off_is_a_noop(monkeypatch):
    monkeypatch.delenv("PF_TRACING", raising=False)
    tracing.reset_tracer()
    t = tracing.get_tracer()
    assert t.enabled is False
    # every call is a safe no-op (and the span yields a usable handle)
    with tracing.span("anything", x=1) as sp:
        sp.update(output="y")
        sp.event("inner")
    tracing.event("evt", n=2)
    tracing.flush()
    with tracing.build("poc-x", tags=["a"]) as sp:
        sp.update(output="z")
    tracing.reset_tracer()


def test_tracing_enabled_but_dep_absent_degrades(monkeypatch):
    # PF_TRACING on but langfuse not installed (the 3.10 box) → still the no-op tracer, no crash.
    monkeypatch.setenv("PF_TRACING", "1")
    tracing.reset_tracer()
    assert tracing.get_tracer().enabled is False     # langfuse import fails → degraded to no-op
    tracing.reset_tracer()


def test_set_and_reset_tracer():
    fake = FakeTracer()
    tracing.set_tracer(fake)
    assert tracing.get_tracer() is fake
    tracing.reset_tracer()
    assert tracing.get_tracer() is not fake


# ── broker-layer spans (in-process Broker with a fake _run) ───────────────────
def test_broker_spans_at_provision_create_exec_destroy(monkeypatch):
    import poc_foundry.sandbox.broker as B

    def fake_run(cmd, *, check, timeout_s=300):
        if cmd[:3] == ["docker", "inspect", "-f"]:
            # State.Running poll → true; IP lookup → an address
            return ExecResult(0, "true\n" if ".State.Running" in cmd[3] else "10.0.0.9\n", "")
        return ExecResult(0, "ok", "")

    monkeypatch.setattr(B, "_run", fake_run)
    monkeypatch.setattr(B.time, "sleep", lambda *a: None)

    fake = FakeTracer()
    tracing.set_tracer(fake)
    try:
        b = B.Broker("poc-trace-0001", load_config(),
                     allowed_images={"poc-foundry-sandbox", "poc-foundry-proxy"})
        b.provision()
        sbx = b.create(mounts=[B.Mount("/var/tmp/ws", "/work", True)], name="iter0")
        sbx.exec("python -m pytest /staged -q")
        b.destroy()
    finally:
        tracing.reset_tracer()

    names = fake.span_names()
    assert "broker.provision" in names
    assert "broker.create" in names
    assert "broker.exec" in names
    assert "broker.destroy" in names


# ── phase-pipeline spans (real phases, fake broker + LLM) ─────────────────────
def test_phase_pipeline_spans_and_proxy_denial_event(tmp_path, monkeypatch):
    # reuse the m1 spine fakes harness so the spans fire exactly where the phases call them
    import importlib.util

    spec_path = Path(__file__).resolve().parent / "test_m1_spine.py"
    s = importlib.util.spec_from_file_location("test_m1_spine_for_trace", spec_path)
    m1 = importlib.util.module_from_spec(s)
    s.loader.exec_module(m1)

    from poc_foundry.artifact import SuccessCriterion
    from poc_foundry.state import Spec

    # a broker whose proxy log carries a DENIED line so the proxy-denials event fires at P7
    class _DenyBroker(m1._FakeBroker):
        def proxy_log(self, tail=200):
            return ("action=TCP_TUNNEL/200 CONNECT pypi.org:443\n"
                    "action=TCP_DENIED/403 CONNECT google.com:443\n")

    monkeypatch.setattr(m1, "_FakeBroker", _DenyBroker)

    fake = FakeTracer()
    tracing.set_tracer(fake)
    try:
        spec = Spec(goal="Echo the user's message with an ECHO: prefix",
                    success_criteria=[SuccessCriterion(text="reply starts with 'ECHO:'", core=True),
                                      SuccessCriterion(text="reply is non-empty")],
                    non_goals=[], demo_scenario="Type hi → ECHO: hi", buildable=True)
        build_dir, state = m1._run_pipeline(tmp_path, monkeypatch, spec)
    finally:
        tracing.reset_tracer()

    names = set(fake.span_names())
    assert "spec" in names                 # P1 architect call
    assert "iterate.verify" in names       # the cumulative VERIFY exec
    assert "gate.diff-scan" in names       # the integrity diff scanner
    assert "critic" in names               # the adequacy review
    assert "cleanroom" in names            # the clean-room verification
    assert ("proxy.denials", {"count": 1}) in fake.events    # the egress denial surfaced as an event
    assert state.status == "done"          # spans did not perturb the build


# ── the raw LLM call span (chat_text — no LangChain handler can see it) ────────
def test_chat_text_emits_llm_span(monkeypatch):
    import json as _json

    import poc_foundry.models as M

    monkeypatch.setattr(M, "resolve_role", lambda role: ("served-model", "http://vllm/v1", "k"))

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _json.dumps({"choices": [{"message": {"content": "ECHO: hi"}}]}).encode()

    monkeypatch.setattr(M.urllib.request, "urlopen", lambda req, timeout=300: _Resp())

    fake = FakeTracer()
    tracing.set_tracer(fake)
    try:
        out = M.chat_text("tester", "write a test")
    finally:
        tracing.reset_tracer()

    assert out == "ECHO: hi"
    assert ("llm.tester", {"role": "tester"}) in fake.spans
    assert any(name == "llm.tester" and kw.get("output") for name, kw in fake.updates)


# ── the real _LangfuseTracer wired against a fake langfuse v4 client (no langfuse installed) ───
# One-trace-per-build via EXPLICIT parenting: children are created FROM the build root observation
# (root.start_as_current_observation / root.create_event), so each records its parent by object.
class _FakeObs:
    def __init__(self, rec, name):
        self.rec = rec
        self.name = name
        self.trace_id = "trace-xyz"

    def update(self, **kw):
        self.rec["updates"].append((self.name, kw))

    def create_event(self, *, name, input=None, **kw):
        self.rec["events"].append({"name": name, "input": input, "parent": self.name})

    def start_as_current_observation(self, *, name, input=None, metadata=None, as_type="span", **kw):
        self.rec["obs"].append({"name": name, "input": input, "metadata": metadata, "parent": self.name})
        return _FakeObsCM(self.rec, name)

    def end(self):
        pass


class _FakeObsCM:
    def __init__(self, rec, name):
        self.rec = rec
        self.name = name

    def __enter__(self):
        return _FakeObs(self.rec, self.name)

    def __exit__(self, *a):
        return False


class _FakeV4Client:
    """Mimics the langfuse 4.x surface we use: start_as_current_observation / create_event / flush.
    The returned observation itself can spawn children (explicit parenting) — see `_FakeObs`."""

    def __init__(self):
        self.rec = {"obs": [], "events": [], "updates": [], "flushed": 0}

    def start_as_current_observation(self, *, name, input=None, metadata=None, as_type="span", **kw):
        self.rec["obs"].append({"name": name, "input": input, "metadata": metadata, "parent": None})
        return _FakeObsCM(self.rec, name)

    def create_event(self, *, name, input=None, **kw):
        self.rec["events"].append({"name": name, "input": input, "parent": None})

    def flush(self):
        self.rec["flushed"] += 1


class _BareObsCM:
    """A v3-style observation that does NOT expose start_as_current_observation (so the tracer must
    fall back to the client-level span creator)."""

    def __enter__(self):
        return type("_BareObs", (), {})()      # no start_as_current_observation / create_event

    def __exit__(self, *a):
        return False


class _FakeV3Client:
    """Mimics the older v3 surface (only start_as_current_span) — feature-detection must still work."""

    def __init__(self):
        self.rec = {"obs": [], "flushed": 0}

    def start_as_current_span(self, *, name, input=None, metadata=None, **kw):
        self.rec["obs"].append(name)
        return _BareObsCM()

    def flush(self):
        self.rec["flushed"] += 1


def test_langfuse_tracer_one_trace_per_build_explicit_parenting():
    from poc_foundry.tracing import _LangfuseTracer

    c = _FakeV4Client()
    t = _LangfuseTracer(c)
    # nest the child span + event INSIDE the build (as the real pipeline does): both must be created
    # FROM the build root → parent == "build/poc-1" → one trace, not scattered roots.
    with t.build("poc-1", tags=["tech-scout", "gradio-chatbot"]) as bsp:
        bsp.update(output="ok")
        with t.span("broker.exec", cmd="pytest -q") as ssp:
            ssp.update(output={"rc": 0})
        t.event("proxy.denials", count=2)
    t.flush()

    obs = {o["name"]: o for o in c.rec["obs"]}
    assert obs["build/poc-1"]["parent"] is None              # the root (its name = the trace name)
    assert obs["broker.exec"]["parent"] == "build/poc-1"     # child attached to the build root
    assert c.rec["events"][0] == {"name": "proxy.denials", "input": {"count": 2}, "parent": "build/poc-1"}
    assert c.rec["flushed"] == 1
    # the build obs carries session_id + tags in metadata (v4 exposes no trace-tag setter)
    assert obs["build/poc-1"]["metadata"]["session_id"] == "poc-1"
    assert "tech-scout" in obs["build/poc-1"]["metadata"]["tags"]


def test_langfuse_tracer_span_outside_build_uses_client():
    # a span created OUTSIDE any build has no root → it's a client-level span (its own trace).
    from poc_foundry.tracing import _LangfuseTracer

    c = _FakeV4Client()
    t = _LangfuseTracer(c)
    with t.span("broker.exec", cmd="x"):
        pass
    assert c.rec["obs"][0] == {"name": "broker.exec", "input": {"cmd": "x"},
                               "metadata": None, "parent": None}


def test_langfuse_tracer_falls_back_to_v3_span_api():
    from poc_foundry.tracing import _LangfuseTracer

    c = _FakeV3Client()
    t = _LangfuseTracer(c)
    # the v3 obs lacks start_as_current_observation, so even inside a build the child falls back to
    # the client-level start_as_current_span (feature-detection both ways).
    with t.build("poc-9"):
        with t.span("spec") as sp:
            sp.update(output="x")
    t.flush()
    assert c.rec["obs"] == ["build/poc-9", "spec"]
    assert c.rec["flushed"] == 1
