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
    open_questions: list[str] = Field(default_factory=list)   # M2c S4: from the artifact → research rung (a)


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
    research_questions: list[str] = Field(default_factory=list)   # M2c S4: open questions → research rung (a)
    test_file: str = ""    # M4 S1 refine: pin the staged-test filename so a FILTERED backlog plan reuses
    #                        the criterion's already-authored (red-first) test rather than re-numbering it


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
    refine_mode: bool = False     # M4 S1: this is a `refine` re-attack of a finished build's descoped
    #                               backlog (workspace already has real code) — disables the iteration-0
    #                               strict-red-first probe (a green test now means "met by existing code")

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
    # M7 Layer-2 (integrity trust-cap downgrade, planning-chat ruling 2026-07-02 → DECISIONS #52). A high
    # integrity incident is caught+contained (its edits are rolled back, never committed) yet permanently
    # capped `trustworthy`. These fields let P7 decide whether a SINGLE contained-and-superseded incident
    # may be downgraded blocking→informational (all four ruling conditions), and stratify evals.
    had_high_incident: bool = False                             # any [high] incident fired (survives rollback)
    high_incident_kinds: list[str] = Field(default_factory=list)     # kinds of the high incidents (eligibility)
    high_incident_criteria: list[str] = Field(default_factory=list)  # criterion texts a high incident targeted

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

    # M2c S4 research-on-gaps escalation rung (design §5.8) — additive
    research_pending: bool = False     # p_critic → p4: run targeted research on next entry (stuck rung b)
    research_error: str = ""           # the error summary to research (set with research_pending)
    last_research_iteration: int = -1  # guard: research at most once per iteration index
    last_coder_stuck: bool = False     # last coder run repeated an error signature (≥ stuck_research_after)
    last_coder_error: str = ""         # last coder run's error tail (for the research query)
    research_calls: int = 0            # model calls spent in research (budget visibility)

    # M6 buggy-test recovery rung — additive. When a coder is stuck AFTER research (the staged test may
    # be flawed/impossible, e.g. asserting str ids against int ids), re-author the test ONCE, feeding the
    # coder's diagnosis to the tester. Re-gated by red-first + the critic so it can't become gameable.
    reauthor_pending: bool = False     # p_critic → p4: re-author this iteration's staged test on re-entry
    reauthor_reason: str = ""          # the coder's stuck diagnosis, handed to the tester as advisory
    reauthor_count: int = 0            # test re-authors spent (vs reauthor_cap)

    # M6 replan-waste fix — additive. A `replan` (SAME spec) preserves the workspace instead of re-stamping,
    # so already-met criteria fast-path via met-existing (skip the coder) and only the unmet tail is
    # re-attacked. Set by p_critic on replan; cleared by p1_spec (a respec = new criteria → fresh scaffold).
    replan_mode: bool = False

    # bookkeeping
    log: list[str] = Field(default_factory=list)   # human-readable phase trace
    caveats: list[str] = Field(default_factory=list)
    error: str | None = None

    def note(self, msg: str) -> None:
        """Append a phase-trace line (mirrored to PROGRESS.md by the close phase)."""
        self.log.append(msg)
