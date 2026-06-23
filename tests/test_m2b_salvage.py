"""M2b S3 fakes — run-cap salvage: abandoned.patch + descope-report entry + gaps (no Docker/LLM).

Drives ``core._salvage_run`` with a fake graph (whose ``get_state`` returns a checkpointed
``BuildState``) over a REAL tmp git workspace that has an uncommitted in-flight edit. Asserts the
in-flight work is captured to ``abandoned.patch``, the workspace is rolled back to the last green
commit, and the emitted artifact is an honest ``incomplete`` with ``caps_hit`` + a descope entry +
gaps. A second test covers the no-in-flight-diff path (no patch written).
"""
from __future__ import annotations

import poc_foundry.core as core
import poc_foundry.models as M
from poc_foundry.artifact import SuccessCriterion
from poc_foundry.artifact import load as load_artifact
from poc_foundry.config import load_config
from poc_foundry.phases import Ctx, load_template
from poc_foundry.phases.context import git, git_commit, git_init
from poc_foundry.state import BuildState, IterationPlan, Plan, Spec


class _StubBroker:
    def proxy_log(self):
        return ""


def _ctx(cfg, bid, ws, stg):
    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=ws.parent, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=stg,
              broker=_StubBroker(), coder=None)
    ctx.run_folder = None
    return ctx


def _fake_graph(state):
    snap = type("Snap", (), {"values": state.model_dump()})()
    return type("Graph", (), {"get_state": lambda self, gcfg: snap})()


def _green_workspace(ws):
    """A committed (green) workspace, then an uncommitted in-flight edit on top."""
    (ws / "core.py").write_text("def generate_reply(m, h=None):\n    return 'GREEN'\n")
    git_init(ws)
    git_commit(ws, "scaffold green")


def test_run_cap_salvage_captures_patch_rolls_back_and_descopes(tmp_path):
    cfg = load_config(tmp_path / "builds")
    bid = "poc-salvage-0001"
    ws, stg = tmp_path / "ws", tmp_path / "stg"
    ws.mkdir(); stg.mkdir()
    _green_workspace(ws)
    (ws / "core.py").write_text("def generate_reply(m, h=None):\n    return 'IN-FLIGHT BROKEN'\n")

    spec = Spec(goal="g", buildable=True, success_criteria=[
        SuccessCriterion(text="core crit", core=True, status="met"),
        SuccessCriterion(text="second crit", status="pending")])
    plan = Plan(iterations=[IterationPlan(goal="i0", acceptance=["core crit"]),
                            IterationPlan(goal="i1", acceptance=["second crit"])])
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws),
                       spec=spec, plan=plan, iteration=1, fix_count=2)

    M.METER.begin_run(cfg)
    for _ in range(3):
        M.METER.count()

    core._salvage_run(_fake_graph(state), _ctx(cfg, bid, ws, stg), cfg.builds_dir / bid, bid,
                      {"configurable": {"thread_id": bid}}, cap="max_llm_calls_per_run")
    M.METER.reset()

    bd = cfg.builds_dir / bid
    # 1) the in-flight work is preserved in abandoned.patch
    assert "IN-FLIGHT BROKEN" in (bd / "abandoned.patch").read_text()
    # 2) the live workspace is rolled back to the last green commit
    assert (ws / "core.py").read_text().strip().endswith("'GREEN'")
    # 3) honest incomplete + cap + descope entry for the in-flight criterion + gaps
    pa = load_artifact(bd)
    assert pa.status == "incomplete"
    assert pa.caps_hit == ["max_llm_calls_per_run"]
    assert any(d.criterion == "second crit" for d in pa.descope_report)
    assert "second crit" in pa.final_verdict.gaps          # pending → a gap vs spec
    assert "core crit" not in pa.final_verdict.gaps        # met criteria are NOT gaps
    # the index advertises the patch
    assert "abandoned.patch" in (bd / "00_INDEX.md").read_text()


def test_descope_targets_first_unmet_not_an_already_met_criterion(tmp_path):
    """The server case: the cap fires on the critic call AFTER iter0 committed green, so
    state.iteration still points at the (now-met) core criterion. The descope entry must name the
    first NOT-met criterion (the resume point), never the already-met one."""
    cfg = load_config(tmp_path / "builds")
    bid = "poc-salvage-0003"
    ws, stg = tmp_path / "ws", tmp_path / "stg"
    ws.mkdir(); stg.mkdir()
    _green_workspace(ws)                                   # iter0 committed → clean tree

    spec = Spec(goal="g", buildable=True, success_criteria=[
        SuccessCriterion(text="core crit", core=True, status="met"),     # iter0 already met
        SuccessCriterion(text="second crit", status="pending"),
        SuccessCriterion(text="third crit", status="pending")])
    plan = Plan(iterations=[IterationPlan(goal="i0", acceptance=["core crit"]),
                            IterationPlan(goal="i1", acceptance=["second crit"]),
                            IterationPlan(goal="i2", acceptance=["third crit"])])
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws),
                       spec=spec, plan=plan, iteration=0, fix_count=0)   # paused AT iter0 (met)

    M.METER.begin_run(cfg)
    core._salvage_run(_fake_graph(state), _ctx(cfg, bid, ws, stg), cfg.builds_dir / bid, bid,
                      {"configurable": {"thread_id": bid}}, cap="max_llm_calls_per_run")
    M.METER.reset()

    pa = load_artifact(cfg.builds_dir / bid)
    assert [d.criterion for d in pa.descope_report] == ["second crit"]    # first unmet, NOT core
    assert pa.descope_report[0].attempts_made == 0       # not the in-flight iteration → 0 charged
    assert "core crit" not in pa.final_verdict.gaps      # met core is never a gap


def test_salvage_with_clean_tree_writes_no_patch(tmp_path):
    cfg = load_config(tmp_path / "builds")
    bid = "poc-salvage-0002"
    ws, stg = tmp_path / "ws", tmp_path / "stg"
    ws.mkdir(); stg.mkdir()
    _green_workspace(ws)                                   # no in-flight edit → clean tree

    spec = Spec(goal="g", buildable=True,
                success_criteria=[SuccessCriterion(text="core crit", core=True, status="met")])
    plan = Plan(iterations=[IterationPlan(goal="i0", acceptance=["core crit"])])
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws),
                       spec=spec, plan=plan, iteration=0, fix_count=0)

    M.METER.begin_run(cfg)
    core._salvage_run(_fake_graph(state), _ctx(cfg, bid, ws, stg), cfg.builds_dir / bid, bid,
                      {"configurable": {"thread_id": bid}}, cap="max_run_wall_clock_s")
    M.METER.reset()

    bd = cfg.builds_dir / bid
    assert not (bd / "abandoned.patch").exists()           # nothing in flight → no patch
    pa = load_artifact(bd)
    assert pa.status == "incomplete" and pa.caps_hit == ["max_run_wall_clock_s"]
