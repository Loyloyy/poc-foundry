"""Load a Stage-2 run folder (``artifacts/<id>/``) — the artifact + its co-located companion files.

The artifact (`vNN.json`) is the primary, validated input; the run folder supplies supplementary
material Stage 3 templates from (`code/**`) and documents from (`report.md` / `comparison.md`).
Contract: ``../ai-engineer-research/docs/STAGE3_CONTRACT.md``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from poc_foundry.stage2_artifact import DeepResearchArtifact


@dataclass
class RunFolder:
    """A loaded Stage-2 run folder. `artifact` is validated against the vendored schema on load."""

    path: Path
    version: int
    artifact: DeepResearchArtifact
    report_md: str | None = None
    comparison_md: str | None = None
    coverage: dict | None = None
    manifest: list[dict] | None = None  # code/manifest.json (advisory, tolerated-absent)
    code_files: list[Path] = field(default_factory=list)


def _latest_version_file(run_dir: Path) -> tuple[int, Path]:
    versions = sorted(
        (int(p.stem[1:]), p) for p in run_dir.glob("v*.json") if p.stem[1:].isdigit()
    )
    if not versions:
        raise FileNotFoundError(f"no vNN.json artifact in {run_dir}")
    return versions[-1]


def load_run(run_dir: str | Path, version: int | None = None) -> RunFolder:
    """Load a run folder by PATH (self-contained; not the artifacts-root + id form).

    Reads the requested (or latest) ``vNN.json`` and validates it against the vendored
    ``DeepResearchArtifact`` schema (raises on shape violation). Companion files are
    tolerated-absent. Semantic invariants are checked separately by ``validate_semantics``.
    """
    run_dir = Path(run_dir)
    if not run_dir.is_dir():
        raise NotADirectoryError(f"not a run folder: {run_dir}")

    if version is None:
        version, vpath = _latest_version_file(run_dir)
    else:
        vpath = run_dir / f"v{version:02d}.json"
        if not vpath.exists():
            raise FileNotFoundError(f"no v{version:02d}.json in {run_dir}")

    artifact = DeepResearchArtifact.model_validate_json(vpath.read_text())

    def _read(name: str) -> str | None:
        p = run_dir / name
        return p.read_text() if p.exists() else None

    def _read_json(name: str):
        p = run_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            return None

    code_dir = run_dir / "code"
    code_files = sorted(p for p in code_dir.rglob("*") if p.is_file() and p.name != "manifest.json") \
        if code_dir.is_dir() else []

    return RunFolder(
        path=run_dir,
        version=version,
        artifact=artifact,
        report_md=_read("report.md"),
        comparison_md=_read("comparison.md"),
        coverage=_read_json("coverage.json"),
        manifest=_read_json("code/manifest.json"),
        code_files=code_files,
    )
