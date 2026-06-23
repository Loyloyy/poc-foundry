"""BuildState — the typed state threaded through the deterministic LangGraph harness (design §5.1).

Checkpointed at every node boundary (SQLite saver). Pydantic so it serializes cleanly. The phases
read/write fields here; P7 assembles the final ``PoCBuildArtifact`` from this state. Kept flat and
serializable (paths as str, structured bits as small pydantic sub-models).
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from poc_foundry.artifact import IterationRecord, SuccessCriterion


class Spec(BaseModel):
    """P1 output — POC_SPEC.md / spec.json (design §5.3 P1)."""

    goal: str = ""
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    demo_scenario: str = ""
    template: str = "gradio-chatbot"
    buildable: bool = True                       # False → NOT_BUILDABLE
    not_buildable_reasons: list[str] = Field(default_factory=list)


class AdequacyReview(BaseModel):
    """Critic adequacy verdict on a staged test (design §5.4) — does passing it actually demonstrate
    the criterion, or is it gameable/trivial? Conservative default: adequate."""

    adequate: bool = True
    reason: str = ""
    suggestion: str = ""


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
    source_dir: str = ""          # the Stage-2 run folder (recorded for resume)

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

    # results carried from P3–P6 → assembled into PoCBuildArtifact at P7
    scaffold_sha: str = ""
    commit_sha: str = ""
    iteration_records: list[IterationRecord] = Field(default_factory=list)
    staged_tests: list[str] = Field(default_factory=list)
    tests_total: int = 0
    tests_passing: int = 0
    cleanroom: dict = Field(default_factory=dict)   # quickstart_ok / suite_ok / demo_ok

    # M2a integrity walls (design §5.5) — additive
    authored_test_ids: list[str] = Field(default_factory=list)  # the inventory ledger (tester's record)
    inventory_ok: bool = True        # collected ∧ passed ⊇ recorded (False blocks `done`)
    red_first_ok: bool = True        # every accepted iteration's staged test was RED pre-coder
    incidents: list[str] = Field(default_factory=list)          # diff-scanner / ledger → security.incidents[]

    # M2a critic gate + verdict ladder (design §5.4, §5.8) — additive
    verdict: str = ""                # last critic verdict: pass | fix | descope | replan | respec
    fix_count: int = 0               # critic `fix` verdicts spent (vs fix_limit_k / degraded_fix_limit_k)
    respec_count: int = 0            # spec revisions spent (vs respec_cap)
    replan_count: int = 0            # replans spent (vs replan_cap)
    degraded_critic: bool = False    # critic.family == coder.family → lower K, recorded in security
    pending_test_src: str = ""       # transient: the staged test the critic reviews for adequacy
    pending_criterion: str = ""      # transient: the criterion text under review
    descope_report: list[dict] = Field(default_factory=list)    # → PoCBuildArtifact.descope_report[]
    green_test_files: list[str] = Field(default_factory=list)   # staged tests of MET iterations → published to clean-room
    demo_quality: str = ""
    demonstrates_core_value: str = "no"

    # M2b budgets / caps (design §5.8) — additive
    caps_hit: list[str] = Field(default_factory=list)   # which budget cap(s) fired → PoCBuildArtifact.caps_hit

    # bookkeeping
    log: list[str] = Field(default_factory=list)   # human-readable phase trace
    caveats: list[str] = Field(default_factory=list)
    error: str | None = None

    def note(self, msg: str) -> None:
        """Append a phase-trace line (mirrored to PROGRESS.md by the close phase)."""
        self.log.append(msg)
