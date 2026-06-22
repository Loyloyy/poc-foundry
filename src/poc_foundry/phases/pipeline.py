"""The deterministic phase pipeline P0→P7 (design §5.3). Each ``pN_*(state, ctx) -> dict`` returns
the ``BuildState`` field updates for that node; ``graph.py`` wires them with LangGraph + a SQLite
checkpointer. Heavy deps (langchain) are lazy-imported inside the phases so this module stays
``py_compile``-able on the 3.10 dev box.

Phase map:
  P0 ingest    — load + validate the Stage-2 run folder (wraps ``ingest.load_run``).
  P1 spec      — architect → ``Spec`` (3–6 criteria, exactly one core; NOT_BUILDABLE possible).
  P2 plan      — deterministic single iteration for M1, interface pinned from the template.
  P3 scaffold  — stamp the gradio template, git-init/commit, GREEN smoke in a fresh VM.
  P4 iterate   — tester writes a RED-first staged test; the CoderEngine makes it green (staged VERIFY).
  P5 docs      — seeded RUN/README/AGENTS + a scribe DEMO.md (optional, fallback-safe).
  P6 cleanroom — fresh VM + fresh clone; run RUN.md install/test/demo blocks.
  P7 emit      — assemble + persist the ``PoCBuildArtifact`` + 00_INDEX.md + the workspace copy.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from poc_foundry import prompts
from poc_foundry.phases import integrity
from poc_foundry.phases.context import (
    Ctx,
    chown_to_builder,
    git,
    git_commit,
    git_diff,
    git_init,
    parse_run_blocks,
    stamp_template,
    staged_tests_mount,
    ws_mount,
)

_CODE_BLOCK = re.compile(r"```(?:python)?\n(?P<body>.*?)\n```", re.DOTALL)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_code(resp: str) -> str:
    m = _CODE_BLOCK.search(resp)
    return (m.group("body") if m else resp).strip() + "\n"


# ── P0 ingest ────────────────────────────────────────────────────────────────
def p0_ingest(state, ctx: Ctx) -> dict:
    from poc_foundry.ingest import clamp_confidence, load_run, validate_semantics

    rf = load_run(ctx.run_dir)
    clamped = clamp_confidence(rf.artifact)
    issues = validate_semantics(rf.artifact)
    errors = [i for i in issues if i.severity == "error"]
    warns = [i for i in issues if i.severity != "error"]
    ctx.run_folder = rf
    ctx.say(f"P0 ingest: loaded {rf.artifact.id} v{rf.version} "
            f"({len(errors)} errors, {len(warns)} warnings, {clamped} confidences clamped)")
    if errors:
        return {"phase": "ingest", "status": "failed",
                "error": "; ".join(str(e) for e in errors[:5]),
                "log": state.log + [f"P0 ingest FAILED: {len(errors)} contract errors"],
                "artifact_id": rf.artifact.id, "version": rf.version}
    return {"phase": "ingest", "artifact_id": rf.artifact.id, "run_id": rf.artifact.id,
            "version": rf.version,
            "caveats": state.caveats + [str(w) for w in warns],
            "log": state.log + [f"P0 ingest: {rf.artifact.id} v{rf.version} OK"]}


# ── P1 spec ──────────────────────────────────────────────────────────────────
def _normalize_spec(spec, template):
    spec.template = template.name
    crits = spec.success_criteria
    cores = [c for c in crits if c.core]
    if not cores and crits:
        crits[0].core = True
    elif len(cores) > 1:
        for c in cores[1:]:
            c.core = False
    for c in crits:
        if c.type not in ("met-by-test", "met-by-demo-evidence"):
            c.type = "met-by-test"
    return spec


def _lint_spec(spec) -> list[str]:
    out = []
    n = len(spec.success_criteria)
    if not (3 <= n <= 6):
        out.append(f"spec-lint: {n} success criteria (expected 3–6)")
    if sum(1 for c in spec.success_criteria if c.core) != 1:
        out.append("spec-lint: not exactly one core criterion")
    if not spec.goal.strip():
        out.append("spec-lint: empty goal")
    return out


def p1_spec(state, ctx: Ctx) -> dict:
    from poc_foundry.models import build_chat_model
    from poc_foundry.state import Spec

    art = ctx.run_folder.artifact
    svcs = getattr(ctx.template, "services", [])
    llm = build_chat_model("architect").with_structured_output(Spec)
    spec = llm.invoke([("system", prompts.spec_system(bool(svcs))),
                       ("human", prompts.spec_prompt(art, ctx.template.interface, svcs))])
    if isinstance(spec, dict):
        spec = Spec(**spec)
    spec = _normalize_spec(spec, ctx.template)
    lint = _lint_spec(spec)

    # A buildable spec with no testable criteria can't drive P4 (and would crash on criteria[0]) —
    # treat it honestly as NOT_BUILDABLE rather than emitting a vacuous build.
    if spec.buildable and not spec.success_criteria:
        spec.buildable = False
        spec.not_buildable_reasons = (spec.not_buildable_reasons or
                                      ["architect produced no testable success criteria"])

    if not spec.buildable:
        ctx.say(f"P1 spec: NOT_BUILDABLE — {'; '.join(spec.not_buildable_reasons) or 'no reason given'}")
        return {"phase": "spec", "spec": spec, "status": "not-buildable",
                "caveats": state.caveats + lint,
                "log": state.log + ["P1 spec: NOT_BUILDABLE"]}

    core = next((c.text for c in spec.success_criteria if c.core), "(none)")
    ctx.say(f"P1 spec: goal={spec.goal!r}; {len(spec.success_criteria)} criteria; core={core!r}")
    return {"phase": "spec", "spec": spec, "caveats": state.caveats + lint,
            "log": state.log + [f"P1 spec: {len(spec.success_criteria)} criteria"]}


# ── P2 plan (deterministic multi-iteration, core-first) ──────────────────────
# M2a S3: one small iteration per testable criterion, the CORE criterion first (it gates `done`).
# Deterministic rather than architect-decomposed by design (DEV_NOTES: the on-prem model is a weak
# self-planner — classifying given criteria into ordered iterations is reliable; open decomposition is
# not). The graph loops P4 over these iterations under a cumulative regression gate; later iterations
# whose criterion the earlier code already satisfies resolve as "met by existing implementation".
def p2_plan(state, ctx: Ctx) -> dict:
    from poc_foundry.state import IterationPlan, Plan

    spec = state.spec
    testable = [c for c in spec.success_criteria if c.type == "met-by-test"] or list(spec.success_criteria)
    core = next((c for c in testable if c.core), testable[0] if testable else None)
    ordered = ([core] + [c for c in testable if c is not core]) if core else testable
    cap = max(1, int(getattr(ctx.cfg, "max_iterations", 8)))
    ordered = ordered[:cap]

    iterations = [IterationPlan(goal=(spec.goal if i == 0 else c.text), acceptance=[c.text],
                                interface=ctx.template.interface,
                                files=list(ctx.template.editable_files))
                  for i, c in enumerate(ordered)]

    # Reset the iteration loop (first pass OR a replan re-entry) + clear staged tests on disk.
    staging_tests = ctx.staging_dir / "tests"
    if staging_tests.exists():
        shutil.rmtree(staging_tests)
    for c in spec.success_criteria:
        c.status = "pending"

    ctx.say(f"P2 plan: {len(iterations)} iteration(s) (core-first); interface pinned to {ctx.template.interface}")
    return {"phase": "plan", "plan": Plan(iterations=iterations), "spec": spec,
            "iteration": 0, "fix_count": 0, "staged_tests": [], "green_test_files": [],
            "authored_test_ids": [], "inventory_ok": True, "red_first_ok": True,
            "log": state.log + [f"P2 plan: {len(iterations)} iteration(s)"]}


# ── P3 scaffold (+ sibling services) ─────────────────────────────────────────
def _spin_services(ctx: Ctx) -> None:
    """Spin the template's declared sibling services (design §5.6) ONCE per build, on the per-build
    internal net, and record each IP as ``PF_SERVICE_<NAME>_HOST`` (reached BY IP — Kata has no
    name-DNS) for injection into every sandbox. The image/tag come from the HARNESS-FIXED vetted list
    (rule #8), never from the template's arbitrary text. Idempotent within a build (a replan re-enters
    P3 → skip if already up)."""
    if ctx.service_env or not getattr(ctx.template, "services", None):
        return
    vetted = getattr(ctx.cfg, "vetted_services", {}) or {}
    for decl in ctx.template.services:
        name = decl["name"]
        spec = vetted.get(decl.get("vetted", name))
        if not spec or str(spec.get("pinned_tag", "")).startswith("<"):
            raise RuntimeError(f"template service {name!r} → no pinned vetted service "
                               f"{decl.get('vetted', name)!r} in pipeline.yaml")
        svc = ctx.broker.create_service(image=spec["image"], name=name,
                                        pinned_tag=str(spec.get("pinned_tag")),
                                        env=spec.get("env"), ready_cmd=spec.get("ready_cmd"))
        ip = ctx.broker.service_ip(svc)
        ctx.services.append(svc)
        up = name.upper()
        ctx.service_env[f"PF_SERVICE_{up}_HOST"] = ip
        for k, v in (spec.get("env") or {}).items():            # e.g. PF_SERVICE_PG_POSTGRES_PASSWORD
            ctx.service_env[f"PF_SERVICE_{up}_{k}"] = str(v)
        ctx.say(f"P3 services: {name} ({spec['image']}:{spec.get('pinned_tag')}) ready @ {ip}")


def p3_scaffold(state, ctx: Ctx) -> dict:
    written = stamp_template(ctx.template, ctx.workspace_dir)
    git_init(ctx.workspace_dir)
    sha = git_commit(ctx.workspace_dir, f"scaffold: stamp {ctx.template.name} template (start-green)")
    chown_to_builder(ctx.workspace_dir)   # sandbox (uid 1000) must write caches into /work

    sbx = ctx.broker.create(mounts=[ws_mount(ctx.workspace_dir)], name="scaffold")
    try:
        res = sbx.exec(f"cd /work && python -m pytest {ctx.template.suite} -q", timeout_s=300)
    finally:
        sbx.destroy()

    if not res.ok:
        ctx.say("P3 scaffold: smoke RED — template suite did not pass in a fresh VM")
        return {"phase": "scaffold", "status": "failed", "scaffold_sha": sha,
                "error": "scaffold smoke failed:\n" + res.combined[-1500:],
                "log": state.log + ["P3 scaffold: smoke RED"]}

    _spin_services(ctx)   # sibling services (if any) up + IPs recorded for P4/P6 injection
    ctx.say(f"P3 scaffold: stamped {len(written)} files; smoke GREEN @ {sha}")
    return {"phase": "scaffold", "scaffold_sha": sha, "commit_sha": sha,
            "log": state.log + [f"P3 scaffold: GREEN @ {sha}"]}


# ── P4 iterate (red-first tester + CoderEngine + cumulative staged VERIFY) ───
def _tester_write(ctx: Ctx, criteria, goal: str, interface: str) -> str:
    from poc_foundry.models import chat_text
    resp = chat_text("tester", prompts.tester_prompt(criteria, goal, interface),
                     system=prompts.TESTER_SYSTEM)
    return _extract_code(resp)


def _verify_file(sbx, rel: str) -> bool:
    """Run ONE staged test file (red-first check on the iteration's NEW test)."""
    r = sbx.exec(f"cd /work && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work "
                 f"python -m pytest /staged/{rel} -q", timeout_s=300)
    return r.ok


def _ledger_collect(sbx) -> set[str]:
    """Inventory ledger (record): the test-function names the tester authored, collected in a
    pristine pre-coder state. ``/staged`` is RO so write no bytecode there."""
    r = sbx.exec("cd /work && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work "
                 "python -m pytest /staged --collect-only -q 2>/dev/null", timeout_s=120)
    return integrity.collected_names(r.combined)


def _ledger_junit(sbx) -> set[str]:
    """Inventory ledger (verify): an authoritative junit run; returns the *passed* test names. The
    junit file lands in writable /work; we ``cat`` it back so the gate needs no host-side fs coupling."""
    r = sbx.exec("cd /work && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work "
                 "python -m pytest /staged -q --junitxml=/work/.pf-junit.xml >/dev/null 2>&1; "
                 "cat /work/.pf-junit.xml 2>/dev/null", timeout_s=300)
    passed, _nonpassed = integrity.junit_passed_names(r.stdout or r.combined)
    return passed


def p4_iterate(state, ctx: Ctx) -> dict:
    from poc_foundry.artifact import IterationRecord

    spec, plan = state.spec, state.plan
    i = state.iteration
    it = plan.iterations[i]
    targets = [c for c in spec.success_criteria if c.text in it.acceptance]   # this iteration's criteria
    test_file = f"test_iter_{i}.py"
    strict_red_first = (i == 0)   # iteration 0 runs against the scaffold echo-stub — a real test MUST be red

    test_src = _tester_write(ctx, it.acceptance, it.goal, it.interface)
    staging_tests = ctx.staging_dir / "tests"
    staging_tests.mkdir(parents=True, exist_ok=True)     # ACCUMULATE: prior iterations' tests stay (cumulative suite)
    (staging_tests / test_file).write_text(test_src)
    chown_to_builder(staging_tests)

    base_sha = state.commit_sha or state.scaffold_sha or "HEAD"
    staged_names = set(state.staged_tests) | {test_file}
    incidents: list = []
    authored: set[str] = set()
    red_first_ok, inv_ok = True, True
    crit_status, it_status, attempts, note = "pending", "pending", 0, ""

    sbx = ctx.broker.create(
        mounts=[ws_mount(ctx.workspace_dir), staged_tests_mount(staging_tests)], name=f"iter{i}",
        env_extra=dict(ctx.service_env))               # sibling-service IPs (by IP — Kata DNS)
    try:
        authored = _ledger_collect(sbx)                  # cumulative authored set (all /staged)

        def verify():
            # DIFF SCANNER (per-attempt): a tampering edit fails the attempt BEFORE the test runs, so
            # a repeat trips the coder's forced-strategy-change (error-signature) path.
            incs = integrity.scan_diff(git_diff(ctx.workspace_dir, base_sha), staged_names)
            if integrity.blocking(incs):
                for inc in incs:
                    if str(inc) not in [str(x) for x in incidents]:
                        incidents.append(inc)
                return False, "INTEGRITY: " + "; ".join(str(x) for x in incs if x.severity == "high")
            # CUMULATIVE regression gate: ALL staged tests must pass (the new one + every prior one).
            r = sbx.exec("cd /work && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work "
                         "python -m pytest /staged -q", timeout_s=300)
            return r.ok, r.combined

        new_is_green = _verify_file(sbx, test_file)       # red-first probe on THIS iteration's new test
        if new_is_green and strict_red_first:
            # iteration 0 against the scaffold: a green test is tester-inadequacy, NOT a pass.
            red_first_ok = False
            note = "staged test passed against the scaffold (not red-first) — tester inadequacy"
            incidents.append(integrity.Incident("red-first", note, severity="high"))
            it_status, crit_status = "red-first-failed", "descoped"
            ctx.say(f"P4 iter{i}: RED-FIRST VIOLATION — staged test green against scaffold (flagged)")
        elif new_is_green:
            # later iteration: the criterion is already satisfied by prior code → met, no coder needed.
            it_status, crit_status = "met-existing", "met"
            note = "criterion already met by a prior iteration's implementation"
            ctx.say(f"P4 iter{i}: criterion already met by existing implementation (no code change)")
        else:
            res = ctx.coder.run(
                workspace=ctx.workspace_dir, goal=it.goal, editable_files=it.files,
                test_sources={test_file: test_src}, verify=verify,
                edit_format="whole", max_attempts=getattr(ctx.cfg, "max_fix_attempts", 3))
            attempts = res.attempts
            if integrity.blocking(incidents):
                it_status, crit_status = "incident", "descoped"
                note = "; ".join(str(x) for x in incidents if x.severity == "high")[:300]
                ctx.say(f"P4 iter{i}: INTEGRITY INCIDENT — {note}")
            elif res.passed:
                passed = _ledger_junit(sbx)               # cumulative collected ∧ passed ⊇ recorded
                inv_ok = integrity.inventory_ok(authored, passed) if authored else True
                if inv_ok:
                    it_status, crit_status = "green", "met"
                    ctx.say(f"P4 iter{i}: RED→GREEN in {attempts} attempt(s); edited {res.edited}; "
                            f"ledger OK ({len(authored)} test(s))")
                else:
                    gap = sorted(integrity.inventory_gap(authored, passed))
                    note = f"inventory ledger gap: {gap} recorded but not passed"
                    incidents.append(integrity.Incident("ledger-gap", note, severity="high"))
                    it_status, crit_status = "incident", "descoped"
                    ctx.say(f"P4 iter{i}: LEDGER FAIL — {note}")
            else:
                it_status, crit_status = "abandoned", "descoped"
                note = res.note or "coder did not reach green"
                ctx.say(f"P4 iter{i}: criterion DESCOPED after {attempts} attempt(s) ({note})")
    finally:
        sbx.destroy()

    met = crit_status == "met"
    sha = state.commit_sha
    if it_status == "green":
        sha = git_commit(ctx.workspace_dir, f"iterate {i}: {it.goal[:56]}")
    elif crit_status != "met":
        # SALVAGE (design §5.8): discard a failed iteration's uncommitted coder edits so the workspace
        # always reflects the last GREEN commit. Otherwise a later commit (P5 publish, or a met-existing
        # iteration's `git add -A`) sweeps broken code into HEAD → the clean-room clones it and fails,
        # tanking a build whose earlier iterations were sound. (`reset --hard` reverts TRACKED files
        # only; untracked .deps / caches are left intact.)
        git(ctx.workspace_dir, "reset", "--hard", "HEAD", check=False)
        ctx.say(f"P4 iter{i}: rolled the workspace back to the last green commit (failed edits discarded)")

    for c in targets:                                    # reflect the outcome on THIS iteration's criteria
        c.status = crit_status

    rec = IterationRecord(goal=it.goal, status=it_status, attempts=attempts,
                          tests_added=len(authored - set(state.authored_test_ids)))
    return {"phase": "iterate", "spec": spec, "commit_sha": sha,
            "iteration_records": state.iteration_records + [rec],
            "staged_tests": sorted(staged_names),
            "green_test_files": state.green_test_files + ([test_file] if met else []),
            "pending_test_src": test_src, "pending_criterion": it.goal,
            "authored_test_ids": sorted(set(state.authored_test_ids) | authored),
            "inventory_ok": state.inventory_ok and inv_ok,
            "red_first_ok": state.red_first_ok and red_first_ok,
            "incidents": state.incidents + [str(inc) for inc in incidents],
            "caveats": state.caveats + ([note] if note else []),
            "log": state.log + [f"P4 iter{i}: {it_status} (attempts={attempts})"]}


# ── critic gate + verdict ladder (design §5.4, §5.8) ─────────────────────────
def _critic_adequacy(ctx: Ctx, criterion: str, test_src: str):
    """Critic adequacy review: is passing this staged test trustworthy evidence for the criterion?
    Defaults to adequate if the critic endpoint is unreachable (the ledger/red-first/scanner walls
    still gate — the critic is an ADDED layer, never the sole gate)."""
    from poc_foundry.state import AdequacyReview
    try:
        from poc_foundry.models import build_chat_model
        llm = build_chat_model("critic").with_structured_output(AdequacyReview)
        rv = llm.invoke([("system", prompts.CRITIC_SYSTEM),
                         ("human", prompts.critic_adequacy_prompt(criterion, test_src, ctx.template.interface))])
        return AdequacyReview(**rv) if isinstance(rv, dict) else rv
    except Exception as e:  # noqa: BLE001 — critic is additive; never crash the build on its absence
        return AdequacyReview(adequate=True, reason=f"critic unavailable ({type(e).__name__}); defaulting adequate")


def p_critic(state, ctx: Ctx) -> dict:
    """Per-iteration adequacy review + the verdict ladder, then LOOP CONTROL. The disposition for the
    current iteration is one of accept / fix / respec / replan / descope (gated by ``fix_limit_k`` —
    or ``degraded_fix_limit_k`` in degraded mode — / ``replan_cap`` / ``respec_cap``); it is then
    mapped to a routing verdict: ``fix`` re-runs the SAME iteration; ``respec``/``replan`` reset and
    go back to P1/P2; ``accept``/``descope`` resolve the criterion and ADVANCE — ``next`` to the next
    iteration (fresh fix budget) or ``proceed`` to docs when the plan is exhausted. Cycles terminate
    via the capped counters (+ a graph recursion_limit)."""
    from poc_foundry.models import same_family

    cfg = ctx.cfg
    degraded = same_family("critic", "coder")
    K = cfg.degraded_fix_limit_k if degraded else cfg.fix_limit_k
    i = state.iteration
    it = state.plan.iterations[i] if state.plan else None
    plan_len = len(state.plan.iterations) if state.plan else 1

    last = state.iteration_records[-1] if state.iteration_records else None
    status = last.status if last else "abandoned"

    upd: dict = {"phase": "critic", "degraded_critic": degraded}
    advisory: str | None = None

    # ── disposition for THIS iteration ──
    if status in ("green", "met-existing"):
        if status == "met-existing":
            disposition, reason = "accept", "criterion met by existing implementation"
        else:
            review = _critic_adequacy(ctx, state.pending_criterion, state.pending_test_src)
            if review.adequate:
                disposition, reason = "accept", review.reason or "adequate"
            elif degraded:
                # A same-family critic can't INDEPENDENTLY certify adequacy (design §5.4) → ADVISORY
                # (recorded, non-blocking). The hard walls still gate; blocking adequacy returns with a
                # distinct frontier critic.
                disposition, reason = "accept", "adequacy concern recorded (degraded critic, non-blocking)"
                advisory = review.reason
            elif state.respec_count < cfg.respec_cap:
                disposition, reason = "respec", review.reason or "test inadequate / gameable"
            else:
                disposition, reason = "descope", review.reason or "test inadequate; respec cap reached"
    elif status == "incident":
        disposition, reason = "descope", "integrity incident — gamed iteration not rewarded"
    elif status == "red-first-failed":
        disposition, reason = (("fix", "red-first failure — re-author the test")
                               if state.fix_count < K else
                               ("descope", "red-first failures exhausted fix budget"))
    else:  # abandoned — coder did not reach green
        if state.fix_count < K:
            disposition, reason = "fix", "coder did not reach green — another iteration"
        elif state.replan_count < cfg.replan_cap:
            disposition, reason = "replan", "fix budget exhausted — replan remaining"
        else:
            disposition, reason = "descope", "fix + replan budgets exhausted"

    # ── map disposition → routing verdict (+ counters / advancement / records) ──
    if advisory is not None:
        upd["caveats"] = state.caveats + [f"critic (degraded, advisory): {advisory}"]

    if disposition == "fix":
        verdict = "fix"
        upd["fix_count"] = state.fix_count + 1
    elif disposition == "respec":
        verdict = "respec"
        upd["respec_count"] = state.respec_count + 1
    elif disposition == "replan":
        verdict = "replan"
        upd["replan_count"] = state.replan_count + 1
    else:  # accept | descope → criterion resolved; advance the loop
        if disposition == "descope":
            spec = state.spec
            for c in (spec.success_criteria if it is None else
                      [c for c in spec.success_criteria if c.text in it.acceptance]):
                c.status = "descoped"
            upd["spec"] = spec
            upd["descope_report"] = state.descope_report + [{
                "criterion": (it.acceptance[0] if it and it.acceptance else state.pending_criterion),
                "attempts_made": state.fix_count + 1, "why_failed": reason,
                "finish_path": "re-run with `refine` on a frontier `coder` endpoint, or finish by hand in OpenCode"}]
        if i + 1 < plan_len:
            verdict = "next"
            upd["iteration"] = i + 1
            upd["fix_count"] = 0          # each iteration gets a fresh fix budget
        else:
            verdict = "proceed"

    upd["verdict"] = verdict
    ctx.say(f"critic: iter{i} {disposition}→{verdict} ({reason}); degraded_critic={degraded}")
    upd["log"] = state.log + [f"critic iter{i}: {disposition}→{verdict} ({reason})"]
    return upd


# ── P5 docs ──────────────────────────────────────────────────────────────────
def _scribe_demo(ctx: Ctx, spec) -> str:
    try:
        from poc_foundry.models import chat_text
        crit = [c.text for c in spec.success_criteria]
        md = chat_text("scribe", prompts.scribe_demo_prompt(spec.goal, spec.demo_scenario, crit),
                       system=prompts.SCRIBE_SYSTEM)
        if md.strip():
            return md.strip() + "\n"
    except Exception:  # noqa: BLE001 — docs never fail a build
        pass
    crit = "\n".join(f"- {c.text}" for c in spec.success_criteria)
    return (f"# Demo\n\n{spec.goal}\n\n## Run\nFollow `RUN.md` to install, test, and launch "
            f"(`python app.py`), then open http://localhost:7860.\n\n## What to look for\n"
            f"{spec.demo_scenario}\n\n## Success criteria\n{crit}\n")


def _publish_tests(state, ctx: Ctx) -> int:
    """Publish the staged tests of MET iterations into the workspace ``tests/`` so the PoC ships with
    its verification AND the clean-room re-runs the cumulative criterion suite (not just the template
    smoke). Descoped/abandoned iterations' tests are NOT published — the clean-room must not fail on a
    criterion we honestly descoped. The coder never saw these as workspace files (staged RO during
    iteration); they are added only now, after the gates have run."""
    src = ctx.staging_dir / "tests"
    dest = ctx.workspace_dir / "tests"
    dest.mkdir(parents=True, exist_ok=True)
    published = 0
    for name in state.green_test_files:
        f = src / name
        if f.exists():
            shutil.copy2(f, dest / name)
            published += 1
    return published


def p5_docs(state, ctx: Ctx) -> dict:
    published = _publish_tests(state, ctx)
    (ctx.workspace_dir / "DEMO.md").write_text(_scribe_demo(ctx, state.spec))
    chown_to_builder(ctx.workspace_dir)   # published tests + DEMO must be writable/readable by uid 1000
    sha = git_commit(ctx.workspace_dir, f"docs: DEMO.md + publish {published} criterion test file(s)")
    ctx.say(f"P5 docs: DEMO.md + published {published} criterion test file(s) into tests/")
    return {"phase": "docs", "commit_sha": sha, "demo_quality": "thin",
            "log": state.log + [f"P5 docs: DEMO.md + {published} test file(s) published"]}


# ── P6 cleanroom (fresh VM + fresh clone) ────────────────────────────────────
def p6_cleanroom(state, ctx: Ctx) -> dict:
    clone = ctx.staging_dir / "cleanroom"
    if clone.exists():
        shutil.rmtree(clone)
    subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q",
                    str(ctx.workspace_dir), str(clone)], check=True)
    chown_to_builder(clone)   # the clean-room VM (uid 1000) installs deps + writes caches here

    blocks = parse_run_blocks((clone / "RUN.md").read_text())
    result = {"quickstart_ok": False, "suite_ok": False, "demo_ok": False}
    fails: list[str] = []
    sbx = ctx.broker.create(mounts=[ws_mount(clone)], name="cleanroom",
                            env_extra=dict(ctx.service_env))   # clean-room reaches the same sibling(s)
    try:
        if "install" in blocks:
            r = sbx.exec(f"cd /work && {blocks['install']}", timeout_s=1800)
            result["quickstart_ok"] = r.ok
            if not r.ok:
                fails.append("install:\n" + r.combined[-700:])
        if "test" in blocks and result["quickstart_ok"]:
            r = sbx.exec(f"cd /work && {blocks['test']}", timeout_s=600)
            result["suite_ok"] = r.ok
            if not r.ok:
                fails.append("test:\n" + r.combined[-700:])
        if "demo" in blocks and result["quickstart_ok"]:
            r = sbx.exec(f"cd /work && {blocks['demo']}", timeout_s=300)
            result["demo_ok"] = r.ok
            if not r.ok:
                fails.append("demo:\n" + r.combined[-700:])
    finally:
        sbx.destroy()

    for f in fails:
        ctx.say("P6 cleanroom FAILED step — " + f)
    ctx.say(f"P6 cleanroom: install={result['quickstart_ok']} test={result['suite_ok']} "
            f"demo={result['demo_ok']}")
    return {"phase": "cleanroom", "cleanroom": result,
            "caveats": state.caveats + [f"cleanroom {f.splitlines()[0]}" for f in fails],
            "tests_total": max(state.tests_total, len(state.staged_tests) + 1),
            "tests_passing": (len(state.staged_tests) + 1) if result["suite_ok"] else state.tests_passing,
            "log": state.log + [f"P6 cleanroom: suite_ok={result['suite_ok']}"]}


# ── P7 emit ──────────────────────────────────────────────────────────────────
def _has_blocking_incident(state) -> bool:
    return any(s.startswith("[high]") for s in state.incidents)


def _trustworthy(state) -> bool:
    """The M2a integrity gate: the build's success claim is only trustworthy if the inventory ledger
    held, every accepted iteration was red-first, and no high-severity integrity incident fired."""
    return bool(state.inventory_ok) and bool(state.red_first_ok) and not _has_blocking_incident(state)


def _final_status(state) -> str:
    if state.spec and not state.spec.buildable:
        return "not-buildable"
    if state.status == "failed":
        return "failed"
    core_met = any(c.core and c.status == "met" for c in (state.spec.success_criteria if state.spec else []))
    if core_met and state.cleanroom.get("suite_ok") and _trustworthy(state):
        return "done"
    return "incomplete"


def p7_emit(state, ctx: Ctx) -> dict:
    from poc_foundry.artifact import (
        Budget,
        CleanroomResult,
        DescopeItem,
        FinalVerdict,
        PoCBuildArtifact,
        SecurityInfo,
        ServiceRef,
        SourceArtifact,
        StackItem,
        TemplateRef,
        TestsSummary,
        save,
    )

    vetted = getattr(ctx.cfg, "vetted_services", {}) or {}
    services = [ServiceRef(name=d["name"],
                           image=(vetted.get(d.get("vetted", d["name"]), {}) or {}).get("image", ""),
                           pinned_tag=(vetted.get(d.get("vetted", d["name"]), {}) or {}).get("pinned_tag"))
                for d in getattr(ctx.template, "services", [])]

    art = ctx.run_folder.artifact if ctx.run_folder else None
    status = _final_status(state)
    core_met = bool(state.spec) and any(c.core and c.status == "met" for c in state.spec.success_criteria)
    demonstrates = ("yes" if (core_met and state.cleanroom.get("suite_ok") and _trustworthy(state))
                    else ("partial" if core_met else "no"))

    allowlist = []
    try:
        import yaml  # tolerated-absent
        y = yaml.safe_load((Path(ctx.cfg.builds_dir).parent / "config" / "pipeline.yaml").read_text())
        allowlist = list((y.get("egress_allowlist", {}) or {}).get("hosts", []))
    except Exception:  # noqa: BLE001
        allowlist = []

    pa = PoCBuildArtifact(
        id=ctx.build_id,
        generated_at=_now_iso(),
        source_artifact=SourceArtifact(id=(art.id if art else state.artifact_id),
                                       version=(art.version if art else 1)),
        driver=state.driver,
        spec_summary=(state.spec.goal if state.spec else ""),
        success_criteria=(state.spec.success_criteria if state.spec else []),
        iterations=state.iteration_records,
        tests=TestsSummary(total=state.tests_total, passing=state.tests_passing,
                           inventory_ok=bool(state.inventory_ok)),
        cleanroom=CleanroomResult(**{k: bool(v) for k, v in state.cleanroom.items()}),
        demo_quality=state.demo_quality,
        final_verdict=FinalVerdict(demonstrates_core_value=demonstrates),
        descope_report=[DescopeItem(criterion=d.get("criterion", ""),
                                    attempts_made=int(d.get("attempts_made", 0)),
                                    why_failed=d.get("why_failed", ""),
                                    finish_path=d.get("finish_path", ""))
                        for d in state.descope_report],
        stack=[StackItem(layer=s.get("layer", ""), choice=s.get("choice", ""),
                         pinned_version=s.get("pinned_version")) for s in ctx.template.stack],
        template=TemplateRef(name=ctx.template.name, version=ctx.template.version),
        services=services,
        licenses=[ctx.template.license] if ctx.template.license else [],
        security=SecurityInfo(sandbox="kata", egress_allowlist=allowlist,
                              incidents=list(state.incidents),
                              degraded_critic=bool(state.degraded_critic)),
        budget=Budget(),
        caveats=state.caveats,
        status=status,
    )

    build_dir = Path(state.build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)
    save(pa, build_dir)

    # copy the standalone PoC workspace (with .git history) into the build folder
    dest_ws = build_dir / "workspace"
    if dest_ws.exists():
        shutil.rmtree(dest_ws)
    shutil.copytree(ctx.workspace_dir, dest_ws)

    # egress evidence + progress + index + report
    logs = build_dir / "logs"
    logs.mkdir(exist_ok=True)
    try:
        (logs / "egress.log").write_text(ctx.broker.proxy_log())
    except Exception:  # noqa: BLE001
        pass
    (build_dir / "PROGRESS.md").write_text(
        "# Progress\n\n" + "\n".join(f"- {ln}" for ln in state.log) + "\n")
    report = _report_md(state, ctx, pa)
    (build_dir / "report.md").write_text(report)
    (build_dir / "00_INDEX.md").write_text(_index_md(pa, build_dir))

    ctx.say(f"P7 emit: status={status}; artifact + workspace written to {build_dir}")
    return {"phase": "emit", "status": status, "demonstrates_core_value": demonstrates,
            "log": state.log + [f"P7 emit: {status}"]}


def _report_md(state, ctx: Ctx, pa) -> str:
    lines = [f"# Build report — {pa.id}", "", f"**Status:** {pa.status}  ",
             f"**Demonstrates core value:** {pa.final_verdict.demonstrates_core_value}  ",
             f"**Source artifact:** {pa.source_artifact.id}", "", "## Phase trace", ""]
    lines += [f"- {ln}" for ln in ctx.report]
    lines += ["", "## Success criteria", ""]
    for c in pa.success_criteria:
        tag = " (core)" if c.core else ""
        lines.append(f"- [{c.status}] {c.text}{tag}")
    lines += ["", "## Integrity walls (§5.5)", "",
              f"- inventory ledger: {'OK' if pa.tests.inventory_ok else 'FAIL'} "
              f"(recorded {len(state.authored_test_ids)} test id(s))",
              f"- red-first: {'OK' if state.red_first_ok else 'VIOLATION'}",
              f"- incidents: {len(pa.security.incidents)}"]
    lines += [f"  - {i}" for i in pa.security.incidents]
    lines += ["", "## Critic gate (§5.4)", "",
              f"- last verdict: {state.verdict or 'pass'}",
              f"- degraded_critic: {pa.security.degraded_critic} "
              f"(fix budget K={ctx.cfg.degraded_fix_limit_k if pa.security.degraded_critic else ctx.cfg.fix_limit_k}; "
              f"fixes={state.fix_count}, respecs={state.respec_count}, replans={state.replan_count})"]
    if pa.descope_report:
        lines += ["", "## Descope report", ""]
        for d in pa.descope_report:
            lines.append(f"- **{d.criterion}** — {d.why_failed} (after {d.attempts_made} attempt(s)); "
                         f"finish: {d.finish_path}")
    lines += ["", "## Clean-room", "",
              f"- install: {pa.cleanroom.quickstart_ok}",
              f"- suite: {pa.cleanroom.suite_ok}",
              f"- demo: {pa.cleanroom.demo_ok}"]
    if pa.caveats:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in pa.caveats]
    return "\n".join(lines) + "\n"


def _index_md(pa, build_dir: Path) -> str:
    return (f"# {pa.id}\n\n"
            f"Stage-3 PoC build from `{pa.source_artifact.id}` — **{pa.status}**.\n\n"
            f"- `v{pa.version:02d}.json` — the PoCBuildArtifact (this build's output contract)\n"
            f"- `workspace/` — the standalone, runnable PoC (see `workspace/RUN.md`)\n"
            f"- `report.md` — human-readable build report\n"
            f"- `PROGRESS.md` — phase trace\n"
            f"- `logs/egress.log` — proxy CONNECT log (egress security evidence)\n")
