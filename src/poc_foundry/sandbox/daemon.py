"""Out-of-process broker daemon (design §5.2/§5.5, M2a S4). The ONLY process that holds the host
docker socket; the orchestrator talks to it over a Unix socket (``rpc``) and never sees docker.sock —
closing the M1 residual. The create-param invariant (rule #8) is enforced HERE, broker-side, by the
real :class:`Broker`, so a compromised orchestrator cannot bypass the allowlists.

State: one :class:`Broker` per ``build_id`` (Ops runs one build at a time, but keying by id keeps it
correct). Each RPC method maps to a Broker/Sandbox call. Run it as::

    python -m poc_foundry.sandbox.daemon          # listens on $PF_BROKER_SOCKET

The ``broker_factory`` seam lets tests inject a fake engine (no docker) while exercising the real RPC.
"""
from __future__ import annotations

import os

from poc_foundry.sandbox import rpc
from poc_foundry.sandbox.broker import Broker, BrokerError, Mount


class BrokerDaemon:
    def __init__(self, cfg, *, broker_factory=Broker):
        self.cfg = cfg
        self._factory = broker_factory
        self._brokers: dict[str, object] = {}

    # ── dispatch ──────────────────────────────────────────────────────────────
    def handle(self, method: str, params: dict):
        bid = params.get("build_id")
        if method == "provision":
            broker = self._factory(bid, self.cfg, allowed_images=set(params["allowed_images"]),
                                   runtime=params.get("runtime"),
                                   vllm_key=params.get("vllm_key", "not-needed"))
            self._brokers[bid] = broker
            broker.provision()
            return {"proxy_url": broker.proxy_url}

        broker = self._brokers.get(bid)
        if broker is None:
            raise BrokerError(f"no provisioned broker for build {bid!r}")

        if method == "create":
            mounts = [Mount(**m) for m in params.get("mounts", [])]
            sbx = broker.create(mounts=mounts, caps=tuple(params.get("caps", ())),
                                name=params.get("name", "sbx"), image=params.get("image"),
                                env_extra=params.get("env_extra"))
            return {"name": sbx.name}
        if method == "create_service":
            sbx = broker.create_service(image=params["image"], name=params["name"],
                                        env=params.get("env"), pinned_tag=params.get("pinned_tag"),
                                        ready_cmd=params.get("ready_cmd"))
            return {"name": sbx.name}
        if method == "exec":
            sbx = self._sandbox(broker, params["sandbox"])
            r = sbx.exec(params["cmd"], timeout_s=params.get("timeout_s", 600))
            return {"rc": r.rc, "stdout": r.stdout, "stderr": r.stderr}
        if method == "destroy_sandbox":
            sbx = self._sandbox(broker, params["sandbox"], required=False)
            if sbx is not None:
                sbx.destroy()
            return {}
        if method == "service_ip":
            sbx = self._sandbox(broker, params["sandbox"])
            return {"ip": broker.service_ip(sbx)}
        if method == "proxy_log":
            return {"log": broker.proxy_log(tail=params.get("tail", 200))}
        if method == "destroy":
            broker.destroy()
            self._brokers.pop(bid, None)
            return {}
        raise BrokerError(f"unknown broker method {method!r}")

    @staticmethod
    def _sandbox(broker, name: str, *, required: bool = True):
        sbx = getattr(broker, "_sandboxes", {}).get(name)
        if sbx is None and required:
            raise BrokerError(f"no sandbox {name!r} on this broker")
        return sbx

    def serve(self, socket_path: str) -> None:
        rpc.serve(socket_path, self.handle)


def main() -> int:
    from poc_foundry import tracing
    from poc_foundry.config import load_config

    # The daemon runs the instrumented in-process Broker but has NO build-root context — its spans would
    # scatter as standalone top-level traces (and duplicate the orchestrator's, which trace broker ops
    # WITH the build context via client.py). So the daemon never traces.
    tracing.disable()

    socket_path = os.environ.get("PF_BROKER_SOCKET", "").strip()
    if not socket_path:
        raise SystemExit("PF_BROKER_SOCKET is not set (the Unix socket path to listen on)")
    cfg = load_config()
    daemon = BrokerDaemon(cfg)
    print(f"[pf-broker] listening on {socket_path}", flush=True)
    daemon.serve(socket_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
