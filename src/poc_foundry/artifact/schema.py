"""PoCBuildArtifact — the Stage-3 OUTPUT contract (design §4.3).

Pydantic v2, flat, **additive-only** evolution (never remove/retype a field without a major bump —
the same discipline Stage 2 follows for its artifact). M1 populates the subset it produces; later
milestones fill the rest. Versioned like Stage 2 (`vNN.json` under `builds/<id>/`).

Pure pydantic + stdlib → imports on the 3.10 dev box (kept out of the heavy agent stack).
"""
from __future__ import annotations

from pydantic import BaseModel, Field


# ── provenance ───────────────────────────────────────────────────────────────
class SourceArtifact(BaseModel):
    id: str                       # the Stage-2 DeepResearchArtifact id (dra-…)
    version: int = 1


# ── spec ─────────────────────────────────────────────────────────────────────
class SuccessCriterion(BaseModel):
    text: str
    type: str = "met-by-test"     # met-by-test | met-by-demo-evidence (design §1.3)
    status: str = "pending"       # pending | met | partial | descoped
    core: bool = False            # exactly ONE criterion is the core_criterion (DONE floor)
    human_verified: str = "n/a"   # pending | done | n/a


# ── per-iteration ────────────────────────────────────────────────────────────
class IterationRecord(BaseModel):
    goal: str
    status: str = "pending"       # pending | green | descoped | abandoned
    attempts: int = 0
    tests_added: int = 0


# ── nested value objects ─────────────────────────────────────────────────────
class TestsSummary(BaseModel):
    total: int = 0
    passing: int = 0
    inventory_ok: bool = False    # collected ∧ passed ⊇ the tester's recorded ids (M2a ledger)


class CleanroomResult(BaseModel):
    quickstart_ok: bool = False
    suite_ok: bool = False
    demo_ok: bool = False


class FinalVerdict(BaseModel):
    demonstrates_core_value: str = "no"   # yes | partial | no (judged vs the ORIGINAL pre-descope spec)
    gaps: list[str] = Field(default_factory=list)


class DescopeItem(BaseModel):
    criterion: str
    attempts_made: int = 0
    why_failed: str = ""
    finish_path: str = ""         # concrete guidance: do it by hand in OpenCode, or refine re-run


class StackItem(BaseModel):
    layer: str
    choice: str
    pinned_version: str | None = None


class ServiceRef(BaseModel):
    name: str
    image: str
    pinned_tag: str | None = None


class TemplateRef(BaseModel):
    name: str
    version: str | None = None


class TemplateRepo(BaseModel):
    name: str
    url: str
    license: str | None = None


class SecurityInfo(BaseModel):
    sandbox: str = "kata"
    kata_version: str | None = None
    egress_allowlist: list[str] = Field(default_factory=list)  # READ BACK from the proxy at emit
    incidents: list[str] = Field(default_factory=list)
    degraded_critic: bool = False
    had_high_incident: bool = False        # M7: a [high] integrity incident fired (survived rollback) —
    #                                        eval-stratification flag (alongside degraded_critic)


class Budget(BaseModel):
    wall_s: float = 0.0
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    contention_indicator: float | None = None   # observed call-latency median (load annotation)


# ── the artifact ─────────────────────────────────────────────────────────────
class PoCBuildArtifact(BaseModel):
    # provenance / lineage
    id: str                                   # poc-<YYYYMMDD>-<HHMMSS>-<rand6>
    version: int = 1
    generated_at: str                         # UTC ISO-8601
    source_artifact: SourceArtifact

    # request shaping
    driver: str = "tech-scout"                # tech-scout | customer | workshop
    spec_summary: str = ""

    # spec + iterations
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    iterations: list[IterationRecord] = Field(default_factory=list)

    # results
    tests: TestsSummary = Field(default_factory=TestsSummary)
    cleanroom: CleanroomResult = Field(default_factory=CleanroomResult)
    demo_quality: str = ""
    final_verdict: FinalVerdict = Field(default_factory=FinalVerdict)
    descope_report: list[DescopeItem] = Field(default_factory=list)

    # what was built
    stack: list[StackItem] = Field(default_factory=list)
    template: TemplateRef | None = None
    services: list[ServiceRef] = Field(default_factory=list)
    template_repo: TemplateRepo | None = None
    licenses: list[str] = Field(default_factory=list)

    # security + provenance + budget
    security: SecurityInfo = Field(default_factory=SecurityInfo)
    model_versions: dict = Field(default_factory=dict)   # {roles: {...}} — names only, never ids
    budget: Budget = Field(default_factory=Budget)
    caps_hit: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)

    # outcome
    status: str = "incomplete"                # done | incomplete | failed | not-buildable
