"""M2a S4b — sibling-service wiring fakes (no Docker, no real pgvector). Proves the harness path:
a template that DECLARES a vetted service → the broker spins it (pinned tag, ready_cmd) in P3 → its IP
is recorded as PF_SERVICE_<NAME>_HOST → injected into the iteration + clean-room sandboxes. The real
pgvector round-trip is the server run; this locks the wiring + the rule-#8 image/tag resolution.

Runs under pytest OR ``scripts/run_spine_tests.py``.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry.config import load_config
from poc_foundry.sandbox import ExecResult
from poc_foundry.state import BuildState, Spec
from poc_foundry.artifact import SuccessCriterion

_FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "sample_artifact"


def test_config_exposes_pinned_vetted_service():
    cfg = load_config()
    assert "pgvector/pgvector:pg16" in cfg.service_refs()        # fully pinned → on the broker allowlist
    assert cfg.vetted_services["pgvector"]["ready_cmd"].startswith("pg_isready")


def test_rag_service_template_declares_its_service():
    from poc_foundry.phases import load_template
    t = load_template("gradio-rag-llm")
    assert t.services == [{"name": "pg", "vetted": "pgvector"}]
    assert t.editable_files == ["core.py"]


class _SvcSbx:
    def __init__(self, name):
        self.name = name

    def exec(self, cmd, timeout_s=600):
        if "/staged" in cmd:                                    # keep iterations cheap; outcome irrelevant here
            if "--collect-only" in cmd:
                return ExecResult(0, "test_iter_0.py::test_x\n", "")
            if "--junitxml" in cmd:
                return ExecResult(0, '<testsuite><testcase name="test_x" classname="test_iter_0"/></testsuite>', "")
            return ExecResult(1, "", "red")                     # red pre-coder (we only assert wiring)
        return ExecResult(0, "ok", "")                          # scaffold smoke green

    def destroy(self):
        pass


class _SvcBroker:
    def __init__(self):
        self.services, self.created = [], []
        self.proxy_url, self.events = "http://10.0.0.2:3128", []

    def provision(self):
        pass

    def create(self, *, mounts, caps=(), name="sbx", image=None, env_extra=None):
        self.created.append((name, dict(env_extra or {})))
        return _SvcSbx(name)

    def create_service(self, *, image, name, env=None, pinned_tag=None, ready_cmd=None):
        self.services.append({"image": image, "tag": pinned_tag, "name": name,
                              "ready_cmd": ready_cmd, "env": env})
        return _SvcSbx(f"svc-{name}")

    def service_ip(self, sbx):
        return "172.30.0.9"

    def proxy_log(self, tail=200):
        return ""

    def destroy(self):
        pass


def _patch_models(monkeypatch):
    import poc_foundry.models as M

    spec = Spec(goal="RAG with citations",
                success_criteria=[SuccessCriterion(text="keyword query returns a [1] citation", core=True)],
                buildable=True, demo_scenario="ask about python")

    class _S:
        def invoke(self, m):
            return spec

    class _Chat:
        def with_structured_output(self, model):
            return _S()

    monkeypatch.setattr(M, "build_chat_model", lambda role, **k: _Chat())
    monkeypatch.setattr(M, "same_family", lambda a, b: True)
    monkeypatch.setattr(M, "chat_text",
                        lambda role, prompt, system=None, **k:
                        "```python\nfrom core import generate_reply\ndef test_x():\n    assert '[1]' in generate_reply('python', [])\n```"
                        if role == "tester" else "*** FILE: core.py\n```python\nx=1\n```")


def _ctx(tmp_path, broker):
    from poc_foundry.coder import BespokeCoder
    from poc_foundry.phases import Ctx, load_template
    cfg = load_config(tmp_path / "builds")
    ws, st = tmp_path / "ws", tmp_path / "staging"
    ws.mkdir(); st.mkdir()
    return Ctx(cfg=cfg, build_id="poc-svc-1", run_dir=_FIXTURE,
               template=load_template("gradio-rag-llm"),
               build_dir=cfg.builds_dir / "poc-svc-1", workspace_dir=ws, staging_dir=st,
               broker=broker, coder=BespokeCoder())


def test_p3_spins_service_and_records_ip(tmp_path, monkeypatch):
    from poc_foundry.phases import p0_ingest, p1_spec, p2_plan, p3_scaffold
    _patch_models(monkeypatch)
    broker = _SvcBroker()
    ctx = _ctx(tmp_path, broker)
    state = BuildState(build_id="poc-svc-1", build_dir=str(ctx.build_dir), workspace_dir=str(ctx.workspace_dir))
    for fn in (p0_ingest, p1_spec, p2_plan, p3_scaffold):
        state = state.model_copy(update=fn(state, ctx))

    # the broker spun pgvector with the HARNESS-FIXED image/tag/ready_cmd (rule #8)
    assert broker.services == [{"image": "pgvector/pgvector", "tag": "pg16", "name": "pg",
                                "ready_cmd": "pg_isready -U postgres", "env": {"POSTGRES_PASSWORD": "pf"}}]
    # its IP is recorded for by-IP reach + the password threaded through
    assert ctx.service_env == {"PF_SERVICE_PG_HOST": "172.30.0.9",
                               "PF_SERVICE_PG_POSTGRES_PASSWORD": "pf"}


def test_iteration_sandbox_receives_service_env(tmp_path, monkeypatch):
    from poc_foundry.phases import p0_ingest, p1_spec, p2_plan, p3_scaffold, p4_iterate
    _patch_models(monkeypatch)
    broker = _SvcBroker()
    ctx = _ctx(tmp_path, broker)
    state = BuildState(build_id="poc-svc-1", build_dir=str(ctx.build_dir), workspace_dir=str(ctx.workspace_dir))
    for fn in (p0_ingest, p1_spec, p2_plan, p3_scaffold, p4_iterate):
        state = state.model_copy(update=fn(state, ctx))

    iter_creates = [env for (name, env) in broker.created if name.startswith("iter")]
    assert iter_creates and iter_creates[0].get("PF_SERVICE_PG_HOST") == "172.30.0.9"
