"""``python -m poc_foundry.web`` — launch the M3 web service (image-only; needs the ``ui`` extra).

Binds **localhost only** by design (the orchestrator holds the secrets; reach it over an SSH tunnel —
the tunnel is the boundary). Override host/port with ``PF_WEB_HOST`` / ``PF_WEB_PORT`` — but DO NOT
bind a public interface (rule #1 / §5.12). Single worker: the ``RunManager`` is a single-slot,
in-process singleton; multiple workers would each own a separate slot and split the SSE fan-out.
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    host = os.environ.get("PF_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("PF_WEB_PORT", "8181"))   # 8770/8008 are vLLM on the shared box — keep clear
    if host not in ("127.0.0.1", "localhost", "::1"):
        # Loud, non-fatal: §5.12 says localhost + SSH tunnel only. Respect the override but warn.
        print(f"WARNING: PF_WEB_HOST={host!r} is not loopback — the web service holds the secrets; "
              f"bind localhost + use an SSH tunnel (rule #1).")
    uvicorn.run("poc_foundry.web.server:app", host=host, port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
