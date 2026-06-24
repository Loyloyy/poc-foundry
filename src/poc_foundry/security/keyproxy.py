"""The vLLM/model **key-proxy** (M4 S2, design §5.2) — a small reverse proxy that keeps the REAL model
key out of the sandbox VM.

The threat it addresses is general, not specific to this on-prem box: a platform that runs key-requiring
providers (OpenAI / Claude / any hosted model) must NEVER place that high-value key in the throwaway VM
that executes untrusted, AI-written code. So:

  sandbox VM  ──(presents a SACRIFICIAL, per-build, rotatable token)──▶  key-proxy
                                                       key-proxy holds the REAL key (orchestrator-side)
                                                       ──(injects Authorization: Bearer <real>)──▶  model

The real key lives ONLY in the proxy (orchestrator-controlled). The VM's env carries only the sacrificial
token — buys inference, nothing else (Finding-0). On this keyless on-prem vLLM the control is demonstrated
with a **canary**: a planted stand-in secret that the demo proves the VM never sees (the claim is written
against enforced reality — §5.2 — we never assert the on-prem vLLM has a real key).

This module is import-light (stdlib only) so it ``py_compile``s on the 3.10 box and the **pure swap logic**
is exercised by the fakes; ``serve()`` (the actual reverse proxy) is image-only / server-validated.
"""
from __future__ import annotations

import os


class KeyProxyDenied(Exception):
    """The sandbox presented a token that is not the expected sacrificial one → no inference, logged."""


def bearer_value(authorization: str) -> str:
    """The token out of an ``Authorization`` header (tolerates a missing ``Bearer `` prefix)."""
    h = (authorization or "").strip()
    return h[7:].strip() if h[:7].lower() == "bearer " else h


def swap_authorization(presented_authorization: str, *, sacrificial_token: str, real_key: str) -> str:
    """The key-proxy's core: validate the sandbox's SACRIFICIAL token, then return the REAL upstream
    ``Authorization`` value to forward. The real key never leaves the proxy toward the VM; a token
    mismatch is DENIED (the VM cannot borrow the proxy's identity by guessing). Pure + unit-tested."""
    presented = bearer_value(presented_authorization)
    if not sacrificial_token or presented != sacrificial_token:
        raise KeyProxyDenied("sandbox token does not match the per-build sacrificial inference token")
    return f"Bearer {real_key}"


def redact(text: str, real_key: str, *, placeholder: str = "<real-model-key>") -> str:
    """Never let the real key/canary reach shared output (logs, demo transcript). Defense-in-depth on
    top of ``scrub`` — the proxy redacts its own secret before anything it emits."""
    return text.replace(real_key, placeholder) if real_key else text


# ── config (orchestrator-side env; NEVER injected into the VM) ─────────────────
def upstream_url() -> str:
    """The real model endpoint the proxy forwards to (e.g. the vLLM ``/v1`` base)."""
    return os.environ.get("PF_KEYPROXY_UPSTREAM", "").strip()


def real_key() -> str:
    """The REAL upstream key (or the demo canary). Held ONLY by the proxy process — never in the VM."""
    return os.environ.get("PF_KEYPROXY_REAL_KEY", "").strip()


def sacrificial_token() -> str:
    """The per-build token the VM presents + the proxy validates (rotatable; injected into the VM as
    ``PF_SANDBOX_VLLM_KEY``). Defaults to ``not-needed`` so an unconfigured proxy still forwards."""
    return os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed").strip() or "not-needed"


def serve(host: str = "0.0.0.0", port: int = 8788) -> None:  # pragma: no cover — image-only reverse proxy
    """Run the reverse proxy (stdlib ``http.server`` + ``urllib``). The sandbox's model ``base_url``
    points here; every request has its ``Authorization`` swapped (sacrificial → real) and is forwarded
    to ``PF_KEYPROXY_UPSTREAM``. Records swap COUNTS only (never the key value). Server-validated, not
    run in the fakes (the swap logic above is what the fakes cover)."""
    import json
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    up, rk, sac = upstream_url(), real_key(), sacrificial_token()
    if not up:
        raise SystemExit("PF_KEYPROXY_UPSTREAM is not set (the real model endpoint to forward to)")
    stats = {"forwarded": 0, "denied": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the proxy quiet; never log the body/key
            return

        def _proxy(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0) or 0))
            try:
                auth = swap_authorization(self.headers.get("Authorization", ""),
                                          sacrificial_token=sac, real_key=rk)
            except KeyProxyDenied as e:
                stats["denied"] += 1
                self.send_response(401)
                self.end_headers()
                self.wfile.write(redact(str(e), rk).encode())
                return
            target = up.rstrip("/") + self.path
            req = urllib.request.Request(target, data=body or None, method=self.command)
            for h in ("Content-Type", "Accept"):
                if self.headers.get(h):
                    req.add_header(h, self.headers[h])
            req.add_header("Authorization", auth)   # the REAL key — added here, never seen by the VM
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = r.read()
                    code = r.status
            except urllib.error.HTTPError as e:
                data, code = e.read(), e.code
            except Exception as e:  # noqa: BLE001
                data, code = redact(str(e), rk).encode(), 502
            stats["forwarded"] += 1
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == "/__keyproxy_stats":   # the demo reads swap counts (no secret) as evidence
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(stats).encode())
                return
            self._proxy()

        def do_POST(self):
            self._proxy()

    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":  # pragma: no cover
    serve()
