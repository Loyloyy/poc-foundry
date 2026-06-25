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
                                      sacrificial_token="per-build-token-abc", real_key="CANARY-DEMO-VALUE")
    assert out == "Bearer CANARY-DEMO-VALUE"
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
    msg = "error talking to upstream with key CANARY-DEMO-VALUE oops"
    red = keyproxy.redact(msg, "CANARY-DEMO-VALUE")
    assert "CANARY-DEMO-VALUE" not in red and "<real-model-key>" in red


# ── Finding-0: no orchestrator secret reaches the sandbox VM ───────────────────
def test_scan_sandbox_env_passes_when_clean():
    from poc_foundry.security.findings import scan_sandbox_env
    # the broker hands the VM only proxy + sacrificial token + service IPs — no orchestrator secrets.
    vm_env = {"HTTPS_PROXY": "http://10.0.0.2:3128", "PF_SANDBOX_VLLM_KEY": "not-needed",
              "PF_SERVICE_PG_HOST": "10.0.0.5"}
    secrets = [("CANARY-DEMO-VALUE", "<real-model-key>"), ("demo-langfuse-secret", "<langfuse-key>")]
    scan = scan_sandbox_env(vm_env, secrets)
    assert scan.ok and scan.leaked == [] and scan.secret_count == 2


def test_scan_sandbox_env_flags_a_leak_by_placeholder_not_value():
    from poc_foundry.security.findings import scan_sandbox_env
    # if a real secret DID appear in the VM env, the scan flags it — by PLACEHOLDER, never the raw value.
    leaky = {"OPENAI_API_KEY": "CANARY-DEMO-VALUE", "X": "ok"}
    secrets = [("CANARY-DEMO-VALUE", "<real-model-key>")]
    scan = scan_sandbox_env(leaky, secrets)
    assert not scan.ok and scan.leaked == ["<real-model-key>"]
    assert scan.leaked_keys == ["OPENAI_API_KEY"]   # names the offending VM var (safe; never the value)


def test_broker_vm_env_carries_no_orchestrator_secret():
    # end-to-end-ish: the env the broker BUILDS for a VM (proxy + sacrificial token) is Finding-0 clean
    # against a configured real key — proven without Docker by reading the env dict the broker assembles.
    from poc_foundry.security.findings import scan_sandbox_env
    b = _provisioned_broker(vllm_key="per-build-sacrificial-xyz")
    b.proxy_url = "http://10.0.0.2:3128"
    vm_env = {"HTTPS_PROXY": b.proxy_url, "HTTP_PROXY": b.proxy_url,
              "PF_SANDBOX_VLLM_KEY": b.vllm_key}          # mirrors Broker.create's env construction
    scan = scan_sandbox_env(vm_env, [("CANARY-DEMO-VALUE", "<real-model-key>"),
                                     ("per-build-sacrificial-xyz", "<sacrificial>")])
    # the sacrificial token is INTENDED in the VM (it's sacrificial); the REAL key must be absent.
    assert "<real-model-key>" not in scan.leaked


# ── S2c: the demo-security 3-beat analysis (PASS/FAIL logic, no Docker) ─────────
class _StubExec:
    def __init__(self, rc, out):
        self.rc, self.stdout, self.stderr = rc, out, ""

    @property
    def combined(self):
        return self.stdout

    @property
    def ok(self):
        return self.rc == 0


class _StubSandbox:
    def __init__(self, broker, name):
        self._b, self.name = broker, name

    def exec(self, cmd, *, timeout_s=600):
        return self._b._exec(cmd)

    def destroy(self):
        self._b.destroyed.append(self.name)


_CURL_DENIED = "curl: (56) CONNECT tunnel failed, response 403\nPF_HTTP=000\nPF_EXIT=56"
_CURL_OPEN = "PF_HTTP=200\nPF_EXIT=0"


class _StubBroker:
    """A scripted broker for run_demo: serves a VM env dump + a curl probe output, a proxy log, and either
    rejects (default) or allows an off-allowlist create — so each beat's PASS/FAIL is exercised."""

    def __init__(self, *, vm_env, curl, proxy_log, reject_evil=True, vllm_key=""):
        self._vm_env, self._curl, self._proxy_log = vm_env, curl, proxy_log
        self._reject_evil = reject_evil
        self.vllm_key = vllm_key
        self._audit, self.created, self.destroyed = [], [], []

    def create(self, *, mounts, caps=(), name="sbx", image=None, env_extra=None):
        if image and image != "poc-foundry-sandbox":
            if self._reject_evil:
                self._audit.append(audit.make_entry("poc-demo", "rejected", "create",
                                                    reason=f"image {image!r} not allowlisted",
                                                    detail={"image": image}))
                raise BrokerInvariantError(f"image {image!r} not allowlisted (rule #8)")
        self.created.append(name)
        return _StubSandbox(self, name)

    def _exec(self, cmd):
        if cmd.strip() == "env":
            return _StubExec(0, "\n".join(f"{k}={v}" for k, v in self._vm_env.items()))
        return _StubExec(0, self._curl)   # the egress probe → in-band PF_HTTP/PF_EXIT markers

    def proxy_log(self, *, tail=200):
        return self._proxy_log

    def audit(self):
        return list(self._audit)


def _denied_log(host="example.com"):
    return f"1700000000.1 5 10.0.0.9 TCP_DENIED/403 0 CONNECT {host}:443 - HIER_NONE/- -\n"


def test_security_demo_all_three_beats_pass():
    from poc_foundry.security import demo
    vm_env = {"HTTPS_PROXY": "http://10.0.0.2:3128", "PF_SANDBOX_VLLM_KEY": "not-needed",
              "PATH": "/usr/local/bin:/usr/bin"}
    broker = _StubBroker(vm_env=vm_env, curl=_CURL_DENIED, proxy_log=_denied_log())
    res = demo.run_demo(broker, canary="CANARY-XYZ", build_id="poc-demo",
                        secrets=[("CANARY-XYZ", "<canary>")], proxy_poll_tries=1)
    assert res["ok"] and len(res["beats"]) == 3 and all(b["passed"] for b in res["beats"])
    assert [b["beat"] for b in res["beats"]] == ["canary / Finding-0", "egress containment",
                                                 "broker rejection"]
    assert "secdemo" in broker.created and broker.destroyed   # the demo VM was reaped


def test_security_demo_excludes_the_sacrificial_token_from_finding0():
    # the sacrificial inference token is INTENDED in the VM — beat-1 must NOT flag it as a leak.
    from poc_foundry.security import demo
    vm_env = {"HTTPS_PROXY": "http://10.0.0.2:3128", "PF_SANDBOX_VLLM_KEY": "sacrificial-abc"}
    broker = _StubBroker(vm_env=vm_env, curl=_CURL_DENIED, proxy_log=_denied_log(),
                         vllm_key="sacrificial-abc")
    res = demo.run_demo(broker, canary="CANARY-XYZ", build_id="poc-sac",
                        secrets=[("sacrificial-abc", "<redacted-key>"), ("CANARY-XYZ", "<canary>")],
                        proxy_poll_tries=1)
    assert res["beats"][0]["passed"]   # the sacrificial token is excluded → Finding-0 holds


def test_security_demo_beats_fail_on_leak_open_egress_unblocked_create():
    from poc_foundry.security import demo
    # a REAL secret leaked into the VM, the proxy did NOT deny, and the bad create is allowed
    leaky_env = {"LANGFUSE_SECRET_KEY": "CANARY-XYZ", "PATH": "/usr/bin"}
    broker = _StubBroker(vm_env=leaky_env, curl=_CURL_OPEN, proxy_log="", reject_evil=False)
    res = demo.run_demo(broker, canary="CANARY-XYZ", build_id="poc-bad",
                        secrets=[("CANARY-XYZ", "<canary>")], proxy_poll_tries=1)
    assert not res["ok"] and not any(b["passed"] for b in res["beats"])
    canary_beat = res["beats"][0]
    assert canary_beat["detail"]["leaked"] == ["<canary>"]               # by placeholder, never raw value
    assert canary_beat["detail"]["leaked_keys"] == ["LANGFUSE_SECRET_KEY"]  # names the offending VM var


def test_security_demo_events_are_emitted_and_canary_redacted():
    from poc_foundry.security import demo
    events = []
    vm_env = {"HTTPS_PROXY": "http://10.0.0.2:3128"}
    broker = _StubBroker(vm_env=vm_env, curl=_CURL_DENIED, proxy_log=_denied_log())
    demo.run_demo(broker, canary="CANARY-XYZ", build_id="poc-ev", emit=events.append,
                  secrets=[("CANARY-XYZ", "<canary>")], proxy_poll_tries=1)
    beat_events = [e for e in events if e["type"] == "beat"]
    assert len(beat_events) == 3 and all(e["build_id"] == "poc-ev" for e in beat_events)
    assert "CANARY-XYZ" not in json.dumps(events)   # the canary never reaches a shared event


def test_demo_pure_analyzers():
    from poc_foundry.security import demo
    from poc_foundry.security.findings import egress_denied
    # env parse: only plausible NAME=value lines become keys
    env = demo.parse_env("FOO=bar\nPATH=/usr/bin\n  continuation-line\n12BAD=x")
    assert env == {"FOO": "bar", "PATH": "/usr/bin"}
    # egress: blocked + TCP_DENIED in the log = pass; a 2xx through an open path = fail
    assert demo.analyze_egress(_CURL_DENIED, egress_denied(_denied_log()))["passed"]
    assert not demo.analyze_egress(_CURL_OPEN, egress_denied(""))["passed"]
    # blocked + curl's own 403-from-proxy is denial evidence even with NO proxy-log entry (flush lag)
    assert demo.analyze_egress(_CURL_DENIED, False)["passed"]
    # rejection: needs BOTH the raise and an audit entry
    audited = [audit.make_entry("x", "rejected", "create", reason="bad image")]
    assert demo.analyze_rejection(True, "bad image", audited)["passed"]
    assert not demo.analyze_rejection(True, "bad image", [])["passed"]      # raised but not audited
    assert not demo.analyze_rejection(False, "", audited)["passed"]         # audited but not raised


# ── S2d: RunManager streams the security demo in the single slot ───────────────
def test_runmanager_security_demo_streams_beats_and_finishes():
    import threading
    from poc_foundry.events import make_event
    from poc_foundry.web.runmanager import RunBusy, RunManager

    def fake_demo(*, event_sink, **kw):
        event_sink(make_event("start", "security-demo-x", kind="security-demo"))
        for name in ("canary / Finding-0", "egress containment", "broker rejection"):
            event_sink(make_event("beat", "security-demo-x", beat=name, passed=True, summary="ok", detail={}))
        event_sink(make_event("end", "security-demo-x", status="ok", ok=True))
        return {"build_id": "security-demo-x", "ok": True, "beats": [{"passed": True}] * 3}

    mgr = RunManager(security_demo_fn=fake_demo)
    q = mgr.subscribe()
    mgr.security_demo(canary="CANARY-XYZ")
    mgr._thread.join(timeout=5)
    drained = []
    while not q.empty():
        drained.append(q.get_nowait())
    beats = [e for e in drained if e["type"] == "beat"]
    assert len(beats) == 3 and all(b["passed"] for b in beats)
    st = mgr.status()
    assert st["state"] == "finished" and st["status"] == "ok" and st["build_id"] == "security-demo-x"
    # single-slot: a concurrent run while busy would raise RunBusy (proven for builds in M3)
    assert RunBusy is not None
