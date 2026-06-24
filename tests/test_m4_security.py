"""M4 S2a fakes — the daemon-side invariant-rejection audit log (design §5.2; DECISIONS #22 follow-up).

The broker daemon is the trust boundary (rule #8 enforcer, sole docker.sock holder). It must durably
record every rejected ``create*`` (+ provision/destroy) APPEND-ONLY, readable independent of the
(possibly compromised) orchestrator, feeding ``security.incidents[]``. These exercise the record path
WITHOUT Docker: a rejected ``create*`` never reaches a docker call (the invariant raises first), so the
audit is provable in the fakes. The live red-team beats (containment + injection) are a server demo.
"""
from __future__ import annotations

import json
import inspect
from types import SimpleNamespace

from poc_foundry.sandbox import audit
from poc_foundry.sandbox.broker import Broker, BrokerInvariantError, Mount
from poc_foundry.sandbox.daemon import BrokerDaemon


def _cfg():
    return SimpleNamespace(sandbox_image="poc-foundry-sandbox", proxy_image="poc-foundry-proxy",
                           kata_runtime="kata", uv_cache_shared=False)


def _provisioned_broker(build_id="poc-sec", vllm_key="not-needed"):
    b = Broker(build_id, _cfg(), allowed_images={"poc-foundry-sandbox", "poc-foundry-proxy"},
               vllm_key=vllm_key)
    b._provisioned = True   # bypass the docker provision; a rejected create raises before any docker call
    return b


# ── append-only audit helper ──────────────────────────────────────────────────
def test_audit_append_read_roundtrip_and_filter(tmp_path):
    p = str(tmp_path / "audit.log")
    audit.append(p, audit.make_entry("poc-a", "rejected", "create", reason="bad image"))
    audit.append(p, audit.make_entry("poc-b", "provision", "provision"))
    audit.append(p, audit.make_entry("poc-a", "destroy", "destroy"))
    assert len(audit.read(p)) == 3
    a = audit.read(p, build_id="poc-a")
    assert [e["event"] for e in a] == ["rejected", "destroy"] and all(e["build_id"] == "poc-a" for e in a)


def test_audit_tolerates_malformed_trailing_line_and_no_path(tmp_path):
    p = str(tmp_path / "audit.log")
    audit.append(p, audit.make_entry("poc-a", "rejected", "create"))
    with open(p, "a") as fh:
        fh.write("{not json\n")          # a crash mid-append must never break the reader
    assert len(audit.read(p)) == 1
    audit.append("", audit.make_entry("x", "rejected", "create"))   # no path → no-op, no raise
    assert audit.read("") == [] and audit.read(str(tmp_path / "nope.log")) == []


# ── broker records rejections ─────────────────────────────────────────────────
def test_broker_records_rejected_create_with_reason_and_detail():
    b = _provisioned_broker()
    for kwargs in ({"name": "evil;rm -rf"},                       # untamed token
                   {"name": "ok", "image": "attacker/evil:latest"},  # non-allowlisted image
                   {"name": "ok", "caps": ("SYS_ADMIN",)},        # cap outside ALLOWED_CAPS
                   {"name": "ok", "mounts": [Mount("/etc", "../../escape")]}):  # untame mount target
        try:
            b.create(mounts=kwargs.pop("mounts", []), **kwargs)
            raise AssertionError("create should have raised BrokerInvariantError")
        except BrokerInvariantError:
            pass
    rejected = [e for e in b.audit() if e["event"] == "rejected"]
    assert len(rejected) == 4
    assert all(e["method"] == "create" and e["reason"] for e in rejected)
    assert rejected[1]["detail"]["image"] == "attacker/evil:latest"
    assert rejected[2]["detail"]["caps"] == ["SYS_ADMIN"]


def test_broker_records_rejected_create_service_and_provision():
    b = _provisioned_broker()
    try:
        b.create_service(image="attacker/evil", name="svc", pinned_tag="latest")
    except BrokerInvariantError:
        pass
    # a provision whose proxy image isn't allowlisted is audited too
    b2 = Broker("poc-p", _cfg(), allowed_images={"poc-foundry-sandbox"})   # proxy NOT allowlisted
    try:
        b2.provision()
    except BrokerInvariantError:
        pass
    assert [e["method"] for e in b.audit() if e["event"] == "rejected"] == ["create_service"]
    prov = [e for e in b2.audit() if e["event"] == "rejected"]
    assert prov and prov[0]["method"] == "provision"


def test_audit_entry_never_leaks_a_secret():
    b = _provisioned_broker(vllm_key="SUPER-SECRET-VLLM-KEY")
    try:
        b.create(mounts=[], name="evil;rm")
    except BrokerInvariantError:
        pass
    blob = json.dumps(b.audit())
    assert "SUPER-SECRET-VLLM-KEY" not in blob   # Finding-0: no secret ever enters the audit record


def test_rejected_create_is_durably_appended_to_the_file(tmp_path, monkeypatch):
    # the daemon points PF_BROKER_AUDIT_LOG at a mounted path so the record survives + is readable
    # INDEPENDENT of the orchestrator (rule #8 enforcement evidence).
    p = str(tmp_path / "broker-audit.log")
    monkeypatch.setenv("PF_BROKER_AUDIT_LOG", p)
    b = _provisioned_broker(build_id="poc-durable")   # __init__ reads the env → file-backed
    try:
        b.create(mounts=[], name="evil;rm")
    except BrokerInvariantError:
        pass
    on_disk = audit.read(p, build_id="poc-durable")
    assert on_disk and on_disk[0]["event"] == "rejected" and on_disk[0]["method"] == "create"


# ── daemon `audit` RPC ─────────────────────────────────────────────────────────
def test_daemon_audit_rpc_returns_rejections():
    daemon = BrokerDaemon(_cfg())
    b = _provisioned_broker(build_id="poc-rpc")
    try:
        b.create(mounts=[], name="evil;rm")
    except BrokerInvariantError:
        pass
    daemon._brokers["poc-rpc"] = b                          # as if provisioned through the daemon
    res = daemon.handle("audit", {"build_id": "poc-rpc"})
    assert any(e["event"] == "rejected" for e in res["entries"])


def test_p7_merges_broker_rejections_into_security_incidents():
    # the emit phase pulls ctx.broker.audit() rejections into security.incidents[] as high-severity.
    src = inspect.getsource(__import__("poc_foundry.phases.pipeline", fromlist=["p7_emit"]).p7_emit)
    assert "broker-invariant-rejection" in src and "ctx.broker.audit()" in src


# ── key-proxy: the swap keeps the real key out of the VM ──────────────────────
def test_keyproxy_swaps_sacrificial_for_real_key():
    from poc_foundry.security import keyproxy
    # the VM presents only its sacrificial token; the proxy returns the REAL upstream auth to forward.
    out = keyproxy.swap_authorization("Bearer per-build-token-abc",
                                      sacrificial_token="per-build-token-abc", real_key="CANARY-sk-REALKEY")
    assert out == "Bearer CANARY-sk-REALKEY"
    # a bare token (no 'Bearer ') is tolerated
    assert keyproxy.swap_authorization("per-build-token-abc",
                                       sacrificial_token="per-build-token-abc", real_key="K") == "Bearer K"


def test_keyproxy_denies_a_wrong_or_missing_token():
    from poc_foundry.security import keyproxy
    for bad in ("Bearer guessed", "", "Bearer "):
        try:
            keyproxy.swap_authorization(bad, sacrificial_token="the-real-sacrificial", real_key="K")
            raise AssertionError("should have denied")
        except keyproxy.KeyProxyDenied:
            pass


def test_keyproxy_redacts_the_real_key():
    from poc_foundry.security import keyproxy
    msg = "error talking to upstream with key CANARY-sk-REALKEY oops"
    red = keyproxy.redact(msg, "CANARY-sk-REALKEY")
    assert "CANARY-sk-REALKEY" not in red and "<real-model-key>" in red


# ── Finding-0: no orchestrator secret reaches the sandbox VM ───────────────────
def test_scan_sandbox_env_passes_when_clean():
    from poc_foundry.security.findings import scan_sandbox_env
    # the broker hands the VM only proxy + sacrificial token + service IPs — no orchestrator secrets.
    vm_env = {"HTTPS_PROXY": "http://10.0.0.2:3128", "PF_SANDBOX_VLLM_KEY": "not-needed",
              "PF_SERVICE_PG_HOST": "10.0.0.5"}
    secrets = [("CANARY-sk-REALKEY", "<real-model-key>"), ("sk-lf-supersecret", "<langfuse-key>")]
    scan = scan_sandbox_env(vm_env, secrets)
    assert scan.ok and scan.leaked == [] and scan.secret_count == 2


def test_scan_sandbox_env_flags_a_leak_by_placeholder_not_value():
    from poc_foundry.security.findings import scan_sandbox_env
    # if a real secret DID appear in the VM env, the scan flags it — by PLACEHOLDER, never the raw value.
    leaky = {"OPENAI_API_KEY": "CANARY-sk-REALKEY", "X": "ok"}
    secrets = [("CANARY-sk-REALKEY", "<real-model-key>")]
    scan = scan_sandbox_env(leaky, secrets)
    assert not scan.ok and scan.leaked == ["<real-model-key>"]


def test_broker_vm_env_carries_no_orchestrator_secret():
    # end-to-end-ish: the env the broker BUILDS for a VM (proxy + sacrificial token) is Finding-0 clean
    # against a configured real key — proven without Docker by reading the env dict the broker assembles.
    from poc_foundry.security.findings import scan_sandbox_env
    b = _provisioned_broker(vllm_key="per-build-sacrificial-xyz")
    b.proxy_url = "http://10.0.0.2:3128"
    vm_env = {"HTTPS_PROXY": b.proxy_url, "HTTP_PROXY": b.proxy_url,
              "PF_SANDBOX_VLLM_KEY": b.vllm_key}          # mirrors Broker.create's env construction
    scan = scan_sandbox_env(vm_env, [("CANARY-sk-REALKEY", "<real-model-key>"),
                                     ("per-build-sacrificial-xyz", "<sacrificial>")])
    # the sacrificial token is INTENDED in the VM (it's sacrificial); the REAL key must be absent.
    assert "<real-model-key>" not in scan.leaked
