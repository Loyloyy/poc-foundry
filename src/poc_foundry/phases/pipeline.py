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

import os
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

# Match a fenced block tolerantly: the opening fence may carry ANY info string (``python``, ``py``,
# ``python3``, or a trailing space — ` ```python `), and the closing fence need not be preceded by a
# newline. The old strict ``(?:python)?\n`` form silently failed on those variants and fell through to
# returning the RAW response WITH the fence → a SyntaxError in the staged test (pilot DECISIONS #34).
_CODE_BLOCK = re.compile(r"```[^\n]*\n(?P<body>.*?)```", re.DOTALL)
_FENCE_LINE = re.compile(r"^\s*```[A-Za-z0-9_+-]*\s*$")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_code(resp: str) -> str:
    m = _CODE_BLOCK.search(resp)
    body = m.group("body") if m else resp
    # Defensive belt-and-suspenders: even if the fence regex did not match (a malformed/half-open
    # fence), NEVER let a bare ```/```python line reach the staged file — it would be a SyntaxError.
    body = "\n".join(ln for ln in body.splitlines() if not _FENCE_LINE.match(ln))
    return body.strip() + "\n"


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

    from poc_foundry import tracing

    art = ctx.run_folder.artifact
    svcs = getattr(ctx.template, "services", [])
    # 8000 (not the 4000 default) for headroom: a reasoning model spends completion tokens on its
    # chain-of-thought before emitting the structured Spec JSON; at 4000 the JSON can be truncated mid-
    # object → openai LengthFinishReasonError. The critic call uses the same headroom for the same reason.
    llm = build_chat_model("architect", max_tokens=8000).with_structured_output(Spec)
    with tracing.span("spec", artifact=art.id):
        spec = llm.invoke([("system", prompts.spec_system(bool(svcs))),
                           ("human", prompts.spec_prompt(art, ctx.template.interface, svcs,
                                                         knowledge=getattr(ctx.template, "knowledge", "")))])
    if isinstance(spec, dict):
        spec = Spec(**spec)
    spec = _normalize_spec(spec, ctx.template)
    # carry the artifact's open questions onto the spec → the research-on-gaps rung (a) (design §5.8)
    if not spec.open_questions:
        spec.open_questions = list(getattr(art, "open_questions", []) or [])
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
            "replan_mode": False,   # a new/respec'd spec = fresh criteria → re-scaffold + strict red-first
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
                                files=list(ctx.template.editable_files),
                                # open questions matter most at the baseline → attach to iteration 0 (S4 rung a)
                                research_questions=(list(spec.open_questions) if i == 0 else []))
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
    # M6 replan-waste fix: a `replan` re-enters P3 on the SAME spec. PRESERVE the workspace (the last-green
    # commit already holds the met iterations' code) instead of re-stamping the stub — so met criteria
    # fast-path via met-existing (skip the coder) and only the unmet tail is re-attacked. Services are
    # already up (idempotent); the smoke already passed this build. A respec resets replan_mode → re-stamp.
    if state.replan_mode and state.scaffold_sha:
        _spin_services(ctx)
        ctx.say(f"P3 scaffold: REPLAN — preserving workspace, no re-stamp (met criteria fast-path via "
                f"met-existing) @ {state.commit_sha or state.scaffold_sha}")
        return {"phase": "scaffold", "scaffold_sha": state.scaffold_sha,
                "commit_sha": state.commit_sha or state.scaffold_sha,
                "log": state.log + ["P3 scaffold: replan — workspace preserved"]}

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
def _tester_write(ctx: Ctx, criteria, goal: str, interface: str, research: str = "",
                  diagnosis: str = "") -> str:
    from poc_foundry.models import chat_text
    prompt = prompts.tester_prompt(criteria, goal, interface, research=research,
                                   knowledge=getattr(ctx.template, "knowledge", ""),
                                   diagnosis=diagnosis)
    # Defense-in-depth: a staged test that does not even PARSE (a stray fence, OR — the run-#8 cause —
    # the tester's output TRUNCATED by max_tokens: a reasoning model spends its budget on chain-of-thought
    # before the test, so a long shortcut-proof test gets cut mid-line → "'(' was never closed" → the test
    # is uncollectable and the (often CORRECT) coder is blamed). Fix: a GENEROUS token budget (reasoning
    # headroom, like the critic's 8000) + up to 3 parse-retries (the tester is non-deterministic).
    code = ""
    for attempt in range(3):
        code = _extract_code(chat_text("tester", prompt, system=prompts.TESTER_SYSTEM, max_tokens=8000))
        try:
            compile(code, "<staged-test>", "exec")
            return code
        except SyntaxError:
            if attempt < 2:
                ctx.say(f"P4: authored test did not parse — re-authoring ({attempt + 1}/2)")
    return code   # best-effort; red-first / collect-only will still surface a persistently broken test


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


def _interface_defs(interface: str, extra=None) -> list[str]:
    """The names a coder edit MUST keep defining — the template's interface function (parsed from e.g.
    ``core.generate_reply(...) -> str`` → ``generate_reply``) plus any template-declared exports. Fed to
    the coder's interface-preservation gate so it can't amputate the scaffold."""
    defs = list(extra or [])
    name = (interface or "").split("(")[0].strip().rpartition(".")[2]
    if name:
        defs.append(name)
    return sorted(set(defs))


def _looks_like_error(text: str) -> bool:
    """Does ``text`` carry a real code-error signal worth a web lookup? Guards the research rung from
    googling a harness META-message (e.g. 'fix-attempt cap reached' → it returned login-lockout help).
    A genuine pytest/traceback failure trips at least one of these tokens."""
    t = (text or "").lower()
    return any(tok in t for tok in
               ("error", "assert", "traceback", "exception", "failed", "no module", "not defined"))


def _persist_iter_forensics(ctx: Ctx, i: int, test_src: str, coder_response: str,
                            verify_output: str, note: str) -> None:
    """Forensic trail for a struggling/descoped iteration. The workspace is rolled back on descope, so
    without this a descoped iteration leaves NO evidence (only an ungrounded lesson). Writes the staged
    test the coder fought + its raw last response + the real verify output. Best-effort; never crashes."""
    try:
        dest = Path(ctx.build_dir) / "iterations" / str(i)
        dest.mkdir(parents=True, exist_ok=True)
        if test_src:
            (dest / "staged_test.py").write_text(test_src)
        (dest / "incident.txt").write_text(
            f"# Iteration {i} incident\n\nnote: {note}\n\n"
            f"## verify output (last)\n{(verify_output or '(no verify ran — no edit ever applied)')[:6000]}\n\n"
            f"## coder last response (raw)\n{(coder_response or '(none captured)')[:8000]}\n")
        ctx.say(f"P4 iter{i}: forensics → iterations/{i}/incident.txt")
    except OSError:
        pass


def _reflect(ctx: Ctx, i: int, it, it_status: str, attempts: int, incidents: list, note: str,
             detail: str = "") -> None:
    """Tier-1 reflection (design §5.3 P4.f, §5.9): on a STRUGGLING iteration, interrogate the coder
    ("what would have helped?") and write ``builds/<id>/iterations/<i>/lessons.md`` grounded in the
    concrete incident. Skipped when the iteration was clean (no incident, first-try green) — a lesson
    MUST cite a real struggle. Best-effort: never fails a build (a ``BudgetExceeded`` is a
    ``BaseException`` and still escapes to the salvage path, as everywhere)."""
    # PF_FORCE_REFLECT=1 is a deterministic validation hook (mirrors PF_STOP_AT_NODE): exercise the
    # reflection seam on a clean fast build without needing a genuinely struggling (slow) one.
    forced = os.environ.get("PF_FORCE_REFLECT") == "1"
    struggled = (forced or attempts >= 2 or bool(incidents)
                 or it_status in ("abandoned", "incident", "red-first-failed"))
    if not struggled:
        return
    # Prefer the REAL failure (the coder's last verify output / raw response) over the harness
    # meta-note — an ungrounded incident ('fix-attempt cap reached') makes the lesson a guess.
    incident = (detail.strip() or "; ".join(str(x) for x in incidents) or note
                or ("(forced reflection — validation hook)" if forced else "(repeated failures)"))
    try:
        from poc_foundry.models import chat_text
        criterion = it.acceptance[0] if it.acceptance else it.goal
        body = chat_text("coder",
                         prompts.reflection_prompt(it.goal, criterion, it_status, attempts, incident),
                         system=prompts.REFLECTION_SYSTEM)
    except Exception:  # noqa: BLE001 — bookkeeping; BudgetExceeded (BaseException) still propagates
        return
    if not body.strip():
        return
    try:   # advisory bookkeeping — a write failure must never crash the build
        dest = Path(ctx.build_dir) / "iterations" / str(i)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "lessons.md").write_text(
            f"# Lessons — iteration {i} ({it_status}, {attempts} attempt(s))\n\n"
            f"**Concrete incident:** {incident[:600]}\n\n"
            f"## What would have helped\n{body.strip()}\n")
        ctx.say(f"P4 iter{i}: reflection → iterations/{i}/lessons.md")
    except OSError:
        pass


# ── refine staging (M4 S1) ───────────────────────────────────────────────────
# Refine re-attacks several descoped criteria, but P4's cumulative gate runs ALL of ``/staged`` — so a
# not-yet-worked backlog test sitting red would block the criterion currently being made green. Refine
# therefore parks the backlog tests in ``staging/refine_pending/`` (done by ``core``) and stages each
# one INTO the active ``/staged`` set only when its iteration runs, removing it again if it stays red.
def _refine_stage_in(ctx: Ctx, test_file: str) -> None:
    """Copy this iteration's parked (already-authored, red-first) backlog test into the active /staged
    set so it joins the cumulative gate now — reuse, NOT re-author. No-op if it isn't parked."""
    parked = ctx.staging_dir / "refine_pending" / test_file
    dest = ctx.staging_dir / "tests" / test_file
    if parked.exists() and not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(parked, dest)


def _refine_park_out(ctx: Ctx, test_file: str) -> None:
    """A refined criterion stayed red → drop its test from the active /staged set so it does not poison
    later backlog iterations' cumulative gate. The authored copy survives in ``refine_pending/``."""
    dest = ctx.staging_dir / "tests" / test_file
    if dest.exists():
        dest.unlink()


# ── research-on-gaps (design §5.3 P4.a, §5.8) ────────────────────────────────
def _maybe_research(ctx: Ctx, state, i: int, it, fresh: bool):
    """Run the targeted research rung if triggered. Returns ``(research_md, incidents, calls, upd)``:
      • trigger (b) STUCK — ``state.research_pending`` set by p_critic after a repeated-error abandon;
      • trigger (a) OPEN QUESTIONS — a fresh iteration carrying ``it.research_questions``.
    Tolerated-absent: SearXNG/deps down → empty md + a caveat, never a crash. Writes a cited
    ``iterations/<i>/research.md``; an injection tripwire hit becomes a medium incident."""
    from poc_foundry import research
    from poc_foundry.phases import integrity

    if state.research_pending and state.research_error:
        query, kind = state.research_error, "error"
        if not _looks_like_error(query):
            # the 'error' is a harness meta-message (e.g. a cap notice or a non-applied-edit), not a
            # real code error — a web lookup can only return noise. Clear the request and mark the
            # research rung consumed for this iteration (so the re-author rung becomes reachable).
            ctx.say(f"P4 iter{i}: research skipped — no real error signal to look up")
            return "", [], 0, {"research_pending": False, "last_research_iteration": i}
    elif fresh and it.research_questions and state.last_research_iteration != i:
        query, kind = "; ".join(it.research_questions[:5]), "questions"
    else:
        return "", [], 0, {}

    cfg = ctx.cfg
    allow = list(getattr(cfg, "research_hosts", []) or [])
    rr = research.run_research(query=query, kind=kind, allow_hosts=allow,
                               max_results=int(getattr(cfg, "max_research_results", 4) or 4))
    incidents: list = []
    if rr.injection_hits:
        incidents.append(integrity.Incident(
            "research-injection",
            "prompt-injection markers in fetched content: " + ", ".join(rr.injection_hits[:3]),
            severity="medium"))
        from poc_foundry import tracing
        tracing.event("research.injection", iteration=i, markers="; ".join(rr.injection_hits[:5])[:300])
    if rr.markdown:
        try:   # advisory — never crash the build on a write failure
            dest = Path(ctx.build_dir) / "iterations" / str(i)
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "research.md").write_text(rr.markdown)
        except OSError:
            pass
        ctx.say(f"P4 iter{i}: research ({kind}) → iterations/{i}/research.md "
                f"({len(rr.citations)} source(s), {rr.calls} call(s)"
                f"{'; INJECTION FLAGGED' if rr.injection_hits else ''})")
    elif rr.ran:
        ctx.say(f"P4 iter{i}: research ({kind}) — no usable sources ({rr.note})")

    upd = {"last_research_iteration": i, "research_pending": False,
           "research_calls": state.research_calls + rr.calls}
    return rr.markdown, incidents, rr.calls, upd


def p4_iterate(state, ctx: Ctx) -> dict:
    from poc_foundry import playbooks
    from poc_foundry.artifact import IterationRecord
    from poc_foundry.models import METER

    METER.begin_iteration()   # fresh per-iteration LLM-call + wall-clock budget (M2b S2)
    spec, plan = state.spec, state.plan
    i = state.iteration
    it = plan.iterations[i]
    targets = [c for c in spec.success_criteria if c.text in it.acceptance]   # this iteration's criteria
    # refine (M4 S1) pins the criterion's original staged-test filename so a filtered backlog plan REUSES
    # the already-authored red-first test instead of re-numbering (and re-authoring) it.
    test_file = it.test_file or f"test_iter_{i}.py"
    # iteration 0 runs against the scaffold echo-stub — a real test MUST be red. In refine the workspace
    # already holds real code (we're past scaffold), so a green probe means "met by existing code", not
    # tester inadequacy → the strict-red-first wall does not apply.
    # iter0's strict red-first assumes a PRISTINE scaffold stub → a green test = tester inadequacy. Disable
    # it ONLY when the workspace already holds REAL committed code (a replan that preserved a prior plan's
    # met code, or a refine): there a green iter0 test = "met by existing code" (met-existing), not a
    # violation. Key off actual committed code (commit != scaffold), NOT merely replan_mode — a replan whose
    # workspace is still the bare stub (core failed first) must KEEP the gate to catch a weak test.
    workspace_has_code = bool(state.commit_sha) and state.commit_sha != state.scaffold_sha
    strict_red_first = (i == 0) and not state.refine_mode and not workspace_has_code

    staging_tests = ctx.staging_dir / "tests"
    staging_tests.mkdir(parents=True, exist_ok=True)     # ACCUMULATE: prior iterations' tests stay (cumulative suite)
    if state.refine_mode:
        _refine_stage_in(ctx, test_file)                 # bring THIS backlog test into the active gate (reuse)
    test_path = staging_tests / test_file
    fresh = not test_path.exists()

    # research-on-gaps rung (design §5.8): trigger (b) a prior stuck-abandon, or (a) open questions on a
    # fresh iteration. Produces cited research notes injected into the tester (fresh) + the coder.
    research_md, research_incidents, _research_calls, research_upd = _maybe_research(ctx, state, i, it, fresh)

    reauthor = state.reauthor_pending          # M6: the critic flagged the staged test as possibly flawed
    if not fresh and not reauthor:
        # FIX-RETRY of this iteration (the critic granted another go): REUSE the same staged test —
        # don't re-author it. Saves a tester call and keeps the coder's target STABLE so it converges
        # instead of chasing a freshly-generated (possibly different) test each round. The workspace was
        # already rolled back to the last green commit, so the coder retries cleanly. (P2 clears the
        # staging dir on a fresh plan, so a reused index only ever means a same-plan fix-retry.)
        test_src = test_path.read_text()
        ctx.say(f"P4 iter{i}: reusing the staged test (fix-retry — no re-author)")
    else:
        if reauthor:   # re-author rung: the prior test was inadequate (too strict/buggy OR too weak/gameable)
            ctx.say(f"P4 iter{i}: re-authoring the staged test (prior test judged inadequate)")
        test_src = _tester_write(ctx, it.acceptance, it.goal, it.interface, research=research_md,
                                 diagnosis=(state.reauthor_reason if reauthor else ""))
        test_path.write_text(test_src)
    chown_to_builder(staging_tests)

    base_sha = state.commit_sha or state.scaffold_sha or "HEAD"
    staged_names = set(state.staged_tests) | {test_file}
    incidents: list = list(research_incidents)
    authored: set[str] = set()
    red_first_ok, inv_ok = True, True
    crit_status, it_status, attempts, note = "pending", "pending", 0, ""
    coder_stuck, coder_error = False, ""   # → the research rung (b): a repeated-error abandon
    coder_last_output, coder_last_response = "", ""   # forensics for a struggling/descoped iteration

    sbx = ctx.broker.create(
        mounts=[ws_mount(ctx.workspace_dir), staged_tests_mount(staging_tests)], name=f"iter{i}",
        env_extra=dict(ctx.service_env))               # sibling-service IPs (by IP — Kata DNS)
    try:
        authored = _ledger_collect(sbx)                  # cumulative authored set (all /staged)

        def verify():
            from poc_foundry import tracing
            # DIFF SCANNER (per-attempt): a tampering edit fails the attempt BEFORE the test runs, so
            # a repeat trips the coder's forced-strategy-change (error-signature) path.
            with tracing.span("gate.diff-scan", iteration=i):
                incs = integrity.scan_diff(git_diff(ctx.workspace_dir, base_sha), staged_names)
            if integrity.blocking(incs):
                for inc in incs:
                    if str(inc) not in [str(x) for x in incidents]:
                        incidents.append(inc)
                tracing.event("gate.incident", iteration=i, kind="diff-scan",
                              detail="; ".join(str(x) for x in incs if x.severity == "high")[:300])
                return False, "INTEGRITY: " + "; ".join(str(x) for x in incs if x.severity == "high")
            # CUMULATIVE regression gate: ALL staged tests must pass (the new one + every prior one).
            with tracing.span("iterate.verify", iteration=i) as _sp:
                r = sbx.exec("cd /work && PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/work "
                             "python -m pytest /staged -q", timeout_s=300)
                _sp.update(output={"ok": r.ok, "tail": r.combined[-400:]})
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
            guidance = playbooks.playbook_section("coder")   # curated building+gotchas + matching hints
            if research_md:                                  # advisory research notes (before "# Task")
                guidance = ((guidance + "\n\n" if guidance else "")
                            + "# Research notes (advisory, from fetched sources)\n" + research_md)
            res = ctx.coder.run(
                workspace=ctx.workspace_dir, goal=it.goal, editable_files=it.files,
                test_sources={test_file: test_src}, verify=verify,
                edit_format="whole", max_attempts=getattr(ctx.cfg, "max_fix_attempts", 3),
                playbook=guidance,
                required_defs=_interface_defs(ctx.template.interface,
                                              getattr(ctx.template, "required_exports", None)))
            attempts = res.attempts
            coder_last_output, coder_last_response = res.last_output, res.last_response
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
                    from poc_foundry import tracing
                    tracing.event("gate.incident", iteration=i, kind="ledger-gap", detail=note[:300])
                    it_status, crit_status = "incident", "descoped"
                    ctx.say(f"P4 iter{i}: LEDGER FAIL — {note}")
            else:
                it_status, crit_status = "abandoned", "descoped"
                note = res.note or "coder did not reach green"
                # STUCK = a repeated error signature (≥ stuck_research_after, default 2 → a dup in the
                # signature trail) OR the deterministic PF_FORCE_RESEARCH validation hook. Drives the
                # research rung (b): p_critic escalates to targeted research before descope/replan.
                dup = len(res.signatures) != len(set(res.signatures))
                coder_stuck = dup or os.environ.get("PF_FORCE_RESEARCH") == "1"
                coder_error = (res.last_output or res.last_response or note)[-1200:]
                ctx.say(f"P4 iter{i}: criterion DESCOPED after {attempts} attempt(s) "
                        f"({note}{'; STUCK' if coder_stuck else ''})")
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

    if state.refine_mode and crit_status != "met":
        _refine_park_out(ctx, test_file)   # still red → keep it out of later backlog iterations' gate

    for c in targets:                                    # reflect the outcome on THIS iteration's criteria
        c.status = crit_status

    if it_status not in ("green", "met-existing"):       # forensic trail for a struggling/descoped iter
        _persist_iter_forensics(ctx, i, test_src, coder_last_response, coder_last_output, note)
    # Tier-1 lessons grounded in the REAL failure (the coder's verify output / raw response), not the
    # bare meta-note — so the lesson diagnoses instead of guessing.
    _reflect(ctx, i, it, it_status, attempts, incidents, note,
             detail=(coder_last_output or coder_last_response))

    rec = IterationRecord(goal=it.goal, status=it_status, attempts=attempts,
                          tests_added=len(authored - set(state.authored_test_ids)))
    # M7 Layer-2: record which HIGH incidents fired this iteration and the criteria they targeted, so P7
    # can weigh a single-contained-and-superseded incident against the trust-cap (DECISIONS #52).
    high_this = [inc for inc in incidents if getattr(inc, "severity", "high") == "high"]
    out = {"phase": "iterate", "spec": spec, "commit_sha": sha,
           "iteration_records": state.iteration_records + [rec],
           "staged_tests": sorted(staged_names),
           "green_test_files": state.green_test_files + ([test_file] if met else []),
           "pending_test_src": test_src, "pending_criterion": it.goal,
           "authored_test_ids": sorted(set(state.authored_test_ids) | authored),
           "inventory_ok": state.inventory_ok and inv_ok,
           "red_first_ok": state.red_first_ok and red_first_ok,
           "had_high_incident": state.had_high_incident or bool(high_this),
           "high_incident_kinds": state.high_incident_kinds + [inc.kind for inc in high_this],
           "high_incident_criteria": state.high_incident_criteria
                                     + ([c.text for c in targets] if high_this else []),
           "incidents": state.incidents + [str(inc) for inc in incidents],
           "caveats": state.caveats + ([note] if note else []),
           # research-rung bookkeeping: stuck signal for p_critic (b) + clear any pending request
           "last_coder_stuck": coder_stuck, "last_coder_error": coder_error,
           "reauthor_pending": False,   # consumed if it was set (the test was just re-authored)
           "log": state.log + [f"P4 iter{i}: {it_status} (attempts={attempts})"]}
    out.update(research_upd)   # last_research_iteration / research_pending=False / research_calls
    return out


# ── critic gate + verdict ladder (design §5.4, §5.8) ─────────────────────────
def _critic_adequacy(ctx: Ctx, criterion: str, test_src: str):
    """Critic adequacy review: is passing this staged test trustworthy evidence for the criterion?
    Defaults to adequate if the critic endpoint is unreachable (the ledger/red-first/scanner walls
    still gate — the critic is an ADDED layer, never the sole gate)."""
    from poc_foundry import tracing
    from poc_foundry.state import AdequacyReview
    try:
        from poc_foundry.models import build_chat_model
        # Give the structured-output call headroom: a reasoning model can spend tokens "thinking" and
        # hit finish_reason=length mid-JSON (→ LengthFinishReasonError) on the default budget. The
        # verdict is short; the extra ceiling just avoids truncation + a wasted call.
        llm = build_chat_model("critic", max_tokens=8000).with_structured_output(AdequacyReview)
        with tracing.span("critic", criterion=criterion[:200]) as _sp:
            rv = llm.invoke([("system", prompts.CRITIC_SYSTEM),
                             ("human", prompts.critic_adequacy_prompt(criterion, test_src, ctx.template.interface))])
            review = AdequacyReview(**rv) if isinstance(rv, dict) else rv
            _sp.update(output={"adequate": review.adequate, "reason": (review.reason or "")[:300]})
            return review
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
        # ONE evidence standard for EVERY promotion path (planning-chat ruling 2026-07-02 → DECISIONS #54):
        # met = passing in a fresh VM ∧ adequacy-vetted ∧ (red-first-validated OR met-existing). A
        # met-existing test is FRESHLY authored, never adequacy-vetted, and (for i>0) never proven red — its
        # gaming vector is the TESTER (a weak/tautological test green against existing code). So it consults
        # adequacy too; an inadequate one routes to the weak-test re-author rung (#42), NOT straight to accept.
        review = _critic_adequacy(ctx, state.pending_criterion, state.pending_test_src)
        if review.adequate:
            disposition, reason = "accept", review.reason or (
                "criterion met by existing implementation" if status == "met-existing" else "adequate")
        elif degraded:
            # A same-family critic can't INDEPENDENTLY certify adequacy (design §5.4) → ADVISORY
            # (recorded, non-blocking). The hard walls still gate; blocking adequacy returns with a
            # distinct frontier critic.
            disposition, reason = "accept", "adequacy concern recorded (degraded critic, non-blocking)"
            advisory = review.reason
        elif state.reauthor_count < cfg.reauthor_cap:
            # M6 weak-test recovery (dual of the buggy-test rung): the test is GREEN but the critic
            # judged it GAMEABLE (a shortcut/echo stub could pass). Strengthen JUST this test —
            # critic's critique → tester — rather than a coarse full respec. The coder must then
            # re-pass the stronger test; red-first + the critic re-gate it. (Applies equally to a
            # met-existing test: an inadequate one is re-authored, not accepted — #54.)
            disposition, reason = "fix", "test green but gameable — strengthening it (re-author)"
            upd["reauthor_pending"] = True
            upd["reauthor_reason"] = ("the test is GAMEABLE — " + (review.reason or "")
                                      + " Make it stronger so a shortcut/echo/keyword stub FAILS.")
            upd["reauthor_count"] = state.reauthor_count + 1
        elif state.respec_count < cfg.respec_cap:
            disposition, reason = "respec", review.reason or "test inadequate / gameable"
        else:
            disposition, reason = "descope", review.reason or "test inadequate; respec/re-author caps reached"
    elif status == "incident":
        disposition, reason = "descope", "integrity incident — gamed iteration not rewarded"
    elif status == "red-first-failed":
        # the staged test passed against the DO-NOTHING scaffold → the TEST is inadequate, not the code;
        # reusing it just loops (run #6 burned 4 attempts this way). Re-author it once WITH feedback, then
        # fall back to the fix/descope budget.
        if state.reauthor_count < cfg.reauthor_cap:
            disposition, reason = "fix", "red-first violation — re-authoring the test (it passed against the stub)"
            upd["reauthor_pending"] = True
            upd["reauthor_reason"] = ("the staged test passed against a DO-NOTHING stub (red-first violation): it "
                                      "does not actually require the behaviour. Tighten it so the unimplemented "
                                      "stub FAILS — assert the real positive signal — while staying shortcut-proof "
                                      "(a constant/echo/lookup stub must also fail).")
            upd["reauthor_count"] = state.reauthor_count + 1
        elif state.fix_count < K:
            disposition, reason = "fix", "red-first failure — retry"
        else:
            disposition, reason = "descope", "red-first failures exhausted budgets"
    else:  # abandoned — coder did not reach green
        # ESCALATION LADDER (design §5.8): a STUCK abandon (repeated error) not yet researched THIS
        # iteration → grant a fix BUT request targeted research first (p4 runs it on re-entry). The
        # rung fires at most once per iteration (last_research_iteration guard) before the normal
        # fix → replan → descope budget ladder resumes.
        researched = state.last_research_iteration == state.iteration   # research rung consumed this iter
        can_research = state.last_coder_stuck and not state.research_pending and not researched
        if can_research and state.fix_count < K:
            disposition, reason = "fix", "stuck on a repeated error — escalating to targeted research"
            upd["research_pending"] = True
            upd["research_error"] = state.last_coder_error or "coder stuck (repeated error)"
        elif state.fix_count < K:
            disposition, reason = "fix", "coder did not reach green — another iteration"
        elif state.reauthor_count < cfg.reauthor_cap:
            # M6 buggy-test recovery: the coder spent its WHOLE fix budget and never went green — the
            # staged test itself may be flawed/impossible (e.g. the str-vs-int citation bug), and the
            # coder can't fix it (it can't edit the test). Re-author it ONCE with the coder's diagnosis +
            # a FRESH fix budget, BEFORE the heavier replan. Fires whether the errors repeated or varied
            # (not gated on "stuck"). Red-first + the critic still gate the re-authored test.
            disposition, reason = "fix", "fix budget exhausted — re-authoring the staged test (it may be flawed)"
            upd["reauthor_pending"] = True
            upd["reauthor_reason"] = (state.last_coder_error or "")[-800:]
            upd["reauthor_count"] = state.reauthor_count + 1
        elif state.replan_count < cfg.replan_cap:
            disposition, reason = "replan", "fix budget exhausted — replan remaining"
        else:
            disposition, reason = "descope", "fix + replan budgets exhausted"

    # ── map disposition → routing verdict (+ counters / advancement / records) ──
    if advisory is not None:
        upd["caveats"] = state.caveats + [f"critic (degraded, advisory): {advisory}"]

    if disposition == "fix":
        verdict = "fix"
        # a re-author grants a FRESH fix budget vs the corrected test; a plain fix consumes one.
        upd["fix_count"] = 0 if upd.get("reauthor_pending") else state.fix_count + 1
    elif disposition == "respec":
        verdict = "respec"
        upd["respec_count"] = state.respec_count + 1
    elif disposition == "replan":
        verdict = "replan"
        upd["replan_count"] = state.replan_count + 1
        upd["replan_mode"] = True     # SAME spec → P3 preserves the workspace; met criteria fast-path
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
            upd["reauthor_count"] = 0     # …and a fresh buggy-test re-author budget
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


# ── Layer-1 rehabilitation sweep (planning-chat ruling 2026-07-02 → DECISIONS #51) ───────────────
def _test_file_for_criterion(state) -> dict:
    """Map each success-criterion text → its already-authored staged-test filename (the plan iteration
    that targeted it). Mirrors p4's ``it.test_file or f"test_iter_{i}.py"``."""
    out: dict[str, str] = {}
    for idx, it in enumerate(state.plan.iterations if state.plan else []):
        tf = it.test_file or f"test_iter_{idx}.py"
        for ctext in it.acceptance:
            out.setdefault(ctext, tf)
    return out


def _rehabilitation_sweep(state, ctx: Ctx):
    """A pass is a pass: a criterion honestly DESCOPED earlier (a core-first descope, or a budget
    exhaustion) may be satisfied by a LATER iteration's code. Re-run each descoped criterion's
    already-authored (red-first, critic-vetted) staged test ONCE against the FINAL workspace in a fresh
    VM; promote descoped→met iff it passes AND the critic certifies the test adequate. Deterministic, no
    coder, no retries (guardrail 4). Promoted tests then publish + re-run in the clean-room's fresh clone
    (a second independent pass — guardrail 1). Mutates ``state.spec`` criteria statuses in place; returns
    ``(promoted_files, new_descope_report, notes)`` — ``new_descope_report`` converts a promoted entry to
    a rehabilitation note rather than deleting it (guardrail 3)."""
    spec = state.spec
    if not spec:
        return [], list(state.descope_report), []
    descoped = [c for c in spec.success_criteria if c.status == "descoped"]
    if not descoped:
        return [], list(state.descope_report), []

    from poc_foundry.models import same_family
    degraded = same_family("critic", "coder")
    test_of = _test_file_for_criterion(state)
    staging_tests = ctx.staging_dir / "tests"
    promoted: list[str] = []          # criterion texts promoted
    promoted_files: list[str] = []
    notes: list[str] = []

    sbx = ctx.broker.create(mounts=[ws_mount(ctx.workspace_dir), staged_tests_mount(staging_tests)],
                            name="rehab", env_extra=dict(ctx.service_env))
    try:
        for c in descoped:
            tf = test_of.get(c.text)
            if not tf or not (staging_tests / tf).exists():
                continue
            if not _verify_file(sbx, tf):          # must be GREEN against the final workspace
                continue
            review = _critic_adequacy(ctx, c.text, (staging_tests / tf).read_text())   # guardrail 2
            if not review.adequate and not degraded:
                notes.append(f"rehab: '{c.text[:60]}' passes on the final workspace but the critic judged "
                             f"the test inadequate ({(review.reason or '')[:80]}) — left descoped")
                continue
            c.status = "met"                       # promote
            promoted.append(c.text)
            if tf not in state.green_test_files and tf not in promoted_files:
                promoted_files.append(tf)
            if not review.adequate and degraded:
                notes.append(f"rehab: '{c.text[:60]}' promoted; adequacy concern recorded "
                             f"(degraded critic, non-blocking)")
    finally:
        sbx.destroy()

    if not promoted:
        return [], list(state.descope_report), notes
    new_report = []
    for e in state.descope_report:
        if e.get("criterion") in promoted:
            e = dict(e, resolved="met by the final implementation (rehabilitation sweep)",
                     originally_descoped=e.get("why_failed", ""),
                     finish_path="none — rehabilitated")
        new_report.append(e)
    for c in promoted:
        ctx.say(f"P5 rehab: criterion promoted descoped→met (staged test green on final workspace): {c[:70]}")
    return promoted_files, new_report, notes


def p5_docs(state, ctx: Ctx) -> dict:
    # Layer-1: promote descoped-but-now-passing criteria BEFORE publish, so their tests ship + the
    # clean-room re-runs them in a fresh clone (the second independent pass).
    promoted_files, new_report, rehab_notes = _rehabilitation_sweep(state, ctx)
    if promoted_files:
        state.green_test_files = state.green_test_files + [f for f in promoted_files
                                                           if f not in state.green_test_files]
    published = _publish_tests(state, ctx)
    (ctx.workspace_dir / "DEMO.md").write_text(_scribe_demo(ctx, state.spec))
    chown_to_builder(ctx.workspace_dir)   # published tests + DEMO must be writable/readable by uid 1000
    sha = git_commit(ctx.workspace_dir, f"docs: DEMO.md + publish {published} criterion test file(s)")
    ctx.say(f"P5 docs: DEMO.md + published {published} criterion test file(s) into tests/")
    log = state.log + ([f"P5 rehab: {len(promoted_files)} descoped criterion(s) promoted to met"]
                       if promoted_files else [])
    log = log + [f"P5 docs: DEMO.md + {published} test file(s) published"]
    return {"phase": "docs", "commit_sha": sha, "demo_quality": "thin",
            "spec": state.spec,
            "green_test_files": state.green_test_files,
            "descope_report": new_report,
            "caveats": state.caveats + rehab_notes,
            "log": log}


# ── P6 cleanroom (fresh VM + fresh clone) ────────────────────────────────────
def p6_cleanroom(state, ctx: Ctx) -> dict:
    clone = ctx.staging_dir / "cleanroom"
    if clone.exists():
        shutil.rmtree(clone)
    subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q",
                    str(ctx.workspace_dir), str(clone)], check=True)
    chown_to_builder(clone)   # the clean-room VM (uid 1000) installs deps + writes caches here

    from poc_foundry import tracing

    blocks = parse_run_blocks((clone / "RUN.md").read_text())
    result = {"quickstart_ok": False, "suite_ok": False, "demo_ok": False}
    fails: list[str] = []
    with tracing.span("cleanroom") as _sp:
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
        _sp.update(output=result)

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


# M7 Layer-2 (planning-chat ruling 2026-07-02 → DECISIONS #52). The walls exist so a build can continue
# SAFELY after a caught attempt; if every wall trip permanently caps the verdict, the walls become
# self-defeating — and a verdict that reads `incomplete/no` for a build meeting every §1.2 DONE condition
# is miscalibrated (teaches people to ignore it). A high incident is EVIDENCE ABOUT THE RUN; the verdict is
# a CLAIM ABOUT THE ARTIFACT — and the shipped HEAD contains none of the rolled-back edit. So a SINGLE
# contained-and-superseded incident may be downgraded blocking→informational iff ALL four hold. Repeated /
# multiple high incidents KEEP the cap (the METR signal: attempts-under-difficulty → scrutiny scales up).
_DOWNGRADEABLE_KINDS = frozenset({          # gate-caught CONSTRUCTION incidents → always rolled back, never
    "hard-exit", "skip-marker", "test-edit", "pytest-config", "assert-deleted", "ledger-gap", "red-first",
})  # committed. Excludes broker-invariant-rejection / research-injection: security-load-bearing, keep cap.


def _incident_downgradeable(state) -> bool:
    """True iff the build's blocking incident may be downgraded to recorded-informational (all four
    ruling conditions). Conservative: any doubt → False (cap stays)."""
    highs = [s for s in state.incidents if s.startswith("[high]")]
    if len(highs) != 1:                                 # (4) exactly one high incident
        return False
    kinds = state.high_incident_kinds
    if not kinds or any(k not in _DOWNGRADEABLE_KINDS for k in kinds):   # (1) rolled-back construction kind
        return False
    if not state.cleanroom.get("suite_ok"):             # (3) clean-room green on the final artifact
        return False
    by_text = {c.text: c.status for c in state.spec.success_criteria} if state.spec else {}
    targeted = state.high_incident_criteria
    if not targeted or any(by_text.get(t) != "met" for t in targeted):   # (2) targeted criterion now met
        return False
    return True


def _trustworthy(state) -> bool:
    """The M2a integrity gate: the build's success claim is only trustworthy if the inventory ledger
    held, every accepted iteration was red-first, and no high-severity integrity incident fired — EXCEPT
    a single contained-and-superseded incident that P7 downgrades (M7 Layer-2)."""
    if not (bool(state.inventory_ok) and bool(state.red_first_ok)):
        return False
    return (not _has_blocking_incident(state)) or _incident_downgradeable(state)


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

    from poc_foundry.models import METER
    snap = METER.snapshot()                                  # llm_calls / wall_s / contention_indicator
    art = ctx.run_folder.artifact if ctx.run_folder else None
    status = _final_status(state)
    core_met = bool(state.spec) and any(c.core and c.status == "met" for c in state.spec.success_criteria)
    demonstrates = ("yes" if (core_met and state.cleanroom.get("suite_ok") and _trustworthy(state))
                    else ("partial" if core_met else "no"))
    # honest gap list: every criterion not `met` (descoped / pending / partial), judged vs the spec.
    gaps = [c.text for c in (state.spec.success_criteria if state.spec else []) if c.status != "met"]

    allowlist = []
    try:
        import yaml  # tolerated-absent
        y = yaml.safe_load((Path(ctx.cfg.builds_dir).parent / "config" / "pipeline.yaml").read_text())
        allowlist = list((y.get("egress_allowlist", {}) or {}).get("hosts", []))
    except Exception:  # noqa: BLE001
        allowlist = []

    # broker-side rejected-create* records (M4 S2, §5.2) → security.incidents[]: the daemon (rule-#8
    # enforcer) durably audits every blocked create*; surface them as high-severity evidence here.
    incidents = list(state.incidents)
    # M7 Layer-2: a downgraded incident is NEVER deleted — it keeps its full record with a visible
    # `resolved:` annotation (the walls fired, it was contained, the shipped code contains none of it).
    downgraded = _incident_downgradeable(state)
    if downgraded:
        incidents = [(s + "  — resolved: rolled-back-and-superseded (contained; not in shipped HEAD)")
                     if s.startswith("[high]") else s for s in incidents]
    try:
        for e in (ctx.broker.audit() if hasattr(ctx.broker, "audit") else []):
            if e.get("event") == "rejected":
                incidents.append(f"[high] broker-invariant-rejection: {e.get('method', '')} — "
                                 f"{(e.get('reason') or '')[:200]}")
    except Exception:  # noqa: BLE001 — audit read is best-effort, never fails emit
        pass

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
        final_verdict=FinalVerdict(demonstrates_core_value=demonstrates, gaps=gaps),
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
                              incidents=incidents,
                              degraded_critic=bool(state.degraded_critic),
                              had_high_incident=bool(state.had_high_incident)),
        budget=Budget(wall_s=snap["wall_s"], llm_calls=snap["llm_calls"],
                      contention_indicator=snap["contention_indicator"]),
        caps_hit=list(state.caps_hit),
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
        from poc_foundry import tracing
        egress = ctx.broker.proxy_log()
        (logs / "egress.log").write_text(egress)
        denied = egress.count("TCP_DENIED")
        if denied:
            tracing.event("proxy.denials", count=denied)   # egress-security evidence (the detective control)
    except Exception:  # noqa: BLE001
        pass
    (build_dir / "PROGRESS.md").write_text(
        "# Progress\n\n" + "\n".join(f"- {ln}" for ln in state.log) + "\n")
    report = _report_md(state, ctx, pa)
    (build_dir / "report.md").write_text(report)
    (build_dir / "00_INDEX.md").write_text(_index_md(pa, build_dir))

    # hygiene scrubber (rule #1): rewrite the vLLM host / served-model id / paths to placeholders in
    # ALL emitted text (artifact JSON, report, index, progress, egress log) so a shared bundle is clean.
    from poc_foundry import scrub
    scrubbed = scrub.scrub_build_dir(build_dir)
    if scrubbed:
        ctx.say(f"P7 emit: hygiene scrubber rewrote {len(scrubbed)} emitted file(s)")

    # experience loop (M2c S3): distil this build's Tier-1 lessons into ONE low-authority EXPIRING
    # hint for future builds. The lessons are already scrubbed on disk (above); scrub again defensively
    # before it lands in the (gitignored) playbooks/hints/ tree — it's LLM-generated + untrusted.
    try:   # the experience loop is a nice-to-have — a hint-write failure must NEVER fail the build
        from poc_foundry import playbooks
        iters_dir = build_dir / "iterations"
        lessons = sorted(iters_dir.glob("*/lessons.md")) if iters_dir.exists() else []
        if lessons:
            raw = "\n\n".join(p.read_text() for p in lessons)
            clean = scrub.scrub_text(raw, scrub.collect_secrets())
            hint = playbooks.write_hint(clean, source_build=ctx.build_id, applies_to=["coder", "gotchas"])
            ctx.say(f"P7 emit: distilled {len(lessons)} lesson(s) → "
                    + (f"low-authority hint {hint.name}" if hint
                       else "hint NOT persisted (hints dir not writable — chmod 777 playbooks/hints)"))
    except Exception as e:  # noqa: BLE001
        ctx.say(f"P7 emit: experience-loop hint skipped ({type(e).__name__})")

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
    if pa.security.had_high_incident:
        if _incident_downgradeable(state):
            lines += ["", "### Integrity events (contained)", "",
                      "- A high-severity integrity event fired during construction and was **contained**: the "
                      "coder's edit was rolled back and never committed, the criterion it targeted was "
                      "subsequently met via a clean gate-approved path, and the clean-room is green on the "
                      "final artifact. The shipped HEAD contains **none** of the flagged edit, so it is "
                      "recorded as informational and does not cap the verdict (single, contained, superseded)."]
        else:
            lines += ["", "### Integrity events", "",
                      "- One or more high-severity integrity events fired and **cap** the verdict (`trustworthy=False`): "
                      "review the incidents above before trusting the success claim."]
    lines += ["", "## Critic gate (§5.4)", "",
              f"- last verdict: {state.verdict or 'pass'}",
              f"- degraded_critic: {pa.security.degraded_critic} "
              f"(fix budget K={ctx.cfg.degraded_fix_limit_k if pa.security.degraded_critic else ctx.cfg.fix_limit_k}; "
              f"fixes={state.fix_count}, respecs={state.respec_count}, replans={state.replan_count})",
              f"- had_high_incident: {pa.security.had_high_incident}"]
    if pa.final_verdict.gaps:
        lines += ["", "## Gaps vs spec", ""] + [f"- {g}" for g in pa.final_verdict.gaps]
    if pa.descope_report:
        lines += ["", "## Descope report", ""]
        for d in pa.descope_report:
            lines.append(f"- **{d.criterion}** — {d.why_failed} (after {d.attempts_made} attempt(s)); "
                         f"finish: {d.finish_path}")
    lines += ["", "## Budget (§5.8)", "",
              f"- llm_calls: {pa.budget.llm_calls}",
              f"- wall_s: {pa.budget.wall_s}",
              f"- contention_indicator (median call latency s): {pa.budget.contention_indicator}",
              f"- caps_hit: {pa.caps_hit or 'none'}"]
    lines += ["", "## Clean-room", "",
              f"- install: {pa.cleanroom.quickstart_ok}",
              f"- suite: {pa.cleanroom.suite_ok}",
              f"- demo: {pa.cleanroom.demo_ok}"]
    if pa.caveats:
        lines += ["", "## Caveats", ""] + [f"- {c}" for c in pa.caveats]
    return "\n".join(lines) + "\n"


def _index_md(pa, build_dir: Path) -> str:
    lines = [f"# {pa.id}\n",
             f"Stage-3 PoC build from `{pa.source_artifact.id}` — **{pa.status}**.\n",
             f"- `v{pa.version:02d}.json` — the PoCBuildArtifact (this build's output contract)",
             "- `workspace/` — the standalone, runnable PoC (see `workspace/RUN.md`)",
             "- `report.md` — human-readable build report",
             "- `PROGRESS.md` — phase trace",
             "- `logs/egress.log` — proxy CONNECT log (egress security evidence)"]
    if (build_dir / "abandoned.patch").exists():
        lines.append("- `abandoned.patch` — un-merged in-flight work from a salvaged iteration "
                     "(apply + finish by hand to complete the descoped criterion)")
    return "\n".join(lines) + "\n"
