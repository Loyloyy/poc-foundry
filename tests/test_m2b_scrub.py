"""M2b S1 fakes — the emitted-output hygiene scrubber (scrub.py).

Pure: no Docker / no LLM. Feeds fake-but-realistic sensitive values (a vLLM host, a served-model id,
an API key, NFS/workspace paths) and asserts they are GONE from emitted text and the placeholders are
present — and that unrelated prose (localhost:7860, generic words) survives untouched.
"""
from __future__ import annotations

import json

from poc_foundry import scrub

# a fake server config (NOT the real .env — these are invented for the test)
_ENV = {
    "ARCHITECT_MODEL": "qwen2.5-coder-32b-instruct-awq",
    "ARCHITECT_API_BASE": "http://10.20.30.40:8000/v1",
    "ARCHITECT_API_KEY": "sk-secret-abc123def456",
    "PF_VLLM_ALLOW_HOST": "10.20.30.40:8000",
    "PF_WORKSPACE_DIR": "/srv/nfs/builder/pf-workspaces",
    "PF_SERVICE_PG_HOST": "172.31.0.5",
    "PF_DEFAULT_ROLE": "architect",          # not a secret — must be ignored
}


def test_collect_secrets_finds_host_model_key_path():
    secrets = scrub.collect_secrets(env=_ENV)
    values = {v for v, _ in secrets}
    assert "10.20.30.40:8000" in values          # host:port (allow-host + api-base authority)
    assert "10.20.30.40" in values               # bare host
    assert "qwen2.5-coder-32b-instruct-awq" in values
    assert "sk-secret-abc123def456" in values
    assert "/srv/nfs/builder/pf-workspaces" in values
    assert "172.31.0.5" in values
    # the role label is NOT a secret
    assert "architect" not in values
    # sorted longest-first so host:port replaces before bare host
    lengths = [len(v) for v, _ in secrets]
    assert lengths == sorted(lengths, reverse=True)


def test_scrub_text_removes_all_sensitive_values():
    secrets = scrub.collect_secrets(env=_ENV)
    text = (
        "CONNECT 10.20.30.40:8000 ...\n"
        "model=qwen2.5-coder-32b-instruct-awq base=http://10.20.30.40:8000/v1\n"
        "Authorization: Bearer sk-secret-abc123def456\n"
        "workspace at /srv/nfs/builder/pf-workspaces/poc-x\n"
        "sibling pg @ 172.31.0.5:5432\n"
        "open http://localhost:7860 to view\n"
    )
    out = scrub.scrub_text(text, secrets)
    for leak in ("10.20.30.40", "qwen2.5-coder-32b-instruct-awq",
                 "sk-secret-abc123def456", "/srv/nfs/builder", "172.31.0.5"):
        assert leak not in out, f"leak survived: {leak}"
    assert scrub.ENDPOINT in out and scrub.MODEL in out and scrub.KEY in out
    assert "localhost:7860" in out               # generic local URL untouched


def test_scrub_build_dir_rewrites_files_and_keeps_json_valid(tmp_path):
    secrets = scrub.collect_secrets(env=_ENV)
    bd = tmp_path / "poc-x"
    (bd / "logs").mkdir(parents=True)
    (bd / "report.md").write_text("endpoint http://10.20.30.40:8000/v1 model qwen2.5-coder-32b-instruct-awq")
    (bd / "PROGRESS.md").write_text("- ran on 10.20.30.40:8000")
    (bd / "00_INDEX.md").write_text("see logs/egress.log")
    (bd / "logs" / "egress.log").write_text("CONNECT 10.20.30.40:8000 TCP_TUNNEL/200")
    art = {"id": "poc-x", "caveats": ["phase crash at http://10.20.30.40:8000/v1"],
           "status": "failed", "security": {"incidents": ["leaked qwen2.5-coder-32b-instruct-awq"]}}
    (bd / "v01.json").write_text(json.dumps(art))

    changed = scrub.scrub_build_dir(bd, secrets)
    assert {(bd / "report.md").name} <= {p.split("/")[-1] for p in changed}

    for f in ("report.md", "PROGRESS.md", "logs/egress.log", "v01.json"):
        body = (bd / f).read_text()
        assert "10.20.30.40" not in body, f"{f} leaked the host"
        assert "qwen2.5-coder-32b-instruct-awq" not in body or f == "00_INDEX.md"
    # the artifact JSON is still parseable after scrubbing (placeholders are quote-free)
    reloaded = json.loads((bd / "v01.json").read_text())
    assert reloaded["status"] == "failed"
    assert scrub.ENDPOINT in reloaded["caveats"][0]


def test_no_secrets_is_a_clean_noop(tmp_path):
    # an env with only placeholders / sentinels yields no substitutions
    secrets = scrub.collect_secrets(env={"ARCHITECT_MODEL": "<served-model-id>",
                                         "ARCHITECT_API_KEY": "not-needed"})
    assert secrets == []
    bd = tmp_path / "poc-y"
    bd.mkdir()
    (bd / "report.md").write_text("nothing sensitive here")
    assert scrub.scrub_build_dir(bd, secrets) == []
    assert (bd / "report.md").read_text() == "nothing sensitive here"
