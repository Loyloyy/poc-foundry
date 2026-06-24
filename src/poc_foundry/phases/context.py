"""Shared context + helpers for the deterministic phase pipeline (design §5.1, §5.3).

``Ctx`` carries the non-serializable build wiring (cfg, broker, coder, paths, the loaded run folder)
that the phases need but that does NOT live in the checkpointed ``BuildState`` — the phases capture
it via a closure in ``graph.py``. Everything here is stdlib + pydantic-light so it imports on 3.10.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from poc_foundry.sandbox import Mount

_REPO_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_ROOT = _REPO_ROOT / "templates"


# ── template ─────────────────────────────────────────────────────────────────
@dataclass
class Template:
    name: str
    version: str
    root: Path
    stamp_dir: Path
    editable_files: list[str]
    smoke_test: str
    suite: str
    interface: str
    run_cmd: str
    stack: list[dict]
    license: str | None = None
    services: list[dict] = field(default_factory=list)   # [{name, vetted}] — sibling services this PoC needs


def load_template(name: str, templates_root: Path | None = None) -> Template:
    root = (templates_root or TEMPLATES_ROOT) / name
    manifest = json.loads((root / "template.json").read_text())
    return Template(
        name=manifest["name"],
        version=str(manifest.get("version", "0")),
        root=root,
        stamp_dir=root / manifest.get("stamp_dir", "files"),
        editable_files=list(manifest.get("editable_files", [])),
        smoke_test=manifest.get("smoke_test", "tests"),
        suite=manifest.get("suite", "tests"),
        interface=manifest.get("interface", ""),
        run_cmd=manifest.get("run_cmd", "python app.py"),
        stack=list(manifest.get("stack", [])),
        license=manifest.get("license"),
        services=list(manifest.get("services", [])),
    )


# ── build context ────────────────────────────────────────────────────────────
@dataclass
class Ctx:
    cfg: object                 # BuildConfig
    build_id: str
    run_dir: Path               # the Stage-2 run folder (input)
    template: Template
    build_dir: Path             # builds/<id>/ (emitted artifact + index)
    workspace_dir: Path         # <PF_WORKSPACE_DIR>/<id>/workspace (local disk; kata-mountable)
    staging_dir: Path           # <PF_WORKSPACE_DIR>/<id>/staging (harness-owned; tests etc.)
    broker: object              # Broker
    coder: object               # CoderEngine
    report: list[str] = field(default_factory=list)
    run_folder: object | None = None   # ingest.RunFolder (loaded at P0)
    service_env: dict = field(default_factory=dict)    # PF_SERVICE_<NAME>_HOST=<ip> injected into sandboxes
    services: list = field(default_factory=list)       # live sibling-service handles (reaped at build end)
    events: object | None = None       # M3 web-UI seam: optional event sink (callable); None on the CLI path

    def say(self, line: str) -> None:
        self.report.append(line)
        # live progress to stderr (stdout stays the final report) so a long run is observable — and an
        # operator can see WHEN to stop/kill it. Silenced with PF_PROGRESS=0 (set by the fakes runner).
        if os.environ.get("PF_PROGRESS", "1") != "0":
            import sys
            print(f"  · {line}", file=sys.stderr, flush=True)
        # M3: mirror the line to the web-UI event sink (additive; stderr keeps working for the CLI).
        if self.events is not None:
            from poc_foundry.events import emit, make_event
            emit(self.events, make_event("log", self.build_id, line=line))


# ── workspace git (the sanctioned deterministic commits, AGENTS rule #2) ─────
# The orchestrator runs git as root over workspaces it has chown'd to the sandbox builder (uid 1000),
# so every call disables git's "dubious ownership" guard for these harness-owned repos — otherwise
# `git add`/`commit`/`clone` abort with exit 128 (in-container only; locally chown no-ops as non-root).
def git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "safe.directory=*", "-C", str(workspace), *args],
                          capture_output=True, text=True, check=check)


def ensure_git_global_safe() -> None:
    """The orchestrator (root, in-container) git-operates on workspaces it chown'd to the sandbox
    builder (uid 1000). Git ignores ``safe.directory`` set via ``-c``/local config (a security
    measure) — local-path ``git clone`` in particular requires it in GLOBAL config. Set it there,
    but ONLY when running as root, so a local dev box's gitconfig is never touched."""
    try:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            subprocess.run(["git", "config", "--global", "safe.directory", "*"],
                           capture_output=True, check=False)
    except Exception:  # noqa: BLE001
        pass


def git_init(workspace: Path) -> None:
    ensure_git_global_safe()
    git(workspace, "init", "-q")
    git(workspace, "config", "user.email", "harness@poc-foundry.local")
    git(workspace, "config", "user.name", "poc-foundry")
    git(workspace, "config", "commit.gpgsign", "false", check=False)


def chown_to_builder(path: Path, uid: int = 1000, gid: int = 1000) -> None:
    """Make a tree owned by the sandbox's non-root builder (uid 1000) so it can write into /work
    (``.deps``, pytest caches) and virtio-fs maps cleanly (M0(a)). The orchestrator runs as root
    in-container, so root keeps write access regardless. Best-effort: a no-op/ignored when the
    orchestrator isn't root (e.g. local dev) — failures are swallowed."""
    subprocess.run(["chown", "-R", f"{uid}:{gid}", str(path)], capture_output=True, check=False)


def git_diff(workspace: Path, base: str | None = None) -> str:
    """Unified diff of the working tree vs ``base`` (a commit-ish; HEAD if None). Used by the
    integrity diff-scanner to inspect the coder's per-iteration edits. Never raises (a fresh repo
    with no commits yet returns ''); includes untracked files via ``--no-index`` is overkill, so we
    add then diff --cached as a fallback for new files."""
    ref = base or "HEAD"
    r = git(workspace, "diff", ref, check=False)
    out = r.stdout
    # `git diff <ref>` misses brand-new untracked files; surface them too (staged against the ref).
    git(workspace, "add", "-A", check=False)
    staged = git(workspace, "diff", "--cached", ref, check=False)
    git(workspace, "reset", "-q", check=False)   # unstage; leave the working tree as the coder left it
    return staged.stdout or out


def git_commit(workspace: Path, message: str) -> str:
    git(workspace, "add", "-A")
    r = git(workspace, "commit", "-q", "-m", message, check=False)
    if r.returncode != 0 and "nothing to commit" not in (r.stdout + r.stderr):
        raise RuntimeError(f"git commit failed: {r.stderr or r.stdout}")
    return git(workspace, "rev-parse", "--short", "HEAD", check=False).stdout.strip()


# ── mounts ───────────────────────────────────────────────────────────────────
def ws_mount(host_workspace: Path, *, read_only: bool = False) -> Mount:
    """The build workspace at /work. ``source`` is the HOST path — PF_WORKSPACE_DIR must be mounted
    at the SAME path inside the orchestrator container (sibling-container semantics; see the
    override example)."""
    return Mount(source=str(host_workspace), target="/work", read_only=read_only)


def staged_tests_mount(staging_tests: Path) -> Mount:
    """The harness-owned red-first tests, mounted READ-ONLY outside the workspace (staged VERIFY)."""
    return Mount(source=str(staging_tests), target="/staged", read_only=True)


# ── RUN.md tagged-block parser (clean-room) ──────────────────────────────────
_PF_BLOCK = re.compile(
    r"<!--\s*pf:(?P<tag>[a-z]+)\s*-->\s*```(?:bash|sh)?\n(?P<body>.*?)\n```", re.DOTALL)


def parse_run_blocks(run_md: str) -> dict[str, str]:
    """Extract ``<!-- pf:TAG -->``-marked bash blocks from RUN.md → {tag: command}."""
    return {m.group("tag"): m.group("body").strip() for m in _PF_BLOCK.finditer(run_md)}


# ── misc ─────────────────────────────────────────────────────────────────────
def stamp_template(template: Template, workspace: Path) -> list[str]:
    """Deterministically copy the template's stamp_dir into a fresh workspace. Returns the files
    written (relative paths)."""
    workspace.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for src in sorted(template.stamp_dir.rglob("*")):
        if src.is_dir():
            continue
        rel = src.relative_to(template.stamp_dir)
        dest = workspace / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        written.append(str(rel))
    return written
