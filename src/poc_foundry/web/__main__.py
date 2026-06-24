"""``python -m poc_foundry.web`` — launch the M3 web service (image-only; needs the ``ui`` extra).

The localhost-only SECURITY boundary is the **host-side publish** — compose maps
``127.0.0.1:8181:8181`` (rule #1 / §5.12), so the service is reachable ONLY from the server's
loopback, then over an SSH tunnel. Reach a process holding the secrets; never publish on 0.0.0.0.

Inside the container uvicorn must bind ``0.0.0.0``: Docker forwards a published port to the
container's eth0, NOT its loopback, so a 127.0.0.1 listen is unreachable through the port map (empty
replies / connection reset). This matches the depot's own services (langfuse/searxng listen 0.0.0.0
in-container, publish on 127.0.0.1). Override host/port with ``PF_WEB_HOST`` / ``PF_WEB_PORT`` for a
non-Docker local run. Single worker: the ``RunManager`` is a single-slot in-process singleton; extra
workers would each own a separate slot and split the SSE fan-out.
"""
from __future__ import annotations

import os


def main() -> None:
    import uvicorn

    # 0.0.0.0 = the IN-CONTAINER listen (see module docstring); the boundary is the host publish
    # `127.0.0.1:8181` in compose, NOT this bind. PF_WEB_HOST overrides for a bare local run.
    host = os.environ.get("PF_WEB_HOST", "0.0.0.0")     # noqa: S104 — host-side publish is loopback-only
    port = int(os.environ.get("PF_WEB_PORT", "8181"))   # 8770/8008 are vLLM on the shared box — keep clear
    uvicorn.run("poc_foundry.web.server:app", host=host, port=port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
