"""M3 S1 fakes — the web-UI event seam + single-slot RunManager (no Docker/LLM/fastapi).

Covers the load-bearing slice the design calls out (§5.12): events flow ``Ctx.say → sink → queue →
SSE serializer``; ``snapshot`` projects the slice board off a ``BuildState``; the seam is
tolerated-absent (a failing sink never crashes a build, and the CLI path — no sink — is unchanged);
the ``RunManager`` is single-slot (a 2nd concurrent start raises ``RunBusy`` → the server's 409),
fans events out to subscribers with replay, emits a terminal ``end`` event, and ``stop`` calls the
cooperative-stop sentinel (it does NOT reimplement stop — M2b S4). ``server.py`` (fastapi) is
image-only and validated over the tunnel; the testable Python lives in ``events``/``runmanager``.
"""
from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

from poc_foundry import events as ev
from poc_foundry.events import emit, make_event, snapshot, sse_format
from poc_foundry.phases import Ctx, load_template
from poc_foundry.web.runmanager import RunBusy, RunManager


# ── event seam ────────────────────────────────────────────────────────────────
def _ctx(tmp_path, sink=None):
    return Ctx(cfg=None, build_id="poc-m3", run_dir=tmp_path, template=load_template("gradio-chatbot"),
               build_dir=tmp_path / "b", workspace_dir=tmp_path / "ws", staging_dir=tmp_path / "stg",
               broker=None, coder=None, events=sink)


def test_say_emits_log_event_through_sink(tmp_path):
    seen = []
    ctx = _ctx(tmp_path, sink=seen.append)
    ctx.say("scaffolded the workspace")
    assert "scaffolded the workspace" in ctx.report     # the existing report behaviour is intact
    assert len(seen) == 1
    e = seen[0]
    assert e["type"] == "log" and e["build_id"] == "poc-m3" and e["line"] == "scaffolded the workspace"
    frame = sse_format(e)                                # say → sink → SSE serializer
    assert frame.startswith("event: log\ndata: ") and frame.endswith("\n\n")
    assert "scaffolded the workspace" in frame


def test_say_without_sink_is_silent_contract_unchanged(tmp_path):
    ctx = _ctx(tmp_path, sink=None)                      # the CLI path: no event sink
    ctx.say("a line")                                    # must not raise
    assert ctx.report == ["a line"]
    assert ctx.events is None


def test_emit_tolerates_a_failing_sink():
    def boom(_e):
        raise RuntimeError("subscriber blew up")
    emit(boom, make_event("log", "poc-x", line="x"))     # tracing-grade tolerance: never raises
    emit(None, make_event("log", "poc-x", line="x"))     # no sink → no-op


def test_snapshot_projects_the_slice_board():
    crit = SimpleNamespace(text="echoes input", status="met", core=True)
    crit2 = SimpleNamespace(text="streams tokens", status="pending", core=False)
    it = SimpleNamespace(goal="wire the echo", acceptance=["echoes input"])
    rec = SimpleNamespace(goal="wire the echo", status="green", attempts=2)
    state = SimpleNamespace(
        spec=SimpleNamespace(success_criteria=[crit, crit2], goal="a chatbot"),
        plan=SimpleNamespace(iterations=[it]), iteration_records=[rec],
        phase="iterate", status="incomplete", iteration=1, verdict="next",
        descope_report=[{"criterion": "streams tokens"}], caps_hit=["wall_s"])
    snap = snapshot(state)
    assert snap["goal"] == "a chatbot" and snap["phase"] == "iterate" and snap["verdict"] == "next"
    assert snap["criteria"][0] == {"text": "echoes input", "status": "met", "core": True}
    assert snap["criteria"][1]["status"] == "pending"
    assert snap["iterations"][0]["goal"] == "wire the echo"
    assert snap["iteration_records"][0]["status"] == "green"
    assert snap["descope_report"] == [{"criterion": "streams tokens"}] and snap["caps_hit"] == ["wall_s"]


def test_snapshot_tolerates_an_empty_state():
    snap = snapshot(SimpleNamespace())                   # early/partial state → all defaults, no raise
    assert snap["criteria"] == [] and snap["iterations"] == [] and snap["phase"] == ""


def test_list_sources_surfaces_the_fixture_topic():
    # the build-form picker shows TOPICS, not raw paths: the committed fixture is discovered with its
    # Stage-2 `topic`/`brief` read straight off vNN.json (no heavy schema validation).
    import poc_foundry.core as core
    rows = core.list_sources()
    fx = [r for r in rows if r["path"].endswith("sample_artifact")]
    assert fx, f"fixture not discovered in {[r['path'] for r in rows]}"
    assert fx[0]["topic"] == "Synthetic RAG-over-docs PoC (fixture)"
    assert fx[0]["id"] == "dra-20260101-000000-abc123-m" and fx[0]["version"] == 1


def test_core_build_poc_accepts_event_sink_kw():
    # the contract stays additive: build_poc/resume_build grow an OPTIONAL event_sink (CLI never passes it)
    import poc_foundry.core as core
    for fn in (core.build_poc, core.resume_build):
        p = inspect.signature(fn).parameters["event_sink"]
        assert p.default is None


# ── RunManager (single-slot, fan-out, stop) ───────────────────────────────────
def _artifact(aid, status, demo="yes"):
    return SimpleNamespace(id=aid, status=status,
                           final_verdict=SimpleNamespace(demonstrates_core_value=demo))


def test_runmanager_rejects_second_concurrent_start_409():
    release = threading.Event()

    def fake_build(source, *, event_sink, **kw):
        event_sink(make_event("start", "poc-busy", kind="build"))
        release.wait(timeout=5)
        return ("report", _artifact("poc-busy", "done"))

    mgr = RunManager(build_fn=fake_build)
    mgr.start("dra-1")
    # spin until the worker thread is actually running before asserting busy
    for _ in range(500):
        if mgr.busy:
            break
        threading.Event().wait(0.002)
    assert mgr.busy
    raised = False
    try:
        mgr.start("dra-2")                               # single-slot → RunBusy (server maps to 409)
    except RunBusy:
        raised = True
    assert raised
    release.set()
    mgr._thread.join(timeout=5)
    assert not mgr.busy


def test_runmanager_fans_out_events_and_emits_end():
    done = threading.Event()

    def fake_build(source, *, event_sink, **kw):
        event_sink(make_event("start", "poc-7", kind="build"))
        event_sink(make_event("node", "poc-7", node="ingest", snapshot={"phase": "ingest"}))
        done.set()
        return ("report", _artifact("poc-7", "done", demo="partial"))

    mgr = RunManager(build_fn=fake_build)
    q = mgr.subscribe()
    mgr.start("dra-1")
    mgr._thread.join(timeout=5)
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    types = [e["type"] for e in drained]
    assert "start" in types and "node" in types and "end" in types
    end = [e for e in drained if e["type"] == "end"][0]
    assert end["status"] == "done" and end["artifact_id"] == "poc-7" and end["demonstrates"] == "partial"
    st = mgr.status()
    assert st["state"] == "finished" and st["status"] == "done" and st["build_id"] == "poc-7"


def test_runmanager_replays_buffer_to_a_late_subscriber():
    def fake_build(source, *, event_sink, **kw):
        event_sink(make_event("start", "poc-9", kind="build"))
        event_sink(make_event("node", "poc-9", node="spec", snapshot={"phase": "spec"}))
        return ("report", _artifact("poc-9", "done"))

    mgr = RunManager(build_fn=fake_build)
    mgr.start("dra-1")
    mgr._thread.join(timeout=5)
    q = mgr.subscribe()                                  # subscribe AFTER the run finished → replay
    replayed = []
    while not q.empty():
        replayed.append(q.get_nowait())
    assert [e["type"] for e in replayed][:2] == ["start", "node"]


def test_runmanager_stop_calls_the_sentinel():
    stopped = {}
    release = threading.Event()

    def fake_build(source, *, event_sink, **kw):
        event_sink(make_event("start", "poc-stop", kind="build"))
        release.wait(timeout=5)
        return ("report", _artifact("poc-stop", "stopped"))

    def fake_stop(build_id):
        stopped["id"] = build_id
        return build_id

    mgr = RunManager(build_fn=fake_build, stop_fn=fake_stop)
    mgr.start("dra-1")
    for _ in range(500):
        if mgr.status().get("build_id") == "poc-stop":
            break
        threading.Event().wait(0.002)
    res = mgr.stop()                                     # no id → stops the current build
    assert res["stopped"] is True and res["build_id"] == "poc-stop"
    assert stopped["id"] == "poc-stop"                   # delegated to request_stop_build (M2b S4)
    release.set()
    mgr._thread.join(timeout=5)


def test_runmanager_surfaces_a_build_error():
    def fake_build(source, *, event_sink, **kw):
        event_sink(make_event("start", "poc-err", kind="build"))
        raise RuntimeError("provision failed")

    mgr = RunManager(build_fn=fake_build)
    q = mgr.subscribe()
    mgr.start("dra-1")
    mgr._thread.join(timeout=5)
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    err = [e for e in drained if e["type"] == "error"]
    assert err and "provision failed" in err[0]["error"]
    assert mgr.status()["state"] == "error"
