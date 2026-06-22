"""M1 walking-skeleton spine tests — broker invariant, coder loop, and the full phase pipeline.

These use FAKES for the broker (no Docker) and the LLM roles (no vLLM), so they run anywhere pytest
+ pydantic are present (in-container or on the 3.10 box). They prove the WIRING + the load-bearing
claims (orchestrator-writes / sandbox-executes; the create-param invariant; RED→GREEN; NOT_BUILDABLE
short-circuit). The real Docker/Kata/LLM path is exercised by the server end-to-end run.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry.artifact import SuccessCriterion, load as load_artifact
from poc_foundry.config import load_config
from poc_foundry.sandbox import Broker, ExecResult, Mount, BrokerInvariantError
from poc_foundry.state import BuildState, Spec


# ── broker invariant (rule #8) ───────────────────────────────────────────────
def _broker():
    return Broker("poc-test", load_config(), allowed_images={"poc-foundry-sandbox", "poc-foundry-proxy"})


def test_broker_rejects_non_allowlisted_image():
    with pytest.raises(BrokerInvariantError):
        _broker()._check_image("evil/image:latest")


def test_broker_rejects_caps_and_untame_mounts():
    b = _broker()
    with pytest.raises(BrokerInvariantError):
        b._check_caps(["SYS_ADMIN"])
    with pytest.raises(BrokerInvariantError):
        b._check_mounts([Mount("relative", "/work")])
    with pytest.raises(BrokerInvariantError):
        b._check_mounts([Mount("/ok", "/work; rm -rf /")])
    b._check_caps([])                                   # empty caps allowed
    b._check_mounts([Mount("/var/tmp/ws", "/work", True)])


# ── coder loop (red→green, forbidden-file guard) ─────────────────────────────
def test_bespoke_coder_red_to_green(tmp_path):
    from poc_foundry.coder import BespokeCoder

    (tmp_path / "core.py").write_text("def reply(m):\n    return ''\n")

    def llm(role, prompt, system=None):
        return "*** FILE: core.py\n```python\ndef reply(m):\n    return 'ECHO: ' + m\n```\n"

    def verify():
        ok = "ECHO:" in (tmp_path / "core.py").read_text()
        return ok, ("1 passed" if ok else "E assert")

    res = BespokeCoder(llm=llm).run(
        workspace=tmp_path, goal="echo", editable_files=["core.py"],
        test_sources={"t.py": "assert"}, verify=verify, max_attempts=3)
    assert res.passed and res.attempts == 1 and "core.py" in res.edited


def test_bespoke_coder_refuses_to_edit_test(tmp_path):
    from poc_foundry.coder import BespokeCoder

    (tmp_path / "core.py").write_text("x = 1\n")

    def llm(role, prompt, system=None):
        return "*** FILE: t.py\n```python\nprint('cheat')\n```\n"

    res = BespokeCoder(llm=llm).run(
        workspace=tmp_path, goal="g", editable_files=["core.py"],
        test_sources={"t.py": "assert"}, verify=lambda: (False, "no"), max_attempts=2)
    assert not res.passed
    assert not (tmp_path / "t.py").exists()             # the test file was never written


# ── full phase pipeline with fakes ───────────────────────────────────────────
class _FakeSandbox:
    def __init__(self, ws):
        self.ws, self.name = ws, "fake"

    def exec(self, cmd, timeout_s=600):
        if "/staged" in cmd:
            ok = "ECHO:" in (self.ws / "core.py").read_text()
            return ExecResult(0 if ok else 1, "1 passed" if ok else "", "" if ok else "E")
        return ExecResult(0, "ok", "")

    def destroy(self):
        pass


class _FakeBroker:
    def __init__(self, ws):
        self.ws, self.proxy_url, self.events = ws, "http://10.0.0.2:3128", []

    def provision(self):
        pass

    def create(self, **kw):
        return _FakeSandbox(self.ws)

    def proxy_log(self, tail=200):
        return "action=TCP_TUNNEL/200 CONNECT pypi.org:443\n"

    def destroy(self):
        pass


def _patch_models(monkeypatch, spec):
    import poc_foundry.models as M

    class _Structured:
        def invoke(self, messages):
            return spec

    class _Chat:
        def with_structured_output(self, model):
            return _Structured()

    def _chat_text(role, prompt, system=None, **kw):
        if role == "tester":
            return ("```python\nfrom core import generate_reply\n"
                    "def test_echo():\n    assert generate_reply('hi', []).startswith('ECHO:')\n```")
        if role == "coder":
            return ("*** FILE: core.py\n```python\n"
                    "def generate_reply(message, history=None):\n"
                    "    return 'ECHO: ' + (message or '').strip()\n```")
        return "# Demo\n"

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "chat_text", _chat_text)


def _run_pipeline(tmp_path, monkeypatch, spec):
    from poc_foundry.coder import BespokeCoder
    from poc_foundry.phases import (Ctx, load_template, p0_ingest, p1_spec, p2_plan,
                                     p3_scaffold, p4_iterate, p5_docs, p6_cleanroom, p7_emit)

    _patch_models(monkeypatch, spec)
    cfg = load_config(tmp_path / "builds")
    bid = "poc-20260622-000000-pytest"
    ws, st = tmp_path / "ws", tmp_path / "staging"
    ws.mkdir(); st.mkdir()
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_artifact"
    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=fixture, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=st,
              broker=_FakeBroker(ws), coder=BespokeCoder())
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws))
    for fn in (p0_ingest, p1_spec, p2_plan, p3_scaffold, p4_iterate, p5_docs, p6_cleanroom, p7_emit):
        state = state.model_copy(update=fn(state, ctx))
    return cfg.builds_dir / bid, state


def test_full_pipeline_emits_done_build(tmp_path, monkeypatch):
    spec = Spec(goal="Echo the user's message with an ECHO: prefix",
                success_criteria=[SuccessCriterion(text="reply starts with 'ECHO:'", core=True),
                                  SuccessCriterion(text="reply is non-empty"),
                                  SuccessCriterion(text="handles empty input")],
                non_goals=["no real LLM"], demo_scenario="Type hi → ECHO: hi", buildable=True)
    build_dir, state = _run_pipeline(tmp_path, monkeypatch, spec)
    pa = load_artifact(build_dir)
    assert pa.status == "done"
    assert pa.final_verdict.demonstrates_core_value == "yes"
    assert any(c.core and c.status == "met" for c in pa.success_criteria)
    assert pa.cleanroom.suite_ok
    assert "ECHO:" in (build_dir / "workspace" / "core.py").read_text()
    for f in ("00_INDEX.md", "report.md", "PROGRESS.md"):
        assert (build_dir / f).exists()
    for f in ("RUN.md", "DEMO.md", "app.py", "core.py", "requirements.txt"):
        assert (build_dir / "workspace" / f).exists()


def test_not_buildable_short_circuits(tmp_path, monkeypatch):
    from poc_foundry.graph import _after_spec
    from poc_foundry.phases import Ctx, load_template, p0_ingest, p1_spec, p7_emit
    from poc_foundry.coder import BespokeCoder

    spec = Spec(goal="", buildable=False, not_buildable_reasons=["needs a live GPU index"])
    _patch_models(monkeypatch, spec)
    cfg = load_config(tmp_path / "builds")
    bid = "poc-20260622-000000-nbtest"
    ws, st = tmp_path / "ws", tmp_path / "staging"
    ws.mkdir(); st.mkdir()
    fixture = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_artifact"
    ctx = Ctx(cfg=cfg, build_id=bid, run_dir=fixture, template=load_template("gradio-chatbot"),
              build_dir=cfg.builds_dir / bid, workspace_dir=ws, staging_dir=st,
              broker=_FakeBroker(ws), coder=BespokeCoder())
    state = BuildState(build_id=bid, build_dir=str(cfg.builds_dir / bid), workspace_dir=str(ws))
    state = state.model_copy(update=p0_ingest(state, ctx))
    state = state.model_copy(update=p1_spec(state, ctx))
    assert _after_spec(state) == "emit"
    state = state.model_copy(update=p7_emit(state, ctx))
    assert load_artifact(cfg.builds_dir / bid).status == "not-buildable"


def test_run_blocks_parse_from_template():
    from poc_foundry.phases import load_template
    from poc_foundry.phases.context import parse_run_blocks

    t = load_template("gradio-chatbot")
    blocks = parse_run_blocks((t.stamp_dir / "RUN.md").read_text())
    assert {"install", "test", "demo", "run"} <= set(blocks)
    assert "uv pip install" in blocks["install"]
