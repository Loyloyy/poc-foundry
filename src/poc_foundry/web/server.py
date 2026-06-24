"""FastAPI presentation over the headless contract (M3 §5.12, rule #5: NO pipeline logic here).

Serves the committed React ``dist/`` + a small JSON API + an **SSE** stream of structured build
events. The orchestrator process holds the secrets → it is published on the server's **loopback
only** (compose ``127.0.0.1:8181:8181``) and reached over an **SSH tunnel** (the tunnel is the
boundary; there is no in-app auth, and we don't claim one — never publish on a public interface).
Uvicorn itself listens 0.0.0.0 IN-CONTAINER (Docker forwards the published port to eth0, not
loopback); the host-side publish is what restricts it to localhost. See ``__main__``.

This module imports the ``ui`` extra (fastapi/uvicorn) at top level, so it is image-only: it is NEVER
imported by the no-pytest fakes (which exercise ``runmanager``/``events`` directly on the 3.10 box).
It stays ``py_compile``-able regardless (compile doesn't execute imports).

Run it:  ``python -m poc_foundry.web``  (see ``__main__.py``; uvicorn on 127.0.0.1:8181).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from queue import Empty, Queue

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from poc_foundry.events import sse_format
from poc_foundry.web.runmanager import RunBusy, RunManager

DIST = Path(__file__).resolve().parent / "dist"
_MAX_FILE_BYTES = 512 * 1024   # cap an inline file read (logs can be huge; the SPA shows the tail)

# Only these emitted build files are exposed to the SPA (read-only, already scrubbed at emit). An
# allow-list of suffixes/relative paths keeps the file endpoint from serving anything sensitive.
_ALLOWED_SUFFIXES = (".md", ".json", ".log", ".patch", ".txt")

manager = RunManager()
app = FastAPI(title="poc-foundry", docs_url=None, redoc_url=None)


# ── request models ────────────────────────────────────────────────────────────
class StartReq(BaseModel):
    source: str
    brief: str = ""
    driver: str = "tech-scout"
    template: str | None = None
    runtime: str | None = None


# ── helpers ───────────────────────────────────────────────────────────────────
def _builds_root() -> Path:
    from poc_foundry.config import load_config
    return load_config().builds_dir


def _safe_build_file(build_id: str, rel: str) -> Path:
    """Resolve ``rel`` inside ``builds/<id>/`` with traversal + suffix guards. 404 on anything else."""
    if "/" in build_id or build_id in ("", ".", ".."):
        raise HTTPException(404, "no such build")
    root = (_builds_root() / build_id).resolve()
    target = (root / rel).resolve()
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise HTTPException(404, "path escapes the build folder")
    if target.suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(404, "file type not exposed")
    if not target.is_file():
        raise HTTPException(404, "no such file")
    return target


def _langfuse_public_url() -> str:
    """The BROWSER-facing Langfuse URL for the 'Traces' link. ``LANGFUSE_HOST`` is the IN-NETWORK target
    the orchestrator pushes to (e.g. ``langfuse-web:3000``) — a laptop browser can't resolve it. Prefer
    an explicit ``PF_LANGFUSE_PUBLIC_URL``; otherwise rewrite the host to ``localhost`` (preserving
    scheme+port) so the link works through the standard SSH tunnel out of the box."""
    explicit = os.environ.get("PF_LANGFUSE_PUBLIC_URL", "").strip()
    if explicit:
        return explicit
    host = os.environ.get("LANGFUSE_HOST", "").strip()
    if not host:
        return ""
    from urllib.parse import urlparse
    u = urlparse(host if "://" in host else f"http://{host}")
    if u.hostname in ("localhost", "127.0.0.1"):
        return host if "://" in host else f"http://{host}"
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme or 'http'}://localhost{port}"


def _build_detail(build_id: str) -> dict:
    from poc_foundry.artifact import load as load_artifact

    root = _builds_root() / build_id
    if not root.is_dir():
        raise HTTPException(404, "no such build")
    detail: dict = {"id": build_id, "files": [], "langfuse_host": _langfuse_public_url()}
    try:
        detail["artifact"] = load_artifact(root).model_dump()
    except Exception as e:  # noqa: BLE001 — half-written / failed build: still inspectable
        detail["artifact"] = None
        detail["artifact_error"] = f"{type(e).__name__}: {e}"
    for f in sorted(root.rglob("*")):
        if f.is_file() and f.suffix in _ALLOWED_SUFFIXES:
            detail["files"].append(str(f.relative_to(root)))
    return detail


# ── API ───────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
def status() -> dict:
    return manager.status()


@app.get("/api/builds")
def builds() -> list[dict]:
    from poc_foundry.core import list_builds
    return list_builds()


@app.get("/api/sources")
def sources() -> list[dict]:
    """Buildable Stage-2 sources (topic/brief/path) for the build-form picker."""
    from poc_foundry.core import list_sources
    return list_sources()


@app.get("/api/builds/{build_id}")
def build_detail(build_id: str) -> dict:
    return _build_detail(build_id)


@app.get("/api/builds/{build_id}/file")
def build_file(build_id: str, path: str):
    target = _safe_build_file(build_id, path)
    data = target.read_bytes()
    truncated = len(data) > _MAX_FILE_BYTES
    text = data[-_MAX_FILE_BYTES:].decode("utf-8", "replace") if truncated else data.decode("utf-8", "replace")
    return JSONResponse({"path": path, "truncated": truncated, "text": text})


@app.post("/api/builds")
def start_build(req: StartReq):
    try:
        cur = manager.start(req.source, brief=req.brief, driver=req.driver,
                            template=req.template, runtime=req.runtime)
    except RunBusy as e:
        raise HTTPException(409, str(e))
    return JSONResponse(cur, status_code=202)


@app.post("/api/builds/{build_id}/resume")
def resume_build(build_id: str, runtime: str | None = None):
    try:
        cur = manager.resume(build_id, runtime=runtime)
    except RunBusy as e:
        raise HTTPException(409, str(e))
    return JSONResponse(cur, status_code=202)


@app.post("/api/builds/{build_id}/stop")
def stop_build(build_id: str) -> dict:
    return manager.stop(build_id)


@app.post("/api/stop")
def stop_current() -> dict:
    """Stop whatever build is currently running — the Stop button needs no id (single-slot)."""
    return manager.stop()


@app.get("/api/events")
async def events(request: Request):
    """SSE stream of the current run's structured events (replayed from the run's start on connect)."""
    q: Queue = manager.subscribe()

    def _next(timeout: float):
        try:
            return q.get(timeout=timeout)
        except Empty:
            return None

    async def gen():
        try:
            yield ":ok\n\n"   # open the stream immediately
            while True:
                if await request.is_disconnected():
                    break
                ev = await asyncio.to_thread(_next, 1.0)
                yield ": keep-alive\n\n" if ev is None else sse_format(ev)
        finally:
            manager.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── SPA (committed dist/) ──────────────────────────────────────────────────────
if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

_PLACEHOLDER = (
    "<!doctype html><meta charset=utf-8><title>poc-foundry</title>"
    "<body style='font-family:system-ui;max-width:40rem;margin:4rem auto;color:#ddd;background:#111'>"
    "<h1>poc-foundry — API up</h1><p>The React <code>dist/</code> is not built yet (M3 S2). "
    "The JSON API + SSE are live: try <code>/api/health</code>, <code>/api/builds</code>, "
    "<code>/api/events</code>.</p></body>"
)


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    """Serve the SPA shell for any non-API path (client-side routing). Placeholder until S2 commits
    the real ``dist/``."""
    index = DIST / "index.html"
    if index.is_file():
        return FileResponse(str(index))
    return HTMLResponse(_PLACEHOLDER)
