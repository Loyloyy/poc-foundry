"""Thin orchestrator-side broker client (M2a S4). ``RemoteBroker``/``RemoteSandbox`` implement the
SAME interface as the in-process ``Broker``/``Sandbox`` but forward every call over the Unix socket to
the out-of-process ``BrokerDaemon`` — so the phases (``ctx.broker``) are agnostic to which one they
hold, and the orchestrator never touches docker.sock.

The invariant (rule #8) is NOT re-checked here — it is authoritative DAEMON-side (a client check would
be bypassable). A violation raises ``BrokerInvariantError`` daemon-side and the rpc layer re-raises the
same type here, so callers see identical behaviour to the in-process broker.
"""
from __future__ import annotations

from poc_foundry.sandbox import rpc
from poc_foundry.sandbox.broker import ExecResult


class RemoteSandbox:
    def __init__(self, broker: "RemoteBroker", name: str):
        self._broker = broker
        self.name = name
        self._alive = True

    def exec(self, cmd: str, *, timeout_s: int = 600) -> ExecResult:
        from poc_foundry import tracing   # manual span around the (out-of-process) VERIFY/exec round-trip
        with tracing.span("broker.exec", sandbox=self.name, cmd=cmd[:300]) as _sp:
            r = self._broker._call("exec", sandbox=self.name, cmd=cmd, timeout_s=timeout_s,
                                   _timeout=timeout_s + 120)
            _sp.update(output={"rc": r["rc"], "tail": (r["stdout"] + r["stderr"])[-500:]})
            return ExecResult(rc=r["rc"], stdout=r["stdout"], stderr=r["stderr"])

    def destroy(self) -> None:
        if self._alive:
            self._broker._call("destroy_sandbox", sandbox=self.name)
            self._alive = False


class RemoteBroker:
    """Drop-in for ``Broker`` that forwards to the daemon. Constructed by ``core._prepare`` when
    ``PF_BROKER_SOCKET`` is set (else the in-process ``Broker`` is used — the default)."""

    def __init__(self, build_id: str, cfg, *, allowed_images: set[str], runtime: str | None = None,
                 vllm_key: str = "not-needed", socket_path: str):
        self.build_id = build_id
        self.cfg = cfg
        self.socket_path = socket_path
        self._allowed_images = set(allowed_images)
        self._runtime = runtime
        self._vllm_key = vllm_key
        self.proxy_url: str | None = None
        self.events: list[str] = []

    # ── transport ─────────────────────────────────────────────────────────────
    def _call(self, method: str, *, _timeout: float = 2100.0, **params):
        params.setdefault("build_id", self.build_id)
        return rpc.call(self.socket_path, method, params, timeout=_timeout)

    # ── context manager (mirror Broker) ────────────────────────────────────────
    def __enter__(self) -> "RemoteBroker":
        return self

    def __exit__(self, *exc) -> None:
        self.destroy()

    # ── lifecycle ───────────────────────────────────────────────────────────────
    def provision(self) -> None:
        from poc_foundry import tracing
        with tracing.span("broker.provision", build_id=self.build_id):
            r = self._call("provision", allowed_images=sorted(self._allowed_images),
                           runtime=self._runtime, vllm_key=self._vllm_key)
            self.proxy_url = r.get("proxy_url")

    def create(self, *, mounts, caps=(), name: str = "sbx", image: str | None = None,
               env_extra: dict | None = None) -> RemoteSandbox:
        from poc_foundry import tracing
        with tracing.span("broker.create", box=name, image=image):
            r = self._call("create", name=name, image=image, caps=list(caps), env_extra=env_extra,
                           mounts=[{"source": m.source, "target": m.target, "read_only": m.read_only}
                                   for m in mounts])
            return RemoteSandbox(self, r["name"])

    def create_service(self, *, image: str, name: str, env: dict | None = None,
                       pinned_tag: str | None = None, ready_cmd: str | None = None) -> RemoteSandbox:
        from poc_foundry import tracing
        with tracing.span("broker.create_service", svc=name, image=image):
            r = self._call("create_service", image=image, name=name, env=env, pinned_tag=pinned_tag,
                           ready_cmd=ready_cmd, _timeout=180)
            return RemoteSandbox(self, r["name"])

    def service_ip(self, sandbox: RemoteSandbox) -> str:
        return self._call("service_ip", sandbox=sandbox.name).get("ip", "")

    def proxy_log(self, *, tail: int = 200) -> str:
        try:
            return self._call("proxy_log", tail=tail).get("log", "")
        except Exception:  # noqa: BLE001 — log read is best-effort
            return ""

    def audit(self) -> list:
        """The daemon-side rejected-create*/lifecycle records for this build (M4 S2). Best-effort:
        the durable copy is the daemon's append-only file (read independently for the security demo)."""
        try:
            return self._call("audit").get("entries", [])
        except Exception:  # noqa: BLE001 — audit read is best-effort at emit
            return []

    def destroy(self) -> None:
        from poc_foundry import tracing
        with tracing.span("broker.destroy", build_id=self.build_id):
            try:
                self._call("destroy")
            except Exception:  # noqa: BLE001 — teardown must not raise out of a finally
                pass
