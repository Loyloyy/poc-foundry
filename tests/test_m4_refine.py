"""M4 S1 fakes — the ``refine`` finish-path (no Docker/LLM/fastapi).

Covers the testable surface of refine (design §6; the descope finish-path made real): backlog
selection (only not-yet-``met`` criteria, each pinned to its ORIGINAL staged-test filename so the
red-first test is REUSED not re-authored); the per-call coder rebind plumbing
(``models.set_role_alias`` → ``resolve_role``/``same_family`` both see the rebound endpoint, WITHOUT
a global ``.env`` change); the critic bar is never weakened (respec/replan pinned to caps → fix →
descope); the one-at-a-time backlog staging that keeps a still-red criterion out of the cumulative
gate; and the additive contract (``build_poc``/``resume_build`` unchanged; CLI + RunManager grow a
refine entry). The live RED→GREEN re-attack is a server demonstration over the tunnel.
"""
from __future__ import annotations

import inspect
import threading
from types import SimpleNamespace

from poc_foundry import core, models
from poc_foundry.artifact import SuccessCriterion
from poc_foundry.events import make_event
from poc_foundry.phases import Ctx, load_template
from poc_foundry.state import BuildState, IterationPlan, Plan, Spec
from poc_foundry.web.runmanager import RunBusy, RunManager


def _finished_state(build_id="poc-fin"):
    """A finished build's BuildState: core criterion MET, two later criteria DESCOPED."""
    crits = [
        SuccessCriterion(text="echoes input", status="met", core=True),
        SuccessCriterion(text="streams tokens", status="descoped"),
        SuccessCriterion(text="remembers history", status="descoped"),
    ]
    spec = Spec(goal="a chatbot", success_criteria=crits, template="gradio-chatbot")
    iters = [IterationPlan(goal="echo", acceptance=["echoes input"]),
             IterationPlan(goal="stream", acceptance=["streams tokens"]),
             IterationPlan(goal="history", acceptance=["remembers history"])]
    return BuildState(build_id=build_id, spec=spec, plan=Plan(iterations=iters),
                      descope_report=[{"criterion": "streams tokens", "attempts_made": 3,
                                       "why_failed": "coder stuck", "finish_path": "refine"},
                                      {"criterion": "remembers history", "attempts_made": 3,
                                       "why_failed": "coder stuck", "finish_path": "refine"}],
                      commit_sha="abc123", green_test_files=["test_iter_0.py"],
                      staged_tests=["test_iter_0.py", "test_iter_1.py", "test_iter_2.py"],
                      fix_count=3, verdict="proceed")


class _Cfg(SimpleNamespace):
    pass


def _cfg():
    return _Cfg(respec_cap=1, replan_cap=1)


# ── backlog selection ─────────────────────────────────────────────────────────
def test_refine_seed_selects_only_descoped_backlog():
    seed, backlog = core._refine_seed(_finished_state(), _cfg())
    # only the two descoped criteria's iterations make the backlog; the met core one is excluded
    assert backlog == ["test_iter_1.py", "test_iter_2.py"]
    assert [it.acceptance[0] for it in seed.plan.iterations] == ["streams tokens", "remembers history"]
    # each backlog iteration pins its ORIGINAL staged-test filename (reuse, not re-author/re-number)
    assert [it.test_file for it in seed.plan.iterations] == ["test_iter_1.py", "test_iter_2.py"]


def test_refine_seed_resets_only_backlog_criteria_and_flags_refine_mode():
    seed, _ = core._refine_seed(_finished_state(), _cfg())
    by = {c.text: c.status for c in seed.spec.success_criteria}
    assert by["echoes input"] == "met"            # met criterion keeps its honest status
    assert by["streams tokens"] == "pending"      # backlog criteria reset for the re-attack
    assert by["remembers history"] == "pending"
    assert seed.refine_mode is True and seed.iteration == 0 and seed.fix_count == 0


def test_refine_seed_never_weakens_the_critic():
    # refine raises coder capability, never re-specs/re-plans → respec/replan pinned to caps (DECISIONS #28)
    cfg = _cfg()
    seed, _ = core._refine_seed(_finished_state(), cfg)
    assert seed.respec_count == cfg.respec_cap and seed.replan_count == cfg.replan_cap
    assert seed.descope_report == []              # rebuilt by the critic; refined criteria drop off


def test_refine_seed_empty_when_all_met():
    st = _finished_state()
    for c in st.spec.success_criteria:
        c.status = "met"
    _seed, backlog = core._refine_seed(st, _cfg())
    assert backlog == []                          # nothing to refine → core returns early (no run)


# ── per-call coder rebind (no global .env change) ─────────────────────────────
def test_set_role_alias_rebinds_resolution(monkeypatch):
    monkeypatch.setenv("CODER_MODEL", "base-coder")
    monkeypatch.setenv("CODER_API_BASE", "http://base")
    monkeypatch.setenv("FRONTIER_MODEL", "frontier-coder")
    monkeypatch.setenv("FRONTIER_API_BASE", "http://frontier")
    monkeypatch.setenv("CRITIC_MODEL", "the-critic")
    monkeypatch.setenv("CRITIC_API_BASE", "http://critic")
    try:
        assert models.resolve_role("coder")[0] == "base-coder"
        assert models.same_family("critic", "coder") is False   # distinct families before
        models.set_role_alias("coder", "frontier")
        # the alias resolves the FRONTIER triple for BOTH the coder loop and the degraded-critic check
        assert models.resolve_role("coder")[0] == "frontier-coder"
        assert models.same_family("critic", "coder") is False   # still independent (good)
        # a frontier coder that happens to equal the critic would read as degraded (conservative)
        models.set_role_alias("coder", "critic")
        assert models.same_family("critic", "coder") is True
    finally:
        models.set_role_alias("coder", None)
    assert models.resolve_role("coder")[0] == "base-coder"       # cleared → back to the base triple


def test_set_role_alias_clear_is_idempotent():
    models.set_role_alias("coder", None)            # clearing an unset alias never raises
    models.set_role_alias("coder", "")              # blank alias also clears
    assert "coder" not in models._ROLE_ALIASES


# ── one-at-a-time backlog staging ─────────────────────────────────────────────
def _ctx(tmp_path):
    return Ctx(cfg=None, build_id="poc-r", run_dir=tmp_path, template=load_template("gradio-chatbot"),
               build_dir=tmp_path / "b", workspace_dir=tmp_path / "ws", staging_dir=tmp_path / "stg",
               broker=None, coder=None)


def test_refine_staging_parks_backlog_then_stages_one_in(tmp_path):
    from poc_foundry.phases import pipeline as pl

    tests = tmp_path / "stg" / "tests"
    tests.mkdir(parents=True)
    for f in ("test_iter_0.py", "test_iter_1.py", "test_iter_2.py"):
        (tests / f).write_text("# " + f)

    # core parks the backlog (1,2) out of the active gate; the met test (0) stays
    core._refine_prepare_staging(tmp_path / "stg", ["test_iter_1.py", "test_iter_2.py"])
    assert (tests / "test_iter_0.py").exists()
    assert not (tests / "test_iter_1.py").exists() and not (tests / "test_iter_2.py").exists()
    assert (tmp_path / "stg" / "refine_pending" / "test_iter_1.py").exists()

    ctx = _ctx(tmp_path)
    pl._refine_stage_in(ctx, "test_iter_1.py")      # iteration 1 brings its (reused) test into the gate
    assert (tests / "test_iter_1.py").exists()
    assert not (tests / "test_iter_2.py").exists()  # iteration 2's test stays parked

    pl._refine_park_out(ctx, "test_iter_1.py")      # stayed red → drop from the gate; authored copy survives
    assert not (tests / "test_iter_1.py").exists()
    assert (tmp_path / "stg" / "refine_pending" / "test_iter_1.py").exists()


def test_p4_strict_red_first_disabled_in_refine_mode():
    # refine works a post-scaffold workspace, so iteration-0's strict-red-first probe must NOT apply
    # (a green probe there means "met by existing code", not tester inadequacy).
    src = inspect.getsource(__import__("poc_foundry.phases.pipeline", fromlist=["p4_iterate"]).p4_iterate)
    assert "strict_red_first = (i == 0) and not state.refine_mode" in src


# ── additive contract ─────────────────────────────────────────────────────────
def test_refine_build_is_additive_and_optional_sink():
    sig = inspect.signature(core.refine_build)
    assert sig.parameters["event_sink"].default is None
    assert sig.parameters["coder_override"].default is None
    # the existing headless contract is untouched
    for fn in (core.build_poc, core.resume_build):
        assert inspect.signature(fn).parameters["event_sink"].default is None


def test_cli_exposes_refine():
    from poc_foundry import cli
    assert hasattr(cli, "_cmd_refine")


# ── RunManager refine (single-slot, fan-out) ──────────────────────────────────
def _artifact(aid, status, demo="partial"):
    return SimpleNamespace(id=aid, status=status,
                           final_verdict=SimpleNamespace(demonstrates_core_value=demo))


def test_runmanager_refine_fans_out_and_passes_coder_override():
    seen = {}
    done = threading.Event()

    def fake_refine(build_id, *, event_sink, **kw):
        seen["build_id"] = build_id
        seen["coder_override"] = kw.get("coder_override")
        event_sink(make_event("start", build_id, kind="refine"))
        done.set()
        return ("report", _artifact(build_id, "done", demo="yes"))

    mgr = RunManager(refine_fn=fake_refine)
    q = mgr.subscribe()
    mgr.refine("poc-fin", coder_override="frontier")
    mgr._thread.join(timeout=5)
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    types = [e["type"] for e in drained]
    assert "start" in types and "end" in types
    assert seen["build_id"] == "poc-fin" and seen["coder_override"] == "frontier"
    assert mgr.status()["kind"] == "refine" and mgr.status()["status"] == "done"


def test_runmanager_refine_is_single_slot():
    release = threading.Event()

    def fake_refine(build_id, *, event_sink, **kw):
        event_sink(make_event("start", build_id, kind="refine"))
        release.wait(timeout=5)
        return ("report", _artifact(build_id, "done"))

    mgr = RunManager(refine_fn=fake_refine)
    mgr.refine("poc-1")
    for _ in range(500):
        if mgr.busy:
            break
        threading.Event().wait(0.002)
    raised = False
    try:
        mgr.refine("poc-2")
    except RunBusy:
        raised = True
    assert raised
    release.set()
    mgr._thread.join(timeout=5)
