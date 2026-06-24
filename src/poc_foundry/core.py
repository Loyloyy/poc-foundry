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


def _prepare(cfg, build_id: str, run_dir: Path, template: str, runtime: str | None,
             event_sink=None):
    """Build the per-build ``Ctx`` + broker (shared by build + resume). ``event_sink`` (M3 §5.12) is
    the optional web-UI event callback carried on ``Ctx.events``; ``None`` on the CLI path."""
    from poc_foundry.coder import BespokeCoder

    workspace_dir = cfg.workspace_dir / build_id / "workspace"
    staging_dir = cfg.workspace_dir / build_id / "staging"
    workspace_dir.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    broker = _make_broker(cfg, build_id, runtime)
    ctx = Ctx(cfg=cfg, build_id=build_id, run_dir=run_dir, template=load_template(template),
              build_dir=cfg.builds_dir / build_id, workspace_dir=workspace_dir,
              staging_dir=staging_dir, broker=broker, coder=BespokeCoder(), events=event_sink)
    return ctx, broker


def build_poc(source: str | Path, brief: str = "", *, driver: str = "tech-scout",
              template: str | None = None, builds_dir: str | Path | None = None,
              runtime: str | None = None, event_sink=None):
    """Build ONE PoC from ONE Stage-2 artifact. Returns ``(report_md, PoCBuildArtifact)``.

    Deterministic spine: provision a per-build sandbox environment (internal net + egress proxy +
    uv-cache), run the LangGraph pipeline P0→P7 (checkpointed), then tear the environment down. The
    emitted artifact + workspace land under ``builds/<build_id>/``.

    ``event_sink`` (M3 §5.12) is the optional web-UI event callback — ``None`` on the CLI path, so the
    headless contract is unchanged. When set, an early ``start`` event carries the freshly minted id.
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

    if event_sink is not None:   # surface the id to the UI before the first (slow) node
        from poc_foundry import events as _ev
        _ev.emit(event_sink, _ev.make_event("start", build_id, kind="build",
                                            source=str(source), template=template, driver=driver))
    ctx, broker = _prepare(cfg, build_id, run_dir, template, runtime, event_sink)
    state = BuildState(build_id=build_id, brief=brief, driver=driver, source_dir=str(run_dir),
                       build_dir=str(build_dir), workspace_dir=str(ctx.workspace_dir))

    from poc_foundry import tracing
    try:
        with tracing.build(build_id, tags=[driver, template]):   # root span (manual; §5.11)
            try:                                                 # teardown INSIDE the build span so
                broker.provision()                               # broker.destroy nests in the build
                graph = build_graph(ctx, cfg)                    # trace (not a stray top-level trace)
                _invoke_with_salvage(graph, state, ctx, cfg, build_dir, build_id)
            finally:
                broker.destroy()
    except Exception as e:  # noqa: BLE001 — leave a forensic artifact, then surface the error
        _emit_failed(build_dir, build_id, ctx, e)
        raise
    finally:
        tracing.flush()   # mandatory: flush queued spans before the ephemeral process exits (after the span closes)

    return _result(build_dir)


def _invoke_with_salvage(graph, first_input, ctx, cfg, build_dir: Path, build_id: str) -> None:
    """Run the graph under the budget meter. ``first_input`` is the initial ``BuildState`` (a fresh
    build) or ``None`` (resume). A ``BudgetExceeded`` cap (design §5.8) escapes the phases' broad
    ``except Exception`` guards (it's a ``BaseException``) → we salvage here rather than crash."""
    from poc_foundry.control import BuildStopped
    from poc_foundry.models import METER, BudgetExceeded

    METER.begin_run(cfg)
    gcfg = {"configurable": {"thread_id": build_id}, "recursion_limit": 60}
    try:
        graph.invoke(first_input, config=gcfg)
    except BudgetExceeded as be:
        _salvage_run(graph, ctx, build_dir, build_id, gcfg, be.cap)
    except BuildStopped:
        _emit_stopped(graph, ctx, build_dir, build_id, gcfg)


def _salvage_run(graph, ctx, build_dir: Path, build_id: str, gcfg: dict, cap: str) -> None:
    """A budget cap fired mid-run: recover the last checkpointed ``BuildState``, roll the workspace
    back to the last green commit (discard in-flight edits), record the cap, and emit an honest
    ``incomplete`` build (the clean-room never ran → it can never be ``done``). The forensic
    ``abandoned.patch`` + the descope-report entry are added in S3."""
    from poc_foundry.phases import p7_emit
    from poc_foundry.state import BuildState

    try:
        snap = graph.get_state(gcfg)
        state = BuildState(**snap.values) if snap and getattr(snap, "values", None) else None
    except Exception:  # noqa: BLE001
        state = None
    if state is None:   # cap fired before the first checkpoint — leave a minimal forensic artifact
        _emit_failed(build_dir, build_id, ctx,
                     RuntimeError(f"budget cap hit before first checkpoint: {cap}"))
        return

    # Capture the in-flight (un-merged) coder edits as a forensic patch BEFORE rolling them back, so a
    # human can finish the abandoned iteration by hand. Then reset the workspace to the last green
    # commit (#17 pattern) so the emitted workspace is sound.
    ws = Path(state.workspace_dir)
    try:
        from poc_foundry.phases.context import git, git_diff
        patch = git_diff(ws)
        if patch.strip():
            build_dir.mkdir(parents=True, exist_ok=True)
            (build_dir / "abandoned.patch").write_text(patch)
            state.caveats = list(state.caveats) + [
                "in-flight work saved to abandoned.patch (apply + finish by hand to complete it)"]
        git(ws, "reset", "--hard", "HEAD", check=False)
    except Exception:  # noqa: BLE001
        pass

    # Record the cap + a descope-report entry for the FIRST not-yet-met criterion — the natural resume
    # point. (Not ``iterations[state.iteration]``: the cap can fire on the critic call AFTER an
    # iteration already committed green, so that index may point at an already-met criterion.) Charge
    # the spent attempts only when that criterion is the one the in-flight iteration was working.
    state.caps_hit = list(state.caps_hit) + [cap]
    state.caveats = list(state.caveats) + [
        f"run halted by budget cap: {cap} — salvaged to the last green commit"]
    crits = state.spec.success_criteria if state.spec else []
    unmet = [c for c in crits if c.status != "met"]
    it = (state.plan.iterations[state.iteration]
          if state.plan and 0 <= state.iteration < len(state.plan.iterations) else None)
    if unmet:
        criterion = unmet[0].text
        in_flight = bool(it and criterion in it.acceptance)   # was the loop actively on this criterion?
        attempts = (state.fix_count + 1) if in_flight else 0
    else:   # everything met but the run was halted before finishing (e.g. mid clean-room) — rare
        criterion = (it.goal if it else state.pending_criterion) or "remaining work"
        attempts = state.fix_count + 1
    state.descope_report = list(state.descope_report) + [{
        "criterion": criterion, "attempts_made": attempts,
        "why_failed": f"run halted by budget cap: {cap}",
        "finish_path": "resume the build (state + workspace persist) with a higher cap, "
                       "or apply abandoned.patch + finish by hand in OpenCode"}]
    ctx.say(f"SALVAGE: budget cap {cap} → rolled back to last green; emitting incomplete")
    p7_emit(state, ctx)   # writes the artifact + scrubs (status will be incomplete: no clean-room)


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


def resume_build(build_id: str, *, builds_dir: str | Path | None = None, runtime: str | None = None,
                 event_sink=None):
    """Resume a checkpointed build from its last completed node (design §5.9). A FRESH sandbox/broker
    is provisioned over the PERSISTED workspace + checkpoint (VMs are cattle; the workspace + state are
    pets). Returns ``(report_md, PoCBuildArtifact)``. ``event_sink`` (M3) streams to the web UI."""
    from poc_foundry.control import clear_stop
    from poc_foundry.graph import build_graph
    from poc_foundry.ingest import load_run

    cfg = load_config(builds_dir)
    build_dir = cfg.builds_dir / build_id
    meta = json.loads((build_dir / "build_meta.json").read_text())
    run_dir = Path(meta["source_dir"])

    clear_stop(build_dir)   # a prior cooperative stop must not immediately re-trip the resumed run
    if event_sink is not None:
        from poc_foundry import events as _ev
        _ev.emit(event_sink, _ev.make_event("start", build_id, kind="resume",
                                            template=meta.get("template", cfg.default_template)))
    ctx, broker = _prepare(cfg, build_id, run_dir, meta.get("template", cfg.default_template), runtime,
                           event_sink)
    ctx.run_folder = load_run(run_dir)   # phases resumed past P0 still need the loaded artifact

    from poc_foundry import tracing
    try:
        with tracing.build(build_id, tags=["resume", meta.get("template", cfg.default_template)]):
            try:
                broker.provision()
                graph = build_graph(ctx, cfg)
                _invoke_with_salvage(graph, None, ctx, cfg, build_dir, build_id)   # None → resume
            finally:
                broker.destroy()
    finally:
        tracing.flush()

    return _result(build_dir)


def request_stop_build(build_id: str, builds_dir: str | Path | None = None) -> Path:
    """Request a cooperative stop (CLI ``stop``): write the sentinel the running build checks at each
    node boundary. The build checkpoints + exits gracefully and is ``resume``-able. Returns the path."""
    from poc_foundry.control import request_stop, stop_path

    cfg = load_config(builds_dir)
    request_stop(cfg.builds_dir / build_id)
    return stop_path(cfg.builds_dir / build_id)


def _emit_stopped(graph, ctx, build_dir: Path, build_id: str, gcfg: dict) -> None:
    """A cooperative stop fired: recover provenance from the last checkpoint and emit a lightweight
    ``stopped`` artifact (resumable — ``resume`` overwrites it on completion). The graph already
    checkpointed the last completed node, so no state is lost."""
    from datetime import datetime, timezone

    from poc_foundry.artifact import PoCBuildArtifact, SourceArtifact, save
    from poc_foundry.state import BuildState

    try:
        snap = graph.get_state(gcfg)
        state = BuildState(**snap.values) if snap and getattr(snap, "values", None) else None
    except Exception:  # noqa: BLE001
        state = None
    art = ctx.run_folder.artifact if getattr(ctx, "run_folder", None) else None
    src_id = (art.id if art else (state.artifact_id if state else "")) or ""
    pa = PoCBuildArtifact(
        id=build_id, generated_at=datetime.now(timezone.utc).isoformat(),
        source_artifact=SourceArtifact(id=src_id, version=(art.version if art else 1)),
        status="stopped",
        caveats=["build stopped cooperatively at a node boundary — "
                 "resume with `poc-foundry resume <id>` (state + workspace persist)"])
    build_dir.mkdir(parents=True, exist_ok=True)
    save(pa, build_dir)
    (build_dir / "report.md").write_text(
        f"# Build {build_id} — STOPPED\n\nStopped cooperatively at a node boundary. "
        f"Resume with `poc-foundry resume {build_id}`.\n")
    from poc_foundry import scrub
    scrub.scrub_build_dir(build_dir)
    ctx.say(f"STOPPED: checkpointed at the last node boundary; resume `{build_id}` to continue")


def _result(build_dir: Path):
    artifact = load_artifact(build_dir)
    report = (build_dir / "report.md").read_text() if (build_dir / "report.md").exists() else ""
    return report, artifact


# ── source discovery (M3: the web-UI build form shows TOPICS, not raw paths) ──
def _source_meta(folder: Path) -> dict | None:
    """Cheaply read a Stage-2 run folder's latest ``vNN.json`` top-level metadata (topic/brief/id/
    version) WITHOUT the heavy schema validation — enough to label a build-source picker. Returns
    ``None`` for a folder with no parseable artifact."""
    vs = sorted((int(p.stem[1:]), p) for p in folder.glob("v*.json") if p.stem[1:].isdigit())
    if not vs:
        return None
    ver, vpath = vs[-1]
    try:
        d = json.loads(vpath.read_text())
    except Exception:  # noqa: BLE001 — a malformed source is just skipped from the picker
        return None
    return {"id": d.get("id", ""), "topic": d.get("topic", "") or folder.name,
            "brief": d.get("brief", ""), "version": int(d.get("version", ver)), "path": str(folder)}


def list_sources() -> list[dict]:
    """Discover buildable Stage-2 sources (READ-ONLY input, rule #6) for the web-UI build picker:
    every committed test fixture + every run folder under ``PF_ARTIFACTS_ROOT`` (if set). Each row is
    ``{id, topic, brief, version, path}`` — the ``path`` is what ``build_poc(source=…)`` resolves."""
    folders: list[Path] = []
    repo_root = Path(__file__).resolve().parents[2]
    fixtures = repo_root / "tests" / "fixtures"
    if fixtures.is_dir():
        folders += [p for p in sorted(fixtures.iterdir()) if p.is_dir()]
    ar = os.environ.get("PF_ARTIFACTS_ROOT", "").strip()
    if ar and Path(ar).is_dir():
        folders += [p for p in sorted(Path(ar).iterdir()) if p.is_dir()]
    out: dict[str, dict] = {}
    for folder in folders:
        meta = _source_meta(folder)
        if meta:
            out[meta["path"]] = meta
    return sorted(out.values(), key=lambda m: m["topic"].lower())


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


# ── template CI (M2c S5, design §5.3 P3) ─────────────────────────────────────
def _discover_templates() -> list[str]:
    """Every template under ``templates/`` (a dir with a ``template.json``)."""
    from poc_foundry.phases.context import TEMPLATES_ROOT
    return sorted(p.parent.name for p in TEMPLATES_ROOT.glob("*/template.json"))


def preflight_templates(template_names: list[str] | None = None, *,
                        builds_dir: str | Path | None = None) -> list[dict]:
    """DOCKERLESS static check (the part provable in the fakes suite): every template resolves +
    every service it declares is PINNED in ``vetted_services`` (rule #8 — an unpinned service can't be
    spun, so the template would fail mid-build). Returns one row per template."""
    cfg = load_config(builds_dir)
    names = template_names or _discover_templates()
    out: list[dict] = []
    for name in names:
        r = {"template": name, "resolves": False, "services_pinned": True,
             "suite": "", "services": [], "error": ""}
        try:
            t = load_template(name)
            r["resolves"] = True
            r["suite"] = t.suite
            for decl in t.services:
                r["services"].append(decl["name"])
                vetted = cfg.vetted_services.get(decl.get("vetted", decl["name"]))
                if not vetted or str(vetted.get("pinned_tag", "")).startswith("<"):
                    r["services_pinned"] = False
                    r["error"] = f"service {decl['name']!r} → not pinned in vetted_services"
        except Exception as e:  # noqa: BLE001 — a broken template is a CI finding, not a crash
            r["error"] = f"load failed: {type(e).__name__}: {e}"
        out.append(r)
    return out


def template_ci(template_names: list[str] | None = None, *, runtime: str | None = None,
                builds_dir: str | Path | None = None) -> list[dict]:
    """Scaffold + smoke each template in a FRESH Kata VM (design §5.3 P3) so template rot (a yanked
    pin, a smoke regression) is caught at maintenance time, not mid-build. Reuses P3's
    ``stamp_template`` + the broker smoke path; ONE broker (net+proxy+uv-vol) for the run, a fresh VM
    per template, everything reaped. Returns per-template rows (preflight + ``smoke_ok``)."""
    from datetime import datetime, timezone

    from poc_foundry.phases.context import (chown_to_builder, git_commit, git_init,
                                            stamp_template, ws_mount)

    cfg = load_config(builds_dir)
    rows = preflight_templates(template_names, builds_dir=builds_dir)
    ci_id = "template-ci-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = cfg.workspace_dir / ci_id            # workspaces on local disk (host==container path for Kata)
    broker = _make_broker(cfg, ci_id, runtime)

    from poc_foundry import tracing
    try:
        with tracing.build(ci_id, tags=["template-ci"]):
            broker.provision()
            try:
                for r in rows:
                    r["smoke_ok"] = False
                    if not r["resolves"] or not r["services_pinned"]:
                        continue            # preflight already failed — don't waste a VM
                    name = r["template"]
                    t = load_template(name)
                    ws = base / name / "workspace"
                    ws.mkdir(parents=True, exist_ok=True)
                    try:
                        stamp_template(t, ws)
                        git_init(ws)
                        git_commit(ws, f"template-ci: stamp {name}")
                        chown_to_builder(ws)
                        with tracing.span("template-ci.smoke", template=name):
                            sbx = broker.create(mounts=[ws_mount(ws)], name=f"tci-{name[:12]}")
                            try:
                                res = sbx.exec(f"cd /work && python -m pytest {t.suite} -q", timeout_s=300)
                            finally:
                                sbx.destroy()
                        r["smoke_ok"] = res.ok
                        if not res.ok:
                            r["error"] = "smoke RED:\n" + res.combined[-700:]
                    except Exception as e:  # noqa: BLE001 — record, keep CI'ing the rest
                        r["error"] = f"{type(e).__name__}: {e}"
            finally:
                broker.destroy()
    finally:
        tracing.flush()
        shutil.rmtree(base, ignore_errors=True)
    return rows


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
