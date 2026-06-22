"""BuildState — the typed state threaded through the deterministic LangGraph harness (design §5.1).

Checkpointed at every node boundary (SQLite saver). Pydantic so it serializes cleanly. The phases
read/write fields here; P7 assembles the final ``PoCBuildArtifact`` from this state. Kept flat and
serializable (paths as str, structured bits as small pydantic sub-models).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from poc_foundry.artifact import SuccessCriterion


class Spec(BaseModel):
    """P1 output — POC_SPEC.md / spec.json (design §5.3 P1)."""

    goal: str = ""
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    demo_scenario: str = ""
    template: str = "gradio-chatbot"
    buildable: bool = True                       # False → NOT_BUILDABLE
    not_buildable_reasons: list[str] = Field(default_factory=list)


class IterationPlan(BaseModel):
    """One planned iteration (design §5.3 P2) — single-feature / small-diff granularity."""

    goal: str
    acceptance: list[str] = Field(default_factory=list)   # maps to success criteria
    interface: str = ""                                    # the pinned public contract (tester+coder)
    files: list[str] = Field(default_factory=list)         # files this iteration touches (advisory)


class Plan(BaseModel):
    """P2 output — plan.json / PLAN.md."""

    iterations: list[IterationPlan] = Field(default_factory=list)


class BuildState(BaseModel):
    # identity / input
    build_id: str
    run_id: str | None = None
    artifact_id: str = ""
    brief: str = ""
    driver: str = "tech-scout"
    version: int | None = None

    # paths (str for serialization)
    build_dir: str = ""           # builds/<build_id>/
    workspace_dir: str = ""       # builds/<build_id>/workspace/  (the PoC; standalone git repo)

    # progress
    phase: str = "init"           # current phase label (for events / PROGRESS.md)
    status: str = "incomplete"    # done | incomplete | failed | not-buildable
    iteration: int = 0

    # phase products
    spec: Spec | None = None
    plan: Plan | None = None

    # bookkeeping
    log: list[str] = Field(default_factory=list)   # human-readable phase trace
    caveats: list[str] = Field(default_factory=list)
    error: str | None = None

    def note(self, msg: str) -> None:
        """Append a phase-trace line (mirrored to PROGRESS.md by the close phase)."""
        self.log.append(msg)
