"""M2b S4 fakes — cooperative stop + resumable stopped emit (no Docker/LLM).

Covers the sentinel round-trip + the node-boundary guard, that ``BuildStopped`` escapes a broad
``except Exception`` (so it unwinds the run), that ``core._emit_stopped`` writes a resumable
``stopped`` artifact, and that ``request_stop_build`` + the resume-clear round-trip. The real
kill-then-resume round-trip is a server test (a fresh broker over the persisted workspace).
"""
from __future__ import annotations

import poc_foundry.core as core
import poc_foundry.models as M
from poc_foundry import control
from poc_foundry.artifact import load as load_artifact
from poc_foundry.config import load_config
from poc_foundry.control import BuildStopped
from poc_foundry.phases import Ctx, load_template
from poc_foundry.state import BuildState


def test_stop_sentinel_roundtrip_and_guard(tmp_path):
    bd = tmp_path / "poc-1"
    assert not control.stop_requested(bd)
    control.raise_if_stopped(bd)                 # no sentinel → no raise
    control.request_stop(bd)
    assert control.stop_requested(bd)
    try:
        control.raise_if_stopped(bd)
        raise AssertionError("expected BuildStopped")
    except BuildStopped:
        pass
    control.clear_stop(bd)
    assert not control.stop_requested(bd)
    control.raise_if_stopped(bd)                 # cleared → no raise again


def test_buildstopped_escapes_except_exception():
    assert issubclass(BuildStopped, BaseException)
    assert not issubclass(BuildStopped, Exception)
    swallowed = False
    try:
        try:
            raise BuildStopped()
        except Exception:
            swallowed = True
    except BuildStopped:
        pass
    assert swallowed is False


def test_request_stop_build_then_resume_clear(tmp_path):
    cfg = load_config(tmp_path / "builds")
    bid = "poc-stop-0001"
    path = core.request_stop_build(bid, builds_dir=tmp_path / "builds")
    assert path.exists() and control.stop_requested(cfg.builds_dir / bid)
    control.clear_stop(cfg.builds_dir / bid)     # what resume_build does before re-invoking
    assert not control.stop_requested(cfg.builds_dir / bid)


def test_emit_stopped_writes_resumable_artifact(tmp_path):
    cfg = load_config(tmp_path / "builds")
    bid = "poc-stop-0002"
    ws, stg = tmp_path / "ws", tmp_path / "stg"
    ws.mkdir(); stg.mkdir()

    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws),
                       artifact_id="dra-xyz", iteration=2)
    snap = type("Snap", (), {"values": state.model_dump()})()
    graph = type("Graph", (), {"get_state": lambda self, gcfg: snap})()

    class _StubBroker:
        def proxy_log(self):
            return ""

    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=tmp_path, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=stg,
              broker=_StubBroker(), coder=None)
    ctx.run_folder = None

    core._emit_stopped(graph, ctx, cfg.builds_dir / bid, bid, {"configurable": {"thread_id": bid}})

    pa = load_artifact(cfg.builds_dir / bid)
    assert pa.status == "stopped"
    assert pa.source_artifact.id == "dra-xyz"          # provenance recovered from the checkpoint
    assert any("resume" in c for c in pa.caveats)
    assert "STOPPED" in (cfg.builds_dir / bid / "report.md").read_text()
