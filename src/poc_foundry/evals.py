"""Tiered evals v1 — CHEAP spec + plan evals against recorded Stage-2 fixtures (design §5.11).

The cheapest, fastest rung of the eval ladder: run **only** P0 ingest → P1 spec → P2 plan on a
committed fixture artifact (NO sandbox, NO clean-room, NO Langfuse — minutes, not a half-hour build),
then score the products two ways:

  * **deterministic checks** — structural invariants the architect's spec / the deterministic plan
    must satisfy (criterion count, exactly-one-core, met-by-test typing, core-first plan, …). These
    are asserted in the fakes suite, so the scoring itself is provable locally with a FAKE architect.
  * a **structured human-grading rubric** — dimensions a reviewer fills in later (the server reality
    is degraded-critic / one model for all roles, so we do NOT auto-judge with an LLM here; the rubric
    is the seam for Tier-2 human grading, recorded ungraded).

Metrics are recorded structured (``metadata.degraded_critic`` etc.) so later runs can stratify. The
module is import-light (stdlib + pydantic; phases lazy-imported inside the runner) so it ``py_compile``s
on the 3.10 box; the real architect call only happens when the runner actually executes a phase.
"""
from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, computed_field


class Check(BaseModel):
    """One deterministic pass/fail scoring check."""

    name: str
    passed: bool
    detail: str = ""


class RubricDimension(BaseModel):
    """A human-grading dimension (Tier-2 seam). ``grade`` is null until a human fills it 1–5."""

    dimension: str
    guidance: str
    grade: int | None = None


class EvalReport(BaseModel):
    """The scored result of one spec+plan eval against one fixture artifact."""

    fixture: str
    artifact_id: str = ""
    template: str = ""
    ok: bool = True               # the eval RAN (ingest+spec+plan) without an internal error
    error: str = ""

    buildable: bool = True
    not_buildable_reasons: list[str] = Field(default_factory=list)
    goal: str = ""
    num_criteria: int = 0

    spec_checks: list[Check] = Field(default_factory=list)
    spec_score: float = 0.0       # fraction of spec_checks passed (0..1)
    spec_lint_caveats: list[str] = Field(default_factory=list)   # the pipeline's own _lint_spec output

    plan_iterations: int = 0
    plan_checks: list[Check] = Field(default_factory=list)
    plan_score: float = 0.0

    rubric: list[RubricDimension] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)   # generated_at, degraded_critic, stratify fields

    @computed_field
    @property
    def overall_score(self) -> float:
        """Equal-weight mean of the two deterministic scores (plan omitted for NOT_BUILDABLE)."""
        parts = [self.spec_score] + ([self.plan_score] if self.buildable else [])
        return round(sum(parts) / len(parts), 3) if parts else 0.0


# ── the human-grading rubric (recorded ungraded; Tier-2 seam) ─────────────────
def default_rubric() -> list[RubricDimension]:
    return [
        RubricDimension(dimension="faithfulness",
                        guidance="Does the spec's goal/criteria reflect the artifact's actual core value, "
                                 "not a generic chatbot?"),
        RubricDimension(dimension="verifiability",
                        guidance="Is each criterion checkable by a deterministic pytest call to "
                                 "generate_reply (concrete, not vague)?"),
        RubricDimension(dimension="core_centrality",
                        guidance="Is the single core criterion the right load-bearing thing for this PoC?"),
        RubricDimension(dimension="scope_realism",
                        guidance="Is the scope a few-small-iterations PoC (not over-/under-scoped)?"),
        RubricDimension(dimension="plan_decomposition",
                        guidance="Does the plan order iterations sensibly (core first), one criterion each?"),
    ]


# ── deterministic scorers (pure; asserted in the fakes suite) ─────────────────
def score_spec(spec) -> list[Check]:
    """Structural invariants a good buildable spec must satisfy."""
    crits = list(spec.success_criteria)
    n = len(crits)
    cores = [c for c in crits if c.core]
    texts = [c.text.strip().lower() for c in crits]
    return [
        Check(name="criteria_count_3_6", passed=3 <= n <= 6, detail=f"{n} criteria"),
        Check(name="exactly_one_core", passed=len(cores) == 1, detail=f"{len(cores)} core"),
        Check(name="goal_nonempty", passed=bool(spec.goal.strip())),
        Check(name="demo_scenario_nonempty", passed=bool(spec.demo_scenario.strip())),
        Check(name="has_non_goals", passed=len(spec.non_goals) >= 1, detail=f"{len(spec.non_goals)}"),
        Check(name="all_met_by_test", passed=n > 0 and all(c.type == "met-by-test" for c in crits)),
        Check(name="criteria_text_substantive",
              passed=n > 0 and all(len(c.text.strip()) >= 12 for c in crits)),
        Check(name="no_duplicate_criteria", passed=n > 0 and len(set(texts)) == n),
    ]


def score_plan(spec, plan, cfg) -> list[Check]:
    """Invariants the deterministic core-first plan must satisfy (P2)."""
    its = list(plan.iterations)
    core = next((c for c in spec.success_criteria if c.core), None)
    cap = max(1, int(getattr(cfg, "max_iterations", 8)))
    return [
        Check(name="has_iterations", passed=len(its) >= 1, detail=f"{len(its)}"),
        Check(name="within_iteration_cap", passed=len(its) <= cap, detail=f"{len(its)} <= {cap}"),
        Check(name="core_first",
              passed=bool(its) and core is not None and core.text in its[0].acceptance),
        Check(name="all_have_acceptance", passed=bool(its) and all(it.acceptance for it in its)),
        Check(name="all_interface_pinned", passed=bool(its) and all(it.interface for it in its)),
    ]


def _frac(checks: list[Check]) -> float:
    return round(sum(1 for c in checks if c.passed) / len(checks), 3) if checks else 0.0


# ── the runner (P0→P1→P2 on a fixture, then score) ────────────────────────────
def run_spec_plan_eval(fixture_dir, cfg=None, template_name: str | None = None) -> EvalReport:
    """Run ingest+spec+plan on one fixture run-folder and score the products. NO broker / sandbox is
    constructed (P0/P1/P2 never touch it); the only external call is the architect LLM in P1 (real on
    the server, FAKE-monkeypatched in the fakes suite). All scratch goes to a temp dir, cleaned up."""
    from poc_foundry.config import load_config
    from poc_foundry.phases import Ctx, load_template, p0_ingest, p1_spec, p2_plan
    from poc_foundry.phases import pipeline as _pl
    from poc_foundry.state import BuildState

    cfg = cfg or load_config()
    template_name = template_name or cfg.default_template
    fixture = Path(fixture_dir)

    degraded = True
    try:
        from poc_foundry.models import same_family
        degraded = same_family("critic", "coder")
    except Exception:  # noqa: BLE001
        pass

    rep = EvalReport(fixture=str(fixture), template=template_name, rubric=default_rubric(),
                     metadata={"generated_at": datetime.now(timezone.utc).isoformat(),
                               "degraded_critic": bool(degraded),
                               "stratify": {"caps_hit": [], "respec": 0}})

    staging = Path(tempfile.mkdtemp(prefix="pf-eval-"))
    try:
        ctx = Ctx(cfg=cfg, build_id="eval", run_dir=fixture, template=load_template(template_name),
                  build_dir=staging, workspace_dir=staging, staging_dir=staging,
                  broker=None, coder=None)
        state = BuildState(build_id="eval")

        state = state.model_copy(update=p0_ingest(state, ctx))
        rep.artifact_id = state.artifact_id
        if state.status == "failed":
            rep.ok = False
            rep.error = f"ingest failed: {state.error}"
            return rep

        state = state.model_copy(update=p1_spec(state, ctx))
        spec = state.spec
        rep.buildable = bool(spec.buildable)
        rep.goal = spec.goal
        rep.num_criteria = len(spec.success_criteria)
        rep.spec_lint_caveats = _pl._lint_spec(spec)

        if not spec.buildable:
            rep.not_buildable_reasons = list(spec.not_buildable_reasons)
            rep.spec_checks = [Check(name="not_buildable_has_reason",
                                     passed=bool(spec.not_buildable_reasons))]
            rep.spec_score = _frac(rep.spec_checks)
            return rep

        rep.spec_checks = score_spec(spec)
        rep.spec_score = _frac(rep.spec_checks)

        state = state.model_copy(update=p2_plan(state, ctx))
        plan = state.plan
        rep.plan_iterations = len(plan.iterations)
        rep.plan_checks = score_plan(spec, plan, cfg)
        rep.plan_score = _frac(rep.plan_checks)
        return rep
    except Exception as e:  # noqa: BLE001 — a crash is an eval result, not a green-bar break
        rep.ok = False
        rep.error = f"{type(e).__name__}: {e}"
        return rep
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def run_evals(fixtures: list, cfg=None, template_name: str | None = None) -> list[EvalReport]:
    return [run_spec_plan_eval(f, cfg=cfg, template_name=template_name) for f in fixtures]


# ── reporting ─────────────────────────────────────────────────────────────────
def format_report(rep: EvalReport) -> str:
    lines = [f"## {Path(rep.fixture).name}  ({rep.artifact_id or '?'} → {rep.template})"]
    if not rep.ok:
        lines.append(f"  ERROR: {rep.error}")
        return "\n".join(lines)
    if not rep.buildable:
        lines.append(f"  NOT_BUILDABLE — {'; '.join(rep.not_buildable_reasons) or 'no reason'}")
        lines.append(f"  spec_score={rep.spec_score}")
        return "\n".join(lines)
    lines.append(f"  goal: {rep.goal!r}")
    lines.append(f"  spec_score={rep.spec_score}  plan_score={rep.plan_score}  "
                 f"overall={rep.overall_score}  (criteria={rep.num_criteria}, "
                 f"iterations={rep.plan_iterations})")
    for c in rep.spec_checks + rep.plan_checks:
        mark = "PASS" if c.passed else "FAIL"
        lines.append(f"    [{mark}] {c.name}" + (f" — {c.detail}" if c.detail else ""))
    if rep.spec_lint_caveats:
        lines += ["  spec-lint caveats:"] + [f"    - {x}" for x in rep.spec_lint_caveats]
    return "\n".join(lines)


def save_reports(reports: list[EvalReport], out_path: Path) -> None:
    import json
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "reports": [r.model_dump() for r in reports]}
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
