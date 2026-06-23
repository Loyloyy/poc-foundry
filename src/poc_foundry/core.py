"""The stable headless contract (design §4.1): ``build_poc(...)`` / ``resume_build(...)``. CLI and
(later) web UI hold NO pipeline logic — they call this. Heavy deps (langgraph, langchain) are reached
only through the graph, lazily, so importing this module stays light.

    report_md, artifact = build_poc(source, brief="", *, driver="tech-scout")

``source`` is a Stage-2 run-folder PATH, or an artifact id resolvable under ``PF_ARTIFACTS_ROOT``.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from poc_foundry.artifact import load as load_artifact
from poc_foundry.artifact import new_build_id
from poc_foundry.config import load_config
from poc_foundry.phases import Ctx, load_template


def resolve_source(source: str | Path, cfg) -> Path:
    """Resolve ``source`` to a Stage-2 run folder. Accepts a path to the folder, or an artifact id
    looked up under ``PF_ARTIFACTS_ROOT`` (``<root>/<id>/``)."""
    p = Path(source)
    if p.is_dir():
        return p
    root = os.environ.get("PF_ARTIFACTS_ROOT", "").strip()
    if root and (Path(root) / str(source)).is_dir():
        return Path(root) / str(source)
    raise FileNotFoundError(
        f"cannot resolve source {source!r}: pass a run-folder path or set PF_ARTIFACTS_ROOT")


def _make_broker(cfg, build_id: str, runtime: str | None):
    """The per-build broker. ``PF_BROKER_SOCKET`` (set on the server compose) selects the
    OUT-OF-PROCESS path (M2a S4): a thin ``RemoteBroker`` that forwards to the daemon holding
    docker.sock, so the orchestrator never mounts the socket. Unset → the in-process ``Broker``
    (the default; unchanged proven path)."""
    allowed = {cfg.sandbox_image, cfg.proxy_image} | cfg.service_refs()   # vetted siblings (image:tag)
    vllm_key = os.environ.get("PF_SANDBOX_VLLM_KEY", "not-needed")
    sock = os.environ.get("PF_BROKER_SOCKET", "").strip()
    if sock:
        from poc_foundry.sandbox.client import RemoteBroker
        return RemoteBroker(build_id, cfg, allowed_images=allowed, runtime=runtime,
                            vllm_key=vllm_key, socket_path=sock)
    from poc_foundry.sandbox import Broker
    return Broker(build_id, cfg, allowed_images=allowed, runtime=runtime, vllm_key=vllm_key)


def _prepare(cfg, build_id: str, run_dir: Path, template: str, runtime: str | None):
    """Build the per-build ``Ctx`` + broker (shared by build + resume)."""
    from poc_foundry.coder import BespokeCoder

    workspace_dir = cfg.workspace_dir / build_id / "workspace"
    staging_dir = cfg.workspace_dir / build_id / "staging"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    broker = _make_broker(cfg, build_id, runtime)
    ctx = Ctx(cfg=cfg, build_id=build_id, run_dir=run_dir, template=load_template(template),
              build_dir=cfg.builds_dir / build_id, workspace_dir=workspace_dir,
              staging_dir=staging_dir, broker=broker, coder=BespokeCoder())
    return ctx, broker


def build_poc(source: str | Path, brief: str = "", *, driver: str = "tech-scout",
              template: str | None = None, builds_dir: str | Path | None = None,
              runtime: str | None = None):
    """Build ONE PoC from ONE Stage-2 artifact. Returns ``(report_md, PoCBuildArtifact)``.

    Deterministic spine: provision a per-build sandbox environment (internal net + egress proxy +
    uv-cache), run the LangGraph pipeline P0→P7 (checkpointed), then tear the environment down. The
    emitted artifact + workspace land under ``builds/<build_id>/``.
    """
    from poc_foundry.graph import build_graph
    from poc_foundry.state import BuildState

    cfg = load_config(builds_dir)
    run_dir = resolve_source(source, cfg)
    template = template or cfg.default_template

    build_id = new_build_id()
    build_dir = cfg.builds_dir / build_id
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "build_meta.json").write_text(json.dumps(
        {"source_dir": str(run_dir), "template": template, "driver": driver, "brief": brief}))

    ctx, broker = _prepare(cfg, build_id, run_dir, template, runtime)
    state = BuildState(build_id=build_id, brief=brief, driver=driver, source_dir=str(run_dir),
                       build_dir=str(build_dir), workspace_dir=str(ctx.workspace_dir))

    try:
        broker.provision()
        graph = build_graph(ctx, cfg)
        graph.invoke(state, config={"configurable": {"thread_id": build_id}, "recursion_limit": 60})
    except Exception as e:  # noqa: BLE001 — leave a forensic artifact, then surface the error
        _emit_failed(build_dir, build_id, ctx, e)
        raise
    finally:
        broker.destroy()

    return _result(build_dir)


def _emit_failed(build_dir: Path, build_id: str, ctx, exc: Exception) -> None:
    """Best-effort minimal artifact when a phase raises before P7, so ``builds/<id>/`` is inspectable
    and `list` works. Never masks the original traceback (the caller re-raises)."""
    if (build_dir / "v01.json").exists():
        return
    try:
        from datetime import datetime, timezone

        from poc_foundry.artifact import PoCBuildArtifact, SourceArtifact, save
        art = ctx.run_folder.artifact if getattr(ctx, "run_folder", None) else None
        pa = PoCBuildArtifact(
            id=build_id, generated_at=datetime.now(timezone.utc).isoformat(),
            source_artifact=SourceArtifact(id=(art.id if art else ""), version=(art.version if art else 1)),
            status="failed", caveats=[f"phase crash: {type(exc).__name__}: {exc}"])
        build_dir.mkdir(parents=True, exist_ok=True)
        save(pa, build_dir)
        (build_dir / "report.md").write_text(
            f"# Build {build_id} — FAILED\n\n```\n{type(exc).__name__}: {exc}\n```\n")
        # scrub the forensic artifact: a phase-crash traceback embeds the vLLM endpoint / id / paths.
        from poc_foundry import scrub
        scrub.scrub_build_dir(build_dir)
    except Exception:  # noqa: BLE001
        pass


def resume_build(build_id: str, *, builds_dir: str | Path | None = None, runtime: str | None = None):
    """Resume a checkpointed build from its last completed node (design §5.9). Returns
    ``(report_md, PoCBuildArtifact)``."""
    from poc_foundry.graph import build_graph
    from poc_foundry.ingest import load_run

    cfg = load_config(builds_dir)
    build_dir = cfg.builds_dir / build_id
    meta = json.loads((build_dir / "build_meta.json").read_text())
    run_dir = Path(meta["source_dir"])

    ctx, broker = _prepare(cfg, build_id, run_dir, meta.get("template", cfg.default_template), runtime)
    ctx.run_folder = load_run(run_dir)   # phases resumed past P0 still need the loaded artifact

    try:
        broker.provision()
        graph = build_graph(ctx, cfg)
        graph.invoke(None, config={"configurable": {"thread_id": build_id},   # None → resume
                                   "recursion_limit": 60})
    finally:
        broker.destroy()

    return _result(build_dir)


def _result(build_dir: Path):
    artifact = load_artifact(build_dir)
    report = (build_dir / "report.md").read_text() if (build_dir / "report.md").exists() else ""
    return report, artifact


# ── small operations used by the CLI ─────────────────────────────────────────
def list_builds(builds_dir: str | Path | None = None) -> list[dict]:
    cfg = load_config(builds_dir)
    out = []
    if not cfg.builds_dir.is_dir():
        return out
    for d in sorted(cfg.builds_dir.iterdir()):
        if not d.is_dir() or not d.name.startswith("poc-"):
            continue
        try:
            a = load_artifact(d)
            out.append({"id": a.id, "status": a.status, "source": a.source_artifact.id,
                        "demonstrates": a.final_verdict.demonstrates_core_value})
        except Exception:  # noqa: BLE001 — half-written builds
            out.append({"id": d.name, "status": "?", "source": "?", "demonstrates": "?"})
    return out


def clean_build(build_id: str, builds_dir: str | Path | None = None,
                *, workspaces: bool = True) -> list[str]:
    """Remove a build's emitted folder (and its local-disk workspace/staging). Returns removed paths."""
    cfg = load_config(builds_dir)
    removed = []
    bdir = cfg.builds_dir / build_id
    if bdir.is_dir():
        shutil.rmtree(bdir)
        removed.append(str(bdir))
    if workspaces:
        wdir = cfg.workspace_dir / build_id
        if wdir.is_dir():
            shutil.rmtree(wdir)
            removed.append(str(wdir))
    return removed
