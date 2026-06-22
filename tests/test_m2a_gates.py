"""M2a S1 — integrity-wall fakes suite (design §5.5). Pure parsers + scanner unit tests, plus
PLANTED-GAMING pipeline tests that prove the M2a acceptance: a test-gaming attempt (trivially-green
test / a coder that games the gate / a deleted-or-skipped test) is CAUGHT and the build does NOT
report ``done``.

Like ``test_m1_spine.py`` these use FAKES (no Docker, no vLLM) so they run in-container under pytest
OR on the 3.10 box via ``scripts/run_spine_tests.py``.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry.artifact import SuccessCriterion
from poc_foundry.config import load_config
from poc_foundry.sandbox import ExecResult
from poc_foundry.state import BuildState, Spec
from poc_foundry.phases import integrity
from poc_foundry.phases.pipeline import _final_status, _trustworthy

_FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_artifact"


# ── pure: the inventory ledger parsers ────────────────────────────────────────
def test_collected_names_parses_node_ids():
    out = ("test_criterion.py::test_alpha\n"
           "/staged/test_criterion.py::test_beta\n"
           "tests/test_other.py::TestX::test_gamma\n\n3 tests collected in 0.01s\n")
    assert integrity.collected_names(out) == {"test_alpha", "test_beta", "test_gamma"}


def test_junit_passed_names_splits_pass_fail_skip():
    xml = ('<testsuite>'
           '<testcase name="test_pass" classname="test_criterion"/>'
           '<testcase name="test_fail" classname="test_criterion"><failure/></testcase>'
           '<testcase name="test_skip" classname="test_criterion"><skipped/></testcase>'
           '</testsuite>')
    passed, nonpassed = integrity.junit_passed_names(xml)
    assert passed == {"test_pass"}
    assert nonpassed == {"test_fail", "test_skip"}


def test_junit_malformed_is_a_ledger_failure():
    passed, nonpassed = integrity.junit_passed_names("not xml at all")
    assert passed == set() and nonpassed == set()


def test_inventory_ok_requires_full_nonempty_cover():
    assert integrity.inventory_ok({"a", "b"}, {"a", "b", "c"}) is True
    assert integrity.inventory_ok({"a", "b"}, {"a"}) is False           # b missing → deleted/skipped
    assert integrity.inventory_ok(set(), {"a"}) is False                # no recorded tests at all
    assert integrity.inventory_gap({"a", "b"}, {"a"}) == {"b"}


# ── pure: the diff scanner ────────────────────────────────────────────────────
def _diff(path, added=(), removed=()):
    body = [f"--- a/{path}", f"+++ b/{path}", "@@ -1,1 +1,2 @@"]
    body += [f"+{ln}" for ln in added] + [f"-{ln}" for ln in removed]
    return "\n".join(body) + "\n"


def test_scan_flags_test_file_edit():
    inc = integrity.scan_diff(_diff("test_criterion.py", added=["assert True"]),
                              {"test_criterion.py"})
    assert any(i.kind == "test-edit" for i in inc)
    assert integrity.blocking(inc)


def test_scan_flags_skip_marker_and_hard_exit_in_core():
    inc = integrity.scan_diff(_diff("core.py", added=["@pytest.mark.skip", "    sys.exit(0)"]))
    kinds = {i.kind for i in inc}
    assert "skip-marker" in kinds and "hard-exit" in kinds


def test_scan_flags_pytest_config_injection():
    inc = integrity.scan_diff(_diff("conftest.py", added=["addopts = -p no:cacheprovider"]))
    kinds = {i.kind for i in inc}
    assert "test-edit" in kinds or "pytest-config" in kinds
    assert integrity.blocking(inc)


def test_scan_flags_assertion_deletion_in_test():
    inc = integrity.scan_diff(_diff("test_criterion.py", removed=["assert reply.startswith('ECHO:')"]),
                              {"test_criterion.py"})
    assert any(i.kind == "assert-deleted" for i in inc)


def test_scan_clean_core_edit_passes():
    clean = _diff("core.py", added=["def generate_reply(m, h=None):", "    return 'ECHO: ' + m"])
    assert integrity.scan_diff(clean) == []


# ── pure: the status gate refuses `done` unless every wall held ───────────────
def _state(**kw):
    base = dict(build_id="poc-x", status="incomplete",
                spec=Spec(goal="g", success_criteria=[SuccessCriterion(text="c", core=True, status="met")]),
                cleanroom={"suite_ok": True}, inventory_ok=True, red_first_ok=True, incidents=[])
    base.update(kw)
    return BuildState(**base)


def test_status_done_only_when_all_walls_hold():
    assert _final_status(_state()) == "done"
    assert _trustworthy(_state()) is True


def test_status_blocked_by_ledger_failure():
    assert _final_status(_state(inventory_ok=False)) == "incomplete"


def test_status_blocked_by_red_first_violation():
    assert _final_status(_state(red_first_ok=False)) == "incomplete"


def test_status_blocked_by_high_incident():
    assert _final_status(_state(incidents=["[high] hard-exit: core.py: + sys.exit(0)"])) == "incomplete"
    assert _trustworthy(_state(incidents=["[low] note: fyi"])) is True   # low-sev does not block


# ── planted-gaming through the real P4 (fakes for broker + models) ────────────
def _junit(passed=(), failed=()):
    cases = "".join(f'<testcase name="{n}" classname="test_criterion"/>' for n in passed)
    cases += "".join(f'<testcase name="{n}" classname="test_criterion"><failure/></testcase>'
                     for n in failed)
    return f"<testsuite>{cases}</testsuite>"


class _Sbx:
    def __init__(self, ws, *, plain_ok, collected, junit_passed=(), junit_failed=()):
        self.ws, self._plain = ws, plain_ok
        self.collected, self.jp, self.jf = collected, junit_passed, junit_failed

    def exec(self, cmd, timeout_s=600):
        if "--collect-only" in cmd:
            return ExecResult(0, "\n".join(f"test_criterion.py::{n}" for n in self.collected) + "\n", "")
        if "--junitxml" in cmd:
            return ExecResult(0, _junit(self.jp, self.jf), "")
        if "/staged" in cmd:
            ok = self._plain() if callable(self._plain) else self._plain
            return ExecResult(0 if ok else 1, "1 passed" if ok else "", "" if ok else "E")
        return ExecResult(0, "ok", "")

    def destroy(self):
        pass


class _Broker:
    def __init__(self, sbx):
        self._sbx, self.proxy_url, self.events = sbx, "http://10.0.0.2:3128", []

    def provision(self):
        pass

    def create(self, **kw):
        return self._sbx

    def proxy_log(self, tail=200):
        return ""

    def destroy(self):
        pass


def _patch(monkeypatch, spec, tester_src, coder_src=""):
    import poc_foundry.models as M

    class _Structured:
        def invoke(self, messages):
            return spec

    class _Chat:
        def with_structured_output(self, model):
            return _Structured()

    def _chat_text(role, prompt, system=None, **kw):
        if role == "tester":
            return tester_src
        if role == "coder":
            return coder_src
        return "# Demo\n"

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "chat_text", _chat_text)


def _spec():
    return Spec(goal="Echo with ECHO: prefix",
                success_criteria=[SuccessCriterion(text="reply starts with 'ECHO:'", core=True),
                                  SuccessCriterion(text="reply non-empty"),
                                  SuccessCriterion(text="handles empty")],
                buildable=True, demo_scenario="type hi")


def _run_through_p4(tmp_path, monkeypatch, *, sbx, tester_src, coder_src=""):
    from poc_foundry.coder import BespokeCoder
    from poc_foundry.phases import Ctx, load_template, p0_ingest, p1_spec, p2_plan, p3_scaffold, p4_iterate

    _patch(monkeypatch, _spec(), tester_src, coder_src)
    cfg = load_config(tmp_path / "builds")
    bid = "poc-20260622-000000-m2a"
    ws, st = tmp_path / "ws", tmp_path / "staging"
    ws.mkdir(); st.mkdir()
    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=_FIXTURE, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=st,
              broker=_Broker(sbx), coder=BespokeCoder())
    sbx.ws = ws
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws))
    for fn in (p0_ingest, p1_spec, p2_plan, p3_scaffold, p4_iterate):
        state = state.model_copy(update=fn(state, ctx))
    return state


def test_p4_red_first_violation_is_caught(tmp_path, monkeypatch):
    """A trivially-green staged test (passes against the scaffold) is a tester-inadequacy signal —
    NOT a pass. red_first_ok is False, an incident fires, the criterion is not met."""
    trivial = "```python\ndef test_trivial():\n    assert True\n```"
    sbx = _Sbx(tmp_path, plain_ok=True, collected={"test_trivial"})   # green pre-coder
    state = _run_through_p4(tmp_path, monkeypatch, sbx=sbx, tester_src=trivial)
    assert state.red_first_ok is False
    assert any("[high] red-first" in i for i in state.incidents)
    assert not any(c.core and c.status == "met" for c in state.spec.success_criteria)
    assert _final_status(state.model_copy(update={"cleanroom": {"suite_ok": True}})) != "done"


def test_p4_inventory_ledger_gap_is_caught(tmp_path, monkeypatch):
    """The coder satisfies the quick verify, but the authoritative junit shows the recorded test did
    NOT pass (deleted/renamed/skipped/errored) → inventory_ok False, criterion not met."""
    tester = "```python\nfrom core import generate_reply\ndef test_real():\n    assert generate_reply('hi', [])\n```"
    coder = ("*** FILE: core.py\n```python\n"
             "def generate_reply(message, history=None):\n    return 'SENTINEL ' + (message or '')\n```")
    sbx = _Sbx(tmp_path, plain_ok=lambda: "SENTINEL" in (tmp_path / "ws" / "core.py").read_text(),
               collected={"test_real"}, junit_passed=(), junit_failed=("test_real",))
    state = _run_through_p4(tmp_path, monkeypatch, sbx=sbx, tester_src=tester, coder_src=coder)
    assert state.inventory_ok is False
    assert any("[high] ledger-gap" in i for i in state.incidents)
    assert not any(c.core and c.status == "met" for c in state.spec.success_criteria)


def test_p4_diff_scanner_catches_a_gaming_coder(tmp_path, monkeypatch):
    """A coder that writes a hard-exit into an allowlisted file to short-circuit pytest is caught by
    the per-attempt diff scanner (real git diff) → the attempt fails, the criterion is descoped, a
    high-severity incident is recorded."""
    tester = "```python\nfrom core import generate_reply\ndef test_real():\n    assert generate_reply('hi', []).startswith('ECHO:')\n```"
    gaming = ("*** FILE: core.py\n```python\nimport sys\n"
              "def generate_reply(message, history=None):\n    sys.exit(0)\n```")
    sbx = _Sbx(tmp_path, plain_ok=False, collected={"test_real"})    # red pre-coder; scan blocks after
    state = _run_through_p4(tmp_path, monkeypatch, sbx=sbx, tester_src=tester, coder_src=gaming)
    assert any("hard-exit" in i for i in state.incidents)
    assert not any(c.core and c.status == "met" for c in state.spec.success_criteria)
    assert _final_status(state.model_copy(update={"cleanroom": {"suite_ok": True}})) != "done"


# ── S2: critic gate + verdict ladder ─────────────────────────────────────────
def _critic_ctx(monkeypatch, tmp_path, *, adequate=True, degraded=False, reason=""):
    import poc_foundry.models as M
    from poc_foundry.phases import Ctx, load_template
    from poc_foundry.state import AdequacyReview

    class _Structured:
        def invoke(self, messages):
            return AdequacyReview(adequate=adequate, reason=reason or ("ok" if adequate else "trivial"))

    class _Chat:
        def with_structured_output(self, model):
            return _Structured()

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "same_family", lambda a, b: degraded)
    cfg = load_config(tmp_path / "builds")
    return Ctx(cfg=cfg, build_id="poc-x", run_dir=tmp_path, template=load_template("gradio-chatbot"),
               build_dir=tmp_path, workspace_dir=tmp_path, staging_dir=tmp_path, broker=None, coder=None)


def _critic_state(status, *, criteria=("reply starts with 'ECHO:'",), iteration=0, incidents=(), **kw):
    from poc_foundry.artifact import IterationRecord
    from poc_foundry.state import IterationPlan, Plan
    crits = [SuccessCriterion(text=t, core=(idx == 0)) for idx, t in enumerate(criteria)]
    plan = Plan(iterations=[IterationPlan(goal=t, acceptance=[t], interface="iface", files=["core.py"])
                            for t in criteria])
    base = dict(build_id="poc-x", spec=Spec(goal="g", success_criteria=crits),
                plan=plan, iteration=iteration,
                iteration_records=[IterationRecord(goal="g", status=status, attempts=1, tests_added=1)],
                pending_criterion=criteria[iteration], pending_test_src="def test_x():\n    assert True\n",
                incidents=list(incidents))
    base.update(kw)
    return BuildState(**base)


def test_critic_proceeds_on_an_adequate_green_last_iteration(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, adequate=True)
    upd = p_critic(_critic_state("green"), ctx)               # single iteration → no more → proceed
    assert upd["verdict"] == "proceed"
    assert upd["degraded_critic"] is False


def test_critic_advances_to_next_iteration_with_fresh_fix_budget(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, adequate=True)
    st = _critic_state("green", criteria=("core crit", "second crit"), iteration=0, fix_count=2)
    upd = p_critic(st, ctx)
    assert upd["verdict"] == "next"          # more iterations remain → advance
    assert upd["iteration"] == 1
    assert upd["fix_count"] == 0             # each iteration gets a fresh fix budget


def test_critic_respecs_an_inadequate_green(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, adequate=False)
    upd = p_critic(_critic_state("green", respec_count=0), ctx)
    assert upd["verdict"] == "respec"
    assert upd["respec_count"] == 1


def test_critic_descopes_inadequate_after_respec_cap(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, adequate=False)   # respec_cap defaults to 1
    upd = p_critic(_critic_state("green", respec_count=1), ctx)
    assert upd["verdict"] == "proceed"                         # last iteration → proceed, criterion descoped
    assert upd["descope_report"] and upd["descope_report"][0]["criterion"] == "reply starts with 'ECHO:'"
    assert any(c.status == "descoped" for c in upd["spec"].success_criteria)


def test_critic_fixes_then_replans_then_descopes_a_failing_coder(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path)                   # fix_limit_k=3 default
    assert p_critic(_critic_state("abandoned", fix_count=0), ctx)["verdict"] == "fix"
    assert p_critic(_critic_state("abandoned", fix_count=3), ctx)["verdict"] == "replan"   # K spent
    desc = p_critic(_critic_state("abandoned", fix_count=3, replan_count=1), ctx)          # replan spent
    assert desc["verdict"] == "proceed" and desc["descope_report"]                         # honest descope


def test_critic_descopes_an_integrity_incident_never_rewards_gaming(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, adequate=True)    # even if a later test looks adequate...
    upd = p_critic(_critic_state("incident"), ctx)             # ...an incident iteration is descoped
    assert upd["verdict"] == "proceed"
    assert upd["descope_report"] and "incident" in upd["descope_report"][0]["why_failed"]


def test_degraded_critic_adequacy_is_advisory_not_blocking(tmp_path, monkeypatch):
    """A same-family (degraded) critic cannot independently certify adequacy → an 'inadequate' verdict
    on a GREEN iteration is recorded as a caveat but does NOT respec/descope (the hard walls gate)."""
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, adequate=False, degraded=True)
    upd = p_critic(_critic_state("green"), ctx)
    assert upd["verdict"] == "proceed"                        # accepted (advisory), not respec/descope
    assert any("degraded" in c for c in upd.get("caveats", []))


def test_met_existing_later_iteration_is_accepted(tmp_path, monkeypatch):
    """A later iteration whose criterion the existing code already satisfies is ACCEPTED (met), not a
    red-first violation — that strictness applies only to iteration 0 against the scaffold."""
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path)
    st = _critic_state("met-existing", criteria=("c0", "c1"), iteration=1)
    upd = p_critic(st, ctx)
    assert upd["verdict"] == "proceed"      # last iteration, accepted
    assert "descope_report" not in upd


def test_degraded_critic_lowers_the_fix_budget(tmp_path, monkeypatch):
    from poc_foundry.phases.pipeline import p_critic
    ctx = _critic_ctx(monkeypatch, tmp_path, degraded=True)   # degraded_fix_limit_k=2 default
    assert p_critic(_critic_state("abandoned", fix_count=0), ctx)["degraded_critic"] is True
    assert p_critic(_critic_state("abandoned", fix_count=1), ctx)["verdict"] == "fix"
    assert p_critic(_critic_state("abandoned", fix_count=2), ctx)["verdict"] == "replan"  # K=2 spent earlier


def test_after_critic_routing_maps_every_verdict():
    from poc_foundry.graph import _after_critic
    cases = {"fix": "iterate", "next": "iterate", "respec": "spec", "replan": "plan", "proceed": "docs"}
    for verdict, node in cases.items():
        assert _after_critic(BuildState(build_id="x", verdict=verdict)) == node


# ── S3: multi-iteration loop + cumulative suite + clean-room publish ──────────
class _FaithfulSbx:
    """A faithful fake sandbox: actually RUNS the staged test functions in-process (importing `core`
    from the workspace), so the cumulative regression gate + the ledger are evaluated for real — no
    pytest needed. Models collect-only / junit / single-file / cumulative pytest invocations."""

    def __init__(self, ws, staged):
        self.ws, self.staged = ws, staged

    def _run(self, files):
        import importlib.util
        passed, failed = set(), set()
        sys.path.insert(0, str(self.ws))
        try:
            for f in files:
                sys.modules.pop("core", None)
                for m in [m for m in sys.modules if m.startswith("test_iter")]:
                    sys.modules.pop(m, None)
                try:
                    spec = importlib.util.spec_from_file_location(f.stem, f)
                    mod = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(mod)
                except Exception:
                    failed.add(f.stem)
                    continue
                for name, fn in vars(mod).items():
                    if name.startswith("test_") and callable(fn):
                        try:
                            fn()
                            passed.add(name)
                        except Exception:
                            failed.add(name)
        finally:
            sys.path.remove(str(self.ws))
        return passed, failed

    def _files(self, cmd):
        import re as _re
        m = _re.search(r"/staged/(test_iter_\d+\.py)", cmd)
        if m:
            return [self.staged / m.group(1)]
        return sorted(self.staged.glob("test_iter_*.py"))

    def exec(self, cmd, timeout_s=600):
        if "/staged" in cmd:
            files = self._files(cmd)
            passed, failed = self._run(files)
            if "--collect-only" in cmd:
                names = passed | failed
                return ExecResult(0, "\n".join(f"{f.stem}::{n}" for f in files for n in
                                                sorted(passed | failed)) or "\n", "")
            if "--junitxml" in cmd:
                return ExecResult(0, _junit(passed, failed), "")
            return ExecResult(0 if (passed and not failed) else 1, "ok" if not failed else "", "F" if failed else "")
        return ExecResult(0, "ok", "")  # scaffold smoke / cleanroom blocks

    def destroy(self):
        pass


class _FaithfulBroker:
    def __init__(self, ws, staged):
        self.ws, self.staged, self.proxy_url, self.events = ws, staged, "http://10.0.0.2:3128", []

    def provision(self):
        pass

    def create(self, **kw):
        return _FaithfulSbx(self.ws, self.staged / "tests")

    def proxy_log(self, tail=200):
        return ""

    def destroy(self):
        pass


def test_multi_iteration_build_completes_with_cumulative_publish(tmp_path, monkeypatch):
    """End-to-end (fakes): a 2-criterion spec → 2 iterations. Iteration 0 (core) is RED→GREEN via the
    coder; iteration 1's criterion is satisfied by the same code → met-existing. The loop is driven by
    following the critic's routing verdict (as the graph would). Build completes `done`; only met
    iterations' tests are published into the clean-room workspace."""
    import poc_foundry.models as M
    from poc_foundry.coder import BespokeCoder
    from poc_foundry.phases import (Ctx, load_template, p0_ingest, p1_spec, p2_plan, p3_scaffold,
                                    p4_iterate, p_critic, p5_docs, p6_cleanroom, p7_emit)
    from poc_foundry.artifact import load as load_artifact
    from poc_foundry.state import AdequacyReview

    spec = Spec(goal="Echo with ECHO: prefix",
                success_criteria=[SuccessCriterion(text="reply starts with 'ECHO:'", core=True),
                                  SuccessCriterion(text="reply is non-empty")],
                buildable=True, demo_scenario="type hi")

    class _Structured:
        def invoke(self, m):
            return AdequacyReview(adequate=True, reason="ok")

    class _Chat:
        def with_structured_output(self, model):
            return _Structured() if model is AdequacyReview else type("S", (), {"invoke": lambda s, m: spec})()

    def _chat_text(role, prompt, system=None, **kw):
        if role == "tester":
            if "non-empty" in prompt:
                return "```python\nfrom core import generate_reply\ndef test_nonempty():\n    assert generate_reply('hi', [])\n```"
            return "```python\nfrom core import generate_reply\ndef test_echo():\n    assert generate_reply('hi', []).startswith('ECHO:')\n```"
        if role == "coder":
            return ("*** FILE: core.py\n```python\n"
                    "def generate_reply(message, history=None):\n    return 'ECHO: ' + (message or '').strip()\n```")
        return "# Demo\n"

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "chat_text", _chat_text)
    monkeypatch.setattr(M, "same_family", lambda a, b: False)

    cfg = load_config(tmp_path / "builds")
    bid = "poc-multi-0001"
    ws, st = tmp_path / "ws", tmp_path / "staging"
    ws.mkdir(); st.mkdir()
    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=_FIXTURE, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=st,
              broker=_FaithfulBroker(ws, st), coder=BespokeCoder())
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws))

    # P0..P3
    for fn in (p0_ingest, p1_spec, p2_plan, p3_scaffold):
        state = state.model_copy(update=fn(state, ctx))
    assert len(state.plan.iterations) == 2          # multi-iteration plan

    # drive the iterate↔critic loop exactly as the graph would, following the verdict
    guard = 0
    while guard < 20:
        guard += 1
        state = state.model_copy(update=p4_iterate(state, ctx))
        state = state.model_copy(update=p_critic(state, ctx))
        v = state.verdict
        if v in ("next", "fix"):
            continue
        if v == "proceed":
            break
        raise AssertionError(f"unexpected verdict in fake loop: {v}")

    for fn in (p5_docs, p6_cleanroom, p7_emit):
        state = state.model_copy(update=fn(state, ctx))

    pa = load_artifact(cfg.builds_dir / bid)
    assert pa.status == "done"
    assert len(state.iteration_records) == 2                       # both iterations ran
    assert all(c.status == "met" for c in pa.success_criteria)     # core (coder) + second (met-existing)
    assert state.inventory_ok and state.red_first_ok
    # both iterations' tests published into the clean-room workspace
    published = sorted((ws / "tests").glob("test_iter_*.py"))
    assert len(published) == 2


def test_abandoned_iteration_rolls_back_to_last_green(tmp_path, monkeypatch):
    """If a later iteration's coder fails (cap reached), its broken UNCOMMITTED edits are discarded so
    the workspace stays at the last green commit — otherwise P5's `git add -A` would sweep broken code
    into HEAD and the clean-room would clone it (the real bug the pgvector server run exposed)."""
    import poc_foundry.models as M
    from poc_foundry.coder import BespokeCoder
    from poc_foundry.phases import (Ctx, load_template, p0_ingest, p1_spec, p2_plan, p3_scaffold,
                                    p4_iterate, p_critic)
    from poc_foundry.state import AdequacyReview

    spec = Spec(goal="markers",
                success_criteria=[SuccessCriterion(text="reply contains M0", core=True),
                                  SuccessCriterion(text="reply contains M1")],
                buildable=True, demo_scenario="x")

    class _S:
        def invoke(self, m):
            return AdequacyReview(adequate=True, reason="ok")

    class _Chat:
        def with_structured_output(self, model):
            return _S() if model is AdequacyReview else type("X", (), {"invoke": lambda s, mm: spec})()

    def _ct(role, prompt, system=None, **k):
        if role == "tester":
            marker = "M1" if "M1" in prompt else "M0"
            return (f"```python\nfrom core import generate_reply\n"
                    f"def test_{marker.lower()}():\n    assert '{marker}' in generate_reply('x', [])\n```")
        if role == "coder":
            if "M1" in prompt:   # iter1's coder is broken — emits neither marker (also breaks M0)
                return "*** FILE: core.py\n```python\ndef generate_reply(message, history=None):\n    return 'BAD'\n```"
            return "*** FILE: core.py\n```python\ndef generate_reply(message, history=None):\n    return 'M0 only'\n```"
        return "# Demo\n"

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "chat_text", _ct)
    monkeypatch.setattr(M, "same_family", lambda a, b: False)

    cfg = load_config(tmp_path / "builds")
    ws, st = tmp_path / "ws", tmp_path / "staging"
    ws.mkdir(); st.mkdir()
    ctx = Ctx(cfg=cfg, build_id="poc-rb", run_dir=_FIXTURE, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / "poc-rb", workspace_dir=ws, staging_dir=st,
              broker=_FaithfulBroker(ws, st), coder=BespokeCoder())
    state = BuildState(build_id="poc-rb", build_dir=str(ctx.build_dir), workspace_dir=str(ws))
    for fn in (p0_ingest, p1_spec, p2_plan, p3_scaffold):
        state = state.model_copy(update=fn(state, ctx))

    state = state.model_copy(update=p4_iterate(state, ctx))          # iter0 → green (M0 only)
    assert state.iteration_records[-1].status == "green"
    state = state.model_copy(update=p_critic(state, ctx))            # → next, iteration 1
    assert state.verdict == "next" and state.iteration == 1
    state = state.model_copy(update=p4_iterate(state, ctx))          # iter1 coder broken → abandoned
    assert state.iteration_records[-1].status == "abandoned"

    core_txt = (ws / "core.py").read_text()
    assert "M0 only" in core_txt and "BAD" not in core_txt           # last GREEN, not the failed edit
