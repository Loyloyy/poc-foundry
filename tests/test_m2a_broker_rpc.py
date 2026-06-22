"""M2a S4 — out-of-process broker fakes. Exercises the REAL rpc transport + daemon dispatch + client
over a real Unix socket, with a FAKE docker engine injected via ``broker_factory`` (no docker needed).
Proves: the full create/exec/service/destroy round-trip forwards correctly, and a create-param
invariant violation (rule #8) raised DAEMON-side re-raises as the SAME type in the orchestrator.

Runs in-container under pytest OR on the 3.10 box via ``scripts/run_spine_tests.py``.
"""
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from poc_foundry.config import load_config
from poc_foundry.sandbox.broker import BrokerInvariantError, ExecResult, Mount
from poc_foundry.sandbox.client import RemoteBroker
from poc_foundry.sandbox.daemon import BrokerDaemon


# ── a fake docker engine (the daemon's broker_factory) ───────────────────────
class _FakeSbx:
    def __init__(self, name):
        self.name, self.alive = name, True

    def exec(self, cmd, *, timeout_s=600):
        return ExecResult(0, f"ran:{cmd}", "")

    def destroy(self):
        self.alive = False


class _FakeEngine:
    """Mirrors the Broker interface the daemon drives — including the invariant guard on create()."""

    def __init__(self, build_id, cfg, *, allowed_images, runtime=None, vllm_key="not-needed"):
        self.build_id = build_id
        self.allowed_images = set(allowed_images)
        self.runtime, self.vllm_key = runtime, vllm_key
        self.proxy_url = None
        self._sandboxes = {}
        self.destroyed = False

    def provision(self):
        self.proxy_url = "http://10.0.0.2:3128"

    def create(self, *, mounts, caps=(), name="sbx", image=None, env_extra=None):
        img = image or "poc-foundry-sandbox"
        if img not in self.allowed_images:                       # the rule-#8 guard, daemon-side
            raise BrokerInvariantError(f"image {img!r} is not allowlisted")
        full = f"pf-{name}-{len(self._sandboxes)}"
        sbx = _FakeSbx(full)
        self._sandboxes[full] = sbx
        return sbx

    def create_service(self, *, image, name, env=None, pinned_tag=None):
        full = f"svc-{name}"
        sbx = _FakeSbx(full)
        self._sandboxes[full] = sbx
        return sbx

    def service_ip(self, sbx):
        return "172.30.0.9"

    def proxy_log(self, *, tail=200):
        return "action=TCP_TUNNEL/200 CONNECT pypi.org:443\n"

    def destroy(self):
        self.destroyed = True


def _start_daemon(tmp_path, cfg):
    sock = str(tmp_path / "broker.sock")
    daemon = BrokerDaemon(cfg, broker_factory=_FakeEngine)
    t = threading.Thread(target=daemon.serve, args=(sock,), daemon=True)
    t.start()
    for _ in range(100):                                          # wait for bind
        if os.path.exists(sock):
            break
        time.sleep(0.01)
    return sock, daemon


def test_broker_rpc_full_round_trip(tmp_path):
    cfg = load_config(tmp_path / "builds")
    sock, daemon = _start_daemon(tmp_path, cfg)

    rb = RemoteBroker("poc-rpc-1", cfg, allowed_images={"poc-foundry-sandbox", "poc-foundry-proxy"},
                      socket_path=sock)
    rb.provision()
    assert rb.proxy_url == "http://10.0.0.2:3128"

    sbx = rb.create(mounts=[Mount("/var/tmp/ws", "/work")], name="iter0")
    assert sbx.name.startswith("pf-iter0")
    r = sbx.exec("echo hi")
    assert r.ok and r.stdout == "ran:echo hi"
    assert rb.service_ip(sbx) == "172.30.0.9"
    assert "CONNECT pypi.org:443" in rb.proxy_log()

    svc = rb.create_service(image="pgvector/pgvector", name="pg", pinned_tag="pg16")
    assert svc.name == "svc-pg"

    sbx.destroy()
    rb.destroy()
    assert daemon._brokers == {}                                  # destroy removed the build's broker


def test_broker_rpc_invariant_raises_same_type_client_side(tmp_path):
    """A create with a non-allowlisted image is rejected DAEMON-side and surfaces as the SAME
    BrokerInvariantError in the orchestrator (the guard is not bypassable from the client)."""
    cfg = load_config(tmp_path / "builds")
    sock, _ = _start_daemon(tmp_path, cfg)

    rb = RemoteBroker("poc-rpc-2", cfg, allowed_images={"poc-foundry-sandbox"}, socket_path=sock)
    rb.provision()
    with pytest.raises(BrokerInvariantError):
        rb.create(mounts=[Mount("/ok", "/work")], name="x", image="evil/image:latest")


def test_remote_broker_matches_broker_interface():
    """The thin client must expose the same surface the phases call on ``ctx.broker``."""
    from poc_foundry.sandbox import Broker
    for attr in ("provision", "create", "create_service", "service_ip", "proxy_log", "destroy"):
        assert hasattr(RemoteBroker, attr) and hasattr(Broker, attr)
