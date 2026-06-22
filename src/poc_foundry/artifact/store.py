"""Persistence + id minting for the output PoCBuildArtifact.

Lives under each build folder: ``builds/<build_id>/vNN.json`` (versioned like Stage 2). The build id
is minted once and is also the folder name.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from .schema import PoCBuildArtifact


def new_build_id() -> str:
    """`poc-<YYYYMMDD>-<HHMMSS>-<rand6>` (UTC, sortable) — the build folder name + artifact id."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"poc-{ts}-{uuid.uuid4().hex[:6]}"


def save(artifact: PoCBuildArtifact, build_dir: Path | str) -> Path:
    build_dir = Path(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    path = build_dir / f"v{artifact.version:02d}.json"
    path.write_text(artifact.model_dump_json(indent=2))
    return path


def load(build_dir: Path | str, version: int | None = None) -> PoCBuildArtifact:
    build_dir = Path(build_dir)
    if version is None:
        versions = sorted(int(p.stem[1:]) for p in build_dir.glob("v*.json") if p.stem[1:].isdigit())
        if not versions:
            raise FileNotFoundError(f"no vNN.json in {build_dir}")
        version = versions[-1]
    return PoCBuildArtifact.model_validate_json((build_dir / f"v{version:02d}.json").read_text())
