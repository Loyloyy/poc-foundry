"""M6 diagnostics fixes — make a STUCK/descoped iteration debuggable and stop the recovery rungs from
being fed the harness's own meta-message.

Covers three fixes surfaced by the thin-template run (every criterion descoped with no usable trail):
  1. coder edit-extraction is robust to a multi-block reasoning-model response (largest fence wins),
     and the coder records its RAW last response + a non-blank last_output even when no edit applied;
  2. reflection (`_reflect`) is grounded in the REAL failure detail, not the bare `note`;
  3. research-on-gaps (`_looks_like_error`) skips a meta-message instead of web-searching it; and a
     struggling iteration leaves a forensic trail (`_persist_iter_forensics`).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


# ── Fix 3: coder edit-extraction robustness + forensics fields ───────────────
def test_coder_applies_largest_block_from_a_multi_block_response(tmp_path):
    """A reasoning model emits SEVERAL fenced snippets (a thought, then the full file). With one
    editable file the coder must apply the LARGEST block — not bail because there is >1 block."""
    from poc_foundry.coder import BespokeCoder

    (tmp_path / "core.py").write_text("def reply(m):\n    return ''\n")

    multi = (
        "Here's my plan, first a sketch:\n"
        "```python\n# sketch: return echo\n```\n"
        "and now the full file:\n"
        "```python\ndef reply(m):\n    return 'ECHO: ' + m\n```\n")

    def llm(role, prompt, system=None):
        return multi

    def verify():
        ok = "ECHO:" in (tmp_path / "core.py").read_text()
        return ok, ("1 passed" if ok else "E assert")

    res = BespokeCoder(llm=llm).run(
        workspace=tmp_path, goal="echo", editable_files=["core.py"],
        test_sources={"t.py": "assert"}, verify=verify, max_attempts=2)
    assert res.passed and res.attempts == 1
    assert res.last_response == multi                     # raw response captured for forensics


def test_coder_records_response_and_nonblank_output_when_no_edit_applies(tmp_path):
    """When the model emits NO code block at all, no edit applies and verify never runs — the result
    must still carry the raw response + a non-blank last_output (the symptom that hid the failure was a
    blank output making the incident the meta-string)."""
    from poc_foundry.coder import BespokeCoder

    (tmp_path / "core.py").write_text("x = 1\n")

    def llm(role, prompt, system=None):
        return "I think you should just return the message, no code here."

    res = BespokeCoder(llm=llm).run(
        workspace=tmp_path, goal="g", editable_files=["core.py"],
        test_sources={"t.py": "assert"}, verify=lambda: (True, "1 passed"), max_attempts=2)
    assert not res.passed
    assert res.last_response                              # the raw prose response was captured
    assert "edit not applied" in res.last_output         # last_output is non-blank → real incident


# ── Fix 2: research/reflection no longer fed a meta-message ──────────────────
def test_looks_like_error_distinguishes_real_errors_from_meta_messages():
    from poc_foundry.phases.pipeline import _looks_like_error

    assert _looks_like_error("E   AssertionError: assert '[1]' in reply")
    assert _looks_like_error("ModuleNotFoundError: no module named 'foo'")
    assert _looks_like_error("Traceback (most recent call last):")
    assert not _looks_like_error("fix-attempt cap reached")
    assert not _looks_like_error("")


# ── Fix 1/3: reflection grounded in the real detail + forensic trail ─────────
def _fake_ctx(tmp_path: Path):
    said: list = []
    return SimpleNamespace(build_dir=str(tmp_path), say=lambda *a, **k: said.append(a)), said


def test_reflect_grounds_the_lesson_in_the_real_detail_not_the_note(tmp_path, monkeypatch):
    """`_reflect` must put the REAL failure (verify output) into the incident the coder reflects on,
    not the bare 'fix-attempt cap reached' note."""
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    captured = {}

    def fake_chat_text(role, prompt, system=None, **kw):
        captured["prompt"] = prompt
        return "- bullet one\n- bullet two"

    monkeypatch.setattr(M, "chat_text", fake_chat_text)
    ctx, _said = _fake_ctx(tmp_path)
    it = SimpleNamespace(goal="answer from corpus", acceptance=["cite the matching doc"])
    real_error = "E   AssertionError: assert '[1]' in 'not implemented'"

    pipeline._reflect(ctx, 0, it, "abandoned", 3, [], "fix-attempt cap reached", detail=real_error)

    assert real_error in captured["prompt"]              # the model reflects on the REAL error
    lessons = (tmp_path / "iterations" / "0" / "lessons.md").read_text()
    assert real_error in lessons and "fix-attempt cap reached" not in lessons.split("##")[0]


def test_persist_iter_forensics_writes_the_trail(tmp_path):
    from poc_foundry.phases import pipeline

    ctx, _said = _fake_ctx(tmp_path)
    pipeline._persist_iter_forensics(
        ctx, 1, test_src="def test_x():\n    assert generate_reply('hi')\n",
        coder_response="```python\ndef generate_reply(m): ...\n```",
        verify_output="E   AssertionError: no citation", note="coder did not reach green")

    d = tmp_path / "iterations" / "1"
    assert (d / "staged_test.py").read_text().startswith("def test_x()")
    incident = (d / "incident.txt").read_text()
    assert "AssertionError: no citation" in incident      # the real verify output is preserved
    assert "def generate_reply" in incident               # the raw coder response is preserved


def test_persist_iter_forensics_marks_a_never_applied_edit(tmp_path):
    from poc_foundry.phases import pipeline

    ctx, _said = _fake_ctx(tmp_path)
    pipeline._persist_iter_forensics(ctx, 0, test_src="", coder_response="", verify_output="",
                                     note="fix-attempt cap reached")
    incident = (tmp_path / "iterations" / "0" / "incident.txt").read_text()
    assert "no verify ran" in incident


# ── interface-preservation gate (general: don't let the coder amputate the scaffold) ─────────
def test_interface_problem_flags_syntax_error_missing_def_and_passes_when_kept(tmp_path):
    from poc_foundry.coder import _interface_problem

    (tmp_path / "core.py").write_text("def generate_reply(:\n  pass\n")          # syntax error
    assert "does not parse" in _interface_problem(tmp_path, ["core.py"], ["generate_reply"])
    (tmp_path / "core.py").write_text("def other():\n    return 1\n")             # def dropped
    assert "generate_reply" in _interface_problem(tmp_path, ["core.py"], ["generate_reply"])
    (tmp_path / "core.py").write_text("def generate_reply():\n    return 1\n")    # kept → OK
    assert _interface_problem(tmp_path, ["core.py"], ["generate_reply"]) == ""


def test_coder_gate_reverts_an_edit_that_amputates_the_required_def(tmp_path):
    """The toy-rewrite failure mode: the model rewrites the editable file and drops the interface. The
    gate must REVERT it (scaffold preserved) and feed the reason back — not proceed on a broken file."""
    from poc_foundry.coder import BespokeCoder

    original = "from ragkit import search\n\ndef generate_reply(m, h=None):\n    return 'stub'\n"
    (tmp_path / "core.py").write_text(original)

    def llm(role, prompt, system=None):
        return "*** FILE: core.py\n```python\ndef helper():\n    return 1\n```\n"   # drops generate_reply

    res = BespokeCoder(llm=llm).run(
        workspace=tmp_path, goal="g", editable_files=["core.py"],
        test_sources={"t.py": "assert"}, verify=lambda: (True, "1 passed"),
        max_attempts=2, required_defs=["generate_reply"])
    assert not res.passed                                      # gate blocked it (verify never ran)
    assert (tmp_path / "core.py").read_text() == original      # reverted — scaffold intact
    assert "generate_reply" in res.last_output                 # feedback names the removed def


def test_coder_gate_allows_an_edit_that_keeps_the_required_def(tmp_path):
    from poc_foundry.coder import BespokeCoder

    (tmp_path / "core.py").write_text("def generate_reply(m, h=None):\n    return ''\n")

    def llm(role, prompt, system=None):
        return ("*** FILE: core.py\n```python\n"
                "def generate_reply(m, h=None):\n    return 'ECHO: ' + m\n```\n")

    def verify():
        ok = "ECHO:" in (tmp_path / "core.py").read_text()
        return ok, ("1 passed" if ok else "E")

    res = BespokeCoder(llm=llm).run(
        workspace=tmp_path, goal="g", editable_files=["core.py"],
        test_sources={"t.py": "assert"}, verify=verify, max_attempts=2,
        required_defs=["generate_reply"])
    assert res.passed


def test_interface_defs_parses_the_function_name():
    from poc_foundry.phases.pipeline import _interface_defs

    assert _interface_defs("core.generate_reply(m: str, h: list | None = None) -> str") == ["generate_reply"]
    both = _interface_defs("core.generate_reply(m)", ["CORPUS"])
    assert "generate_reply" in both and "CORPUS" in both


# ── buggy-test recovery rung (stuck-after-research → re-author the staged test once) ──────────
def _critic_state(tmp_path, **kw):
    from poc_foundry.config import load_config
    from poc_foundry.state import BuildState, IterationPlan, Plan, Spec
    from poc_foundry.artifact import SuccessCriterion, IterationRecord

    cfg = load_config(tmp_path / "builds")
    ctx = SimpleNamespace(cfg=cfg, say=lambda *a, **k: None)
    spec = Spec(goal="g", success_criteria=[SuccessCriterion(text="c", core=True)], buildable=True)
    base = dict(build_id="poc-x", spec=spec,
                plan=Plan(iterations=[IterationPlan(goal="g", acceptance=["c"], interface="x")]),
                iteration=0, fix_count=3,   # fix budget (K=3 non-degraded) EXHAUSTED → re-author rung
                iteration_records=[IterationRecord(goal="g", status="abandoned", attempts=3)],
                last_coder_stuck=False,     # errors VARIED (not "stuck") — re-author must still fire
                last_coder_error="'1' in {1,2,3,4} is always False (str vs int)")
    base.update(kw)
    return BuildState(**base), ctx


def test_critic_grants_one_reauthor_when_fix_budget_exhausted(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    monkeypatch.setattr(M, "same_family", lambda a, b: False)   # non-degraded → K=3
    st, ctx = _critic_state(tmp_path, reauthor_count=0)
    upd = pipeline.p_critic(st, ctx)
    assert upd["verdict"] == "fix"
    assert upd["reauthor_pending"] is True and upd["reauthor_count"] == 1
    assert upd["fix_count"] == 0                     # fresh fix budget vs the re-authored test
    assert "str vs int" in upd["reauthor_reason"]


def test_critic_replans_once_reauthor_budget_is_spent(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    st, ctx = _critic_state(tmp_path, reauthor_count=2)   # cap is 2 → re-author spent → replan
    upd = pipeline.p_critic(st, ctx)
    assert upd["verdict"] == "replan" and not upd.get("reauthor_pending")


def test_critic_replan_sets_replan_mode(monkeypatch, tmp_path):
    import poc_foundry.models as M
    from poc_foundry.phases import pipeline

    monkeypatch.setattr(M, "same_family", lambda a, b: False)
    st, ctx = _critic_state(tmp_path, reauthor_count=2, replan_count=0)   # fix+reauthor spent → replan
    upd = pipeline.p_critic(st, ctx)
    assert upd["verdict"] == "replan"
    assert upd["replan_mode"] is True                # SAME spec → P3 will preserve the workspace


def test_p3_scaffold_preserves_workspace_on_replan(monkeypatch, tmp_path):
    from poc_foundry.phases import pipeline
    from poc_foundry.state import BuildState, Spec
    from poc_foundry.artifact import SuccessCriterion

    calls = {"stamp": 0}
    monkeypatch.setattr(pipeline, "stamp_template",
                        lambda *a, **k: calls.__setitem__("stamp", calls["stamp"] + 1) or [])
    ctx = SimpleNamespace(template=SimpleNamespace(services=[], name="t"), workspace_dir=tmp_path,
                          service_env={"x": "1"}, say=lambda *a, **k: None, broker=None)
    st = BuildState(build_id="poc-x", replan_mode=True, scaffold_sha="sha0", commit_sha="sha9",
                    spec=Spec(goal="g", success_criteria=[SuccessCriterion(text="c", core=True)]))
    upd = pipeline.p3_scaffold(st, ctx)
    assert calls["stamp"] == 0                        # workspace PRESERVED — the stub was NOT re-stamped
    assert upd["scaffold_sha"] == "sha0" and upd.get("status") != "failed"
    assert upd["commit_sha"] == "sha9"                # HEAD (met iterations' code) kept


def test_tester_prompt_includes_the_diagnosis_only_when_reauthoring():
    from poc_foundry import prompts

    with_diag = prompts.tester_prompt("crit", "goal", "core.generate_reply(m)",
                                      diagnosis="citation '1' (str) never in {1,2,3,4} (int)")
    assert "INADEQUATE" in with_diag and "str) never in" in with_diag
    assert "INADEQUATE" not in prompts.tester_prompt("crit", "goal", "core.generate_reply(m)")


# ── anti-shortcut discrimination (tests must fail an echo/keyword/constant stub) ──────────────
def test_tester_and_critic_prompts_demand_a_shortcut_proof_test():
    from poc_foundry import prompts

    tester = prompts.tester_prompt("crit", "goal", "core.generate_reply(m)")
    assert "SHORTCUT" in tester and "GENERALISATION" in tester       # echo/keyword stub must fail
    assert "paraphrase" in tester.lower() or "phrased DIFFERENTLY" in tester

    critic = prompts.critic_adequacy_prompt("crit", "def test_x():\n    assert True\n", "iface")
    assert "shortcut" in critic.lower() and "echo" in critic.lower() # inadequate if an echo stub passes


# ── reasoning-model token headroom: the tester (run #8) truncated mid-test → uncollectable ────
def test_tester_write_uses_generous_budget_and_retries_past_a_truncated_test(monkeypatch, tmp_path):
    from poc_foundry.phases import pipeline
    import poc_foundry.models as M

    seen = {"tokens": [], "n": 0}
    outputs = [
        "```python\ndef test_x(arg\n```",                  # TRUNCATED (unclosed paren) — the run-#8 bug
        "```python\ndef test_x():\n    assert True\n```",   # complete on retry
    ]

    def fake_chat_text(role, prompt, system=None, max_tokens=4000, **kw):
        seen["tokens"].append(max_tokens)
        out = outputs[min(seen["n"], len(outputs) - 1)]
        seen["n"] += 1
        return out

    monkeypatch.setattr(M, "chat_text", fake_chat_text)
    ctx = SimpleNamespace(template=SimpleNamespace(knowledge=""), say=lambda *a, **k: None)
    src = pipeline._tester_write(ctx, ["c"], "goal", "core.generate_reply(m)")
    assert "assert True" in src and "test_x(arg" not in src   # recovered past the truncated attempt
    assert seen["tokens"][0] == 8000                          # reasoning headroom (was the default 4000)
    assert seen["n"] == 2                                     # retried instead of staging the broken test


# ── degenerate-loop robustness: a recursion-limit hit SALVAGES (incomplete), never crashes ────
def test_invoke_with_salvage_salvages_a_recursion_error(monkeypatch, tmp_path):
    from poc_foundry import core
    import poc_foundry.models as M

    class _Recur(Exception):
        pass

    captured = {}
    monkeypatch.setattr(core, "_graph_recursion_error", lambda: _Recur)
    monkeypatch.setattr(M.METER, "begin_run", lambda cfg: None)
    monkeypatch.setattr(core, "_salvage_run",
                        lambda graph, ctx, build_dir, build_id, gcfg, cap: captured.update(cap=cap))

    class _Graph:
        def invoke(self, first, config=None):
            assert config["recursion_limit"] == 150          # config-driven ceiling, not the old 60
            raise _Recur("Recursion limit of 150 reached")

    core._invoke_with_salvage(_Graph(), None, ctx=None, cfg=SimpleNamespace(recursion_limit=150),
                              build_dir=tmp_path, build_id="poc-x")
    assert "recursion-limit" in captured["cap"]              # routed to salvage → honest incomplete


# ── tool-calling pilot (gradio-tool): kit+glue + a shortcut-proof tool contract ──────────────
def test_gradio_tool_template_declares_tool_sibling_and_shortcut_proof_knowledge():
    from poc_foundry.phases.context import load_template

    t = load_template("gradio-tool")
    assert t.editable_files == ["core.py"]                          # kit+glue: only the glue is editable
    assert any(s.get("vetted") == "toolserver" for s in t.services)  # the private tool sibling
    k = t.knowledge.lower()
    assert "call_tool" in k and "opaque" in k                       # the tool-only value is the anchor
    assert "shortcut-proof" in k and ("price_usd" in k or "sku" in k)
