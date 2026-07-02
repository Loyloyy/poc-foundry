"""M7 — rehabilitation sweep (Layer 1) + integrity trust-cap downgrade (Layer 2).

Ratifies the planning-chat ruling of 2026-07-02 (DECISIONS #51/#52), surfaced by the A3 durable-agent
pilot: a build that meets every §1.2 DONE condition reported ``incomplete/no`` because (1) a core-first
criterion descoped on an iter0 integrity incident is never reconsidered even after a later iteration
satisfies it, and (2) a single caught-and-rolled-back high incident permanently caps ``trustworthy``.

Pins (per the ruling's "fakes tests"):
  • sweep promotes ONLY on a green re-check against the final workspace;
  • sweep routes every promotion through the critic adequacy check;
  • a promoted criterion's descope entry converts to a rehabilitation note (never vanishes) + its test
    joins ``green_test_files`` so P5 publishes it (the second, clean-room pass);
  • the incident downgrade requires ALL FOUR conditions; multiple/repeated high incidents keep the cap.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


# ── fakes ────────────────────────────────────────────────────────────────────
class _FakeSbx:
    """A sandbox whose /staged pytest run passes for the files named in ``green``."""

    def __init__(self, green: set[str]):
        self.green = green
        self.destroyed = False

    def exec(self, cmd: str, timeout_s: int | None = None):
        ok = any(f"/staged/{g}" in cmd for g in self.green)
        return SimpleNamespace(ok=ok, combined="1 passed" if ok else "1 failed")

    def destroy(self):
        self.destroyed = True


def _sweep_ctx(tmp_path: Path, green: set[str]):
    staging = tmp_path / "staging"
    (staging / "tests").mkdir(parents=True)
    for name in ("test_iter_0.py", "test_iter_1.py"):
        (staging / "tests" / name).write_text("def test_x():\n    assert True\n")
    sbx = _FakeSbx(green)
    said: list = []
    ctx = SimpleNamespace(
        staging_dir=staging, workspace_dir=tmp_path / "ws", service_env={},
        broker=SimpleNamespace(create=lambda **kw: sbx),
        say=lambda *a, **k: said.append(a[0] if a else ""))
    (tmp_path / "ws").mkdir()
    return ctx, sbx, said


def _state_with_descope(**kw):
    from poc_foundry.state import BuildState, IterationPlan, Plan, Spec
    from poc_foundry.artifact import SuccessCriterion

    spec = Spec(goal="g", buildable=True, success_criteria=[
        SuccessCriterion(text="core crash-resume", core=True, status="descoped"),
        SuccessCriterion(text="fresh task completes", core=False, status="met"),
    ])
    plan = Plan(iterations=[
        IterationPlan(goal="core", acceptance=["core crash-resume"]),
        IterationPlan(goal="fresh", acceptance=["fresh task completes"]),
    ])
    base = dict(build_id="poc-x", spec=spec, plan=plan,
                descope_report=[{"criterion": "core crash-resume", "attempts_made": 1,
                                 "why_failed": "integrity incident — gamed iteration not rewarded",
                                 "finish_path": "re-run with refine"}],
                green_test_files=["test_iter_1.py"])
    base.update(kw)
    return BuildState(**base)


# ── Layer 1: the rehabilitation sweep ────────────────────────────────────────
def test_sweep_promotes_a_descoped_criterion_that_now_passes(tmp_path, monkeypatch):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=True, reason="ok"))
    st = _state_with_descope()
    ctx, sbx, _said = _sweep_ctx(tmp_path, green={"test_iter_0.py"})   # the descoped test now passes

    promoted_files, new_report, notes = pipeline._rehabilitation_sweep(st, ctx)

    assert promoted_files == ["test_iter_0.py"]                        # its test joins the publish set
    assert st.spec.success_criteria[0].status == "met"                # descoped→met
    assert sbx.destroyed                                              # VM reaped
    # guardrail 3: the descope entry converts to a rehabilitation note (not deleted)
    entry = next(e for e in new_report if e["criterion"] == "core crash-resume")
    assert entry["resolved"].startswith("met by the final implementation")
    assert entry["finish_path"] == "none — rehabilitated"
    assert "integrity incident" in entry["originally_descoped"]
    assert notes == []


def test_sweep_does_not_promote_when_the_test_still_fails(tmp_path, monkeypatch):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=True))
    st = _state_with_descope()
    ctx, _sbx, _said = _sweep_ctx(tmp_path, green=set())              # nothing passes on the final ws

    promoted_files, new_report, notes = pipeline._rehabilitation_sweep(st, ctx)

    assert promoted_files == []
    assert st.spec.success_criteria[0].status == "descoped"          # a pass is a pass — no pass, no promote
    assert new_report == st.descope_report                           # entry untouched


def test_sweep_runs_adequacy_and_leaves_a_gameable_test_descoped(tmp_path, monkeypatch):
    """Guardrail 2: even a GREEN descoped test is not promoted if the (non-degraded) critic judges it
    inadequate — this closes the known 'met-existing skips adequacy' residual rather than widening it."""
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)         # non-degraded → adequacy is blocking
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=False, reason="an echo stub passes"))
    st = _state_with_descope()
    ctx, _sbx, _said = _sweep_ctx(tmp_path, green={"test_iter_0.py"})

    promoted_files, _new_report, notes = pipeline._rehabilitation_sweep(st, ctx)

    assert promoted_files == []
    assert st.spec.success_criteria[0].status == "descoped"          # green but inadequate → stays descoped
    assert any("inadequate" in n for n in notes)


def test_sweep_promotes_under_a_degraded_critic_with_a_non_blocking_note(tmp_path, monkeypatch):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: True)          # degraded → adequacy is advisory
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=False, reason="concern"))
    st = _state_with_descope()
    ctx, _sbx, _said = _sweep_ctx(tmp_path, green={"test_iter_0.py"})

    promoted_files, _new_report, notes = pipeline._rehabilitation_sweep(st, ctx)

    assert promoted_files == ["test_iter_0.py"]                       # promoted (degraded can't block)
    assert st.spec.success_criteria[0].status == "met"
    assert any("degraded critic" in n for n in notes)                # concern recorded, non-blocking


def test_p5_docs_threads_promoted_tests_into_the_publish_set(tmp_path, monkeypatch):
    """Guardrail 1: the sweep runs BEFORE publish, so a promoted criterion's test lands in
    ``green_test_files`` → ships in workspace/tests/ → the clean-room re-runs it (second pass)."""
    from poc_foundry.phases import pipeline

    monkeypatch.setattr(pipeline, "_rehabilitation_sweep",
                        lambda state, ctx: (["test_iter_0.py"], [{"criterion": "c", "resolved": "x"}], ["n"]))
    seen = {}
    monkeypatch.setattr(pipeline, "_publish_tests",
                        lambda state, ctx: seen.setdefault("green", list(state.green_test_files)) and 0 or len(state.green_test_files))
    monkeypatch.setattr(pipeline, "_scribe_demo", lambda ctx, spec: "# demo\n")
    monkeypatch.setattr(pipeline, "git_commit", lambda ws, msg: "sha1")
    monkeypatch.setattr(pipeline, "chown_to_builder", lambda p: None)
    st = _state_with_descope()
    ctx = SimpleNamespace(workspace_dir=tmp_path, say=lambda *a, **k: None)

    upd = pipeline.p5_docs(st, ctx)

    assert "test_iter_0.py" in seen["green"]                          # publish saw the promoted test
    assert upd["descope_report"] == [{"criterion": "c", "resolved": "x"}]
    assert "n" in upd["caveats"]
    assert any("promoted to met" in ln for ln in upd["log"])


# ── Layer 2: the integrity trust-cap downgrade ───────────────────────────────
def _downgrade_state(**kw):
    """All-four-conditions-met baseline (single contained hard-exit, criterion now met, clean-room green)."""
    from poc_foundry.state import BuildState, Spec
    from poc_foundry.artifact import SuccessCriterion

    spec = Spec(goal="g", success_criteria=[SuccessCriterion(text="core", core=True, status="met")])
    base = dict(build_id="poc-x", spec=spec,
                incidents=["[high] hard-exit: core.py: + os._exit(1)"],
                had_high_incident=True, high_incident_kinds=["hard-exit"],
                high_incident_criteria=["core"],
                cleanroom={"quickstart_ok": True, "suite_ok": True, "demo_ok": True},
                inventory_ok=True, red_first_ok=True)
    base.update(kw)
    return BuildState(**base)


def test_downgrade_when_all_four_conditions_hold(monkeypatch):
    from poc_foundry.phases import pipeline

    st = _downgrade_state()
    assert pipeline._incident_downgradeable(st) is True
    assert pipeline._trustworthy(st) is True                          # cap lifted → trustworthy
    assert pipeline._final_status(st) == "done"


def test_multiple_high_incidents_keep_the_cap():
    from poc_foundry.phases import pipeline

    st = _downgrade_state(
        incidents=["[high] hard-exit: core.py: + os._exit(1)",
                   "[high] test-edit: coder diff touches a test file: tests/t.py"],
        high_incident_kinds=["hard-exit", "test-edit"])
    assert pipeline._incident_downgradeable(st) is False             # (4) >1 high → scrutiny scales up
    assert pipeline._trustworthy(st) is False
    assert pipeline._final_status(st) == "incomplete"


def test_downgrade_denied_when_targeted_criterion_not_met():
    from poc_foundry.phases import pipeline
    from poc_foundry.artifact import SuccessCriterion

    st = _downgrade_state()
    st.spec.success_criteria = [SuccessCriterion(text="core", core=True, status="descoped")]
    assert pipeline._incident_downgradeable(st) is False             # (2) criterion never satisfied
    assert pipeline._trustworthy(st) is False


def test_downgrade_denied_when_cleanroom_red():
    from poc_foundry.phases import pipeline

    st = _downgrade_state(cleanroom={"quickstart_ok": True, "suite_ok": False, "demo_ok": True})
    assert pipeline._incident_downgradeable(st) is False             # (3) clean-room must be green
    assert pipeline._trustworthy(st) is False


def test_downgrade_denied_for_a_non_construction_incident_kind():
    from poc_foundry.phases import pipeline

    # a kind outside the rolled-back-construction allowlist (e.g. a security-load-bearing event) keeps the cap
    st = _downgrade_state(high_incident_kinds=["broker-invariant-rejection"])
    assert pipeline._incident_downgradeable(st) is False             # (1) not a contained construction edit
    assert pipeline._trustworthy(st) is False


def test_downgrade_never_overrides_a_ledger_or_red_first_failure():
    from poc_foundry.phases import pipeline

    # the downgrade only touches the incident term; inventory/red-first failures still block unconditionally
    assert pipeline._trustworthy(_downgrade_state(inventory_ok=False)) is False
    assert pipeline._trustworthy(_downgrade_state(red_first_ok=False)) is False


# ── #57: suite-aware adequacy + scope limit ──────────────────────────────────
def test_adequacy_prompt_is_suite_aware_and_scope_limited():
    from poc_foundry import prompts

    p = prompts.critic_adequacy_prompt(
        "durable exactly-once ledger across a kill", "def test_k(): assert True\n",
        "core.generate_reply(m)",
        siblings=[("S1", "resume correct for varied K=1 and K=3", "def test_varied(): assert True\n")])
    assert "Sibling tests" in p and "[S1]" in p and "varied K=1 and K=3" in p
    assert "SUITE-AWARE" in p and "credited_siblings" in p          # calibration pole: suite-covered → adequate
    assert "SCOPE" in p and "real rag" in p.lower()                 # do not import the goal's domain framing


def test_adequacy_prompt_without_siblings_has_no_suite_block_but_keeps_scope():
    from poc_foundry import prompts

    p = prompts.critic_adequacy_prompt("c", "def t(): assert True\n", "iface")
    assert "Sibling tests" not in p
    assert "SCOPE" in p                                             # scope limit is always present


def test_critic_adequacy_maps_credited_labels_back_to_sibling_texts(monkeypatch):
    import contextlib
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    class _Sp:
        def update(self, **k):
            pass

    monkeypatch.setattr("poc_foundry.tracing.span", lambda *a, **k: contextlib.nullcontext(_Sp()))

    class _LLM:
        def invoke(self, msgs):
            return AdequacyReview(adequate=True, reason="secured by sibling", credited_siblings=["S1"])

    class _Base:
        def with_structured_output(self, model):
            return _LLM()

    monkeypatch.setattr(M, "build_chat_model", lambda role, **kw: _Base())
    ctx = SimpleNamespace(template=SimpleNamespace(interface="core.generate_reply(m)"))
    review = pipeline._critic_adequacy(ctx, "criterion A", "def test_a(): assert True\n",
                                       siblings=[("varied-K sibling", "def test_varied(): assert True\n")])
    assert review.adequate is True
    assert review.credited_siblings == ["varied-K sibling"]         # S1 → the sibling's criterion TEXT


def test_p_critic_persists_adequacy_credits_on_a_suite_credited_accept(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    monkeypatch.setattr(pipeline, "_sibling_met_tests", lambda state, ctx, ex: [("sibling B", "def t(): assert True\n")])
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=True, credited_siblings=["sibling B"]))
    st, ctx = _metexisting_state(tmp_path)
    upd = pipeline.p_critic(st, ctx)
    assert upd["verdict"] == "proceed"
    assert upd["adequacy_credits"]["c"] == ["sibling B"]            # keyed by the criterion text (it.acceptance[0])


def _recheck_state(tmp_path, a_status="met", b_status="descoped"):
    from poc_foundry.state import BuildState, IterationPlan, Plan, Spec
    from poc_foundry.artifact import SuccessCriterion

    staging = tmp_path / "staging"
    (staging / "tests").mkdir(parents=True)
    (staging / "tests" / "test_iter_0.py").write_text("def test_a():\n    assert True\n")
    ctx = SimpleNamespace(staging_dir=staging, say=lambda *a, **k: None)
    spec = Spec(goal="g", success_criteria=[
        SuccessCriterion(text="A", core=True, status=a_status),
        SuccessCriterion(text="B", core=False, status=b_status)])
    plan = Plan(iterations=[IterationPlan(goal="a", acceptance=["A"], test_file="test_iter_0.py"),
                            IterationPlan(goal="b", acceptance=["B"], test_file="test_iter_1.py")])
    st = BuildState(build_id="poc-x", spec=spec, plan=plan,
                    green_test_files=(["test_iter_0.py"] + (["test_iter_1.py"] if b_status == "met" else [])),
                    adequacy_credits={"A": ["B"]})
    return st, ctx


def test_credit_recheck_demotes_when_a_credited_sibling_was_descoped(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    monkeypatch.setattr(pipeline, "_sibling_met_tests", lambda state, ctx, ex: [])
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=False, reason="shortcut re-opened"))
    st, ctx = _recheck_state(tmp_path)                             # A met, credited on B; B descoped
    demoted, report, notes = pipeline._credit_recheck(st, ctx, list(st.descope_report))
    assert demoted == ["test_iter_0.py"]
    assert st.spec.success_criteria[0].status == "descoped"        # A demoted — its shortcut-proofing is gone
    assert any("credited sibling" in n for n in notes)
    assert any("later descoped" in e.get("why_failed", "") for e in report)


def test_credit_recheck_keeps_criterion_when_siblings_intact(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    called = {"n": 0}
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or AdequacyReview(adequate=False))
    st, ctx = _recheck_state(tmp_path, b_status="met")             # B still ships → nothing to re-check
    demoted, _report, _notes = pipeline._credit_recheck(st, ctx, [])
    assert demoted == [] and st.spec.success_criteria[0].status == "met"
    assert called["n"] == 0                                        # no wasted critic call when the credit holds


def test_credit_recheck_is_advisory_under_a_degraded_critic(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: True)       # degraded → non-blocking
    monkeypatch.setattr(pipeline, "_sibling_met_tests", lambda state, ctx, ex: [])
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=False))
    st, ctx = _recheck_state(tmp_path)
    demoted, _report, _notes = pipeline._credit_recheck(st, ctx, [])
    assert demoted == [] and st.spec.success_criteria[0].status == "met"   # single-endpoint not bricked


def test_durable_agent_knowledge_frames_goal_as_durability_not_domain():
    from poc_foundry.phases.context import load_template

    k = load_template("durable-agent").knowledge.lower()
    assert "goal framing" in k and "durable execution" in k
    assert "never" in k and "orthogonal" in k                      # criteria are durability, not 'real RAG work'


# ── Finding A: met-existing now consults adequacy (DEC #54) ──────────────────
def _metexisting_state(tmp_path, **kw):
    from poc_foundry.config import load_config
    from poc_foundry.state import BuildState, IterationPlan, Plan, Spec
    from poc_foundry.artifact import SuccessCriterion, IterationRecord

    cfg = load_config(tmp_path / "builds")
    (tmp_path / "staging" / "tests").mkdir(parents=True, exist_ok=True)   # #57: p_critic gathers sibling met tests
    ctx = SimpleNamespace(cfg=cfg, staging_dir=tmp_path / "staging", say=lambda *a, **k: None)
    spec = Spec(goal="g", success_criteria=[SuccessCriterion(text="c", core=True)], buildable=True)
    base = dict(build_id="poc-x", spec=spec,
                plan=Plan(iterations=[IterationPlan(goal="g", acceptance=["c"], interface="x")]),
                iteration=0,
                iteration_records=[IterationRecord(goal="g", status="met-existing", attempts=0)],
                pending_criterion="c", pending_test_src="def test_x():\n    assert True\n")
    base.update(kw)
    return BuildState(**base), ctx


def test_met_existing_consults_adequacy_and_accepts_when_adequate(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    seen = {}

    def _adq(ctx, crit, src, siblings=None):
        seen["checked"] = True
        return AdequacyReview(adequate=True)

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    monkeypatch.setattr(pipeline, "_critic_adequacy", _adq)
    st, ctx = _metexisting_state(tmp_path)
    upd = pipeline.p_critic(st, ctx)
    assert seen.get("checked") is True                    # adequacy WAS consulted (was skipped pre-#54)
    assert upd["verdict"] == "proceed" and not upd.get("reauthor_pending")


def test_met_existing_inadequate_routes_to_reauthor_not_accept(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: False)   # non-degraded → adequacy is blocking
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=False, reason="a tautological test"))
    st, ctx = _metexisting_state(tmp_path, reauthor_count=0)
    upd = pipeline.p_critic(st, ctx)
    assert upd["verdict"] == "fix" and upd["reauthor_pending"] is True   # strengthen the test, don't accept
    assert upd["reauthor_count"] == 1


def test_met_existing_inadequate_under_degraded_critic_accepts_advisory(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline
    from poc_foundry.state import AdequacyReview

    monkeypatch.setattr(M, "same_family", lambda a, b: True)    # degraded → advisory, non-blocking (unchanged)
    monkeypatch.setattr(pipeline, "_critic_adequacy",
                        lambda ctx, crit, src, siblings=None: AdequacyReview(adequate=False, reason="concern"))
    st, ctx = _metexisting_state(tmp_path)
    upd = pipeline.p_critic(st, ctx)
    assert upd["verdict"] == "proceed" and not upd.get("reauthor_pending")   # single-endpoint not bricked
    assert any("advisory" in c for c in upd.get("caveats", []))


# ── durable-agent coder steer: the crash is injected, don't hard-exit in core.py ──────────────
def test_durable_agent_core_steers_the_coder_away_from_a_hard_exit():
    """Root cause of the A3 re-run's repeated hard-exit incidents: the coder reads the staged test's
    'assert killed subprocess exited non-zero' and adds os._exit to core.py — not realising
    agentkit.checkpoint injects the crash. The scaffold docstring (the one channel the coder reads
    directly) must say so, so a well-behaved coder writes the resume loop instead of tripping the wall."""
    core = (Path(__file__).resolve().parents[1] / "templates" / "durable-agent" / "files" / "core.py").read_text()
    head = " ".join(core.split("def generate_reply", 1)[0].lower().split())   # docstring, whitespace-normalised
    assert "never call" in head and "os._exit" in head             # explicit prohibition
    assert "injected for you" in head or "injects the crash" in head          # crash is not the coder's job
    assert "resume loop" in head


def test_coder_system_prompt_forbids_hard_exit_persistently():
    """P2 (A3 re-run #3): the scaffold-docstring steer (#53) is lost the moment the coder overwrites the
    file, so it only protected iter0 — the coder relapsed into os._exit/sys.exit at iter2. The prohibition
    must live in the coder's SYSTEM prompt (every iteration, every template) since the diff-scanner forbids
    hard exits universally. This pins that it does, and that it names the 'exit comes from a primitive' framing."""
    from poc_foundry.coder import SYSTEM

    s = SYSTEM.lower()
    assert "os._exit" in s and "sys.exit" in s and "systemexit" in s
    assert "discards your whole edit" in s or "flagged as integrity" in s
    assert "primitive" in s                                         # the exit is produced FOR the coder
