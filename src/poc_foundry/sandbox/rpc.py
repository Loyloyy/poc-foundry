"""Tiny newline-delimited-JSON RPC over a Unix socket — the wire between the orchestrator's thin
broker client (``client.RemoteBroker``) and the out-of-process broker daemon (``daemon.BrokerDaemon``).

M2a S4 moves the broker out-of-process so the ORCHESTRATOR no longer holds the host docker socket
(the M1 residual): the daemon is the only process with ``/var/run/docker.sock``, and it alone enforces
the create-param invariant (rule #8). This module is the transport only — stdlib sockets + json, no
docker, no heavy deps (imports on the 3.10 box; unit-testable with a real socket + a fake engine).

Framing: one request = one JSON object + ``\\n``; one response = one JSON object + ``\\n``. A request is
``{"method": str, "params": {...}}``; a response is ``{"ok": true, "result": {...}}`` or
``{"ok": false, "error": str, "error_type": str}``. Connection-per-call (the pipeline is sequential),
so there is no multiplexing to get wrong.
"""
from __future__ import annotations

import json
import socket
import time

from poc_foundry.sandbox.broker import BrokerError, BrokerInvariantError

# error_type string → exception class, so the client re-raises the SAME type the daemon raised
# (notably ``BrokerInvariantError`` for a rule-#8 violation must surface as itself, not a generic error).
_ERRORS = {
    "BrokerInvariantError": BrokerInvariantError,
    "BrokerError": BrokerError,
}


def _send(sock: socket.socket, obj: dict) -> None:
    sock.sendall((json.dumps(obj) + "\n").encode())


def _recv_line(sock: socket.socket) -> str:
    """Read one ``\\n``-terminated line from a stream socket (small messages; no length framing)."""
    buf = bytearray()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        buf.extend(chunk)
        if buf.endswith(b"\n"):
            break
    return buf.decode()


def call(socket_path: str, method: str, params: dict, *, timeout: float = 2100.0,
         connect_retry_s: float = 15.0) -> dict:
    """Open a connection, send one request, read one response, close. Raises the daemon's error type
    on failure. ``connect_retry_s`` rides out the daemon-startup race (depends_on waits for 'started',
    not 'listening'); ``timeout`` must exceed the longest sandbox ``exec`` (uv install ~1800s)."""
    deadline = time.time() + connect_retry_s
    while True:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(socket_path)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            sock.close()
            if time.time() >= deadline:
                raise BrokerError(f"broker daemon not reachable at {socket_path!r}")
            time.sleep(0.25)
    try:
        sock.settimeout(timeout)
        _send(sock, {"method": method, "params": params})
        resp = json.loads(_recv_line(sock) or "{}")
    finally:
        sock.close()
    if not resp.get("ok"):
        exc = _ERRORS.get(resp.get("error_type", "BrokerError"), BrokerError)
        raise exc(resp.get("error", "broker rpc failed"))
    return resp.get("result", {})


def serve(socket_path: str, handler) -> None:
    """Single-threaded accept loop: each connection carries ONE request; ``handler(method, params)``
    returns a result dict or raises. Single-threaded on purpose — it serializes every docker op + the
    invariant checks (no races), which suits the one-build-at-a-time Ops model."""
    import os

    if os.path.exists(socket_path):
        os.unlink(socket_path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(socket_path)
    os.chmod(socket_path, 0o660)
    srv.listen(8)
    try:
        while True:
            conn, _ = srv.accept()
            try:
                req = json.loads(_recv_line(conn) or "{}")
                try:
                    result = handler(req.get("method", ""), req.get("params", {}) or {})
                    _send(conn, {"ok": True, "result": result})
                except BrokerError as e:
                    _send(conn, {"ok": False, "error": str(e), "error_type": type(e).__name__})
                except Exception as e:  # noqa: BLE001 — never let one bad request kill the daemon
                    _send(conn, {"ok": False, "error": f"{type(e).__name__}: {e}",
                                 "error_type": "BrokerError"})
            finally:
                conn.close()
    finally:
        srv.close()
        if os.path.exists(socket_path):
            os.unlink(socket_path)
