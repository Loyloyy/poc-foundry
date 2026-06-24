"""M2c S5 — template CI fakes suite (dockerless static preflight).

The broker scaffold+smoke runs on the server; locally we prove the part that needs no VM: the runner
enumerates every ``templates/*/template.json``, resolves each, and asserts each declared service is
pinned in ``vetted_services`` (rule #8 — an unpinned service can't be spun, so the template would rot).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry.core import _discover_templates, preflight_templates


def test_discovers_and_resolves_all_real_templates():
    names = _discover_templates()
    assert "gradio-chatbot" in names and "gradio-rag-pgvector" in names
    rows = preflight_templates()
    assert {r["template"] for r in rows} == set(names)
    for r in rows:
        assert r["resolves"] is True, r
        assert r["services_pinned"] is True, r   # chatbot has none; pgvector's pg→pgvector is pinned
        assert r["suite"]                        # a smoke suite is declared


def test_pgvector_declares_pinned_service():
    rows = {r["template"]: r for r in preflight_templates()}
    pg = rows["gradio-rag-pgvector"]
    assert pg["services"] == ["pg"] and pg["services_pinned"] is True


def test_preflight_flags_unpinned_service(monkeypatch):
    import poc_foundry.core as core
    from poc_foundry.phases.context import Template

    # a synthetic template declaring milvus — which IS in vetted_services but pinned "<pin-before-use>"
    fake = Template(name="faketmpl", version="0", root=Path("."), stamp_dir=Path("."),
                    editable_files=[], smoke_test="tests", suite="tests", interface="x",
                    run_cmd="python app.py", stack=[], services=[{"name": "v", "vetted": "milvus"}])
    monkeypatch.setattr(core, "load_template", lambda name: fake)
    rows = core.preflight_templates(["faketmpl"])
    assert rows[0]["resolves"] is True
    assert rows[0]["services_pinned"] is False
    assert "not pinned" in rows[0]["error"]


def test_preflight_records_unresolvable_template(monkeypatch):
    import poc_foundry.core as core

    def _boom(name):
        raise FileNotFoundError("no such template")
    monkeypatch.setattr(core, "load_template", _boom)
    rows = core.preflight_templates(["ghost"])
    assert rows[0]["resolves"] is False and "load failed" in rows[0]["error"]


def test_cli_preflight_exit_code():
    from poc_foundry.cli import main
    assert main(["template-ci", "--preflight"]) == 0   # the real templates all pass preflight
