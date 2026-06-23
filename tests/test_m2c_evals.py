"""M2c S2 — tiered evals v1 fakes suite.

Proves the eval harness runs P0→P1→P2 on the committed fixture with a FAKE architect (no vLLM, no
sandbox, no Langfuse) and produces a scored report, AND that the deterministic scorers
(``score_spec``/``score_plan``) are exact. The real architect path is exercised by the server run.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry.artifact import SuccessCriterion
from poc_foundry.config import load_config
from poc_foundry.evals import (
    Check,
    format_report,
    run_spec_plan_eval,
    save_reports,
    score_plan,
    score_spec,
)
from poc_foundry.state import IterationPlan, Plan, Spec

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_artifact"


def _patch_architect(monkeypatch, spec, *, degraded=True):
    import poc_foundry.models as M

    class _Structured:
        def invoke(self, messages):
            return spec

    class _Chat:
        def with_structured_output(self, model):
            return _Structured()

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "same_family", lambda a, b: degraded)


def _good_spec():
    return Spec(goal="Answer questions about the corpus with grounded citations",
                success_criteria=[
                    SuccessCriterion(text="replies to a corpus question include a citation marker", core=True),
                    SuccessCriterion(text="replies are non-empty for known corpus keywords"),
                    SuccessCriterion(text="off-corpus questions get an honest 'I don't know'"),
                    SuccessCriterion(text="empty input is handled without error"),
                ],
                non_goals=["no real LLM", "no auth"],
                demo_scenario="Ask about a known topic → answer cites a source",
                buildable=True)


# ── full harness path (fake architect) ────────────────────────────────────────
def test_good_spec_scores_full(monkeypatch):
    _patch_architect(monkeypatch, _good_spec())
    rep = run_spec_plan_eval(FIXTURE, cfg=load_config())
    assert rep.ok and rep.buildable
    assert rep.spec_score == 1.0, [c for c in rep.spec_checks if not c.passed]
    assert rep.plan_score == 1.0, [c for c in rep.plan_checks if not c.passed]
    assert rep.overall_score == 1.0
    assert rep.num_criteria == 4
    assert rep.plan_iterations == 4
    assert rep.metadata["degraded_critic"] is True
    assert rep.rubric and all(d.grade is None for d in rep.rubric)   # ungraded Tier-2 seam


def test_weak_spec_fails_specific_checks(monkeypatch):
    # 2 criteria, empty demo_scenario, no non_goals, duplicate texts → count/demo/non_goals/dupes fail.
    weak = Spec(goal="do a thing",
                success_criteria=[SuccessCriterion(text="reply is a string okay", core=True),
                                  SuccessCriterion(text="reply is a string okay")],
                non_goals=[], demo_scenario="", buildable=True)
    _patch_architect(monkeypatch, weak)
    rep = run_spec_plan_eval(FIXTURE, cfg=load_config())
    assert rep.ok and rep.buildable
    fails = {c.name for c in rep.spec_checks if not c.passed}
    assert {"criteria_count_3_6", "demo_scenario_nonempty", "has_non_goals", "no_duplicate_criteria"} <= fails
    assert rep.spec_score < 1.0


def test_not_buildable_spec(monkeypatch):
    nb = Spec(goal="", buildable=False, not_buildable_reasons=["needs a live GPU index"])
    _patch_architect(monkeypatch, nb)
    rep = run_spec_plan_eval(FIXTURE, cfg=load_config())
    assert rep.ok and not rep.buildable
    assert rep.not_buildable_reasons == ["needs a live GPU index"]
    assert rep.spec_checks[0].name == "not_buildable_has_reason" and rep.spec_checks[0].passed
    assert rep.plan_checks == []   # plan not scored for NOT_BUILDABLE


# ── deterministic scorers (independent of pipeline normalization) ─────────────
def test_score_spec_catches_structural_defects():
    bad = Spec(goal="g",
               success_criteria=[SuccessCriterion(text=f"criterion number {i} here", core=False)
                                 for i in range(7)],   # 7 criteria, ZERO core
               non_goals=["x"], demo_scenario="d", buildable=True)
    bad.success_criteria[0].type = "met-by-demo-evidence"
    checks = {c.name: c.passed for c in score_spec(bad)}
    assert checks["criteria_count_3_6"] is False     # 7 > 6
    assert checks["exactly_one_core"] is False        # 0 core
    assert checks["all_met_by_test"] is False         # one is demo-evidence
    assert checks["goal_nonempty"] is True


def test_score_plan_core_first():
    spec = _good_spec()
    core = next(c for c in spec.success_criteria if c.core)
    cfg = load_config()
    good = Plan(iterations=[IterationPlan(goal="g", acceptance=[core.text], interface="x"),
                            IterationPlan(goal="g2", acceptance=["other"], interface="x")])
    checks = {c.name: c.passed for c in score_plan(spec, good, cfg)}
    assert checks["core_first"] is True and checks["has_iterations"] is True

    misordered = Plan(iterations=[IterationPlan(goal="g", acceptance=["other"], interface="x"),
                                  IterationPlan(goal="g2", acceptance=[core.text], interface="")])
    checks2 = {c.name: c.passed for c in score_plan(spec, misordered, cfg)}
    assert checks2["core_first"] is False
    assert checks2["all_interface_pinned"] is False   # second iteration has empty interface


# ── reporting ──────────────────────────────────────────────────────────────────
def test_format_and_save_reports(monkeypatch, tmp_path):
    _patch_architect(monkeypatch, _good_spec())
    rep = run_spec_plan_eval(FIXTURE, cfg=load_config())
    text = format_report(rep)
    assert "spec_score=1.0" in text and "[PASS] core_first" in text
    out = tmp_path / "evals" / "out.json"
    save_reports([rep], out)
    payload = json.loads(out.read_text())
    assert payload["reports"][0]["spec_score"] == 1.0
    assert payload["reports"][0]["overall_score"] == 1.0   # computed property serialized
